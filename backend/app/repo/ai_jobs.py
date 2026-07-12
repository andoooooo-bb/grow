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


async def total_cost_usd(conn: asyncpg.Connection, task_id: object) -> float:
    """タスクの累計コスト（sum(ai_jobs.cost_usd)。null は 0 扱い）。

    #21 コスト上限（assign-ai の enqueue 前チェック）と #22 指揮者の予算判断が読む。
    task_id は tasks.id（UUID）。
    """
    value = await conn.fetchval(
        "select coalesce(sum(cost_usd), 0) from ai_jobs where task_id = $1", task_id
    )
    return float(value)


async def list_jobs(conn: asyncpg.Connection, task_row: asyncpg.Record) -> list[AiJob]:
    """タスクのジョブを created_at 昇順で返す（#19 リレー・タイムラインの履歴）。"""
    rows = await conn.fetch(
        "select * from ai_jobs where task_id = $1 order by created_at, id",
        task_row["id"],
    )
    return [job_from_row(row, task_row["human_id"]) for row in rows]


async def create_job(
    conn: asyncpg.Connection,
    task_row: asyncpg.Record,
    *,
    kind: AiJobKind = AiJobKind.EXECUTE,
    applied_rule_ids: Sequence[UUID] = (),
) -> asyncpg.Record:
    """ジョブ行を作成する（status=queued）。トランザクション内で呼ぶこと。

    created_at は clock_timestamp() で明示する（comments と同じ理由 —
    既定の now() はトランザクション開始時刻のため、同一トランザクション内で
    先に投稿したコメントより「過去」になり、セッション境界の集計
    （orchestrate._session_step_count 等）が狂うのを防ぐ）。
    """
    return await conn.fetchrow(
        "insert into ai_jobs (task_id, kind, status, applied_rule_ids, created_at) "
        "values ($1, $2, 'queued', $3::uuid[], clock_timestamp()) returning *",
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
