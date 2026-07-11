"""学習・コストダッシュボード API（GET /api/stats, #25）。

『ルール適用の増加とともに差し戻しが減り、AI完了が増える』学習曲線と累計コストを
1リクエストで返す。FE は KnowledgeOverlay のスタットタイル＋スパークラインが読む。
"""

from fastapi import APIRouter

from app.db import get_pool
from app.domain.dto import StatsResponse
from app.repo import stats as stats_repo

router = APIRouter(tags=["stats"])


@router.get("/stats")
async def get_stats() -> StatsResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await stats_repo.fetch_stats(conn)
