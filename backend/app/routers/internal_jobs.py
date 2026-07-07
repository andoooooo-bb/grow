"""worker エンドポイント（POST /internal/jobs/run, §7.2）。

Cloud Tasks の push ターゲット。/api prefix の外に置く（main.py で直接 include）。
local ランナーは同エンドポイントを経由せず run_execute_job を直接起動するが、
デバッグ・再実行用途にどちらのモードでも本エンドポイントは有効。

リトライ設計（§7.2）:
- JOB_RUNNER=local: ジョブ内で最大2回リトライし、最終失敗時のハンドオフ
  （you_todo 戻し＋失敗コメント）まで run_execute_job が行う。常に 200 を返す。
- JOB_RUNNER=cloud_tasks: ジョブ内リトライはせず 1 回だけ実行し、失敗は 5xx を
  返して Cloud Tasks の指数バックオフ再試行に任せる。最終試行かどうかは
  X-CloudTasks-TaskRetryCount ヘッダで判定し、上限到達時のみ人へのハンドオフを
  行って 200 を返す（Cloud Tasks 側のキュー再試行上限と揃えること）。
"""

import secrets

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.domain.dto import JobRunRequest
from app.jobs.execute import JobNotFoundError, run_execute_job

router = APIRouter(tags=["internal"])

# Cloud Tasks キューの再試行上限（キュー設定 max-attempts=4 = 初回+3再試行 と揃える）
CLOUD_TASKS_MAX_RETRY_COUNT = 3


@router.post("/internal/jobs/run")
async def run_job(payload: JobRunRequest, request: Request) -> dict[str, str]:
    settings = get_settings()
    # INTERNAL_JOBS_TOKEN 設定時のみヘッダの一致を検証する（#16）。
    # 本番は --allow-unauthenticated のため、enqueue 側（app/jobs/queue.py）が
    # 付与する X-Internal-Jobs-Token で外部からの直接叩き込みを拒否する。
    if settings.internal_jobs_token and not secrets.compare_digest(
        request.headers.get("X-Internal-Jobs-Token", ""), settings.internal_jobs_token
    ):
        raise HTTPException(status_code=403, detail="invalid internal jobs token")
    try:
        if settings.job_runner == "cloud_tasks":
            retry_count = int(request.headers.get("X-CloudTasks-TaskRetryCount", "0"))
            is_last_attempt = retry_count >= CLOUD_TASKS_MAX_RETRY_COUNT
            ok = await run_execute_job(
                payload.job_id, max_retries=0, handoff_on_failure=is_last_attempt
            )
            if not ok and not is_last_attempt:
                # 5xx を返して Cloud Tasks に再試行させる（読み取り専用ゆえ安全 §00 #3）
                raise HTTPException(status_code=500, detail="job failed; will be retried")
        else:
            ok = await run_execute_job(payload.job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:  # jobId が UUID として不正
        raise HTTPException(status_code=422, detail=f"invalid jobId: {payload.job_id}") from exc
    return {"status": "succeeded" if ok else "failed"}
