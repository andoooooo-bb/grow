"""ジョブランナー抽象（app/jobs/queue.py, JOB_RUNNER=local|cloud_tasks）の単体テスト。

DB 不要。cloud_tasks は CloudTasksClient をフェイクに差し替え、実GCPは呼ばない。
"""

import json

import pytest

from app.config import get_settings
from app.jobs import queue as jobs_queue


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch):
    """環境変数で settings を切り替えるヘルパ（テスト後にキャッシュを戻す）。"""

    def _set(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


async def test_local_runner_spawns_coroutine(
    settings_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local: run_execute_job が asyncio.create_task でプロセス内起動される。"""
    settings_env(JOB_RUNNER="local")
    ran: list[str] = []

    async def _fake_run(job_id: str) -> bool:
        ran.append(job_id)
        return True

    monkeypatch.setattr("app.jobs.execute.run_execute_job", _fake_run)
    await jobs_queue.enqueue_job("job-local-1")
    await jobs_queue.drain_local_jobs()
    assert ran == ["job-local-1"]


async def test_cloud_tasks_runner_enqueues_http_task(
    settings_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cloud_tasks: {SELF_URL}/internal/jobs/run への HTTP POST タスクが投入される。"""
    from google.cloud import tasks_v2

    settings_env(
        JOB_RUNNER="cloud_tasks",
        GCP_PROJECT="test-proj",
        GCP_LOCATION="asia-northeast1",
        CLOUD_TASKS_QUEUE="grow-jobs",
        SELF_URL="https://grow.example.run.app",
    )
    created: list[dict] = []

    class _FakeClient:
        def queue_path(self, project: str, location: str, queue: str) -> str:
            return f"projects/{project}/locations/{location}/queues/{queue}"

        def create_task(self, request: dict) -> None:
            created.append(request)

    monkeypatch.setattr(tasks_v2, "CloudTasksClient", _FakeClient)

    await jobs_queue.enqueue_job("job-ct-1")

    assert len(created) == 1
    request = created[0]
    assert request["parent"] == "projects/test-proj/locations/asia-northeast1/queues/grow-jobs"
    http = request["task"]["http_request"]
    assert http["http_method"] == tasks_v2.HttpMethod.POST
    assert http["url"] == "https://grow.example.run.app/internal/jobs/run"
    assert json.loads(http["body"]) == {"jobId": "job-ct-1"}
