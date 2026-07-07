"""Issue #5: イベントバスと SSE（GET /api/events）のテスト。"""

import asyncio

from app.events import EventBus, bus

# ---- イベントバス直テスト ----------------------------------------------------------


async def test_event_bus_delivers_to_all_subscribers():
    local_bus = EventBus()
    queue1 = local_bus.subscribe()
    queue2 = local_bus.subscribe()

    local_bus.publish("task.updated", {"id": "T-1"})
    assert queue1.get_nowait() == {"type": "task.updated", "payload": {"id": "T-1"}}
    assert queue2.get_nowait() == {"type": "task.updated", "payload": {"id": "T-1"}}

    local_bus.unsubscribe(queue1)
    local_bus.publish("comment.created", {"id": "c-1"})
    assert queue1.empty()  # 購読解除後は届かない
    assert queue2.get_nowait()["type"] == "comment.created"


# ---- mutation → publish（バス経由での受信） ---------------------------------------


async def test_task_mutation_publishes_task_updated(api_client):
    queue = bus.subscribe()
    try:
        res = await api_client.patch("/api/tasks/T-098", json={"progress": 70})
        assert res.status_code == 200

        event = queue.get_nowait()
        assert event["type"] == "task.updated"
        assert event["payload"]["id"] == "T-098"
        assert event["payload"]["progress"] == 70
        assert event["payload"]["laneKey"] == "progress"  # payload は camelCase DTO
    finally:
        bus.unsubscribe(queue)


async def test_task_create_publishes_task_updated(api_client):
    queue = bus.subscribe()
    try:
        res = await api_client.post("/api/tasks", json={"laneKey": "backlog", "title": "新規"})
        assert res.status_code == 201

        event = queue.get_nowait()
        assert event["type"] == "task.updated"
        assert event["payload"]["id"] == "T-131"
    finally:
        bus.unsubscribe(queue)


async def test_comment_mutation_publishes_comment_created(api_client):
    queue = bus.subscribe()
    try:
        res = await api_client.post(
            "/api/tasks/T-104/comments", json={"author": "ai", "text": "着手します"}
        )
        assert res.status_code == 201

        event = queue.get_nowait()
        assert event["type"] == "comment.created"
        assert event["payload"]["taskId"] == "T-104"
        assert event["payload"]["text"] == "着手します"
    finally:
        bus.unsubscribe(queue)


# ---- SSE スモーク（/api/events に接続してイベントを受信できる） --------------------


async def test_sse_stream_receives_published_event(api_client):
    baseline = bus.subscriber_count

    # maxEvents=1: 1件受信後にサーバ側からストリームを閉じる（テスト用パラメータ）
    request_task = asyncio.create_task(
        api_client.get("/api/events", params={"maxEvents": 1})
    )

    # SSE ハンドラが購読を開始するまで待つ
    for _ in range(500):
        if bus.subscriber_count > baseline:
            break
        await asyncio.sleep(0.01)
    else:
        request_task.cancel()
        raise AssertionError("SSE subscriber が登録されなかった")

    # mutation を発行 → SSE 経由で受信されるはず
    res = await api_client.post(
        "/api/tasks/T-104/comments", json={"author": "human", "text": "SSEテスト"}
    )
    assert res.status_code == 201

    response = await asyncio.wait_for(request_task, timeout=10)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: comment.created" in response.text
    assert "SSEテスト" in response.text
    assert bus.subscriber_count == baseline  # 切断後に購読解除されている
