"""kind → 実行関数のディスパッチテーブル（#18 共通基盤）。

worker（POST /internal/jobs/run）と local ランナーの実行部はどちらも
dispatch_job() に収束する: jobId → ai_jobs 行取得 → kind に応じた実行関数。

新 kind の登録手順（指揮者 #22 / セルフレビュー #23 / ナレッジCI #26 など後続 Wave）:

1. ジョブ本体モジュールに統一シグネチャのハンドラを実装する:

       async def run_xxx_job_row(
           job_row: asyncpg.Record,
           *,
           max_retries: int | None = None,
           handoff_on_failure: bool = True,
       ) -> bool: ...

   - job_row: ディスパッチ時点の ai_jobs 行（kind 判定に使った行をそのまま渡す）。
     最新状態が必要ならハンドラ側で job_row["id"] から再取得すること。
   - max_retries: ジョブ内リトライ回数（None はハンドラ既定）。cloud_tasks 経由は 0
     が渡る（キュー側の指数バックオフ再試行に任せる, §7.2）。
   - handoff_on_failure: 最終失敗時に人へのハンドオフ（コメント等）を行うか。
   - 戻り値: 成功 True / 最終失敗 False（worker が 5xx 再試行の判断に使う）。

2. 本モジュール末尾の登録ブロックに 1 行追加する:

       register(AiJobKind.XXX, run_xxx_job_row)

未知 kind（未登録）は ai_jobs=failed + エラーログの上で UnknownJobKindError を
送出する（worker は 422 を返す。再試行しても成功しないため 5xx にはしない）。
"""

import logging
from typing import Protocol

import asyncpg

from app.db import get_pool
from app.domain.models import AiJobKind, AiJobStatus
from app.jobs.execute import JobNotFoundError, run_execute_job_row
from app.jobs.orchestrate import run_orchestrate_job_row
from app.jobs.review import run_review_job_row
from app.repo import ai_jobs as ai_jobs_repo

logger = logging.getLogger(__name__)


class UnknownJobKindError(Exception):
    """ai_jobs.kind に対応する実行関数が登録されていない。"""


class JobHandler(Protocol):
    """kind ごとのジョブ実行関数の統一シグネチャ（モジュール docstring 参照）。"""

    async def __call__(
        self,
        job_row: asyncpg.Record,
        *,
        max_retries: int | None = None,
        handoff_on_failure: bool = True,
    ) -> bool: ...


_HANDLERS: dict[str, JobHandler] = {}


def register(kind: AiJobKind | str, handler: JobHandler) -> None:
    """kind に実行関数を登録する（同一 kind への再登録は上書き）。"""
    _HANDLERS[str(kind)] = handler


def get_handler(kind: AiJobKind | str) -> JobHandler:
    """kind の実行関数を返す。未登録なら UnknownJobKindError。"""
    handler = _HANDLERS.get(str(kind))
    if handler is None:
        raise UnknownJobKindError(f"unknown job kind: {kind}")
    return handler


async def dispatch_job(
    job_id: str,
    *,
    max_retries: int | None = None,
    handoff_on_failure: bool = True,
) -> bool:
    """jobId から ai_jobs 行を取得し、kind に応じた実行関数へディスパッチする。

    - 行が存在しない → JobNotFoundError（worker は 404）。
    - kind が未登録 → ai_jobs=failed ＋ エラーログの上で UnknownJobKindError
      （worker は 422。既に終端状態なら failed 更新はスキップ）。
    - それ以外はハンドラの戻り値（成功 True / 最終失敗 False）をそのまま返す。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        job_row = await ai_jobs_repo.get_job_row(conn, job_id)
        if job_row is None:
            raise JobNotFoundError(f"ai_job not found: {job_id}")
        kind = job_row["kind"]
        try:
            handler = get_handler(kind)
        except UnknownJobKindError:
            logger.error(
                "no handler registered for ai_jobs.kind=%r (job %s); marking failed",
                kind,
                job_id,
            )
            if job_row["status"] not in (AiJobStatus.SUCCEEDED, AiJobStatus.FAILED):
                await ai_jobs_repo.mark_failed(conn, job_id, error=f"unknown job kind: {kind}")
            raise
    # ハンドラはコネクション返却後に呼ぶ（長時間ジョブでプールを塞がないため）
    return await handler(
        job_row, max_retries=max_retries, handoff_on_failure=handoff_on_failure
    )


# ---- 登録ブロック（後続 Wave は新 kind をここに1行追加） -----------------------------
register(AiJobKind.EXECUTE, run_execute_job_row)
register(AiJobKind.ORCHESTRATE, run_orchestrate_job_row)
register(AiJobKind.REVIEW, run_review_job_row)
