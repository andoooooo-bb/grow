"""プロセス内イベントバス（SSE 配信基盤, §5.4）。

mutation を行う各エンドポイント（および後続 Wave の execute ジョブ等）が
`publish_event()` で配信し、GET /api/events の各接続（subscriber）が
自分専用の asyncio.Queue から受信する。

イベント形式:
    {"type": <下記のイベント種別>, "payload": <DTO の camelCase dict>}

payload には `model_dump(mode="json", by_alias=True)` した DTO を渡すこと。
"""

import asyncio
from typing import Any

# イベント種別（全 Wave の定数をここに集約する）
TASK_UPDATED = "task.updated"  # payload: Task DTO
COMMENT_CREATED = "comment.created"  # payload: Comment DTO
CHAT_MESSAGE_CREATED = "chat.message.created"  # payload: ChatMessage DTO（#11 壁打ち）
SUBTASK_PROPOSAL = "subtask.proposal"  # payload: SubtaskProposalEvent DTO（#11 分解候補）
ARTIFACT_CREATED = "artifact.created"  # payload: Artifact DTO（#9 成果物）
# #24 ライブ実況: 実行AIの生成テキスト増分（非永続）。
# payload: {"taskId": human_id, "delta": 増分テキスト, "seq": 1始まりの受信連番}
# seq=1 は新しいストリームの開始（FE はここで liveDraft をリセットして連結し直す）
ARTIFACT_DELTA = "artifact.delta"
RULE_CREATED = "rule.created"  # payload: Rule DTO（#13 蒸留候補の採用）
RULE_UPDATED = "rule.updated"  # payload: Rule DTO（#13 昇格・applied++ の同期）

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
