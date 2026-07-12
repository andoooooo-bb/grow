"""成果物 API（GET/POST /api/tasks/{human_id}/artifacts, §02.6）。

- GET: 全版を version 昇順で返す（#10 のレビュー画面が使用。末尾が最新）。
- POST: 人の編集を新版として保存し、artifact.created を publish する。
"""

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import ArtifactCreate, ArtifactResponse
from app.domain.models import Artifact
from app.events import ARTIFACT_CREATED, publish_event
from app.repo import tasks as tasks_repo
from app.repo.artifacts import create_artifact, list_artifacts

router = APIRouter(tags=["artifacts"])


@router.get("/tasks/{human_id}/artifacts")
async def get_artifacts(human_id: str) -> ArtifactResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        task_row = await tasks_repo.get_task_row(conn, human_id)
        if task_row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        artifacts = await list_artifacts(conn, task_row)
    return ArtifactResponse(task_id=human_id, artifacts=artifacts)


@router.post("/tasks/{human_id}/artifacts", status_code=201)
async def post_artifact(human_id: str, payload: ArtifactCreate) -> Artifact:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        task_row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
        if task_row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
        # 人の編集は job_id なしの新版として積む（AI版と同じ履歴に並ぶ）
        artifact = await create_artifact(conn, task_row, payload.content_md, job_id=None)
    publish_event(ARTIFACT_CREATED, artifact.model_dump(mode="json", by_alias=True))
    return artifact
