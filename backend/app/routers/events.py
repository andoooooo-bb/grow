"""GET /api/events — SSE でタスク更新・コメント追加をクライアントへ push する（§5.4）。"""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from app.events import bus

router = APIRouter(tags=["events"])


@router.get("/events")
async def stream_events(
    max_events: int | None = Query(default=None, alias="maxEvents", ge=1),
) -> EventSourceResponse:
    """イベントストリーム。

    - SSE の event 名はイベント種別（task.updated / comment.created）、
      data は {"type", "payload"} の JSON（payload は DTO の camelCase）。
    - max_events はテスト・デバッグ用（N 件受信後に切断）。通常は指定しない。
    """
    queue = bus.subscribe()

    async def generator() -> AsyncIterator[dict[str, Any]]:
        try:
            sent = 0
            while max_events is None or sent < max_events:
                event = await queue.get()
                yield {
                    "event": event["type"],
                    "data": json.dumps(event, ensure_ascii=False),
                }
                sent += 1
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(generator())
