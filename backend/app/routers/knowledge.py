"""ナレッジCIの公開 API（#26 — 受信箱と手動実行）。

- POST /api/knowledge/ci/run                 手動でCIを即時実行（デモの「今すぐメンテナンス実行」）
- GET  /api/knowledge/proposals              pending の提案一覧（受信箱。新しい順）
- POST /api/knowledge/proposals/{id}/adopt   提案を採用（kind 別に反映。下記参照）
- POST /api/knowledge/proposals/{id}/dismiss 提案を却下（status=dismissed + feedback 記録のみ）

adopt の kind 別処理（単一トランザクション。§6.4b/c・§6.6）:
- distill : 新ルールを作成（rule.created）
- merge   : 対象ルールを archived=true にして統合ルールを作成（rule.updated + rule.created）
- conflict: 対象ルールを archived=true にして置き換えルールを作成（rule.updated + rule.created）
- demote  : 対象ルールを archived=true にするのみ（rule.updated）
全 kind で rule_feedback に採否を記録する（task_id は null 可 — CI 由来のお手本ログ §6.4a）。
"""

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import (
    KnowledgeAdoptResponse,
    KnowledgeCiRunResponse,
    KnowledgeProposalDto,
    KnowledgeProposalsResponse,
)
from app.domain.models import Rule
from app.events import RULE_CREATED, RULE_UPDATED, publish_event
from app.guard import guard_ai_action
from app.jobs.knowledge_ci import run_knowledge_ci
from app.repo import knowledge as knowledge_repo
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["knowledge"])

# 新ルールを作る kind（distill=新規蒸留 / merge=統合文案 / conflict=置き換え文案）
_CREATING_KINDS = frozenset({"distill", "merge", "conflict"})
# 対象ルールをアーカイブする kind
_ARCHIVING_KINDS = frozenset({"merge", "conflict", "demote"})


@router.post("/knowledge/ci/run")
async def run_ci_manually() -> KnowledgeCiRunResponse:
    """デモ用の手動実行（認証なし公開API）。夜間バッチと同じ処理を即時実行する。"""
    # #security: 無認証の公開APIで reconcile（Gemini）が走るためガードする
    pool = await get_pool()
    async with pool.acquire() as conn:
        await guard_ai_action(conn)
    outcome = await run_knowledge_ci(trigger="manual")
    return KnowledgeCiRunResponse(
        run_id=outcome.run_id, proposals_created=outcome.proposals_created
    )


@router.get("/knowledge/proposals")
async def list_proposals() -> KnowledgeProposalsResponse:
    """pending の提案一覧（受信箱）。作成の新しい順。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        board = await tasks_repo.get_default_board(conn)
        rows = await knowledge_repo.list_pending(conn, board["workspace_id"])
        proposals = [
            await knowledge_repo.proposal_dto_from_row(conn, row) for row in rows
        ]
    return KnowledgeProposalsResponse(proposals=proposals)


@router.post("/knowledge/proposals/{proposal_id}/adopt")
async def adopt_proposal(proposal_id: str) -> KnowledgeAdoptResponse:
    """提案を採用する（kind 別処理はモジュール docstring 参照）。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _get_pending_row(conn, proposal_id)
        rule: Rule | None = None
        archived: list[Rule] = []
        if row["kind"] in _CREATING_KINDS:
            rule = await knowledge_repo.create_rule_from_proposal(conn, row)
        if row["kind"] in _ARCHIVING_KINDS:
            archived = await knowledge_repo.archive_rules(
                conn, list(row["target_rule_ids"])
            )
        await knowledge_repo.add_proposal_feedback(conn, row, action="adopt")
        decided = await knowledge_repo.mark_decided(
            conn, row["id"], status=knowledge_repo.ADOPTED
        )
        proposal = await knowledge_repo.proposal_dto_from_row(conn, decided)

    # コミット後に SSE 配信（アーカイブ → 新規の順で FE のナレッジ一覧が自然に入れ替わる）
    for archived_rule in archived:
        publish_event(RULE_UPDATED, archived_rule.model_dump(mode="json", by_alias=True))
    if rule is not None:
        publish_event(RULE_CREATED, rule.model_dump(mode="json", by_alias=True))
    return KnowledgeAdoptResponse(
        proposal=proposal,
        rule=rule,
        archived_rule_ids=[archived_rule.id for archived_rule in archived],
    )


@router.post("/knowledge/proposals/{proposal_id}/dismiss")
async def dismiss_proposal(proposal_id: str) -> KnowledgeProposalDto:
    """提案を却下する（ルールは変更せず feedback 記録のみ §6.4a）。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _get_pending_row(conn, proposal_id)
        await knowledge_repo.add_proposal_feedback(conn, row, action="dismiss")
        decided = await knowledge_repo.mark_decided(
            conn, row["id"], status=knowledge_repo.DISMISSED
        )
        return await knowledge_repo.proposal_dto_from_row(conn, decided)


async def _get_pending_row(conn, proposal_id: str):
    """pending の提案行をロック付きで取得する（404 / 409 / 422 の共通検証）。"""
    try:
        row = await knowledge_repo.get_proposal_row(conn, proposal_id, for_update=True)
    except ValueError as exc:  # UUID として不正
        raise HTTPException(
            status_code=422, detail=f"invalid proposal id: {proposal_id}"
        ) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"proposal not found: {proposal_id}")
    if row["status"] != knowledge_repo.PENDING:
        raise HTTPException(
            status_code=409, detail=f"proposal already decided: {row['status']}"
        )
    return row
