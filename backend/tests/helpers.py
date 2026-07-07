"""#9 系テストの共通ヘルパ（DB 直接検証・SSE バス購読）。"""

import asyncio
from typing import Any

import asyncpg

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
