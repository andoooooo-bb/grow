"""#9 系テストの共通ヘルパ（DB 直接検証・SSE バス購読・ジョブ連鎖の消化）。"""

import asyncio
from typing import Any

import asyncpg
import httpx

from tests.conftest import TEST_DB_URL


async def db_connect() -> asyncpg.Connection:
    """grow_test への直接コネクション（api_client fixture が seed 済みであること）。"""
    return await asyncpg.connect(TEST_DB_URL, timeout=3)


def drain_events(queue: asyncio.Queue) -> list[dict[str, Any]]:
    """バス購読キューに溜まったイベントを全て取り出す。"""
    events: list[dict[str, Any]] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events


async def drain_jobs(api_client: httpx.AsyncClient, captured_jobs: list[str]) -> None:
    """捕捉済みジョブを先頭から順に worker で実行する（実行中に積まれた分も消化）。

    #23 以降、execute は review を、review（revise）は execute を enqueue するため、
    enqueue をフェイクした captured_jobs には連鎖分が追記されていく。
    すべて succeeded で完走することも同時に検証する。
    """
    ran = 0
    while ran < len(captured_jobs):
        res = await api_client.post(
            "/internal/jobs/run", json={"jobId": captured_jobs[ran]}
        )
        assert res.status_code == 200
        assert res.json() == {"status": "succeeded"}
        ran += 1
