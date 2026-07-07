"""AI 実作業 API（POST /api/tasks/{human_id}/assign-ai, §1.5 / §5.3 assignAI / §7.2）。

同期部分（step 1〜4 + ジョブ作成）だけをここで行い、実作業はジョブへ委ねる:
1. retrieval（§6.3 上限8件・confidence降順）
2. ai_work・progress 0 に更新し progress レーン末尾へ（遷移は state_machine で検証）
3. 着手コメント投稿（文言は Grow.dc.html assignAI 準拠）
4. 適用ルールの applied++ / last_applied_at / rule_applications 記録
5. ai_jobs 作成（queued）→ enqueue → 202 {jobId}
"""

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import AssignAiResponse, CommentCreate
from app.domain.models import Author, LaneKey, TaskStatus
from app.domain.state_machine import can_transition
from app.events import COMMENT_CREATED, TASK_UPDATED, publish_event
from app.jobs import queue as jobs_queue
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo

router = APIRouter(tags=["ai"])


def _start_comment_text(rule_rows: list) -> str:
    """着手コメント文言（Grow.dc.html assignAI を踏襲）。"""
    if not rule_rows:
        return "承知しました。着手します。"
    first = rule_rows[0]["text"]
    extra = f"「{first}」ほか計{len(rule_rows)}件" if len(rule_rows) > 1 else f"「{first}」"
    return f"承知しました。あなた／チームのルール{extra}を前提に着手します。"


@router.post("/tasks/{human_id}/assign-ai", status_code=202)
async def assign_ai(human_id: str) -> AssignAiResponse:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail=f"task not found: {human_id}")

        current_status = TaskStatus(row["status"])
        if not can_transition(current_status, TaskStatus.AI_WORK):
            # 不正遷移ならジョブは作らない（409）
            raise HTTPException(
                status_code=409,
                detail=f"invalid status transition: {current_status} -> ai_work",
            )

        # 1) retrieval（§6.3 / §00 #8: 上限8件・confidence降順・personal/team 両対象）
        rule_rows = await rules_repo.relevant_rules(conn, row)

        # 3) 着手コメント（先に挿入し、後続の Task DTO に commentCount を反映させる）
        comment = await comments_repo.create_comment(
            conn, row, CommentCreate(author=Author.AI, text=_start_comment_text(rule_rows))
        )

        # 4) 適用ルールの applied++ / last_applied_at / rule_applications（§6.3）
        await rules_repo.record_applications(conn, row, rule_rows)

        # 2) ai_work・progress 0・progress レーン末尾へ（§1.5 step2）
        task = await tasks_repo.apply_patch(
            conn,
            row,
            {"status": TaskStatus.AI_WORK, "progress": 0, "lane_key": LaneKey.PROGRESS},
        )

        # 5) ai_jobs 行作成（kind=execute, status=queued）
        job_row = await ai_jobs_repo.create_job(
            conn, row, applied_rule_ids=[r["id"] for r in rule_rows]
        )

    # SSE 配信と enqueue はコミット後（ジョブは別コネクションで行を読むため）
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    job_id = str(job_row["id"])
    await jobs_queue.enqueue_job(job_id)
    return AssignAiResponse(job_id=job_id)
