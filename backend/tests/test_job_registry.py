"""kind ディスパッチテーブル（app/jobs/registry.py, #18）のテスト。

- テーブル操作（登録/取得/未知kind）は DB 不要の単体テスト。
- dispatch_job の実 DB 経路（行取得 → ハンドラ呼び出し / 未知kind→failed）は
  grow_test を使う（既存 conftest の api_client / helpers.db_connect に乗る）。
- kind='execute' の既存フロー自体は test_execute_job.py / test_internal_jobs_token.py
  が worker エンドポイント経由で検証済み（非破壊の確認はそちらに委ねる）。
"""

from uuid import uuid4

import httpx
import pytest

from app.domain.models import AiJobKind
from app.jobs import registry
from app.jobs.execute import JobNotFoundError, run_execute_job_row
from tests.helpers import db_connect

# ---- テーブル操作（DB 不要） -------------------------------------------------------


def test_execute_kind_is_registered() -> None:
    """既定登録: kind='execute' は run_execute_job_row に解決される。"""
    assert registry.get_handler(AiJobKind.EXECUTE) is run_execute_job_row
    assert registry.get_handler("execute") is run_execute_job_row  # 文字列でも同じ


def test_get_handler_unknown_kind_raises() -> None:
    """未登録 kind は UnknownJobKindError（メッセージに kind を含む）。"""
    with pytest.raises(registry.UnknownJobKindError, match="no-such-kind"):
        registry.get_handler("no-such-kind")


def test_register_adds_new_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """新 kind は register() の1行で解決可能になる（テーブルはテスト後に復元）。"""
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))

    async def _handler(job_row, *, max_retries=None, handoff_on_failure=True) -> bool:
        return True

    registry.register("orchestrate", _handler)
    assert registry.get_handler("orchestrate") is _handler
    # 既存登録には影響しない
    assert registry.get_handler(AiJobKind.EXECUTE) is run_execute_job_row


# ---- dispatch_job（実 DB） ---------------------------------------------------------


async def _insert_job(kind: str, status: str = "queued") -> str:
    """seed 済み grow_test に任意 kind の ai_jobs 行を作る（対象タスクは T-104）。"""
    conn = await db_connect()
    try:
        row = await conn.fetchrow(
            "insert into ai_jobs (task_id, kind, status) "
            "select id, $1, $2 from tasks where human_id = 'T-104' returning id",
            kind,
            status,
        )
        return str(row["id"])
    finally:
        await conn.close()


async def test_dispatch_calls_registered_handler_with_job_row(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dispatch_job: ai_jobs 行の kind でハンドラを引き、行とオプションを渡す。"""
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    calls: list[dict] = []

    async def _handler(job_row, *, max_retries=None, handoff_on_failure=True) -> bool:
        calls.append(
            {
                "job_id": str(job_row["id"]),
                "kind": job_row["kind"],
                "max_retries": max_retries,
                "handoff_on_failure": handoff_on_failure,
            }
        )
        return True

    registry.register("orchestrate", _handler)
    job_id = await _insert_job("orchestrate")

    ok = await registry.dispatch_job(job_id, max_retries=0, handoff_on_failure=False)

    assert ok is True
    assert calls == [
        {
            "job_id": job_id,
            "kind": "orchestrate",
            "max_retries": 0,
            "handoff_on_failure": False,
        }
    ]


async def test_dispatch_unknown_kind_marks_failed_and_raises(
    api_client: httpx.AsyncClient,
) -> None:
    """未知 kind: ai_jobs=failed（error 記録）+ UnknownJobKindError（明確なエラー, #18）。"""
    job_id = await _insert_job("no_such_kind")

    with pytest.raises(registry.UnknownJobKindError, match="no_such_kind"):
        await registry.dispatch_job(job_id)

    conn = await db_connect()
    try:
        row = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert row["status"] == "failed"
        assert row["error"] == "unknown job kind: no_such_kind"
        assert row["finished_at"] is not None
    finally:
        await conn.close()


async def test_dispatch_unknown_kind_keeps_terminal_status(
    api_client: httpx.AsyncClient,
) -> None:
    """未知 kind でも終端状態（failed/succeeded）の行は上書きしない（再配信に安全）。"""
    job_id = await _insert_job("no_such_kind", status="failed")
    conn = await db_connect()
    try:
        await conn.execute(
            "update ai_jobs set error = 'original error' where id = $1::uuid", job_id
        )
    finally:
        await conn.close()

    with pytest.raises(registry.UnknownJobKindError):
        await registry.dispatch_job(job_id)

    conn = await db_connect()
    try:
        row = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert row["status"] == "failed"
        assert row["error"] == "original error"  # 元の失敗理由を保持
    finally:
        await conn.close()


async def test_dispatch_missing_job_raises_not_found(api_client: httpx.AsyncClient) -> None:
    """存在しない jobId は JobNotFoundError（worker は 404 に変換）。"""
    with pytest.raises(JobNotFoundError):
        await registry.dispatch_job(str(uuid4()))


# ---- worker エンドポイント経由（未知 kind → 422） ----------------------------------


async def test_worker_endpoint_unknown_kind_returns_422(
    api_client: httpx.AsyncClient,
) -> None:
    """POST /internal/jobs/run: 未知 kind は 422 を返し、ai_jobs は failed になる。"""
    job_id = await _insert_job("no_such_kind")

    res = await api_client.post("/internal/jobs/run", json={"jobId": job_id})

    assert res.status_code == 422
    assert "unknown job kind: no_such_kind" in res.json()["detail"]
    conn = await db_connect()
    try:
        status = await conn.fetchval(
            "select status from ai_jobs where id = $1::uuid", job_id
        )
        assert status == "failed"
    finally:
        await conn.close()
