"""タスク API（POST /api/tasks, PATCH /api/tasks/{human_id}）。

- ステータス遷移は domain/state_machine.can_transition で検証（不正は 409）。
- progress は §5.6 不変条件（ai_work のときのみ非null）。手動指定の違反は 422、
  ai_work 以外への遷移時は自動で null 化する。
- 変更後は updated_at を更新し、更新後の Task DTO を返してイベントバスへ publish する。
- POST /tasks（直接呼び出し）成功後は受付エージェント（#27 kind='intake'）を enqueue し、
  execute | hearing | breakdown のルートをAIが自分で判定する。confirmBreakdown 経由の
  サブタスク（parentId あり。repo 直呼びで本エンドポイントを通らない）と seed 済み
  タスク（SQL 投入）は対象外。
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import TaskCreate, TaskPatch
from app.domain.models import AiJobKind, Task, TaskStatus
from app.domain.state_machine import can_transition, validate_progress_invariant
from app.events import TASK_UPDATED, publish_event
from app.guard import guard_ai_action
from app.jobs import queue as jobs_queue
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["tasks"])


@router.post("/tasks", status_code=201)
async def create_task(payload: TaskCreate) -> Task:
    pool = await get_pool()
    intake_job_id: str | None = None
    async with pool.acquire() as conn:
        # #security: 直接作成カードは受付エージェント（intake, Gemini）が走るためガードする
        await guard_ai_action(conn)
        async with conn.transaction():
            try:
                task = await tasks_repo.create_task(conn, payload)
            except tasks_repo.InvalidParentError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if payload.parent_id is None:
                # #27 受付エージェント: 直接作成されたカードのみ判定する
                # （親から生成されるサブタスクには走らせない）
                row = await tasks_repo.get_task_row(conn, task.id)
                job_row = await ai_jobs_repo.create_job(conn, row, kind=AiJobKind.INTAKE)
                intake_job_id = str(job_row["id"])
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    if intake_job_id is not None:
        # enqueue はコミット後（ジョブ側が別コネクションで行を読むため §7.2）
        await jobs_queue.enqueue_job(intake_job_id, kind=AiJobKind.INTAKE.value)
    return task


@router.patch("/tasks/{human_id}")
async def patch_task(human_id: str, payload: TaskPatch) -> Task:
    # exclude_unset 相当: 明示的に送られたフィールドだけを扱う
    # （progress は "null で明示クリア" と "未指定" を区別する必要がある）
    fields: dict[str, Any] = {name: getattr(payload, name) for name in payload.model_fields_set}

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")

        current_status = TaskStatus(row["status"])
        new_status = fields["status"] if fields.get("status") is not None else current_status
        if not can_transition(current_status, new_status):
            raise HTTPException(
                status_code=409,
                detail=f"invalid status transition: {current_status} -> {new_status}",
            )

        if "progress" in fields:
            if not validate_progress_invariant(new_status, fields["progress"]):
                raise HTTPException(
                    status_code=422,
                    detail="progress は status=ai_work のときのみ 0..100 を指定できます",
                )
        elif fields.get("status") is not None and new_status is not TaskStatus.AI_WORK:
            fields["progress"] = None  # §5.6: ai_work 以外へ遷移したら自動 null 化

        try:
            # PATCH はボード操作＝人の操作（#28: 差し戻し遷移なら autonomy 自動降格）
            task = await tasks_repo.apply_patch(conn, row, fields, actor="human")
        except tasks_repo.InvalidParentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    return task
