"""プロセス内イベントバス（SSE 配信基盤, §5.4）。

mutation を行う各エンドポイント（および後続 Wave の execute ジョブ等）が
`publish_event()` で配信し、GET /api/events の各接続（subscriber）が
自分専用の asyncio.Queue から受信する。

イベント形式:
    {"type": "task.updated" | "comment.created", "payload": <DTO の camelCase dict>}

payload には `model_dump(mode="json", by_alias=True)` した DTO を渡すこと。
"""

import asyncio
from typing import Any

# イベント種別（後続 Wave もこの定数を使う）
TASK_UPDATED = "task.updated"
COMMENT_CREATED = "comment.created"

Event = dict[str, Any]


class EventBus:
    """subscriber ごとに asyncio.Queue を持つ最小のプロセス内 pub/sub。

    Cloud Run 1インスタンス・単一プロセス前提の MVP 実装。
    publish は同期呼び出し（put_nowait）なのでルーターから直接呼べる。
    """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[Event]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    def subscribe(self) -> asyncio.Queue[Event]:
        """新しい subscriber キューを登録して返す。使い終えたら unsubscribe すること。"""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._queues.discard(queue)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """全 subscriber へイベントを配る（購読者がいなければ何もしない）。"""
        event: Event = {"type": event_type, "payload": payload}
        for queue in self._queues:
            queue.put_nowait(event)


# プロセス共有のシングルトン
bus = EventBus()


def publish_event(event_type: str, payload: dict[str, Any]) -> None:
    """公開 API: 全 subscriber へイベントを配信する。

    後続 Wave（AIジョブの進捗・完了通知等）もこの関数を使う。
    """
    bus.publish(event_type, payload)
