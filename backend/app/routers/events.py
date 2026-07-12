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
            # 接続確立を即座に1イベント流す。Cloud Run / GFE は本文の最初の1バイトが
            # 届くまでレスポンスヘッダをクライアントへ転送しないため、これが無いと
            # queue.get() でブロックしている間ストリームが確立せず EventSource が
            # onopen しない（本番で SSE が沈黙し AI 更新が画面に出ない原因だった）。
            # FE は既知の event 種別のみ addEventListener で購読するため "ready" は無視される。
            yield {"event": "ready", "data": "{}"}
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

    # ping=10: 10秒ごとにコメント行を送り、アイドル中も接続を温め続ける
    # （プロキシのアイドルタイムアウト対策 ＋ 途中経路のバッファを定期的に flush）。
    # headers: プロキシのバッファリングを明示的に無効化する。
    return EventSourceResponse(
        generator(),
        ping=10,
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
