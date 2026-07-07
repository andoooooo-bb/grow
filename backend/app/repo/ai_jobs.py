"""AIジョブ（ai_jobs）のリポジトリ層（§7.2 / §00 #16）。

ジョブの状態遷移（queued → running → succeeded | failed）と、
コスト可視化のためのトークン/コスト記録を担う。
"""

from collections.abc import Sequence
from uuid import UUID

import asyncpg

from app.domain.models import AiJob, AiJobKind


def job_from_row(row: asyncpg.Record, task_human_id: str) -> AiJob:
    """DB 行を AiJob DTO へ変換する（task_id は human_id で表す）。"""
    return AiJob(
        id=str(row["id"]),
        task_id=task_human_id,
        kind=row["kind"],
        status=row["status"],
        applied_rule_ids=[str(rule_id) for rule_id in row["applied_rule_ids"]],
        error=row["error"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        created_at=row["created_at"].isoformat(),
        finished_at=row["finished_at"].isoformat() if row["finished_at"] is not None else None,
    )


async def create_job(
    conn: asyncpg.Connection,
    task_row: asyncpg.Record,
    *,
    kind: AiJobKind = AiJobKind.EXECUTE,
    applied_rule_ids: Sequence[UUID] = (),
) -> asyncpg.Record:
    """ジョブ行を作成する（status=queued）。トランザクション内で呼ぶこと。"""
    return await conn.fetchrow(
        "insert into ai_jobs (task_id, kind, status, applied_rule_ids) "
        "values ($1, $2, 'queued', $3::uuid[]) returning *",
        task_row["id"],
        kind.value,
        list(applied_rule_ids),
    )


async def get_job_row(conn: asyncpg.Connection, job_id: UUID | str) -> asyncpg.Record | None:
    return await conn.fetchrow("select * from ai_jobs where id = $1", UUID(str(job_id)))


async def mark_running(conn: asyncpg.Connection, job_id: UUID | str) -> None:
    await conn.execute(
        "update ai_jobs set status = 'running' where id = $1", UUID(str(job_id))
    )


async def mark_succeeded(
    conn: asyncpg.Connection,
    job_id: UUID | str,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """成功で確定し、トークン/コストを記録する（§00 #16 / §7.6）。"""
    await conn.execute(
        "update ai_jobs set status = 'succeeded', input_tokens = $2, output_tokens = $3, "
        "cost_usd = $4, error = null, finished_at = now() where id = $1",
        UUID(str(job_id)),
        input_tokens,
        output_tokens,
        cost_usd,
    )


async def mark_failed(conn: asyncpg.Connection, job_id: UUID | str, *, error: str) -> None:
    """最終失敗で確定する（リトライ中の一時失敗では呼ばない）。"""
    await conn.execute(
        "update ai_jobs set status = 'failed', error = $2, finished_at = now() where id = $1",
        UUID(str(job_id)),
        error,
    )
