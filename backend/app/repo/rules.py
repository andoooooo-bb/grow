"""ルール（ナレッジ）のリポジトリ層 — retrieval（§6.3 / §00 #8）が本体。

`relevant_rules()` は execute（#9）だけでなく壁打ち・分解（#13）からも再利用する公開API。
入力はタスク行（asyncpg.Record: workspace_id / labels を持つこと）、出力はルール行のリスト。

#13 で手動蒸留の永続化系（create_rule / promote_rule / add_feedback）を追加。
API 境界の ruleId は human_id（例 "K-01"）、DB 主キーは UUID（§00 #9）。
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from app.domain.models import Confidence, Rule, RuleScope

# 注入件数の上限（§00 #8 で確定: 上限8件・confidence 降順で足切り）
RETRIEVAL_LIMIT = 8


async def relevant_rules(
    conn: asyncpg.Connection, task: asyncpg.Record, *, limit: int = RETRIEVAL_LIMIT
) -> list[asyncpg.Record]:
    """タスクに適用すべきルール行を返す（§6.3 retrieval, MVP はタグ一致・決定的）。

    - 対象: tags が空（全体ルール）または task.labels と交差するもの。
      personal / team の両 scope を対象にする（§00 #8）。
      アーカイブ済み（#26 棚卸し）は除外する。
    - 並び: confidence 降順（high > med > low）→ 同 confidence は applied 降順
      → 決定性のため human_id 昇順。上限 limit 件（既定 8）で足切り。
    """
    return await conn.fetch(
        """
        select * from rules
        where workspace_id = $1
          and (cardinality(tags) = 0 or tags && $2::text[])
          and not archived
        order by
          case confidence when 'high' then 0 when 'med' then 1 else 2 end,
          applied desc,
          human_id
        limit $3
        """,
        task["workspace_id"],
        list(task["labels"]),
        limit,
    )


async def record_applications(
    conn: asyncpg.Connection, task: asyncpg.Record, rule_rows: Sequence[asyncpg.Record]
) -> None:
    """適用の証跡を記録する（§6.3）: applied++ / last_applied_at / rule_applications。

    トランザクション内で呼ぶこと。
    """
    if not rule_rows:
        return
    rule_ids = [row["id"] for row in rule_rows]
    await conn.execute(
        "update rules set applied = applied + 1, last_applied_at = now(), updated_at = now() "
        "where id = any($1::uuid[])",
        rule_ids,
    )
    await conn.executemany(
        "insert into rule_applications (rule_id, task_id) values ($1, $2)",
        [(rule_id, task["id"]) for rule_id in rule_ids],
    )


async def get_rules_by_uuids(
    conn: asyncpg.Connection, rule_ids: Sequence[UUID]
) -> list[asyncpg.Record]:
    """UUID のリストでルール行を引く（渡した順序を保持）。ai_jobs.applied_rule_ids 用。"""
    if not rule_ids:
        return []
    rows = await conn.fetch("select * from rules where id = any($1::uuid[])", list(rule_ids))
    by_id = {row["id"]: row for row in rows}
    return [by_id[rule_id] for rule_id in rule_ids if rule_id in by_id]


def rule_prompt_dict(row: asyncpg.Record) -> dict[str, Any]:
    """AiProvider へ注入するルール dict（§7.3 の system プロンプト材料）。"""
    return {
        "id": row["human_id"],
        "text": row["text"],
        "scope": row["scope"],
        "confidence": row["confidence"],
        "tags": list(row["tags"]),
        "source": row["source"],
    }


# ---- 手動蒸留の永続化（#13 §6.4a） -------------------------------------------------


def _row_to_rule(row: asyncpg.Record, source_task_human_id: str | None) -> Rule:
    """DB 行を Rule DTO へ変換する（source_task_id は human_id で表現する §2.2）。"""
    return Rule(
        id=row["human_id"],
        workspace_id=str(row["workspace_id"]),
        scope=row["scope"],
        owner_user_id=(
            str(row["owner_user_id"]) if row["owner_user_id"] is not None else None
        ),
        text=row["text"],
        tags=list(row["tags"]),
        source=row["source"],
        source_task_id=source_task_human_id,
        confidence=row["confidence"],
        applied=row["applied"],
        last_applied_at=(
            row["last_applied_at"].isoformat() if row["last_applied_at"] is not None else None
        ),
        archived=row["archived"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


async def rule_dto_from_row(conn: asyncpg.Connection, row: asyncpg.Record) -> Rule:
    """ルール行を Rule DTO にする（source_task の human_id を逆引きで補完）。"""
    source_task_human_id = None
    if row["source_task_id"] is not None:
        source_task_human_id = await conn.fetchval(
            "select human_id from tasks where id = $1", row["source_task_id"]
        )
    return _row_to_rule(row, source_task_human_id)


async def next_rule_human_id(conn: asyncpg.Connection, workspace_id: Any) -> str:
    """workspace 内の K-{seq} 連番（既存最大値 +1、プロト準拠の2桁ゼロ詰め）。

    トランザクション内で呼ぶこと（tasks.next_human_id と同方針）。
    """
    max_seq = await conn.fetchval(
        "select coalesce(max(substring(human_id from 3)::int), 0) from rules "
        "where workspace_id = $1",
        workspace_id,
    )
    return f"K-{max_seq + 1:02d}"


async def create_rule(
    conn: asyncpg.Connection,
    task_row: asyncpg.Record,
    *,
    text: str,
    scope: RuleScope,
    tags: list[str],
    confidence: Confidence,
) -> Rule:
    """採用された蒸留候補をルールとして永続化する（§5.3 adoptLearn）。

    human_id=K-{seq} 連番・applied=0・source=「{taskId} から学習」・source_task_id=当該タスク。
    personal のときは owner をタスクの担当者にする（§6.7）。トランザクション内で呼ぶこと。
    """
    human_id = await next_rule_human_id(conn, task_row["workspace_id"])
    owner_user_id = task_row["owner_user_id"] if scope is RuleScope.PERSONAL else None
    row = await conn.fetchrow(
        """
        insert into rules
          (human_id, workspace_id, scope, owner_user_id,
           text, tags, source, source_task_id, confidence, applied)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0)
        returning *
        """,
        human_id,
        task_row["workspace_id"],
        scope.value,
        owner_user_id,
        text,
        tags,
        f"{task_row['human_id']} から学習",
        task_row["id"],
        confidence.value,
    )
    return _row_to_rule(row, task_row["human_id"])


async def get_rule_by_human_id(
    conn: asyncpg.Connection, human_id: str, *, for_update: bool = False
) -> asyncpg.Record | None:
    """human_id でルール行を引く（更新系はトランザクション内で for_update=True 推奨）。"""
    query = "select * from rules where human_id = $1"
    if for_update:
        query += " for update"
    return await conn.fetchrow(query, human_id)


async def promote_rule(conn: asyncpg.Connection, row: asyncpg.Record) -> Rule:
    """個人ルールをチームへ昇格する（§1.8 / §5.3 promoteRule: scope=team）。"""
    new_row = await conn.fetchrow(
        "update rules set scope = 'team', updated_at = now() where id = $1 returning *",
        row["id"],
    )
    return await rule_dto_from_row(conn, new_row)


# confidence の1段降格の対応（#23。low が下限 — それ以上は下げない）
_CONFIDENCE_DOWNGRADE: dict[str, Confidence] = {
    "high": Confidence.MED,
    "med": Confidence.LOW,
}

# confidence の1段昇格の対応（#26 §6.6。high が上限 — それ以上は上げない）
_CONFIDENCE_UPGRADE: dict[str, Confidence] = {
    "med": Confidence.HIGH,
    "low": Confidence.MED,
}


async def downgrade_confidence(
    conn: asyncpg.Connection, row: asyncpg.Record
) -> Rule | None:
    """ルールの confidence を1段降格する（#23 矛盾検出 / 夜間ナレッジCI #26 と共用）。

    high→med / med→low。既に low なら何もしない（None を返す）。
    降格した場合は更新後の Rule DTO を返す（RULE_UPDATED の SSE payload 用）。
    トランザクション内で呼ぶこと。
    """
    next_confidence = _CONFIDENCE_DOWNGRADE.get(row["confidence"])
    if next_confidence is None:
        return None
    new_row = await conn.fetchrow(
        "update rules set confidence = $2, updated_at = now() where id = $1 returning *",
        row["id"],
        next_confidence.value,
    )
    return await rule_dto_from_row(conn, new_row)


async def upgrade_confidence(
    conn: asyncpg.Connection, row: asyncpg.Record
) -> Rule | None:
    """ルールの confidence を1段昇格する（#26 §6.6 確度ライフサイクル）。

    low→med / med→high。既に high なら何もしない（None を返す）。
    昇格した場合は更新後の Rule DTO を返す（RULE_UPDATED の SSE payload 用）。
    トランザクション内で呼ぶこと。
    """
    next_confidence = _CONFIDENCE_UPGRADE.get(row["confidence"])
    if next_confidence is None:
        return None
    new_row = await conn.fetchrow(
        "update rules set confidence = $2, updated_at = now() where id = $1 returning *",
        row["id"],
        next_confidence.value,
    )
    return await rule_dto_from_row(conn, new_row)


# ---- 暗黙評価シグナル（#26 §6.6。レビュー承認/差し戻しの自動記録） -------------------

# (from_status, to_status) → シグナル。§5.6 の承認/差し戻し/再開の遷移に対応する
_TRANSITION_SIGNALS: dict[tuple[str, str], str] = {
    ("you_review", "done"): "positive",  # 承認
    ("reviewing", "done"): "positive",  # 承認
    ("you_review", "ai_work"): "negative",  # 差し戻し
    ("reviewing", "ai_work"): "negative",  # 差し戻し
    ("done", "you_todo"): "negative",  # 完了の再オープン（暗黙の不満）
}


async def record_transition_signals(
    conn: asyncpg.Connection, task_row: asyncpg.Record, *, old_status: str, new_status: str
) -> None:
    """ステータス遷移を適用済みルールへの暗黙評価として rule_signals に記録する（#26）。

    承認（you_review/reviewing→done）は positive、差し戻し（→ai_work）と
    再オープン（done→you_todo）は negative を、直近 execute ジョブの
    applied_rule_ids 全件に記録する。対象ジョブが無ければ何もしない。
    repo/tasks.py apply_patch の遷移フックから呼ばれる（トランザクション内）。
    """
    signal = _TRANSITION_SIGNALS.get((old_status, new_status))
    if signal is None:
        return
    rule_ids = await conn.fetchval(
        "select applied_rule_ids from ai_jobs "
        "where task_id = $1 and kind = 'execute' "
        "order by created_at desc, id desc limit 1",
        task_row["id"],
    )
    if not rule_ids:
        return
    await conn.executemany(
        "insert into rule_signals (rule_id, task_id, signal) values ($1, $2, $3)",
        [(rule_id, task_row["id"], signal) for rule_id in rule_ids],
    )


async def add_feedback(
    conn: asyncpg.Connection,
    task_row: asyncpg.Record,
    *,
    action: str,
    text: str,
    scope: RuleScope,
    tags: list[str],
    confidence: Confidence,
) -> None:
    """人の採用/却下ログを rule_feedback に保存する（§6.4 将来の自動化のお手本）。"""
    await conn.execute(
        "insert into rule_feedback (task_id, action, text, scope, tags, confidence) "
        "values ($1, $2, $3, $4, $5, $6)",
        task_row["id"],
        action,
        text,
        scope.value,
        tags,
        confidence.value,
    )
