"""夜間ナレッジCI（#26）のリポジトリ層 — rule_proposals（受信箱）と knowledge_ci_runs。

API 境界の ruleId / taskId は human_id（K-xx / T-xx）、DB 主キーは UUID（§00 #9）。
proposal 自体の id は UUID をそのまま使う（提案は一過性で human_id を振らない）。
"""

from typing import Any
from uuid import UUID

import asyncpg

from app.ai.provider import CiProposal, TokenUsage
from app.domain.dto import KnowledgeProposalDto
from app.domain.models import Rule
from app.repo import rules as rules_repo

# 提案ステータス（rule_proposals.status）
PENDING = "pending"
ADOPTED = "adopted"
DISMISSED = "dismissed"


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# ---- 受信箱（rule_proposals） -------------------------------------------------------


async def proposal_dto_from_row(
    conn: asyncpg.Connection, row: asyncpg.Record
) -> KnowledgeProposalDto:
    """rule_proposals 行を DTO へ変換する（UUID → human_id の逆引きを含む）。"""
    target_human_ids: list[str] = []
    if row["target_rule_ids"]:
        rule_rows = await conn.fetch(
            "select id, human_id from rules where id = any($1::uuid[])",
            list(row["target_rule_ids"]),
        )
        by_id = {r["id"]: r["human_id"] for r in rule_rows}
        target_human_ids = [
            by_id[rule_id] for rule_id in row["target_rule_ids"] if rule_id in by_id
        ]
    source_task_human_id = None
    if row["source_task_id"] is not None:
        source_task_human_id = await conn.fetchval(
            "select human_id from tasks where id = $1", row["source_task_id"]
        )
    return KnowledgeProposalDto(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        kind=row["kind"],
        text=row["text"],
        scope=row["scope"],
        tags=list(row["tags"]),
        confidence=row["confidence"],
        source=row["source"],
        target_rule_ids=target_human_ids,
        note=row["note"],
        source_task_id=source_task_human_id,
        status=row["status"],
        created_at=row["created_at"].isoformat(),
        decided_at=_iso(row["decided_at"]),
    )


async def has_pending_duplicate(
    conn: asyncpg.Connection,
    workspace_id: Any,
    proposal: CiProposal,
    target_rule_uuids: list[Any],
) -> bool:
    """同内容の pending 提案が既にあるか（毎晩のCIで受信箱が重複で膨れるのを防ぐ）。"""
    return bool(
        await conn.fetchval(
            "select exists(select 1 from rule_proposals "
            "where workspace_id = $1 and status = 'pending' and kind = $2 "
            "  and text = $3 and target_rule_ids = $4::uuid[])",
            workspace_id,
            proposal.kind,
            proposal.text,
            list(target_rule_uuids),
        )
    )


async def insert_proposal(
    conn: asyncpg.Connection,
    workspace_id: Any,
    proposal: CiProposal,
    *,
    target_rule_uuids: list[Any],
    source_task_uuid: Any | None,
) -> asyncpg.Record:
    """CiProposal を rule_proposals（status=pending）へ保存する。トランザクション内で呼ぶ。"""
    return await conn.fetchrow(
        """
        insert into rule_proposals
          (workspace_id, kind, text, scope, tags, confidence, source,
           target_rule_ids, note, source_task_id)
        values ($1, $2, $3, $4, $5, $6, $7, $8::uuid[], $9, $10)
        returning *
        """,
        workspace_id,
        proposal.kind,
        proposal.text,
        proposal.scope,
        list(proposal.tags),
        proposal.confidence,
        proposal.source,
        list(target_rule_uuids),
        proposal.note,
        source_task_uuid,
    )


async def list_pending(
    conn: asyncpg.Connection, workspace_id: Any
) -> list[asyncpg.Record]:
    """pending の提案を作成の新しい順で返す（受信箱一覧）。"""
    return await conn.fetch(
        "select * from rule_proposals where workspace_id = $1 and status = 'pending' "
        "order by created_at desc, id desc",
        workspace_id,
    )


async def get_proposal_row(
    conn: asyncpg.Connection, proposal_id: str, *, for_update: bool = False
) -> asyncpg.Record | None:
    """UUID で提案行を引く（更新系はトランザクション内で for_update=True 推奨）。"""
    query = "select * from rule_proposals where id = $1"
    if for_update:
        query += " for update"
    return await conn.fetchrow(query, UUID(proposal_id))


async def mark_decided(
    conn: asyncpg.Connection, proposal_id: Any, *, status: str
) -> asyncpg.Record:
    """提案を adopted / dismissed で確定する。"""
    return await conn.fetchrow(
        "update rule_proposals set status = $2, decided_at = now() "
        "where id = $1 returning *",
        proposal_id,
        status,
    )


# ---- 採用時のルール操作（adopt の kind 別処理） --------------------------------------


async def create_rule_from_proposal(
    conn: asyncpg.Connection, row: asyncpg.Record
) -> Rule:
    """提案（distill / merge / conflict）から新ルールを作成する。

    - human_id は K-{seq} 連番、applied=0（新ルールとして実績を積み直す）
    - source は提案の source（例「ナレッジCI: K-04 と K-06 の矛盾検出」）
    - personal は由来タスクの担当者（無ければ workspace 先頭ユーザー）を owner にする（§6.7）
    トランザクション内で呼ぶこと。
    """
    workspace_id = row["workspace_id"]
    human_id = await rules_repo.next_rule_human_id(conn, workspace_id)
    owner_user_id = None
    if row["scope"] == "personal":
        if row["source_task_id"] is not None:
            owner_user_id = await conn.fetchval(
                "select owner_user_id from tasks where id = $1", row["source_task_id"]
            )
        if owner_user_id is None:
            owner_user_id = await conn.fetchval(
                "select id from users where workspace_id = $1 order by created_at limit 1",
                workspace_id,
            )
    new_row = await conn.fetchrow(
        """
        insert into rules
          (human_id, workspace_id, scope, owner_user_id,
           text, tags, source, source_task_id, confidence, applied)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0)
        returning *
        """,
        human_id,
        workspace_id,
        row["scope"],
        owner_user_id,
        row["text"],
        list(row["tags"]),
        row["source"],
        row["source_task_id"],
        row["confidence"],
    )
    return await rules_repo.rule_dto_from_row(conn, new_row)


async def archive_rules(
    conn: asyncpg.Connection, rule_uuids: list[Any]
) -> list[Rule]:
    """対象ルールをアーカイブする（archived=true。物理削除はしない §6.6）。

    更新後の Rule DTO を返す（RULE_UPDATED の SSE payload 用）。
    既にアーカイブ済みの行も冪等に含める。トランザクション内で呼ぶこと。
    """
    if not rule_uuids:
        return []
    rows = await conn.fetch(
        "update rules set archived = true, updated_at = now() "
        "where id = any($1::uuid[]) returning *",
        list(rule_uuids),
    )
    # 決定性のため human_id 昇順で返す
    return [
        await rules_repo.rule_dto_from_row(conn, row)
        for row in sorted(rows, key=lambda r: r["human_id"])
    ]


async def add_proposal_feedback(
    conn: asyncpg.Connection, row: asyncpg.Record, *, action: str
) -> None:
    """提案への採否を rule_feedback に記録する（§6.4 お手本ログ。task_id は null 可）。

    demote など text が空の提案は note を記録内容にする（feedback.text は not null）。
    """
    await conn.execute(
        "insert into rule_feedback (task_id, action, text, scope, tags, confidence) "
        "values ($1, $2, $3, $4, $5, $6)",
        row["source_task_id"],
        action,
        row["text"] or row["note"],
        row["scope"],
        list(row["tags"]),
        row["confidence"],
    )


# ---- 実行記録（knowledge_ci_runs） --------------------------------------------------


async def insert_run(conn: asyncpg.Connection, *, trigger: str) -> asyncpg.Record:
    """実行記録を開始する（started_at=now）。"""
    return await conn.fetchrow(
        "insert into knowledge_ci_runs (trigger) values ($1) returning *", trigger
    )


async def finish_run(
    conn: asyncpg.Connection,
    run_id: Any,
    *,
    proposals_created: int,
    rules_scanned: int,
    tasks_scanned: int,
    usage: TokenUsage,
    cost_usd: float,
) -> asyncpg.Record:
    """実行記録を確定する（件数・トークン・コスト #25 と同じ可視化方針）。"""
    return await conn.fetchrow(
        """
        update knowledge_ci_runs
        set proposals_created = $2, rules_scanned = $3, tasks_scanned = $4,
            input_tokens = $5, output_tokens = $6, cost_usd = $7, finished_at = now()
        where id = $1 returning *
        """,
        run_id,
        proposals_created,
        rules_scanned,
        tasks_scanned,
        usage.input_tokens,
        usage.output_tokens,
        cost_usd,
    )
