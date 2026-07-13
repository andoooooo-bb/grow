"""手動蒸留 & ルール API（#13 §1.7 / §1.8 / §5.3 learnFrom・adoptLearn・promoteRule）。

- GET  /tasks/{human_id}/learn          完了系（you_review/reviewing/done）のカードで
                                        ルール候補を1〜複数生成して返す（永続化しない §6.4a）
- POST /tasks/{human_id}/learn/adopt    候補を採用: rules へ永続化＋feedback 記録＋AIコメント
- POST /tasks/{human_id}/learn/dismiss  候補を却下: feedback 記録のみ（204）
- POST /rules/{human_id}/promote        個人ルールをチームへ昇格（scope=team。冪等）
- POST /rules/{human_id}/generalize     機微情報を除去した一般化文案を返す（#29。非永続）

#29 DLPガードレール（§6.7: 固有名詞・秘密情報をルール文に焼き込まない）:
昇格前にルール文を機微情報スキャン（app/security/dlp.py — 本番は Cloud DLP、
mock は正規表現スタブ）し、検出時は 409 {detail, findings} で昇格をブロックする。
人は AI 一般化（generalize）の文案を確認・編集し、text 付き promote で再昇格する。

人の採用/却下ログ（rule_feedback）は将来の半自動/自動蒸留のお手本データになる（§6.4）。
NEW バッジ（isNew）はクライアント表示状態であり、サーバは永続化しない（§5.3）。
"""

from typing import Any
from uuid import uuid4

import asyncpg
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.ai import get_provider
from app.db import get_pool
from app.domain.dto import (
    CommentCreate,
    DlpFindingDto,
    GeneralizeResponse,
    LearnDecisionRequest,
    PromoteRuleRequest,
    RuleProposalDto,
)
from app.domain.models import AgentRole, Author, Rule, RuleScope, TaskStatus
from app.events import (
    COMMENT_CREATED,
    RULE_CREATED,
    RULE_UPDATED,
    TASK_UPDATED,
    publish_event,
)
from app.guard import assert_write_rate, guard_ai_action
from app.repo import chat as chat_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo
from app.security.dlp import Finding, inspect_rule_text

router = APIRouter(tags=["rules"])

# #29: DLP 検出で昇格をブロックしたときの 409 detail（FE の警告モーダル文言と対）
PROMOTE_BLOCKED_DETAIL = "機微情報が含まれるためチーム昇格できません"

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
        # #security: 「学ぶ」は読み取り操作だが Gemini（propose_rules）を呼ぶためガードする
        await guard_ai_action(conn)
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
async def adopt_learn(
    human_id: str, payload: LearnDecisionRequest, request: Request
) -> Rule:
    """候補を採用する（§1.7 step4 / §5.3 adoptLearn / §6.8 基準①）。

    単一トランザクションで: rules 追加（K-{seq}, applied 0, source=「{taskId} から学習」）
    → rule_feedback に adopt を記録 → カードへAIコメント。コミット後に SSE 配信。
    """
    assert_write_rate(request)  # #security: IP単位の書き込みレート制限
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
                author=Author.AI,
                text=ADOPT_COMMENT_TEMPLATE.format(text=payload.text),
                agent_role=AgentRole.DISTILLER,  # 蒸留の採用は学習AIの名義（#19）
            ),
        )
        task = await tasks_repo.task_from_row(conn, row)  # commentCount 同期用（#7）

    publish_event(RULE_CREATED, rule.model_dump(mode="json", by_alias=True))
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    return rule


@router.post("/tasks/{human_id}/learn/dismiss", status_code=204)
async def dismiss_learn(
    human_id: str, payload: LearnDecisionRequest, request: Request
) -> Response:
    """候補を却下する（§5.3 dismissLearn）。ルールは作らず feedback のみ記録する。"""
    assert_write_rate(request)  # #security: IP単位の書き込みレート制限
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


# ---- 昇格（promoteRule §1.8 / #29 DLPガードレール §6.7） ---------------------------


def _promote_blocked_response(findings: list[Finding]) -> JSONResponse:
    """#29: 機微情報検出時の 409 応答 {detail, findings:[{infoType, quote}]}。"""
    return JSONResponse(
        status_code=409,
        content={
            "detail": PROMOTE_BLOCKED_DETAIL,
            "findings": [
                DlpFindingDto(info_type=f.info_type, quote=f.quote).model_dump(
                    by_alias=True
                )
                for f in findings
            ],
        },
    )


# response_model=Rule を明示: 409 は JSONResponse を直接返すため、返り値注釈から
# response_model を導出させない（Union[Rule, JSONResponse] は Pydantic field 不可）
@router.post("/rules/{human_id}/promote", response_model=Rule)
async def promote_rule(
    human_id: str, request: Request, payload: PromoteRuleRequest | None = None
) -> Rule | JSONResponse:
    """個人ルールをチームへ昇格する（scope=team）。既に team なら何もせず 200（冪等）。

    #29 DLPガードレール: 昇格前にルール文（payload.text 指定時はその文案）を
    機微情報スキャンし、検出時は 409 {detail, findings} で昇格をブロックする
    （text も更新しない）。text 指定かつスキャン通過なら text を更新してから
    昇格する（AI一般化文案を人が確認・編集した結果の反映）。
    """
    assert_write_rate(request)  # #security: IP単位の書き込みレート制限
    new_text = payload.text if payload is not None and payload.text else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await rules_repo.get_rule_by_human_id(conn, human_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"rule not found: {human_id}")

    # スキャン（本番は Cloud DLP の外部呼び出し）は行ロック・トランザクションの外で行う
    findings = await inspect_rule_text(new_text if new_text is not None else row["text"])
    if findings:
        return _promote_blocked_response(findings)

    promoted = False
    text_updated = False
    async with pool.acquire() as conn, conn.transaction():
        row = await rules_repo.get_rule_by_human_id(conn, human_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail=f"rule not found: {human_id}")
        if new_text is not None and new_text != row["text"]:
            row = await rules_repo.update_rule_text(conn, row, new_text)
            text_updated = True
        if RuleScope(row["scope"]) is RuleScope.TEAM:
            rule = await rules_repo.rule_dto_from_row(conn, row)
        else:
            rule = await rules_repo.promote_rule(conn, row)
            promoted = True

    if promoted or text_updated:
        publish_event(RULE_UPDATED, rule.model_dump(mode="json", by_alias=True))
    return rule


# ---- AI一般化（#29 §6.7: 固有名詞・秘密情報を除去した文案の提示） --------------------


@router.post("/rules/{human_id}/generalize")
async def generalize_rule(human_id: str) -> GeneralizeResponse:
    """機微情報を除去した一般化文案を返す（#29。永続化しない）。

    DLP スキャンの findings を provider（Flash / mock）へ渡し、固有名詞・
    機微情報を除去したルール文案を作らせる。人が文案を確認・編集してから
    text 付き promote で反映する（人の承認が最終ゲート）。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await rules_repo.get_rule_by_human_id(conn, human_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"rule not found: {human_id}")

    original = row["text"]
    findings = await inspect_rule_text(original)
    result = await get_provider().generalize_rule_text(
        original,
        [{"infoType": f.info_type, "quote": f.quote} for f in findings],
    )
    return GeneralizeResponse(original=original, generalized=result.text)


# ---- ヘルパ -----------------------------------------------------------------------


def _task_prompt_dict(task_row: asyncpg.Record) -> dict[str, Any]:
    """AiProvider へ渡すタスク dict（provider.py の想定キー: id/humanId/title/labels）。"""
    return {
        "id": str(task_row["id"]),
        "humanId": task_row["human_id"],
        "title": task_row["title"],
        "labels": list(task_row["labels"]),
    }
