"""意思決定トレース API（GET /api/tasks/{human_id}/trace, #25）。

『なぜAIはこう動いたか』を1枚で説明する: 成果物の版ごとに
「どのジョブが・どのルール（K-xx）を前提に・何トークン/$いくらで生成したか」を返す。
人の編集版（job_id なし）はジョブ由来フィールドが null/空 = FE は「あなたが編集」と表示。
"""

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import TraceResponse
from app.repo import artifacts as artifacts_repo
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["trace"])


@router.get("/tasks/{human_id}/trace")
async def get_trace(human_id: str) -> TraceResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        task_row = await tasks_repo.get_task_row(conn, human_id)
        if task_row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        entries = await artifacts_repo.list_trace_entries(conn, task_row)
    return TraceResponse(task_id=human_id, entries=entries)
