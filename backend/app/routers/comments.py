"""コメント API（POST/GET /api/tasks/{human_id}/comments）。"""

import asyncpg
from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import CommentCreate
from app.domain.models import Comment
from app.events import COMMENT_CREATED, TASK_UPDATED, publish_event
from app.repo import comments as comments_repo
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["comments"])


@router.post("/tasks/{human_id}/comments", status_code=201)
async def create_comment(human_id: str, payload: CommentCreate) -> Comment:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        task_row = await tasks_repo.get_task_row(conn, human_id)
        if task_row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        try:
            comment = await comments_repo.create_comment(conn, task_row, payload)
        except comments_repo.InvalidAuthorUserError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise HTTPException(
                status_code=422, detail=f"author_user_id が存在しません: {payload.author_user_id}"
            ) from exc
        # commentCount を含む最新の Task を導出（挿入後なので件数は加算済み）
        task = await tasks_repo.task_from_row(conn, task_row)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    # コメント件数の同期（#7）: カードの commentCount を task.updated でも配信する
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    return comment


@router.get("/tasks/{human_id}/comments")
async def list_comments(human_id: str) -> list[Comment]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        task_row = await tasks_repo.get_task_row(conn, human_id)
        if task_row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        return await comments_repo.list_comments(conn, task_row)
