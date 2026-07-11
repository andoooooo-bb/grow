"""壁打ちチャット & 分解 API（§1.6 / §5.3 startChat・sendChat・confirmBreakdown / §7.4）。

- GET  /tasks/{human_id}/chat              メッセージ一覧（created_at 昇順）
- POST /tasks/{human_id}/chat/start        冪等。chat が空なら AI 初期質問を生成し、
                                           spec へ遷移可能なら status=spec（レーンは変えない §5.2）
- POST /tasks/{human_id}/chat              人メッセージを即保存 → バックグラウンド
                                           （+0.85s §4.4）で AI 応答＋分解候補を SSE 配信
- POST /tasks/{human_id}/breakdown/confirm 候補を子カード化し先頭AI子が自動着手（§1.6 step5）

分解候補はサーバ側に永続化しない: subtask.proposal イベントでクライアントへ届け、
「ボードに反映する」でクライアントが confirm へ送り返す設計。
"""

import asyncio
import logging
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException

from app.ai import get_provider
from app.db import get_pool
from app.domain.dto import (
    BreakdownConfirmRequest,
    BreakdownConfirmResponse,
    ChatSendRequest,
    CommentCreate,
    SubtaskProposal,
    SubtaskProposalEvent,
    TaskCreate,
)
from app.domain.models import (
    AgentRole,
    Author,
    ChatMessage,
    Comment,
    LaneKey,
    Owner,
    Task,
    TaskStatus,
)
from app.domain.state_machine import can_transition
from app.events import (
    CHAT_MESSAGE_CREATED,
    COMMENT_CREATED,
    SUBTASK_PROPOSAL,
    TASK_UPDATED,
    publish_event,
)
from app.repo import chat as chat_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# 壁打ち応答の演出ディレイ（§4.4: 人の送信 → +850ms でAI応答＋分解候補提示。
# テストでは monkeypatch で 0 にする）
CHAT_REPLY_DELAY_SEC = 0.85

# --- 文言（Grow.dc.html sendChat / confirmBreakdown 準拠） ---
FIRST_AI_START_COMMENT = "まずこのサブタスクから着手します。"
CONFIRM_CHAT_MESSAGE = "ボードに反映しました。進行中のサブタスクから順に進めます。"


def _confirm_comment_text(count: int) -> str:
    return f"{count}件のサブタスクに分解してボードに反映しました。着手できるものから進めます。"


# ---- バックグラウンドの壁打ち応答ジョブ（app/jobs/queue.py の local ランナーと同型） ----

_reply_tasks: set[asyncio.Task[None]] = set()


def _spawn_chat_reply(human_id: str) -> None:
    """AI応答＋分解候補の生成をバックグラウンドで起動する（GC防止に参照を保持）。"""
    task = asyncio.create_task(_chat_reply_job(human_id))
    _reply_tasks.add(task)
    task.add_done_callback(_reply_tasks.discard)


async def drain_chat_replies() -> None:
    """実行中の壁打ち応答ジョブの完了を待つ（テスト・シャットダウン用）。"""
    while _reply_tasks:
        await asyncio.gather(*list(_reply_tasks), return_exceptions=True)


async def _chat_reply_job(human_id: str) -> None:
    """sendChat のバックグラウンド部分（§5.3 sendChat step2）。

    +850ms 後に (1) AI応答を chat_messages へ保存して chat.message.created を配信し、
    (2) propose_subtasks の分解候補を subtask.proposal で配信する（永続化しない）。
    """
    try:
        await asyncio.sleep(CHAT_REPLY_DELAY_SEC)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await tasks_repo.get_task_row(conn, human_id)
            if row is None:  # 応答待ちの間に削除された場合は静かに終了
                return
            history = await chat_repo.list_chat_messages(conn, row)
            rule_rows = await rules_repo.relevant_rules(conn, row)

        task_dict = _task_prompt_dict(row)
        rule_dicts = [rules_repo.rule_prompt_dict(r) for r in rule_rows]
        chat_dicts = [_chat_prompt_dict(m) for m in history]

        provider = get_provider()
        reply = await provider.chat_reply(task_dict, chat_dicts, rule_dicts)
        async with pool.acquire() as conn:
            message = await chat_repo.create_chat_message(
                conn, row, author=Author.AI, text=reply.text
            )
        publish_event(CHAT_MESSAGE_CREATED, message.model_dump(mode="json", by_alias=True))

        proposal = await provider.propose_subtasks(
            task_dict, [*chat_dicts, _chat_prompt_dict(message)], rule_dicts
        )
        event = SubtaskProposalEvent(
            task_id=human_id,
            subtasks=[
                SubtaskProposal(title=s.title, owner=Owner(s.owner), rationale=s.rationale)
                for s in proposal.subtasks
            ],
        )
        publish_event(SUBTASK_PROPOSAL, event.model_dump(mode="json", by_alias=True))
    except Exception:  # noqa: BLE001 — バックグラウンドなので握って記録（§5.5）
        logger.exception("chat reply job failed for task %s", human_id)


# ---- エンドポイント ---------------------------------------------------------------


@router.get("/tasks/{human_id}/chat")
async def get_chat_messages(human_id: str) -> list[ChatMessage]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await tasks_repo.get_task_row(conn, human_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        return await chat_repo.list_chat_messages(conn, row)


@router.post("/tasks/{human_id}/chat/start")
async def start_chat(human_id: str) -> list[ChatMessage]:
    """壁打ち開始（§5.3 startChat）。冪等 — chat が空のときだけ AI 初期質問を投入する。

    初回は retrieval ルールを渡した chat_reply（§7.4a）で初期質問を生成し、
    spec へ遷移可能なら status=spec（レーンは変えない §5.2）。2回目以降は一覧を返すだけ。
    """
    pool = await get_pool()
    created: ChatMessage | None = None
    updated: Task | None = None
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        messages = await chat_repo.list_chat_messages(conn, row)
        if not messages:
            rule_rows = await rules_repo.relevant_rules(conn, row)
            reply = await get_provider().chat_reply(
                _task_prompt_dict(row),
                [],
                [rules_repo.rule_prompt_dict(r) for r in rule_rows],
            )
            created = await chat_repo.create_chat_message(
                conn, row, author=Author.AI, text=reply.text
            )
            messages = [created]
            current = TaskStatus(row["status"])
            if current is not TaskStatus.SPEC and can_transition(current, TaskStatus.SPEC):
                updated = await tasks_repo.apply_patch(conn, row, {"status": TaskStatus.SPEC})
    if created is not None:
        publish_event(CHAT_MESSAGE_CREATED, created.model_dump(mode="json", by_alias=True))
    if updated is not None:
        publish_event(TASK_UPDATED, updated.model_dump(mode="json", by_alias=True))
    return messages


@router.post("/tasks/{human_id}/chat", status_code=201)
async def send_chat(human_id: str, payload: ChatSendRequest) -> ChatMessage:
    """人メッセージ送信（§5.3 sendChat step1）。保存して即返し、応答は背景ジョブへ。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await tasks_repo.get_task_row(conn, human_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        message = await chat_repo.create_chat_message(
            conn, row, author=Author.HUMAN, text=payload.text
        )
    publish_event(CHAT_MESSAGE_CREATED, message.model_dump(mode="json", by_alias=True))
    _spawn_chat_reply(human_id)
    return message


@router.post("/tasks/{human_id}/breakdown/confirm")
async def confirm_breakdown(
    human_id: str, payload: BreakdownConfirmRequest
) -> BreakdownConfirmResponse:
    """「この内容でボードに反映する」（§1.6 step5 / §5.3 confirmBreakdown）。

    単一トランザクションで:
    a. 候補を子カード化（parent=親, labels 継承, todo レーン末尾へ順に）。
       ai→queued / human→you_todo。最初の ai 子のみ ai_work・progress 10・着手コメント。
    b. 親を ai_work にして progress レーン先頭へ ＋ 反映コメント。
    c. chat に AI メッセージを追記。
    コミット後に SSE（子・親の task.updated / comment・chat の created）を配信する。
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")

        current = TaskStatus(row["status"])
        # done→ai_work は再オープン（管理操作 §5.6）専用のため、confirm では許可しない
        if current is TaskStatus.DONE or not can_transition(current, TaskStatus.AI_WORK):
            raise HTTPException(
                status_code=409,
                detail=f"invalid status transition: {current} -> ai_work",
            )

        # a) 子カード生成（todo レーン末尾へ候補の順に）
        parent_labels = list(row["labels"])
        children: list[Task] = []
        first_ai_comment: Comment | None = None
        started_first_ai = False
        for item in payload.subtasks:
            starts_now = item.owner is Owner.AI and not started_first_ai
            if starts_now:
                status = TaskStatus.AI_WORK  # 分解し終わったものから AI が進める（§1.6）
            elif item.owner is Owner.AI:
                status = TaskStatus.QUEUED
            else:
                status = TaskStatus.YOU_TODO
            child = await tasks_repo.create_task(
                conn,
                TaskCreate(
                    lane_key=LaneKey.TODO,
                    title=item.title,
                    status=status,
                    labels=parent_labels,
                    parent_id=human_id,
                ),
            )
            if starts_now:
                started_first_ai = True
                child_row = await tasks_repo.get_task_row(conn, child.id)
                first_ai_comment = await comments_repo.create_comment(
                    conn,
                    child_row,
                    CommentCreate(
                        author=Author.AI,
                        text=FIRST_AI_START_COMMENT,
                        agent_role=AgentRole.PLANNER,  # 分解の反映は計画AIの名義（#19）
                    ),
                )
                child = await tasks_repo.apply_patch(conn, child_row, {"progress": 10})
            children.append(child)

        # b) 親: 反映コメント → ai_work・progress レーン先頭（orderInLane=0）
        parent_comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI,
                text=_confirm_comment_text(len(children)),
                agent_role=AgentRole.PLANNER,  # 分解の反映は計画AIの名義（#19）
            ),
        )
        parent = await tasks_repo.apply_patch(
            conn,
            row,
            {
                "status": TaskStatus.AI_WORK,
                "lane_key": LaneKey.PROGRESS,
                "order_in_lane": 0,
            },
        )

        # c) 壁打ちへの締めメッセージ
        chat_message = await chat_repo.create_chat_message(
            conn, row, author=Author.AI, text=CONFIRM_CHAT_MESSAGE
        )

    # d) SSE 配信（コミット後）。親の task.updated は childIds 込み。
    for child in children:
        publish_event(TASK_UPDATED, child.model_dump(mode="json", by_alias=True))
    if first_ai_comment is not None:
        publish_event(COMMENT_CREATED, first_ai_comment.model_dump(mode="json", by_alias=True))
    publish_event(COMMENT_CREATED, parent_comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, parent.model_dump(mode="json", by_alias=True))
    publish_event(CHAT_MESSAGE_CREATED, chat_message.model_dump(mode="json", by_alias=True))

    return BreakdownConfirmResponse(parent=parent, children=children)


# ---- ヘルパ -----------------------------------------------------------------------


def _task_prompt_dict(task_row: asyncpg.Record) -> dict[str, Any]:
    """AiProvider へ渡すタスク dict（provider.py の想定キー: id/humanId/title/labels）。"""
    return {
        "id": str(task_row["id"]),
        "humanId": task_row["human_id"],
        "title": task_row["title"],
        "labels": list(task_row["labels"]),
    }


def _chat_prompt_dict(message: ChatMessage) -> dict[str, Any]:
    """AiProvider へ渡すチャット dict（provider.py の想定キー: who/text）。"""
    return {"who": message.author.value, "text": message.text}
