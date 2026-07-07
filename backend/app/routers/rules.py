"""手動蒸留 & ルール API（#13 §1.7 / §1.8 / §5.3 learnFrom・adoptLearn・promoteRule）。

- GET  /tasks/{human_id}/learn          完了系（you_review/reviewing/done）のカードで
                                        ルール候補を1〜複数生成して返す（永続化しない §6.4a）
- POST /tasks/{human_id}/learn/adopt    候補を採用: rules へ永続化＋feedback 記録＋AIコメント
- POST /tasks/{human_id}/learn/dismiss  候補を却下: feedback 記録のみ（204）
- POST /rules/{human_id}/promote        個人ルールをチームへ昇格（scope=team。冪等）

人の採用/却下ログ（rule_feedback）は将来の半自動/自動蒸留のお手本データになる（§6.4）。
NEW バッジ（isNew）はクライアント表示状態であり、サーバは永続化しない（§5.3）。
"""

from typing import Any
from uuid import uuid4

import asyncpg
from fastapi import APIRouter, HTTPException, Response

from app.ai import get_provider
from app.db import get_pool
from app.domain.dto import CommentCreate, LearnDecisionRequest, RuleProposalDto
from app.domain.models import Author, Rule, RuleScope, TaskStatus
from app.events import (
    COMMENT_CREATED,
    RULE_CREATED,
    RULE_UPDATED,
    TASK_UPDATED,
    publish_event,
)
from app.repo import chat as chat_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["rules"])

# 「✧ 学ぶ」が有効になる完了系ステータス（§1.7 step1 / §6.4a トリガー）
LEARNABLE_STATUSES = frozenset(
    {TaskStatus.YOU_REVIEW, TaskStatus.REVIEWING, TaskStatus.DONE}
)

# 採用コメント文言（Grow.dc.html adoptLearn 準拠）
ADOPT_COMMENT_TEMPLATE = "ナレッジに追加しました:「{text}」次回から自動で前提にします。"


# ---- 蒸留: 候補生成（learnFrom） ---------------------------------------------------


@router.get("/tasks/{human_id}/learn")
async def learn_proposals(human_id: str) -> list[RuleProposalDto]:
    """タスク履歴からルール候補を生成する（§6.4a / §7.5 distill）。

    候補はサーバ側に永続化しない: 各候補に tempId を付けて返し、クライアントが
    採用/却下の判断ごとに adopt / dismiss へ内容を送り返す設計（subtask.proposal と同型）。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await tasks_repo.get_task_row(conn, human_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        status = TaskStatus(row["status"])
        if status not in LEARNABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"task is not learnable in status: {status}",
            )
        history = await comments_repo.list_comments(conn, row)
        chat_history = await chat_repo.list_chat_messages(conn, row)

    result = await get_provider().propose_rules(
        _task_prompt_dict(row),
        [{"who": c.author.value, "text": c.text} for c in history],
        [{"who": m.author.value, "text": m.text} for m in chat_history],
    )
    return [
        RuleProposalDto(
            temp_id=str(uuid4()),
            task_id=human_id,
            text=proposal.text,
            scope=RuleScope(proposal.scope),
            tags=list(proposal.tags),
            confidence=proposal.confidence,
            source=proposal.source,
        )
        for proposal in result.rules
    ]


# ---- 蒸留: 採用 / 却下（adoptLearn / dismissLearn） --------------------------------


@router.post("/tasks/{human_id}/learn/adopt", status_code=201)
async def adopt_learn(human_id: str, payload: LearnDecisionRequest) -> Rule:
    """候補を採用する（§1.7 step4 / §5.3 adoptLearn / §6.8 基準①）。

    単一トランザクションで: rules 追加（K-{seq}, applied 0, source=「{taskId} から学習」）
    → rule_feedback に adopt を記録 → カードへAIコメント。コミット後に SSE 配信。
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        rule = await rules_repo.create_rule(
            conn,
            row,
            text=payload.text,
            scope=payload.scope,
            tags=payload.tags,
            confidence=payload.confidence,
        )
        await rules_repo.add_feedback(
            conn,
            row,
            action="adopt",
            text=payload.text,
            scope=payload.scope,
            tags=payload.tags,
            confidence=payload.confidence,
        )
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI, text=ADOPT_COMMENT_TEMPLATE.format(text=payload.text)
            ),
        )
        task = await tasks_repo.task_from_row(conn, row)  # commentCount 同期用（#7）

    publish_event(RULE_CREATED, rule.model_dump(mode="json", by_alias=True))
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    return rule


@router.post("/tasks/{human_id}/learn/dismiss", status_code=204)
async def dismiss_learn(human_id: str, payload: LearnDecisionRequest) -> Response:
    """候補を却下する（§5.3 dismissLearn）。ルールは作らず feedback のみ記録する。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, human_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        await rules_repo.add_feedback(
            conn,
            row,
            action="dismiss",
            text=payload.text,
            scope=payload.scope,
            tags=payload.tags,
            confidence=payload.confidence,
        )
    return Response(status_code=204)


# ---- 昇格（promoteRule §1.8） ------------------------------------------------------


@router.post("/rules/{human_id}/promote")
async def promote_rule(human_id: str) -> Rule:
    """個人ルールをチームへ昇格する（scope=team）。既に team なら何もせず 200（冪等）。"""
    pool = await get_pool()
    promoted = False
    async with pool.acquire() as conn, conn.transaction():
        row = await rules_repo.get_rule_by_human_id(conn, human_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail=f"rule not found: {human_id}")
        if RuleScope(row["scope"]) is RuleScope.TEAM:
            rule = await rules_repo.rule_dto_from_row(conn, row)
        else:
            rule = await rules_repo.promote_rule(conn, row)
            promoted = True

    if promoted:
        publish_event(RULE_UPDATED, rule.model_dump(mode="json", by_alias=True))
    return rule


# ---- ヘルパ -----------------------------------------------------------------------


def _task_prompt_dict(task_row: asyncpg.Record) -> dict[str, Any]:
    """AiProvider へ渡すタスク dict（provider.py の想定キー: id/humanId/title/labels）。"""
    return {
        "id": str(task_row["id"]),
        "humanId": task_row["human_id"],
        "title": task_row["title"],
        "labels": list(task_row["labels"]),
    }
