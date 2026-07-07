"""artifacts API（GET/POST /api/tasks/{id}/artifacts, §02.6）のテスト。"""

import httpx
import pytest

from app.events import bus
from tests.helpers import drain_events


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


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
