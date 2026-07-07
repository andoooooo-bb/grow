"""成果物（artifacts）のリポジトリ層（§02.6）。

AI実作業の Markdown レポートを版として重ねる（最大 version が最新）。
人の編集（#10 レビュー画面）も同じく新版として保存する。
"""

from typing import Any
from uuid import UUID

import asyncpg

from app.domain.models import Artifact


def _row_to_artifact(row: asyncpg.Record, task_human_id: str) -> Artifact:
    return Artifact(
        id=str(row["id"]),
        task_id=task_human_id,
        job_id=str(row["job_id"]) if row["job_id"] is not None else None,
        version=row["version"],
        content_md=row["content_md"],
        created_at=row["created_at"].isoformat(),
    )


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
    return _row_to_artifact(row, task_row["human_id"])


async def list_artifacts(
    conn: asyncpg.Connection, task_row: asyncpg.Record
) -> list[Artifact]:
    """タスクの全版を version 昇順で返す（末尾が最新）。"""
    rows = await conn.fetch(
        "select * from artifacts where task_id = $1 order by version",
        task_row["id"],
    )
    return [_row_to_artifact(row, task_row["human_id"]) for row in rows]
