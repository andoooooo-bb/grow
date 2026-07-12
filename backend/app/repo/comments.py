"""コメント（カードのアクティビティ）のリポジトリ層。

API 境界の taskId は human_id、コメントの id は UUID 文字列をそのまま使う。
"""

from uuid import UUID

import asyncpg

from app.domain.dto import CommentCreate
from app.domain.models import Comment


class InvalidAuthorUserError(Exception):
    """author_user_id が UUID として不正。"""


def _row_to_comment(row: asyncpg.Record, task_human_id: str) -> Comment:
    return Comment(
        id=str(row["id"]),
        task_id=task_human_id,
        author=row["author"],
        author_user_id=(
            str(row["author_user_id"]) if row["author_user_id"] is not None else None
        ),
        text=row["text"],
        agent_role=row["agent_role"],
        created_at=row["created_at"].isoformat(),
    )


async def create_comment(
    conn: asyncpg.Connection, task_row: asyncpg.Record, data: CommentCreate
) -> Comment:
    """コメントを作成する（task_row は tasks の行。human_id/UUID 両方を持つ）。"""
    author_user_uuid = None
    if data.author_user_id is not None:
        try:
            author_user_uuid = UUID(data.author_user_id)
        except ValueError as exc:
            raise InvalidAuthorUserError(
                f"invalid author_user_id: {data.author_user_id}"
            ) from exc
    # created_at は clock_timestamp() で明示する: 既定の now() はトランザクション開始
    # 時刻のため、同一トランザクションで複数コメントを投稿するフロー（#21 L3 の
    # 完了→自動承認、#23 レビュー承認→完了ハンドオフ）で時刻が同値になり、
    # created_at 順の表示・検証が不定になるのを防ぐ。
    row = await conn.fetchrow(
        "insert into comments (task_id, author, author_user_id, text, agent_role, created_at) "
        "values ($1, $2, $3, $4, $5, clock_timestamp()) returning *",
        task_row["id"],
        data.author.value,
        author_user_uuid,
        data.text,
        data.agent_role.value if data.agent_role is not None else None,
    )
    return _row_to_comment(row, task_row["human_id"])


async def list_comments(
    conn: asyncpg.Connection, task_row: asyncpg.Record
) -> list[Comment]:
    """タスクのコメントを作成時刻の昇順で返す。"""
    rows = await conn.fetch(
        "select * from comments where task_id = $1 order by created_at, id",
        task_row["id"],
    )
    return [_row_to_comment(row, task_row["human_id"]) for row in rows]
