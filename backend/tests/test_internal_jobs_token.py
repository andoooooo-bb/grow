"""POST /internal/jobs/run のトークン保護（INTERNAL_JOBS_TOKEN, #16）のテスト。

本番は Cloud Run を --allow-unauthenticated で公開するため、worker エンドポイントは
INTERNAL_JOBS_TOKEN 設定時のみ X-Internal-Jobs-Token ヘッダの一致を検証する
（enqueue 側 app/jobs/queue.py が同ヘッダを付与）。
未設定（ローカル・テスト既定）は従来通り素通しで後方互換を保つ。
"""

import httpx
import pytest

from app.config import get_settings
from app.jobs import execute as execute_mod
from app.jobs import queue as jobs_queue

TOKEN = "test-internal-jobs-token"


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch):
    """INTERNAL_JOBS_TOKEN を設定して settings キャッシュを更新する。"""
    monkeypatch.setenv("INTERNAL_JOBS_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def job_test_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """assign-ai の enqueue をフェイク化し演出ディレイを 0 にする（test_execute_job と同方針）。"""

    async def _fake_enqueue(job_id: str) -> None:
        return None

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    monkeypatch.setattr(execute_mod, "PROGRESS_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "COMPLETE_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


async def test_rejects_request_without_token(token_env) -> None:
    """設定時: ヘッダ無しは 403（DB に触れる前に拒否するため DB 不要）。"""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/internal/jobs/run", json={"jobId": "any"})
    assert res.status_code == 403


async def test_rejects_request_with_wrong_token(token_env) -> None:
    """設定時: 不一致ヘッダは 403。"""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/internal/jobs/run",
            json={"jobId": "any"},
            headers={"X-Internal-Jobs-Token": "wrong-token"},
        )
    assert res.status_code == 403


async def test_accepts_matching_token(
    api_client: httpx.AsyncClient, token_env, job_test_setup
) -> None:
    """設定時: 一致ヘッダはジョブ実行に到達し 200 を返す。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]

    run = await api_client.post(
        "/internal/jobs/run",
        json={"jobId": job_id},
        headers={"X-Internal-Jobs-Token": TOKEN},
    )
    assert run.status_code == 200
    assert run.json() == {"status": "succeeded"}


async def test_passes_through_when_token_not_configured(
    api_client: httpx.AsyncClient, job_test_setup
) -> None:
    """未設定時: ヘッダ無しでも従来通り実行される（ローカル互換）。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]

    run = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert run.status_code == 200
    assert run.json() == {"status": "succeeded"}
