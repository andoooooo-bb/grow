"""コスト実算定（#25 app/costs.py / config 単価テーブル）のテスト。

- calc_cost_usd 単体: Pro（execute）/ Flash（それ以外）・ゼロ usage・環境変数上書き
- 統合: execute 完走で ai_jobs.cost_usd > 0（mock でも $ が動く）
- 統合: #21 のコスト上限（policy.costCapUsd）が実算定の累計で実際に発火する
"""

import httpx
import pytest

from app.ai.provider import TokenUsage
from app.config import get_settings
from app.costs import calc_cost_usd
from app.domain.models import AiJobKind
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_jobs


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（ジョブは手動で /internal/jobs/run を叩く）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str, *, kind: str | None = None) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


# ---- calc_cost_usd 単体 --------------------------------------------------------------


def test_calc_cost_usd_execute_uses_pro_prices() -> None:
    """execute は Pro 単価（input 1.25 / output 10.0 USD/100万トークン）。"""
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert calc_cost_usd(AiJobKind.EXECUTE, usage) == pytest.approx(1.25 + 10.0)
    # 端数もそのまま比例する（100万分の1）
    small = TokenUsage(input_tokens=1000, output_tokens=2000)
    assert calc_cost_usd(AiJobKind.EXECUTE, small) == pytest.approx(
        (1000 * 1.25 + 2000 * 10.0) / 1_000_000
    )


@pytest.mark.parametrize(
    "kind",
    [AiJobKind.REVIEW, AiJobKind.ORCHESTRATE, AiJobKind.BREAKDOWN, AiJobKind.DISTILL],
)
def test_calc_cost_usd_light_kinds_use_flash_prices(kind: AiJobKind) -> None:
    """execute 以外（review/orchestrate/breakdown/distill）は Flash 単価。"""
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert calc_cost_usd(kind, usage) == pytest.approx(0.30 + 2.50)


def test_calc_cost_usd_accepts_str_kind_and_zero_usage() -> None:
    """kind は文字列（DB 行の kind）でもよく、ゼロ usage はコスト 0。"""
    zero = TokenUsage(input_tokens=0, output_tokens=0)
    assert calc_cost_usd("execute", zero) == 0.0
    assert calc_cost_usd("review", zero) == 0.0
    usage = TokenUsage(input_tokens=100, output_tokens=0)
    assert calc_cost_usd("review", usage) == pytest.approx(100 * 0.30 / 1_000_000)


def test_calc_cost_usd_prices_overridable_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """単価テーブルは環境変数で上書きできる（単価改定への追従）。"""
    monkeypatch.setenv("PRICE_PRO_INPUT_USD_PER_MTOK", "2.0")
    monkeypatch.setenv("PRICE_PRO_OUTPUT_USD_PER_MTOK", "20.0")
    get_settings.cache_clear()
    try:
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=500_000)
        assert calc_cost_usd(AiJobKind.EXECUTE, usage) == pytest.approx(2.0 + 10.0)
    finally:
        monkeypatch.delenv("PRICE_PRO_INPUT_USD_PER_MTOK")
        monkeypatch.delenv("PRICE_PRO_OUTPUT_USD_PER_MTOK")
        get_settings.cache_clear()


# ---- 統合: execute 完走で cost_usd が実額になる ---------------------------------------


async def test_execute_records_real_cost(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """mock でも execute（Pro）の cost_usd が正の実額で記録される（張りぼて廃止 #25）。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202
    await drain_jobs(api_client, captured_jobs)

    conn = await db_connect()
    try:
        jobs = await conn.fetch(
            "select j.* from ai_jobs j join tasks t on t.id = j.task_id "
            "where t.human_id = 'T-104' order by j.created_at"
        )
        assert all(j["status"] == "succeeded" for j in jobs)
        for job in jobs:
            # DB 記録（numeric(10,4) 丸め後）が単価テーブルの実算定と一致する
            expected = calc_cost_usd(
                job["kind"],
                TokenUsage(
                    input_tokens=job["input_tokens"], output_tokens=job["output_tokens"]
                ),
            )
            assert float(job["cost_usd"]) == pytest.approx(expected, abs=5e-5)
        # execute（Pro・数百トークン規模）は丸め後も必ず正 = デモで $ が動く
        assert all(float(j["cost_usd"]) > 0.0 for j in jobs if j["kind"] == "execute")
        # タスク累計（#21 コスト上限の判定値）も正になる
        total = await conn.fetchval(
            "select sum(cost_usd) from ai_jobs j join tasks t on t.id = j.task_id "
            "where t.human_id = 'T-104'"
        )
        assert float(total) > 0.0
    finally:
        await conn.close()


# ---- 統合: コスト上限（#21）が実算定で発火する ----------------------------------------


async def test_cost_cap_fires_with_real_accounting(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """cap を極小にすると、実算定の累計だけで上限 409 が発火する（挿入シードなし）。"""
    # 上限は極小（1回の execute 実コストより確実に小さい）に設定して1周完走させる
    res = await api_client.patch(
        "/api/tasks/T-104",
        json={"policy": {"allowWebSearch": True, "costCapUsd": 0.001}},
    )
    assert res.status_code == 200

    first = await api_client.post("/api/tasks/T-104/assign-ai")
    assert first.status_code == 202  # 累計 0 < cap なので1回目は通る
    await drain_jobs(api_client, captured_jobs)

    conn = await db_connect()
    try:
        spent = await conn.fetchval(
            "select coalesce(sum(cost_usd), 0) from ai_jobs j "
            "join tasks t on t.id = j.task_id where t.human_id = 'T-104'"
        )
        assert float(spent) >= 0.001  # 実算定の累計が cap を超えている
    finally:
        await conn.close()

    # 2回目は実算定の累計により 409（cost cap reached）— 上限機能が実際に効く
    second = await api_client.post("/api/tasks/T-104/assign-ai")
    assert second.status_code == 409
    assert "cost cap reached" in second.json()["detail"]

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        # you_review → you_todo は許可外遷移（§5.6）なので現状維持のまま停止する
        assert task["status"] == "you_review"
        # 停止理由コメントが残り、新しい execute ジョブは作られない（#21 と同型）
        stop_comment = await conn.fetchval(
            "select count(*) from comments where task_id = $1 and text like $2",
            task["id"],
            "コスト上限%",
        )
        assert stop_comment == 1
        jobs = await conn.fetchval(
            "select count(*) from ai_jobs where task_id = $1", task["id"]
        )
        assert jobs == len(captured_jobs)  # 完走した連鎖ぶんのみ（新規なし）
    finally:
        await conn.close()
