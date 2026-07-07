"""手動蒸留 API（#13 §6.4a）のテスト — learn 候補生成 / adopt / dismiss。

MockProvider の固定候補（T-091 / 汎用）を前提に、候補生成の活性条件（完了系のみ）、
採用時の永続化（K-{seq} 採番・feedback・AIコメント・SSE）、却下時の feedback 記録を検証する。
"""

import httpx
import pytest

from app.events import bus
from tests.helpers import db_connect, drain_events

ADOPT_TEXT_T091 = "確定申告サマリーは控除候補を別セクションで先に提示する"
ADOPT_COMMENT_T091 = (
    f"ナレッジに追加しました:「{ADOPT_TEXT_T091}」次回から自動で前提にします。"
)
ADOPT_BODY_T091 = {
    "text": ADOPT_TEXT_T091,
    "scope": "personal",
    "tags": ["経理"],
    "confidence": "med",
}


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


# ---- 候補生成（GET /tasks/:id/learn） ---------------------------------------------


async def test_learn_returns_proposals_for_you_review(api_client: httpx.AsyncClient) -> None:
    """T-091（you_review）: Mock 固定候補1件が tempId/taskId 付きで返る。永続化されない。"""
    res = await api_client.get("/api/tasks/T-091/learn")
    assert res.status_code == 200
    proposals = res.json()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["tempId"]  # uuid 文字列が付与される
    assert proposal["taskId"] == "T-091"
    assert proposal["text"] == ADOPT_TEXT_T091
    assert proposal["scope"] == "personal"
    assert proposal["tags"] == ["経理"]
    assert proposal["confidence"] == "med"

    conn = await db_connect()
    try:
        # 候補は永続化しない（rules はシードの5件のまま / feedback も無し）
        assert await conn.fetchval("select count(*) from rules") == 5
        assert await conn.fetchval("select count(*) from rule_feedback") == 0
    finally:
        await conn.close()


async def test_learn_generic_proposal_for_done_task(api_client: httpx.AsyncClient) -> None:
    """T-080（done, labels=[経理]）: 汎用候補1件（low, tags=カードのlabels §5.3）。"""
    res = await api_client.get("/api/tasks/T-080/learn")
    assert res.status_code == 200
    proposals = res.json()
    assert len(proposals) == 1
    assert proposals[0]["text"] == "このタスクで繰り返した指示を、今後の既定の進め方にする"
    assert proposals[0]["tags"] == ["経理"]
    assert proposals[0]["confidence"] == "low"


async def test_learn_rejected_for_non_completed_task(api_client: httpx.AsyncClient) -> None:
    """T-098（ai_work）: 完了系（you_review/reviewing/done）以外は 409（§1.7 step1）。"""
    res = await api_client.get("/api/tasks/T-098/learn")
    assert res.status_code == 409


async def test_learn_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.get("/api/tasks/T-999/learn")
    assert res.status_code == 404


# ---- 採用（POST /tasks/:id/learn/adopt） ------------------------------------------


async def test_adopt_persists_rule_feedback_comment_and_sse(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """採用で K-06 が増え（applied 0・source=T-091 から学習）、コメント・feedback・SSE が揃う。"""
    res = await api_client.post("/api/tasks/T-091/learn/adopt", json=ADOPT_BODY_T091)
    assert res.status_code == 201
    rule = res.json()
    assert rule["id"] == "K-06"  # 既存 K-01〜K-05 の次（採番 §5.3 adoptLearn）
    assert rule["applied"] == 0
    assert rule["source"] == "T-091 から学習"
    assert rule["sourceTaskId"] == "T-091"
    assert rule["scope"] == "personal"
    assert rule["tags"] == ["経理"]
    assert rule["confidence"] == "med"

    conn = await db_connect()
    try:
        row = await conn.fetchrow("select * from rules where human_id = 'K-06'")
        assert row is not None
        assert row["text"] == ADOPT_TEXT_T091
        assert row["applied"] == 0
        assert row["source"] == "T-091 から学習"
        task = await conn.fetchrow("select * from tasks where human_id = 'T-091'")
        assert row["source_task_id"] == task["id"]
        # personal ルールはタスク担当者を owner にする（§6.7）
        assert row["owner_user_id"] == task["owner_user_id"]

        # カードにAIコメント（Grow.dc.html adoptLearn 文言 / §6.8 基準①）
        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert len(comments) == 1
        assert comments[0]["author"] == "ai"
        assert comments[0]["text"] == ADOPT_COMMENT_T091

        # 採用ログが1行（§6.4 将来の自動化のお手本）
        feedback = await conn.fetch("select * from rule_feedback")
        assert len(feedback) == 1
        assert feedback[0]["action"] == "adopt"
        assert feedback[0]["task_id"] == task["id"]
        assert feedback[0]["text"] == ADOPT_TEXT_T091
        assert list(feedback[0]["tags"]) == ["経理"]
        assert feedback[0]["confidence"] == "med"
    finally:
        await conn.close()

    # SSE: rule.created → comment.created → task.updated（commentCount 同期）
    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["rule.created", "comment.created", "task.updated"]
    assert events[0]["payload"]["id"] == "K-06"
    assert events[0]["payload"]["applied"] == 0
    assert events[1]["payload"]["text"] == ADOPT_COMMENT_T091
    assert events[2]["payload"]["id"] == "T-091"
    assert events[2]["payload"]["commentCount"] == 1


async def test_adopt_sequences_human_ids(api_client: httpx.AsyncClient) -> None:
    """連続採用で K-06 → K-07 と連番になる。"""
    first = await api_client.post("/api/tasks/T-091/learn/adopt", json=ADOPT_BODY_T091)
    second = await api_client.post(
        "/api/tasks/T-080/learn/adopt",
        json={
            "text": "経費分類は先に費目候補の一覧を出す",
            "scope": "team",
            "tags": ["経理"],
            "confidence": "low",
        },
    )
    assert first.json()["id"] == "K-06"
    assert second.json()["id"] == "K-07"
    # team ルールは owner を持たない（シード K-04/K-05 と同方針）
    assert second.json()["ownerUserId"] is None
    assert second.json()["source"] == "T-080 から学習"


async def test_adopt_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post("/api/tasks/T-999/learn/adopt", json=ADOPT_BODY_T091)
    assert res.status_code == 404


# ---- 却下（POST /tasks/:id/learn/dismiss） ----------------------------------------


async def test_dismiss_records_feedback_without_rule(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """却下: rules は増えず、rule_feedback に dismiss が1行残る。SSE は出ない。"""
    res = await api_client.post("/api/tasks/T-091/learn/dismiss", json=ADOPT_BODY_T091)
    assert res.status_code == 204

    conn = await db_connect()
    try:
        assert await conn.fetchval("select count(*) from rules") == 5
        feedback = await conn.fetch("select * from rule_feedback")
        assert len(feedback) == 1
        assert feedback[0]["action"] == "dismiss"
        assert feedback[0]["text"] == ADOPT_TEXT_T091
        assert feedback[0]["scope"] == "personal"
    finally:
        await conn.close()

    assert drain_events(event_queue) == []


async def test_dismiss_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post("/api/tasks/T-999/learn/dismiss", json=ADOPT_BODY_T091)
    assert res.status_code == 404
