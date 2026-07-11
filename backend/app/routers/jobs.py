"""AIジョブ参照 API（GET /api/tasks/{human_id}/jobs, #19 リレー・タイムライン）。

ai_jobs を created_at 昇順で返し、FE のドロワーが
「計画AI → 実行AI → あなた」のリレー・タイムラインとして描画する。
kind → 役割名の対応（breakdown=計画AI / execute=実行AI / distill=学習AI）は
FE（AgentTimeline.tsx）が持つ。後続エージェント（#22 指揮者 / #23 レビュー）は
新しい kind でジョブを作るだけでタイムラインに乗る。
"""

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import JobsResponse
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["jobs"])


@router.get("/tasks/{human_id}/jobs")
async def get_jobs(human_id: str) -> JobsResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        task_row = await tasks_repo.get_task_row(conn, human_id)
        if task_row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        jobs = await ai_jobs_repo.list_jobs(conn, task_row)
    return JobsResponse(task_id=human_id, jobs=jobs)
