"""AI 利用ガード（#security — 公開デプロイのコスト暴走・悪意アクセス対策）のテスト。

2 層の安全弁を検証する:
- レート制限（assert_ai_rate）: プロセス内スライディングウィンドウ。上限超で 429、
  ウィンドウ経過で回復。ai_guard_enabled=False で素通し。
- 日次予算キルスイッチ（assert_ai_budget）: 当日 UTC の sum(ai_jobs.cost_usd) が
  上限以上で 429。昨日のコストは当日カウントに入らない。ai_guard_enabled=False で素通し。
- 統合: 予算超過で assign-ai が 429（ジョブを作らない）／予算内なら 202 のまま。

予算ガードのDB集計は api_client の truncate で毎テスト 0 から始まる。
レートウィンドウ（プロセス内 deque）は conftest の autouse フィクスチャで毎テスト空になる。
"""

import httpx
import pytest
from fastapi import HTTPException

from app import guard
from app.config import get_settings
from app.guard import (
    BUDGET_LIMIT_DETAIL,
    RATE_LIMIT_DETAIL,
    assert_ai_budget,
    assert_ai_rate,
    reset_rate_state,
)
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect


async def _insert_job_cost(conn, cost: float, *, day_offset: int = 0) -> None:
    """seed 済みタスクに紐づく ai_jobs を1件差し込む（day_offset 日ずらした created_at）。"""
    task_id = await conn.fetchval("select id from tasks limit 1")
    await conn.execute(
        "insert into ai_jobs (task_id, kind, status, cost_usd, created_at) "
        "values ($1, 'execute', 'succeeded', $2, now() - ($3 || ' days')::interval)",
        task_id,
        cost,
        str(day_offset),
    )


# ---- レート制限（プロセス内スライディングウィンドウ） -------------------------------


def test_assert_ai_rate_blocks_over_limit_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上限+1回目で 429、ウィンドウ経過で回復する（monotonic は _now を差し替え）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_max", 3)
    monkeypatch.setattr(settings, "ai_rate_window_sec", 100)
    reset_rate_state()

    clock = {"t": 1000.0}
    monkeypatch.setattr(guard, "_now", lambda: clock["t"])

    # 上限ちょうど（3回）は素通し
    for _ in range(3):
        assert_ai_rate()

    # 4回目（上限+1）は 429
    with pytest.raises(HTTPException) as exc_info:
        assert_ai_rate()
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == RATE_LIMIT_DETAIL

    # ウィンドウ経過（+100s）で古い記録が抜けて回復する
    clock["t"] = 1000.0 + 100
    assert_ai_rate()  # 例外が出なければ回復している


def test_assert_ai_rate_disabled_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """ai_guard_enabled=False なら上限を無視して素通しする。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", False)
    monkeypatch.setattr(settings, "ai_rate_max", 1)
    reset_rate_state()

    for _ in range(10):
        assert_ai_rate()  # 何回呼んでも 429 にならない


# ---- 日次予算キルスイッチ（当日 UTC の sum(cost_usd)） -----------------------------


async def test_assert_ai_budget_yesterday_not_counted(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """昨日のコストは当日集計に入らない。今日のコストが上限以上になると 429。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_budget_usd", 0.5)

    conn = await db_connect()
    try:
        # 昨日のコスト（上限超）だけでは当日集計は 0 → 素通し
        await _insert_job_cost(conn, 1.0, day_offset=1)
        await assert_ai_budget(conn)  # 例外なし

        # 今日のコストを積むと当日集計が上限以上 → 429
        await _insert_job_cost(conn, 1.0, day_offset=0)
        with pytest.raises(HTTPException) as exc_info:
            await assert_ai_budget(conn)
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == BUDGET_LIMIT_DETAIL
    finally:
        await conn.close()


async def test_assert_ai_budget_disabled_passes_through(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ai_guard_enabled=False なら当日コストが上限超でも素通しする。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", False)
    monkeypatch.setattr(settings, "ai_daily_budget_usd", 0.5)

    conn = await db_connect()
    try:
        await _insert_job_cost(conn, 10.0, day_offset=0)
        await assert_ai_budget(conn)  # 無効なので例外なし
    finally:
        await conn.close()


# ---- 統合: assign-ai エンドポイント -------------------------------------------------


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue_job をフェイク化して実ジョブを走らせない（test_assign_ai と同型）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


async def test_assign_ai_blocked_by_budget(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当日コストが日次予算以上なら assign-ai が 429。ジョブも作られない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_budget_usd", 0.5)

    conn = await db_connect()
    try:
        await _insert_job_cost(conn, 1.0, day_offset=0)  # 当日 1.0 >= 0.5
    finally:
        await conn.close()

    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 429
    assert res.json()["detail"] == BUDGET_LIMIT_DETAIL
    assert captured_jobs == []  # enqueue されていない

    conn = await db_connect()
    try:
        # ガードは transaction 前に効くので T-104 は元の状態のまま（着手されていない）
        task = await conn.fetchrow("select status from tasks where human_id = 'T-104'")
        assert task["status"] != "ai_work"
        # 差し込んだ 1 件以外に新しいジョブ（queued）は作られていない
        queued = await conn.fetchval("select count(*) from ai_jobs where status = 'queued'")
        assert queued == 0
    finally:
        await conn.close()


async def test_assign_ai_within_budget_still_202(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """予算内・レート内ならガードを入れても従来どおり 202 で着手できる。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_budget_usd", 5.0)

    conn = await db_connect()
    try:
        await _insert_job_cost(conn, 0.10, day_offset=0)  # 当日 0.10 < 5.0
    finally:
        await conn.close()

    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202
    assert captured_jobs == [res.json()["jobId"]]


async def test_assign_ai_blocked_by_rate(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """レート上限に達すると assign-ai が 429（予算は潤沢）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "ai_daily_budget_usd", 5.0)
    monkeypatch.setattr(settings, "ai_rate_max", 1)
    monkeypatch.setattr(settings, "ai_rate_window_sec", 600)
    reset_rate_state()

    # 1回目（上限ちょうど）は通る
    res1 = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res1.status_code == 202

    # 2回目はレート超過で 429（別タスクに投げても同じ = プロセス全体のレート）
    res2 = await api_client.post("/api/tasks/T-121/assign-ai")
    assert res2.status_code == 429
    assert res2.json()["detail"] == RATE_LIMIT_DETAIL
