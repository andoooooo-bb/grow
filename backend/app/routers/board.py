"""GET /api/board — ボード全体を §2.3 の正規化形で返す。"""

from fastapi import APIRouter

from app.db import get_pool
from app.domain.dto import BoardResponse
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["board"])


@router.get("/board")
async def get_board() -> BoardResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await tasks_repo.fetch_board(conn)
