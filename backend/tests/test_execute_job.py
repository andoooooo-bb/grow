"""execute ジョブ（app/jobs/execute.py, §7.2/§7.3）のテスト。

演出ディレイ・リトライバックオフは 0 秒に差し替え、
worker エンドポイント POST /internal/jobs/run 経由で実行を検証する。
local ランナー（asyncio.create_task）経路は test_local_runner_completes_job で検証。
"""

from uuid import uuid4

import httpx
import pytest

from app.config import get_settings
from app.events import bus
from app.jobs import execute as execute_mod
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_events


@pytest.fixture
def zero_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_mod, "PROGRESS_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "COMPLETE_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """assign-ai の enqueue をフェイク化（ジョブは手動で /internal/jobs/run を叩く）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


class _FailingProvider:
    """execute が常に失敗するモック（リトライ検証用）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task: dict, rules: list, comments: list):
        self.calls += 1
        raise RuntimeError("模擬的な失敗: provider が応答しません")


async def test_execute_job_success(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """完走: 中間コメント → artifacts v1 → you_review/review レーン/progress null → succeeded。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    drain_events(event_queue)  # assign 分のイベントは捨て、ジョブ分だけを検証する

    run = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert run.status_code == 200
    assert run.json() == {"status": "succeeded"}

    conn = await db_connect()
    try:
        # you_review・progress null（§5.6 不変条件）・review レーン末尾（T-091, T-089 の後ろ）
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_review"
        assert task["progress"] is None
        assert task["lane_key"] == "review"
        assert task["order_in_lane"] == 2

        # コメント: 着手 → 中間 → 完了（文言は Grow.dc.html 準拠）
        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert len(comments) == 3
        assert comments[1]["text"] == "作業を進めています…（途中経過を共有します）"
        assert comments[2]["text"] == (
            "完了しました。学習済みのルールに沿って仕上げています。レビューをお願いします。"
        )

        # artifacts: v1 が保存され、生成ジョブに紐づく。ルール文言が反映されている
        artifacts = await conn.fetch(
            "select * from artifacts where task_id = $1 order by version", task["id"]
        )
        assert len(artifacts) == 1
        assert artifacts[0]["version"] == 1
        assert str(artifacts[0]["job_id"]) == job_id
        assert "競合SaaS 5社の料金プランを調査" in artifacts[0]["content_md"]
        assert "絵文字は使わない。文体は簡潔・断定調に統一する" in artifacts[0]["content_md"]

        # ai_jobs: succeeded ＋ トークン/コスト記録（§00 #16。mock は cost 0.0）
        job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert job["status"] == "succeeded"
        assert job["input_tokens"] > 0
        assert job["output_tokens"] > 0
        assert float(job["cost_usd"]) == 0.0
        assert job["finished_at"] is not None
        assert job["error"] is None
    finally:
        await conn.close()

    # SSE: 中間（comment→task 45%）→ artifact.created → 完了（comment→task you_review）
    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "comment.created",
        "task.updated",
        "artifact.created",
        "comment.created",
        "task.updated",
    ]
    assert events[1]["payload"]["progress"] == 45
    assert events[1]["payload"]["status"] == "ai_work"
    artifact_payload = events[2]["payload"]
    assert artifact_payload["taskId"] == "T-104"
    assert artifact_payload["version"] == 1
    assert artifact_payload["jobId"] == job_id
    assert artifact_payload["contentMd"].startswith("# ")
    assert events[4]["payload"]["status"] == "you_review"
    assert events[4]["payload"]["progress"] is None
    assert events[4]["payload"]["laneKey"] == "review"


async def test_local_runner_completes_job(
    api_client: httpx.AsyncClient, zero_delays
) -> None:
    """JOB_RUNNER=local: assign-ai だけでジョブが asyncio.create_task 経由で完走する。"""
    assert get_settings().job_runner == "local"
    res = await api_client.post("/api/tasks/T-121/assign-ai")
    assert res.status_code == 202
    await jobs_queue.drain_local_jobs()

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-121'")
        assert task["status"] == "you_review"
        assert task["lane_key"] == "review"
        version = await conn.fetchval(
            "select max(version) from artifacts where task_id = $1", task["id"]
        )
        assert version == 1
        status = await conn.fetchval(
            "select status from ai_jobs where id = $1::uuid", res.json()["jobId"]
        )
        assert status == "succeeded"
    finally:
        await conn.close()


async def test_execute_job_failure_retries_then_handoff(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    event_queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最終失敗: 最大2回リトライ（計3試行）→ you_todo ＋ 失敗コメント ＋ ai_jobs failed。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    provider = _FailingProvider()
    monkeypatch.setattr(execute_mod, "get_provider", lambda: provider)
    drain_events(event_queue)

    run = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert run.status_code == 200
    assert run.json() == {"status": "failed"}
    assert provider.calls == 3  # 初回 + 2回リトライ

    conn = await db_connect()
    try:
        # you_todo へ戻る（再試行導線 = 再度「AIにまかせる」できる状態）・progress null
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_todo"
        assert task["progress"] is None

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        # 着手 → 中間（初回試行のみ。リトライでは重複しない）→ 失敗
        assert len(comments) == 3
        assert comments[2]["text"] == (
            "作業中にエラーが発生しました。内容を確認のうえ、再度お任せください。"
            "（理由: 模擬的な失敗: provider が応答しません）"
        )

        job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert job["status"] == "failed"
        assert "模擬的な失敗" in job["error"]
        assert job["finished_at"] is not None

        assert await conn.fetchval("select count(*) from artifacts") == 0
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert events[-2]["type"] == "comment.created"
    assert "作業中にエラーが発生しました" in events[-2]["payload"]["text"]
    assert events[-1]["type"] == "task.updated"
    assert events[-1]["payload"]["status"] == "you_todo"
    assert events[-1]["payload"]["progress"] is None


async def test_run_job_idempotent_after_success(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """確定済みジョブの再実行（Cloud Tasks の二重配信相当）は no-op。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    assert (await api_client.post("/internal/jobs/run", json={"jobId": job_id})).status_code == 200

    rerun = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert rerun.status_code == 200
    assert rerun.json() == {"status": "succeeded"}

    conn = await db_connect()
    try:
        assert await conn.fetchval("select count(*) from artifacts") == 1
        assert await conn.fetchval("select count(*) from comments") == 3
    finally:
        await conn.close()


async def test_run_job_unknown_or_invalid_id(
    api_client: httpx.AsyncClient, zero_delays
) -> None:
    res = await api_client.post("/internal/jobs/run", json={"jobId": str(uuid4())})
    assert res.status_code == 404
    res = await api_client.post("/internal/jobs/run", json={"jobId": "not-a-uuid"})
    assert res.status_code == 422


async def test_cloud_tasks_mode_returns_5xx_until_last_attempt(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JOB_RUNNER=cloud_tasks: 失敗は 5xx（Cloud Tasks が再試行）。最終試行のみハンドオフ。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    provider = _FailingProvider()
    monkeypatch.setattr(execute_mod, "get_provider", lambda: provider)

    monkeypatch.setenv("JOB_RUNNER", "cloud_tasks")
    get_settings.cache_clear()
    try:
        # 再試行余地あり → 500 を返し、ジョブは failed 確定させない
        r1 = await api_client.post(
            "/internal/jobs/run",
            json={"jobId": job_id},
            headers={"X-CloudTasks-TaskRetryCount": "0"},
        )
        assert r1.status_code == 500
        assert provider.calls == 1  # ジョブ内リトライはしない

        conn = await db_connect()
        try:
            assert (
                await conn.fetchval("select status from ai_jobs where id = $1::uuid", job_id)
                == "running"
            )
        finally:
            await conn.close()

        # 最終試行（再試行上限到達）→ ハンドオフして 200
        r2 = await api_client.post(
            "/internal/jobs/run",
            json={"jobId": job_id},
            headers={"X-CloudTasks-TaskRetryCount": "3"},
        )
        assert r2.status_code == 200
        assert r2.json() == {"status": "failed"}

        conn = await db_connect()
        try:
            job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
            assert job["status"] == "failed"
            task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
            assert task["status"] == "you_todo"
        finally:
            await conn.close()
    finally:
        monkeypatch.delenv("JOB_RUNNER")
        get_settings.cache_clear()
