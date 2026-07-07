"""ジョブランナー抽象（§7.2）— JOB_RUNNER=local|cloud_tasks で enqueue 先を切り替える。

- local（既定・ローカル/テスト）: asyncio.create_task でジョブコルーチンを
  プロセス内で直接実行する。外部依存ゼロ。
- cloud_tasks（本番）: Cloud Tasks に HTTP タスクを投入し、
  POST {SELF_URL}/internal/jobs/run（app/routers/internal_jobs.py）へ push させる。

どちらのモードでも実行本体は同じ worker エンドポイント/コルーチン
（app/jobs/execute.py の run_execute_job）に収束する。
"""

import asyncio
import json

from app.config import get_settings

# local ランナーが生成した実行中タスクの参照（GC 防止 + テストでの完了待ち用）
_local_tasks: set[asyncio.Task[object]] = set()


async def enqueue_job(job_id: str) -> None:
    """ai_jobs.id を受け取り、設定されたランナーへ enqueue する。

    呼び出しはトランザクションのコミット後に行うこと
    （ジョブ側が別コネクションでコミット済みの行を読むため）。
    """
    if get_settings().job_runner == "cloud_tasks":
        await _enqueue_cloud_tasks(job_id)
    else:
        _enqueue_local(job_id)


def _enqueue_local(job_id: str) -> None:
    """local: 実行中のイベントループ上でジョブコルーチンを直接起動する。"""
    from app.jobs.execute import run_execute_job

    task = asyncio.create_task(run_execute_job(job_id))
    _local_tasks.add(task)
    task.add_done_callback(_local_tasks.discard)


async def _enqueue_cloud_tasks(job_id: str) -> None:
    """cloud_tasks: {SELF_URL}/internal/jobs/run への HTTP POST タスクを投入する。

    リトライは Cloud Tasks のキュー設定（指数バックオフ）に任せる（§7.2）。
    クライアント生成・API 呼び出しは同期 SDK のため to_thread で逃がす。
    """
    settings = get_settings()

    def _create_task() -> None:
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(
            settings.gcp_project, settings.gcp_location, settings.cloud_tasks_queue
        )
        client.create_task(
            request={
                "parent": parent,
                "task": {
                    "http_request": {
                        "http_method": tasks_v2.HttpMethod.POST,
                        "url": f"{settings.self_url}/internal/jobs/run",
                        "headers": {"Content-Type": "application/json"},
                        "body": json.dumps({"jobId": job_id}).encode("utf-8"),
                    }
                },
            }
        )

    await asyncio.to_thread(_create_task)


async def drain_local_jobs() -> None:
    """local ランナーで実行中の全ジョブの完了を待つ（テスト・シャットダウン用）。"""
    while _local_tasks:
        await asyncio.gather(*list(_local_tasks), return_exceptions=True)
