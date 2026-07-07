"""POST /api/tasks/{id}/breakdown/confirm（§1.6 step5 / §5.3 confirmBreakdown, #11）のテスト。

T-130 を壁打ち（start → spec）してから §2.5 の5件（ai3/human2）を反映し、
子カード生成・先頭AI子の自動着手・親の ai_work/childIds/コメント/チャット追記と
SSE 配信（バス購読）・board レスポンスの childIds を検証する。
"""

import httpx
import pytest

from app.events import bus
from tests.helpers import db_connect, drain_events

# §2.5 の分解候補（subtask.proposal でクライアントへ届き、confirm で送り返される形）
SUBTASKS_T130 = [
    {"title": "情報設計・サイトマップ作成", "owner": "ai"},
    {"title": "ワイヤーフレーム作成", "owner": "ai"},
    {"title": "掲載する実績コンテンツの選定", "owner": "human"},
    {"title": "デザイン方向性の決定", "owner": "human"},
    {"title": "コーディング・実装", "owner": "ai"},
]

FIRST_AI_COMMENT = "まずこのサブタスクから着手します。"
PARENT_COMMENT = "5件のサブタスクに分解してボードに反映しました。着手できるものから進めます。"
CONFIRM_CHAT_MESSAGE = "ボードに反映しました。進行中のサブタスクから順に進めます。"


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


async def _start_spec(api_client: httpx.AsyncClient, human_id: str = "T-130") -> None:
    """壁打ち開始で breakdown → spec にしておく（§1.6 の正規フロー）。"""
    res = await api_client.post(f"/api/tasks/{human_id}/chat/start")
    assert res.status_code == 200


async def test_confirm_creates_children_and_starts_first_ai(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """T-130 に5件（ai3/human2）: 子5枚・先頭ai子のみ自動着手・親は progress レーン先頭。"""
    await _start_spec(api_client)
    drain_events(event_queue)

    res = await api_client.post(
        "/api/tasks/T-130/breakdown/confirm", json={"subtasks": SUBTASKS_T130}
    )
    assert res.status_code == 200
    body = res.json()
    parent, children = body["parent"], body["children"]

    # 親: ai_work・progress レーン先頭・childIds 5件・labels/progress は不変
    assert parent["id"] == "T-130"
    assert parent["status"] == "ai_work"
    assert parent["laneKey"] == "progress"
    assert parent["orderInLane"] == 0
    assert parent["childIds"] == ["T-131", "T-132", "T-133", "T-134", "T-135"]
    assert parent["progress"] is None  # 親の進捗は childIds から FE が巻き上げ（§1.6 step6）

    # 子: 候補の順に todo レーン末尾（既存 T-104/T-109/T-112 の後ろ）へ
    assert [c["id"] for c in children] == ["T-131", "T-132", "T-133", "T-134", "T-135"]
    assert [c["title"] for c in children] == [s["title"] for s in SUBTASKS_T130]
    assert all(c["laneKey"] == "todo" for c in children)
    assert [c["orderInLane"] for c in children] == [3, 4, 5, 6, 7]
    assert all(c["parentId"] == "T-130" for c in children)
    assert all(c["labels"] == ["個人", "デザイン"] for c in children)  # 親の labels を継承

    # 先頭 ai 子（T-131）のみ ai_work・progress 10。他の ai 子は queued、人子は you_todo
    assert (children[0]["status"], children[0]["progress"]) == ("ai_work", 10)
    assert (children[1]["status"], children[1]["progress"]) == ("queued", None)
    assert (children[2]["status"], children[2]["progress"]) == ("you_todo", None)
    assert (children[3]["status"], children[3]["progress"]) == ("you_todo", None)
    assert (children[4]["status"], children[4]["progress"]) == ("queued", None)

    conn = await db_connect()
    try:
        # 着手コメントは先頭 ai 子のみ（author=ai）
        rows = await conn.fetch(
            "select t.human_id, c.author, c.text from comments c "
            "join tasks t on t.id = c.task_id where t.parent_id is not null "
            "order by t.human_id"
        )
        assert [(r["human_id"], r["author"], r["text"]) for r in rows] == [
            ("T-131", "ai", FIRST_AI_COMMENT)
        ]

        # 親: 反映コメント＋既存レーンの並び直し（T-098/T-101 が後ろへ）
        parent_comments = await conn.fetch(
            "select author, text from comments c join tasks t on t.id = c.task_id "
            "where t.human_id = 'T-130' order by c.created_at"
        )
        assert [(r["author"], r["text"]) for r in parent_comments] == [("ai", PARENT_COMMENT)]
        progress_lane = await conn.fetch(
            "select human_id from tasks where lane_key = 'progress' order by order_in_lane"
        )
        assert [r["human_id"] for r in progress_lane] == ["T-130", "T-098", "T-101"]

        # chat に締めメッセージが追記される（greeting → 反映メッセージ）
        chat_rows = await conn.fetch(
            "select m.author, m.text from chat_messages m join tasks t on t.id = m.task_id "
            "where t.human_id = 'T-130' order by m.created_at, m.id"
        )
        assert chat_rows[-1]["author"] == "ai"
        assert chat_rows[-1]["text"] == CONFIRM_CHAT_MESSAGE
        assert len(chat_rows) == 2
    finally:
        await conn.close()

    # SSE: 子5件の task.updated → 着手/反映コメント → 親 task.updated（childIds込み） → chat
    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "task.updated",
        "task.updated",
        "task.updated",
        "task.updated",
        "task.updated",
        "comment.created",
        "comment.created",
        "task.updated",
        "chat.message.created",
    ]
    assert [e["payload"]["id"] for e in events[:5]] == [
        "T-131",
        "T-132",
        "T-133",
        "T-134",
        "T-135",
    ]
    assert events[5]["payload"]["taskId"] == "T-131"
    assert events[5]["payload"]["text"] == FIRST_AI_COMMENT
    assert events[6]["payload"]["taskId"] == "T-130"
    assert events[6]["payload"]["text"] == PARENT_COMMENT
    assert events[7]["payload"]["id"] == "T-130"
    assert events[7]["payload"]["childIds"] == ["T-131", "T-132", "T-133", "T-134", "T-135"]
    assert events[8]["payload"]["text"] == CONFIRM_CHAT_MESSAGE


async def test_confirm_children_visible_on_board_with_rollup(
    api_client: httpx.AsyncClient,
) -> None:
    """board レスポンスに childIds が出て、サブタスク進捗（done/total）を集計できる。"""
    await _start_spec(api_client)
    await api_client.post("/api/tasks/T-130/breakdown/confirm", json={"subtasks": SUBTASKS_T130})

    # 人子 T-133 を完了にする（you_todo → done は §5.6 で許可）
    res = await api_client.patch("/api/tasks/T-133", json={"status": "done", "laneKey": "done"})
    assert res.status_code == 200

    board = (await api_client.get("/api/board")).json()
    cards = board["cards"]
    child_ids = cards["T-130"]["childIds"]
    assert child_ids == ["T-131", "T-132", "T-133", "T-134", "T-135"]

    # FE と同じ巻き上げ計算（§5.1 派生値）: done/total = 1/5
    done = sum(1 for cid in child_ids if cards[cid]["status"] == "done")
    assert (done, len(child_ids)) == (1, 5)

    # レーンの並び: todo 末尾に残りの子、progress 先頭に親
    lanes = {lane["key"]: lane["cardIds"] for lane in board["lanes"]}
    assert lanes["todo"] == ["T-104", "T-109", "T-112", "T-131", "T-132", "T-134", "T-135"]
    assert lanes["progress"][0] == "T-130"
    assert "T-133" in lanes["done"]


async def test_confirm_empty_subtasks_is_422(api_client: httpx.AsyncClient) -> None:
    """空配列は 422（1件以上が必須）。"""
    await _start_spec(api_client)
    res = await api_client.post("/api/tasks/T-130/breakdown/confirm", json={"subtasks": []})
    assert res.status_code == 422


async def test_confirm_on_done_parent_is_409_and_rolls_back(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """done の親に confirm は 409（再オープンは管理操作であり confirm では不可）。副作用なし。"""
    res = await api_client.post(
        "/api/tasks/T-080/breakdown/confirm", json={"subtasks": SUBTASKS_T130}
    )
    assert res.status_code == 409

    conn = await db_connect()
    try:
        assert await conn.fetchval("select count(*) from tasks") == 11  # 子は作られていない
        assert await conn.fetchval("select count(*) from comments") == 0
        assert await conn.fetchval("select count(*) from chat_messages") == 0
        task = await conn.fetchrow("select * from tasks where human_id = 'T-080'")
        assert task["status"] == "done"
        assert task["lane_key"] == "done"
    finally:
        await conn.close()
    assert drain_events(event_queue) == []


async def test_confirm_on_breakdown_parent_is_409(api_client: httpx.AsyncClient) -> None:
    """壁打ちを経ていない breakdown の親も 409（breakdown → ai_work は §5.6 で不可）。"""
    res = await api_client.post(
        "/api/tasks/T-130/breakdown/confirm", json={"subtasks": SUBTASKS_T130}
    )
    assert res.status_code == 409


async def test_confirm_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post(
        "/api/tasks/T-999/breakdown/confirm", json={"subtasks": SUBTASKS_T130}
    )
    assert res.status_code == 404
