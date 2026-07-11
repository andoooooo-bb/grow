"""夜間ナレッジCI バッチ本体（#26 §6.4b/c・§6.6 / §7.5 reconcile_rules）。

Cloud Scheduler（毎日 JST 03:00 → POST /internal/knowledge/ci）と
デモ用の手動実行（POST /api/knowledge/ci/run）の両方がここに収束する。

処理フロー:
  1. 対象データ収集 — 既存ルール全件（archived 除外）・直近の完了タスク（未蒸留判定付き）・
     rule_feedback（人の採否のお手本）・rule_signals（承認/差し戻しの暗黙評価）
  2. provider.reconcile_rules — 新規蒸留 / 重複統合 / 矛盾検出 / 棚卸しの提案を判定
  3. 提案を rule_proposals（受信箱, status=pending）へ保存（同内容の pending は重複スキップ）
  4. 確度の自動昇降格（§6.6）— これは提案を介さず自動適用する:
     positive シグナル≥2 で1段昇格、negative≥2 で1段降格（negative 優先 = 安全側）
  5. knowledge_ci_runs へ実行記録（件数・トークン・コスト。Flash 単価で実算定 #25）
  6. SSE 配信 — rule.updated（昇降格）/ rule_proposal.created（受信箱ライブ更新）/
     knowledge.ci.completed（実行サマリー）

提案の採用/却下は人が受信箱（routers/knowledge.py）で行う。自動採用はしない
（§6.4 の閾値運用思想 — 確度の昇降格のみ自動、ルールの生死は人が握る）。
"""

import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.ai import get_provider
from app.costs import calc_cost_usd
from app.db import get_pool
from app.domain.dto import KnowledgeCiCompletedEvent, RuleProposalCreatedEvent
from app.domain.models import AiJobKind, Rule
from app.events import (
    KNOWLEDGE_CI_COMPLETED,
    RULE_PROPOSAL_CREATED,
    RULE_UPDATED,
    publish_event,
)
from app.repo import knowledge as knowledge_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo

logger = logging.getLogger(__name__)

# 収集上限（トークン管理 §7.6。ルールは全件 = MVP の件数なら全件コンテキスト投入で成立）
RECENT_TASKS_LIMIT = 20
FEEDBACK_LIMIT = 20
SIGNALS_LIMIT = 100

# 確度の自動昇降格しきい値（§6.6: 承認≥2 で1段昇格 / 差し戻し≥2 で1段降格）
SIGNAL_UPGRADE_THRESHOLD = 2
SIGNAL_DOWNGRADE_THRESHOLD = 2


@dataclass(frozen=True, slots=True)
class KnowledgeCiOutcome:
    """1回のCI実行の結果サマリー（API 応答と knowledge.ci.completed の材料）。"""

    run_id: str
    trigger: str
    proposals_created: int
    rules_scanned: int
    tasks_scanned: int
    cost_usd: float


async def run_knowledge_ci(*, trigger: str) -> KnowledgeCiOutcome:
    """ナレッジCIを1回実行する（trigger: 'scheduled' | 'manual'）。"""
    pool = await get_pool()

    # 1) 対象データ収集 ＋ 実行記録の開始
    async with pool.acquire() as conn:
        board = await tasks_repo.get_default_board(conn)
        workspace_id = board["workspace_id"]
        run_row = await knowledge_repo.insert_run(conn, trigger=trigger)
        rule_rows = await conn.fetch(
            "select * from rules where workspace_id = $1 and not archived "
            "order by human_id",
            workspace_id,
        )
        # done かつ未蒸留（rules.source_task_id に現れない）の判定付きで直近完了タスクを集める
        task_rows = await conn.fetch(
            """
            select t.*,
                   exists(select 1 from rules r where r.source_task_id = t.id) as distilled
            from tasks t
            where t.workspace_id = $1 and t.status = 'done'
            order by t.updated_at desc, t.human_id
            limit $2
            """,
            workspace_id,
            RECENT_TASKS_LIMIT,
        )
        feedback_rows = await conn.fetch(
            "select * from rule_feedback order by created_at desc, id desc limit $1",
            FEEDBACK_LIMIT,
        )
        signal_rows = await conn.fetch(
            """
            select s.signal, r.human_id as rule_human_id
            from rule_signals s
            join rules r on r.id = s.rule_id
            where r.workspace_id = $1
            order by s.created_at desc, s.id desc
            limit $2
            """,
            workspace_id,
            SIGNALS_LIMIT,
        )

    # 2) AI 判定（reconcile。mock は決定的 / gemini は Flash の強制 FC）
    result = await get_provider().reconcile_rules(
        [_rule_ci_dict(row) for row in rule_rows],
        [_task_ci_dict(row) for row in task_rows],
        [_feedback_ci_dict(row) for row in feedback_rows],
        [
            {"ruleId": row["rule_human_id"], "signal": row["signal"]}
            for row in signal_rows
        ],
    )
    cost_usd = calc_cost_usd(AiJobKind.DISTILL, result.usage)  # Flash 単価（#25）

    # 3) 提案の保存 ＋ 4) 確度の自動昇降格 ＋ 5) 実行記録（単一トランザクション）
    created_dtos = []
    updated_rules: list[Rule] = []
    async with pool.acquire() as conn, conn.transaction():
        rule_uuid_by_human = {row["human_id"]: row["id"] for row in rule_rows}
        task_uuid_by_human = {row["human_id"]: row["id"] for row in task_rows}
        for proposal in result.proposals:
            target_uuids = [
                rule_uuid_by_human[human_id]
                for human_id in proposal.target_rule_ids
                if human_id in rule_uuid_by_human
            ]
            if len(target_uuids) != len(proposal.target_rule_ids):
                continue  # 実在しない対象を含む提案は保存しない（幻覚の最終防波堤）
            source_task_uuid = (
                task_uuid_by_human.get(proposal.source_task_id)
                if proposal.source_task_id
                else None
            )
            if await knowledge_repo.has_pending_duplicate(
                conn, workspace_id, proposal, target_uuids
            ):
                continue  # 毎晩の再実行で受信箱を重複で埋めない
            row = await knowledge_repo.insert_proposal(
                conn,
                workspace_id,
                proposal,
                target_rule_uuids=target_uuids,
                source_task_uuid=source_task_uuid,
            )
            created_dtos.append(await knowledge_repo.proposal_dto_from_row(conn, row))

        updated_rules = await _apply_confidence_lifecycle(conn, rule_rows)

        await knowledge_repo.finish_run(
            conn,
            run_row["id"],
            proposals_created=len(created_dtos),
            rules_scanned=len(rule_rows),
            tasks_scanned=len(task_rows),
            usage=result.usage,
            cost_usd=cost_usd,
        )

    # 6) SSE 配信（コミット後 — 受信箱バッジ・確度バッジがライブ更新される）
    for rule in updated_rules:
        publish_event(RULE_UPDATED, rule.model_dump(mode="json", by_alias=True))
    if created_dtos:
        proposal_event = RuleProposalCreatedEvent(
            count=len(created_dtos), proposals=created_dtos
        )
        publish_event(
            RULE_PROPOSAL_CREATED, proposal_event.model_dump(mode="json", by_alias=True)
        )
    outcome = KnowledgeCiOutcome(
        run_id=str(run_row["id"]),
        trigger=trigger,
        proposals_created=len(created_dtos),
        rules_scanned=len(rule_rows),
        tasks_scanned=len(task_rows),
        cost_usd=cost_usd,
    )
    completed_event = KnowledgeCiCompletedEvent(
        run_id=outcome.run_id,
        trigger=outcome.trigger,
        proposals_created=outcome.proposals_created,
        rules_scanned=outcome.rules_scanned,
        tasks_scanned=outcome.tasks_scanned,
        cost_usd=outcome.cost_usd,
    )
    publish_event(
        KNOWLEDGE_CI_COMPLETED, completed_event.model_dump(mode="json", by_alias=True)
    )
    logger.info(
        "knowledge CI run %s (%s): proposals=%d rules=%d tasks=%d cost=$%.4f",
        outcome.run_id,
        trigger,
        outcome.proposals_created,
        outcome.rules_scanned,
        outcome.tasks_scanned,
        outcome.cost_usd,
    )
    return outcome


async def _apply_confidence_lifecycle(
    conn: asyncpg.Connection, rule_rows: list[asyncpg.Record]
) -> list[Rule]:
    """確度の自動昇降格（§6.6）。提案を介さず適用し、変更ルールの DTO を返す。

    rule_signals の全累計で判定する: negative≥2 なら1段降格（優先 = 安全側）、
    そうでなく positive≥2 なら1段昇格。上限 high / 下限 low で収束する
    （既に端のルールは no-op なので毎晩実行しても暴れない）。
    """
    count_rows = await conn.fetch(
        "select rule_id, signal, count(*)::int as n from rule_signals group by 1, 2"
    )
    counts: dict[tuple[Any, str], int] = {
        (row["rule_id"], row["signal"]): row["n"] for row in count_rows
    }
    updated: list[Rule] = []
    for rule_row in rule_rows:
        negative = counts.get((rule_row["id"], "negative"), 0)
        positive = counts.get((rule_row["id"], "positive"), 0)
        rule = None
        if negative >= SIGNAL_DOWNGRADE_THRESHOLD:
            rule = await rules_repo.downgrade_confidence(conn, rule_row)
        elif positive >= SIGNAL_UPGRADE_THRESHOLD:
            rule = await rules_repo.upgrade_confidence(conn, rule_row)
        if rule is not None:
            updated.append(rule)
    return updated


# ---- provider へ渡す dict 変換（provider.py reconcile_rules の想定キー） -------------


def _rule_ci_dict(row: asyncpg.Record) -> dict[str, Any]:
    """既存ルール1件（rule_prompt_dict に適用実績・作成日時を加えた判定材料）。"""
    return {
        "id": row["human_id"],
        "text": row["text"],
        "scope": row["scope"],
        "tags": list(row["tags"]),
        "confidence": row["confidence"],
        "source": row["source"],
        "applied": row["applied"],
        "lastAppliedAt": (
            row["last_applied_at"].isoformat()
            if row["last_applied_at"] is not None
            else None
        ),
        "createdAt": row["created_at"].isoformat(),
    }


def _task_ci_dict(row: asyncpg.Record) -> dict[str, Any]:
    """完了タスク1件（distilled = 既に rules.source_task_id に現れているか）。"""
    return {
        "humanId": row["human_id"],
        "title": row["title"],
        "labels": list(row["labels"]),
        "status": row["status"],
        "distilled": bool(row["distilled"]),
    }


def _feedback_ci_dict(row: asyncpg.Record) -> dict[str, Any]:
    """人の採用/却下ログ1件（few-shot 材料 §6.4a）。"""
    return {
        "action": row["action"],
        "text": row["text"],
        "scope": row["scope"],
        "tags": list(row["tags"]),
        "confidence": row["confidence"],
    }
