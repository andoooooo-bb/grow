"""壁打ちチャット（chat_messages）のリポジトリ層（§2.2 ChatMessage, #11）。

Comment（カードのアクティビティ）とは別スレッド。API 境界の taskId は human_id。
"""

import asyncpg

from app.domain.models import Author, ChatMessage


def _row_to_message(row: asyncpg.Record, task_human_id: str) -> ChatMessage:
    return ChatMessage(
        id=str(row["id"]),
        task_id=task_human_id,
        author=row["author"],
        text=row["text"],
        created_at=row["created_at"].isoformat(),
    )


async def create_chat_message(
    conn: asyncpg.Connection, task_row: asyncpg.Record, *, author: Author, text: str
) -> ChatMessage:
    """メッセージを保存する（task_row は tasks の行。human_id/UUID 両方を持つ）。"""
    row = await conn.fetchrow(
        "insert into chat_messages (task_id, author, text) values ($1, $2, $3) returning *",
        task_row["id"],
        author.value,
        text,
    )
    return _row_to_message(row, task_row["human_id"])


async def list_chat_messages(
    conn: asyncpg.Connection, task_row: asyncpg.Record
) -> list[ChatMessage]:
    """タスクの壁打ちメッセージを作成時刻の昇順で返す。"""
    rows = await conn.fetch(
        "select * from chat_messages where task_id = $1 order by created_at, id",
        task_row["id"],
    )
    return [_row_to_message(row, task_row["human_id"]) for row in rows]
