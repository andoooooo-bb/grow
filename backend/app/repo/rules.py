"""ルール（ナレッジ）のリポジトリ層 — retrieval（§6.3 / §00 #8）が本体。

`relevant_rules()` は execute（#9）だけでなく壁打ち・分解（#13）からも再利用する公開API。
入力はタスク行（asyncpg.Record: workspace_id / labels を持つこと）、出力はルール行のリスト。
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

# 注入件数の上限（§00 #8 で確定: 上限8件・confidence 降順で足切り）
RETRIEVAL_LIMIT = 8


async def relevant_rules(
    conn: asyncpg.Connection, task: asyncpg.Record, *, limit: int = RETRIEVAL_LIMIT
) -> list[asyncpg.Record]:
    """タスクに適用すべきルール行を返す（§6.3 retrieval, MVP はタグ一致・決定的）。

    - 対象: tags が空（全体ルール）または task.labels と交差するもの。
      personal / team の両 scope を対象にする（§00 #8）。
    - 並び: confidence 降順（high > med > low）→ 同 confidence は applied 降順
      → 決定性のため human_id 昇順。上限 limit 件（既定 8）で足切り。
    """
    return await conn.fetch(
        """
        select * from rules
        where workspace_id = $1
          and (cardinality(tags) = 0 or tags && $2::text[])
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
