"""壁打ちチャット API（GET/POST /api/tasks/{id}/chat, /chat/start, #11）のテスト。

- startChat: 初期質問（greetings 相当）の投入・spec 遷移・冪等性（§5.3 / §7.4a）
- sendChat: 人メッセージ即返却 → バックグラウンド（ディレイ0に差し替え）で
  AI 応答保存＋subtask.proposal イベント配信（§1.6 / §4.4）
SSE はバス購読（tests/helpers.drain_events）で検証する。
"""

import httpx
import pytest

from app.events import bus
from app.routers import chat as chat_router
from tests.helpers import db_connect, drain_events

# --- Mock の文言（§7.4a / §2.5 / Grow.dc.html 準拠であることをここで固定する） ---
GREETING_T130 = (
    "「ポートフォリオサイトのリニューアル」ですね。分解の前に確認させてください。\n"
    "① 公開したい時期は？\n"
    "② 既存サイトの資産は流用しますか？\n"
    "③ いちばん見せたい実績は？"
)
GREETING_GENERIC = (
    "このタスクですね。進め方を一緒に詰めましょう。やりたいこと・前提を教えてください。"
)
CHAT_FOLLOWUP = (
    "ありがとうございます、イメージできました。"
    "いただいた前提をふまえ、次のように分解するのはいかがでしょう。"
)

# 分解候補（T-130 は §2.5 の5件 / その他は汎用4件）
PROPOSAL_T130 = [
    ("情報設計・サイトマップ作成", "ai"),
    ("ワイヤーフレーム作成", "ai"),
    ("掲載する実績コンテンツの選定", "human"),
    ("デザイン方向性の決定", "human"),
    ("コーディング・実装", "ai"),
]
PROPOSAL_GENERIC = [
    ("要件・前提の整理", "ai"),
    ("たたき台の作成", "ai"),
    ("内容の確認・決定", "human"),
    ("仕上げ", "ai"),
]


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


@pytest.fixture
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """壁打ち応答の演出ディレイ（§4.4 850ms）をテストでは 0 にする。"""
    monkeypatch.setattr(chat_router, "CHAT_REPLY_DELAY_SEC", 0)


async def _chat_rows(human_id: str) -> list:
    conn = await db_connect()
    try:
        return await conn.fetch(
            "select m.* from chat_messages m join tasks t on t.id = m.task_id "
            "where t.human_id = $1 order by m.created_at, m.id",
            human_id,
        )
    finally:
        await conn.close()


# ---- startChat --------------------------------------------------------------------


async def test_start_chat_creates_greeting_and_moves_to_spec(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """T-130（breakdown）: 3問形式の初期質問が保存され status=spec（レーンは不変 §5.2）。"""
    res = await api_client.post("/api/tasks/T-130/chat/start")
    assert res.status_code == 200
    messages = res.json()
    assert len(messages) == 1
    assert messages[0]["taskId"] == "T-130"
    assert messages[0]["author"] == "ai"
    assert messages[0]["text"] == GREETING_T130

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-130'")
        assert task["status"] == "spec"
        assert task["lane_key"] == "backlog"  # 壁打ち開始でレーンは変えない（§5.2）
        assert task["order_in_lane"] == 0
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["chat.message.created", "task.updated"]
    assert events[0]["payload"]["text"] == GREETING_T130
    assert events[1]["payload"]["id"] == "T-130"
    assert events[1]["payload"]["status"] == "spec"
    assert events[1]["payload"]["laneKey"] == "backlog"


async def test_start_chat_is_idempotent(api_client: httpx.AsyncClient, event_queue) -> None:
    """2回目の start ではメッセージが増えず、イベントも配信されない。"""
    first = await api_client.post("/api/tasks/T-130/chat/start")
    drain_events(event_queue)

    second = await api_client.post("/api/tasks/T-130/chat/start")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(await _chat_rows("T-130")) == 1
    assert drain_events(event_queue) == []


async def test_start_chat_keeps_status_when_spec_unreachable(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """T-121（queued）: spec へ遷移不可なら status 据え置き。初期質問は汎用文言。"""
    res = await api_client.post("/api/tasks/T-121/chat/start")
    assert res.status_code == 200
    assert res.json()[0]["text"] == GREETING_GENERIC

    conn = await db_connect()
    try:
        status = await conn.fetchval("select status from tasks where human_id = 'T-121'")
        assert status == "queued"
    finally:
        await conn.close()

    # status が変わらないので task.updated は配信されない
    assert [e["type"] for e in drain_events(event_queue)] == ["chat.message.created"]


async def test_start_chat_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post("/api/tasks/T-999/chat/start")
    assert res.status_code == 404


# ---- sendChat ---------------------------------------------------------------------


async def test_send_chat_returns_human_message_then_ai_reply_and_proposal(
    api_client: httpx.AsyncClient, event_queue, no_delay
) -> None:
    """T-130: 人メッセージ即返却 → drain 後 AI 応答保存＋subtask.proposal（§2.5 の5件）。"""
    await api_client.post("/api/tasks/T-130/chat/start")
    drain_events(event_queue)

    res = await api_client.post(
        "/api/tasks/T-130/chat", json={"text": "秋頃公開。実績は直近の3件を見せたい。"}
    )
    assert res.status_code == 201
    body = res.json()
    assert body["taskId"] == "T-130"
    assert body["author"] == "human"
    assert body["text"] == "秋頃公開。実績は直近の3件を見せたい。"

    await chat_router.drain_chat_replies()

    rows = await _chat_rows("T-130")
    assert [(r["author"], r["text"]) for r in rows] == [
        ("ai", GREETING_T130),
        ("human", "秋頃公開。実績は直近の3件を見せたい。"),
        ("ai", CHAT_FOLLOWUP),
    ]

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "chat.message.created",  # 人メッセージ（即時）
        "chat.message.created",  # AI 応答（バックグラウンド）
        "subtask.proposal",
    ]
    assert events[1]["payload"]["author"] == "ai"
    assert events[1]["payload"]["text"] == CHAT_FOLLOWUP

    proposal = events[2]["payload"]
    assert proposal["taskId"] == "T-130"
    assert [(s["title"], s["owner"]) for s in proposal["subtasks"]] == PROPOSAL_T130
    # 担当理由（rationale）は human 候補にのみ付く（§7.4b）
    assert proposal["subtasks"][2]["rationale"] == "掲載内容の取捨選択は本人の意思決定が必要なため"
    assert proposal["subtasks"][0]["rationale"] is None

    # 候補はサーバ側に永続化しない（confirm でクライアントが送り返す設計）
    conn = await db_connect()
    try:
        assert await conn.fetchval("select count(*) from tasks") == 11
        assert await conn.fetchval("select count(*) from ai_jobs") == 0
    finally:
        await conn.close()


async def test_send_chat_generic_proposal(
    api_client: httpx.AsyncClient, event_queue, no_delay
) -> None:
    """T-130 以外は汎用4件の分解候補が配信される。"""
    res = await api_client.post("/api/tasks/T-104/chat", json={"text": "進め方を相談したい"})
    assert res.status_code == 201

    await chat_router.drain_chat_replies()

    events = drain_events(event_queue)
    proposal = events[-1]["payload"]
    assert events[-1]["type"] == "subtask.proposal"
    assert proposal["taskId"] == "T-104"
    assert [(s["title"], s["owner"]) for s in proposal["subtasks"]] == PROPOSAL_GENERIC


async def test_send_chat_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post("/api/tasks/T-999/chat", json={"text": "hello"})
    assert res.status_code == 404


# ---- GET /chat --------------------------------------------------------------------


async def test_get_chat_messages_in_created_order(
    api_client: httpx.AsyncClient, no_delay
) -> None:
    """一覧は created_at 昇順（greeting → 人 → AI 応答）。"""
    assert (await api_client.get("/api/tasks/T-130/chat")).json() == []

    await api_client.post("/api/tasks/T-130/chat/start")
    await api_client.post("/api/tasks/T-130/chat", json={"text": "前提を共有します"})
    await chat_router.drain_chat_replies()

    res = await api_client.get("/api/tasks/T-130/chat")
    assert res.status_code == 200
    messages = res.json()
    assert [(m["author"], m["text"]) for m in messages] == [
        ("ai", GREETING_T130),
        ("human", "前提を共有します"),
        ("ai", CHAT_FOLLOWUP),
    ]
    # ChatMessage DTO は camelCase
    assert {"id", "taskId", "author", "text", "createdAt"} <= set(messages[0].keys())


async def test_get_chat_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.get("/api/tasks/T-999/chat")
    assert res.status_code == 404
