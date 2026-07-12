"""ジョブランナー抽象（§7.2）— JOB_RUNNER=local|cloud_tasks で enqueue 先を切り替える。

- local（既定・ローカル/テスト）: asyncio.create_task でジョブコルーチンを
  プロセス内で直接実行する。外部依存ゼロ。
- cloud_tasks（本番）: Cloud Tasks に HTTP タスクを投入し、
  POST {SELF_URL}/internal/jobs/run（app/routers/internal_jobs.py）へ push させる。

どちらのモードでも実行本体は同じディスパッチャ
（app/jobs/registry.py の dispatch_job — jobId → ai_jobs.kind → 実行関数, #18）
に収束する。enqueue は jobId のみで完結し、kind は ai_jobs 行から引かれる。
"""

import asyncio
import json

from app.config import get_settings

# local ランナーが生成した実行中タスクの参照（GC 防止 + テストでの完了待ち用）
_local_tasks: set[asyncio.Task[object]] = set()


async def enqueue_job(job_id: str, *, kind: str | None = None) -> None:
    """ai_jobs.id を受け取り、設定されたランナーへ enqueue する（kind 非依存, #18）。

    - kind は任意の同梱情報（cloud_tasks の body に含め、キュー上の可観測性を上げる）。
      worker 側のディスパッチは常に ai_jobs 行の kind で行うため、指定は必須ではない。
    - 呼び出しはトランザクションのコミット後に行うこと
      （ジョブ側が別コネクションでコミット済みの行を読むため）。
    """
    if get_settings().job_runner == "cloud_tasks":
        await _enqueue_cloud_tasks(job_id, kind=kind)
    else:
        _enqueue_local(job_id)


def _enqueue_local(job_id: str) -> None:
    """local: 実行中のイベントループ上でディスパッチャを直接起動する。"""
    from app.jobs import registry

    task = asyncio.create_task(registry.dispatch_job(job_id))
    _local_tasks.add(task)
    task.add_done_callback(_local_tasks.discard)


async def _enqueue_cloud_tasks(job_id: str, *, kind: str | None = None) -> None:
    """cloud_tasks: {SELF_URL}/internal/jobs/run への HTTP POST タスクを投入する。

    リトライは Cloud Tasks のキュー設定（指数バックオフ）に任せる（§7.2）。
    クライアント生成・API 呼び出しは同期 SDK のため to_thread で逃がす。
    """
    settings = get_settings()

    # INTERNAL_JOBS_TOKEN 設定時は worker 側（internal_jobs.py）が検証するヘッダを付与（#16）
    headers = {"Content-Type": "application/json"}
    if settings.internal_jobs_token:
        headers["X-Internal-Jobs-Token"] = settings.internal_jobs_token

    # body は jobId が正。kind は指定時のみ同梱する参考情報（worker は DB の kind を使う）
    body: dict[str, str] = {"jobId": job_id}
    if kind is not None:
        body["kind"] = kind

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
                        "headers": headers,
                        "body": json.dumps(body).encode("utf-8"),
                    }
                },
            }
        )

    await asyncio.to_thread(_create_task)


async def drain_local_jobs() -> None:
    """local ランナーで実行中の全ジョブの完了を待つ（テスト・シャットダウン用）。"""
    while _local_tasks:
        await asyncio.gather(*list(_local_tasks), return_exceptions=True)
