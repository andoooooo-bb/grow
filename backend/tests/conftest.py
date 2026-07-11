"""API テスト用フィクスチャ（専用DB grow_test を使用）。

dev DB（grow）には触れない。postgres 管理DBへ接続して grow_test を再作成し、
backend/db/schema.sql を適用、各テストの前に seed.sql を再投入する。
DB へ接続できない環境では skip する（test_schema.py と同じ方針）。

これらのフィクスチャは要求したテストにのみ適用される（既存テストへは影響しない）。
"""

import asyncio
from pathlib import Path

import asyncpg
import httpx
import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / "db"
_ADMIN_URL = "postgresql://grow:grow@localhost:54329/postgres"
TEST_DB_URL = "postgresql://grow:grow@localhost:54329/grow_test"


def _recreate_test_db() -> str | None:
    """grow_test を作り直してスキーマを適用する。失敗時は理由を返す（成功時 None）。"""

    async def _run() -> None:
        admin = await asyncpg.connect(_ADMIN_URL, timeout=3)
        try:
            await admin.execute("drop database if exists grow_test with (force)")
            await admin.execute("create database grow_test owner grow")
        finally:
            await admin.close()
        conn = await asyncpg.connect(TEST_DB_URL, timeout=3)
        try:
            await conn.execute((_DB_DIR / "schema.sql").read_text())
        finally:
            await conn.close()

    try:
        asyncio.run(_run())
    except (OSError, asyncpg.PostgresError) as exc:
        return str(exc)
    return None


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """セッションで一度だけ grow_test を再作成する（接続不可なら skip）。"""
    error = _recreate_test_db()
    if error is not None:
        pytest.skip(f"テスト用DB grow_test を準備できないため skip: {error}")
    return TEST_DB_URL


@pytest.fixture
async def api_client(test_db_url: str, monkeypatch: pytest.MonkeyPatch):
    """grow_test に向けた ASGI クライアント。各テスト前にシードを再投入する。"""
    from app import db
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    get_settings.cache_clear()
    await db.close_pool()  # 前のイベントループに紐づくプールを破棄

    conn = await asyncpg.connect(test_db_url, timeout=3)
    try:
        await conn.execute(
            # rule_signals / rule_proposals / rule_feedback は cascade（rules/tasks/workspaces
            # への FK）で共に消える。knowledge_ci_runs は FK を持たないため明示する（#26）
            "truncate workspaces, users, boards, lanes, tasks, comments, chat_messages, "
            "rules, rule_applications, ai_jobs, artifacts, knowledge_ci_runs cascade"
        )
        await conn.execute((_DB_DIR / "seed.sql").read_text())
    finally:
        await conn.close()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # local ランナーの実行中ジョブ（#27 intake の自動 enqueue 等）を先に完了させる
    # （プール破棄後に走ると grow_test 外へ書きに行く危険があるため）
    from app.jobs.queue import drain_local_jobs

    await drain_local_jobs()
    await db.close_pool()
    get_settings.cache_clear()  # 後続テスト（test_schema 等）は dev DB 設定に戻す
