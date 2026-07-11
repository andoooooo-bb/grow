"""成果物（artifacts）のリポジトリ層（§02.6）。

AI実作業の Markdown レポートを版として重ねる（最大 version が最新）。
人の編集（#10 レビュー画面）も同じく新版として保存する。
#20: 各版の由来ルール（生成ジョブ ai_jobs.applied_rule_ids）を human_id で DTO に載せる。
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from app.domain.models import Artifact


def _row_to_artifact(
    row: asyncpg.Record, task_human_id: str, applied_rule_ids: Sequence[str] = ()
) -> Artifact:
    return Artifact(
        id=str(row["id"]),
        task_id=task_human_id,
        job_id=str(row["job_id"]) if row["job_id"] is not None else None,
        applied_rule_ids=list(applied_rule_ids),
        version=row["version"],
        content_md=row["content_md"],
        created_at=row["created_at"].isoformat(),
    )


async def _applied_rule_human_ids(
    conn: asyncpg.Connection, job_id: UUID | None
) -> list[str]:
    """ジョブが注入したルールを human_id（例 "K-01"）で返す（applied_rule_ids の順序を保持）。"""
    if job_id is None:
        return []
    rows = await conn.fetch(
        """
        select r.human_id
        from ai_jobs j
        cross join unnest(j.applied_rule_ids) with ordinality as u(rule_id, ord)
        join rules r on r.id = u.rule_id
        where j.id = $1
        order by u.ord
        """,
        job_id,
    )
    return [row["human_id"] for row in rows]


async def create_artifact(
    conn: asyncpg.Connection,
    task_row: asyncpg.Record,
    content_md: str,
    *,
    job_id: UUID | str | None = None,
) -> Artifact:
    """新版として保存する（version = 既存最大 + 1）。トランザクション内で呼ぶこと。"""
    job_uuid: Any = UUID(str(job_id)) if job_id is not None else None
    row = await conn.fetchrow(
        """
        insert into artifacts (task_id, job_id, version, content_md)
        values (
          $1, $2,
          (select coalesce(max(version), 0) + 1 from artifacts where task_id = $1),
          $3
        )
        returning *
        """,
        task_row["id"],
        job_uuid,
        content_md,
    )
    # artifact.created の SSE payload にも由来ルールを載せる（#20。FE store が版を追記する）
    applied_rule_ids = await _applied_rule_human_ids(conn, job_uuid)
    return _row_to_artifact(row, task_row["human_id"], applied_rule_ids)


async def list_artifacts(
    conn: asyncpg.Connection, task_row: asyncpg.Record
) -> list[Artifact]:
    """タスクの全版を version 昇順で返す（末尾が最新）。

    ai_jobs を join し、各版の由来ルール（applied_rule_ids）を human_id で復元する（#20）。
    人の編集版（job_id なし）は空配列。
    """
    rows = await conn.fetch(
        """
        select a.*,
               coalesce(
                 (
                   select array_agg(r.human_id order by u.ord)
                   from unnest(j.applied_rule_ids) with ordinality as u(rule_id, ord)
                   join rules r on r.id = u.rule_id
                 ),
                 '{}'
               ) as applied_rule_human_ids
        from artifacts a
        left join ai_jobs j on j.id = a.job_id
        where a.task_id = $1
        order by a.version
        """,
        task_row["id"],
    )
    return [
        _row_to_artifact(row, task_row["human_id"], row["applied_rule_human_ids"])
        for row in rows
    ]
