"""artifacts API（GET/POST /api/tasks/{id}/artifacts, §02.6）のテスト。"""

import httpx
import pytest

from app.events import bus
from app.jobs import execute as execute_mod
from app.jobs import queue as jobs_queue
from tests.helpers import drain_events


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


@pytest.fixture
def zero_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """assign-ai の enqueue をフェイク化（ジョブは手動で /internal/jobs/run を叩く）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


async def test_get_artifacts_empty(api_client: httpx.AsyncClient) -> None:
    res = await api_client.get("/api/tasks/T-104/artifacts")
    assert res.status_code == 200
    assert res.json() == {"taskId": "T-104", "artifacts": []}


async def test_post_artifact_creates_new_version(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """人の編集が新版として積まれ、artifact.created が配信される。"""
    res = await api_client.post(
        "/api/tasks/T-104/artifacts", json={"contentMd": "# 下書き v1\n\n本文"}
    )
    assert res.status_code == 201
    body = res.json()
    assert body["taskId"] == "T-104"
    assert body["version"] == 1
    assert body["jobId"] is None  # 人の編集はジョブに紐づかない
    assert body["contentMd"] == "# 下書き v1\n\n本文"

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["artifact.created"]
    assert events[0]["payload"] == body

    # 2版目 → GET は version 昇順の全版
    res2 = await api_client.post(
        "/api/tasks/T-104/artifacts", json={"contentMd": "# 下書き v2"}
    )
    assert res2.json()["version"] == 2

    listing = await api_client.get("/api/tasks/T-104/artifacts")
    artifacts = listing.json()["artifacts"]
    assert [a["version"] for a in artifacts] == [1, 2]
    assert artifacts[1]["contentMd"] == "# 下書き v2"


async def test_artifacts_task_not_found(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/tasks/T-999/artifacts")).status_code == 404
    res = await api_client.post("/api/tasks/T-999/artifacts", json={"contentMd": "x"})
    assert res.status_code == 404


async def test_human_artifact_has_empty_applied_rule_ids(
    api_client: httpx.AsyncClient,
) -> None:
    """人の編集版（job_id なし）は appliedRuleIds が空配列（#20）。"""
    res = await api_client.post("/api/tasks/T-104/artifacts", json={"contentMd": "# 下書き"})
    assert res.status_code == 201
    assert res.json()["appliedRuleIds"] == []

    listing = await api_client.get("/api/tasks/T-104/artifacts")
    assert listing.json()["artifacts"][0]["appliedRuleIds"] == []


async def test_ai_artifact_applied_rule_ids_from_job_join(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """AI生成版は ai_jobs join で由来ルールを human_id（retrieval 順）で返す（#20）。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    drain_events(event_queue)  # assign 分のイベントは捨て、ジョブ分だけを検証する

    run = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert run.status_code == 200

    # T-104（仕事,調査）の retrieval 順: K-02, K-04, K-01, K-03（§6.3）
    expected_rule_ids = ["K-02", "K-04", "K-01", "K-03"]

    # artifact.created の SSE payload にも由来ルールが載る（FE store が版を追記するため）
    events = drain_events(event_queue)
    artifact_events = [e for e in events if e["type"] == "artifact.created"]
    assert len(artifact_events) == 1
    assert artifact_events[0]["payload"]["appliedRuleIds"] == expected_rule_ids

    # GET は ai_jobs join で復元する（UUID は境界に出さない §00 #9）
    listing = await api_client.get("/api/tasks/T-104/artifacts")
    artifacts = listing.json()["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["jobId"] == job_id
    assert artifacts[0]["appliedRuleIds"] == expected_rule_ids

    # 人の編集で新版を積む → AI版の由来ルールは維持・編集版は空配列
    res2 = await api_client.post("/api/tasks/T-104/artifacts", json={"contentMd": "# 手直し"})
    assert res2.status_code == 201
    listing2 = await api_client.get("/api/tasks/T-104/artifacts")
    assert [a["appliedRuleIds"] for a in listing2.json()["artifacts"]] == [
        expected_rule_ids,
        [],
    ]
