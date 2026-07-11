"""AI 実作業 API（POST /api/tasks/{human_id}/assign-ai, §1.5 / §5.3 assignAI / §7.2）。

同期部分（step 1〜4 + ジョブ作成）だけをここで行い、実作業はジョブへ委ねる:
1. retrieval（§6.3 上限8件・confidence降順）
2. ai_work・progress 0 に更新し progress レーン末尾へ（遷移は state_machine で検証）
3. 着手コメント投稿（文言は Grow.dc.html assignAI 準拠）
4. 適用ルールの applied++ / last_applied_at / rule_applications 記録
5. ai_jobs 作成（queued）→ enqueue → 202 {jobId}

POST /api/tasks/{human_id}/autopilot（#22 指揮者エージェント）は同型のもっと薄い版:
着手コメント（指揮者AI名義）＋ ai_jobs(kind='orchestrate') 作成 → enqueue → 202。
タスクの状態は変えない（次に何をするかは orchestrate ジョブが判断する）。
"""

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.domain.dto import AssignAiResponse, CommentCreate
from app.domain.models import (
    AgentRole,
    AiJobKind,
    Author,
    AutonomyLevel,
    LaneKey,
    TaskStatus,
)
from app.domain.state_machine import can_transition
from app.events import COMMENT_CREATED, RULE_UPDATED, TASK_UPDATED, publish_event
from app.jobs import queue as jobs_queue
from app.jobs.orchestrate import TAKEOVER_COMMENT
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


# #21 コスト上限（policy.costCapUsd）到達時の停止コメント（停止理由の明示）
COST_CAP_COMMENT_TEMPLATE = (
    "コスト上限 ${cap} に達したため停止しました。上限を変更するか、人が引き継いでください。"
)


def _format_usd(value: float) -> str:
    """コメント表示用の USD 表記（1.0 → "1" / 2.5 → "2.5"）。"""
    return f"{value:g}"


@router.post("/tasks/{human_id}/assign-ai", status_code=202)
async def assign_ai(human_id: str) -> AssignAiResponse:
    pool = await get_pool()
    capped = None  # コスト上限到達時の (comment, task, spent, cap)（#21）
    async with pool.acquire() as conn:
        async with conn.transaction():
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

            # 0) コスト上限チェック（#21）: enqueue 前に累計コストを集計し、
            #    上限到達なら停止コメント＋you_todo 戻し（既存の失敗ハンドオフと同型）で
            #    409 を返す。ジョブは作らない。
            policy = tasks_repo.policy_from_row(row)
            if policy.cost_cap_usd is not None:
                spent = await ai_jobs_repo.total_cost_usd(conn, row["id"])
                if spent >= policy.cost_cap_usd:
                    cap_text = COST_CAP_COMMENT_TEMPLATE.format(
                        cap=_format_usd(policy.cost_cap_usd)
                    )
                    cap_comment = await comments_repo.create_comment(
                        conn,
                        row,
                        CommentCreate(
                            author=Author.AI,
                            text=cap_text,
                            agent_role=AgentRole.EXECUTOR,
                        ),
                    )
                    fields = {}
                    if can_transition(current_status, TaskStatus.YOU_TODO):
                        # 人へハンドオフ（§7.2 の失敗戻しと同じ遷移。不可なら現状維持）
                        fields = {"status": TaskStatus.YOU_TODO, "progress": None}
                    cap_task = await tasks_repo.apply_patch(conn, row, fields)
                    capped = (cap_comment, cap_task, spent, policy.cost_cap_usd)

            if capped is None:
                # 1) retrieval（§6.3 / §00 #8: 上限8件・confidence降順・personal/team 両対象）
                rule_rows = await rules_repo.relevant_rules(conn, row)

                # 3) 着手コメント（先に挿入し、後続の Task DTO に commentCount を反映させる）
                # 着手〜完了は実行AIの担当（#19 役割バッジ）
                comment = await comments_repo.create_comment(
                    conn,
                    row,
                    CommentCreate(
                        author=Author.AI,
                        text=_start_comment_text(rule_rows),
                        agent_role=AgentRole.EXECUTOR,
                    ),
                )

                # 4) 適用ルールの applied++ / last_applied_at / rule_applications（§6.3）
                await rules_repo.record_applications(conn, row, rule_rows)
                # applied++ 後の最新値で Rule DTO を作る（FE の applied 表示同期用, #13）
                updated_rule_rows = await rules_repo.get_rules_by_uuids(
                    conn, [r["id"] for r in rule_rows]
                )
                applied_rules = [
                    await rules_repo.rule_dto_from_row(conn, r) for r in updated_rule_rows
                ]

                # 2) ai_work・progress 0・progress レーン末尾へ（§1.5 step2）
                task = await tasks_repo.apply_patch(
                    conn,
                    row,
                    {
                        "status": TaskStatus.AI_WORK,
                        "progress": 0,
                        "lane_key": LaneKey.PROGRESS,
                    },
                )

                # 5) ai_jobs 行作成（kind=execute, status=queued）
                job_row = await ai_jobs_repo.create_job(
                    conn, row, applied_rule_ids=[r["id"] for r in rule_rows]
                )

    if capped is not None:
        # 停止コメント・you_todo 戻しはコミット済み。SSE で理由を届けてから 409
        cap_comment, cap_task, spent, cap = capped
        publish_event(COMMENT_CREATED, cap_comment.model_dump(mode="json", by_alias=True))
        publish_event(TASK_UPDATED, cap_task.model_dump(mode="json", by_alias=True))
        raise HTTPException(
            status_code=409,
            detail=f"cost cap reached: spent {spent:.4f} >= cap {cap:g} USD",
        )

    # SSE 配信と enqueue はコミット後（ジョブは別コネクションで行を読むため）
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    # 適用ルールの applied 表示鮮度を FE と同期する（#13。適用件数ぶん配信）
    for rule in applied_rules:
        publish_event(RULE_UPDATED, rule.model_dump(mode="json", by_alias=True))
    job_id = str(job_row["id"])
    await jobs_queue.enqueue_job(job_id)
    return AssignAiResponse(job_id=job_id)


# ---- オートパイロット（#22 指揮者エージェント） -------------------------------------


@router.post("/tasks/{human_id}/autopilot", status_code=202)
async def autopilot(human_id: str) -> AssignAiResponse:
    """指揮者エージェントを起動する（assign-ai と同型: 404 / コスト上限 409 / 202）。

    ここではタスクの状態を変えない（次の一手は orchestrate ジョブが判断する）。
    - ai_work / done は指揮者が動かせる余地がないため 409
    - オートノミー L0（計画のみ, #21）はオートパイロット無効（FE もボタンを無効化）
    """
    pool = await get_pool()
    capped = None  # コスト上限到達時の (comment, task, spent, cap)（#21 と同型）
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
            if row is None:
                raise HTTPException(status_code=404, detail=f"task not found: {human_id}")

            current_status = TaskStatus(row["status"])
            if current_status in (TaskStatus.AI_WORK, TaskStatus.DONE):
                raise HTTPException(
                    status_code=409,
                    detail=f"autopilot not available for status: {current_status}",
                )
            if AutonomyLevel(row["autonomy"]) is AutonomyLevel.L0:
                raise HTTPException(
                    status_code=409,
                    detail="autonomy L0 is plan-only; autopilot is disabled",
                )

            # 0) コスト上限チェック（#21。assign-ai ステップ0と同型。名義は指揮者AI）
            policy = tasks_repo.policy_from_row(row)
            if policy.cost_cap_usd is not None:
                spent = await ai_jobs_repo.total_cost_usd(conn, row["id"])
                if spent >= policy.cost_cap_usd:
                    cap_text = COST_CAP_COMMENT_TEMPLATE.format(
                        cap=_format_usd(policy.cost_cap_usd)
                    )
                    cap_comment = await comments_repo.create_comment(
                        conn,
                        row,
                        CommentCreate(
                            author=Author.AI,
                            text=cap_text,
                            agent_role=AgentRole.CONDUCTOR,
                        ),
                    )
                    fields = {}
                    if can_transition(current_status, TaskStatus.YOU_TODO):
                        fields = {"status": TaskStatus.YOU_TODO, "progress": None}
                    cap_task = await tasks_repo.apply_patch(conn, row, fields)
                    capped = (cap_comment, cap_task, spent, policy.cost_cap_usd)

            if capped is None:
                # 着手コメント（指揮者AI名義 #19。orchestrate のセッション境界の目印にもなる）
                comment = await comments_repo.create_comment(
                    conn,
                    row,
                    CommentCreate(
                        author=Author.AI,
                        text=TAKEOVER_COMMENT,
                        agent_role=AgentRole.CONDUCTOR,
                    ),
                )
                # 状態は変えないが commentCount 同期のため Task DTO を配信する
                task = await tasks_repo.task_from_row(conn, row)
                job_row = await ai_jobs_repo.create_job(
                    conn, row, kind=AiJobKind.ORCHESTRATE
                )

    if capped is not None:
        cap_comment, cap_task, spent, cap = capped
        publish_event(COMMENT_CREATED, cap_comment.model_dump(mode="json", by_alias=True))
        publish_event(TASK_UPDATED, cap_task.model_dump(mode="json", by_alias=True))
        raise HTTPException(
            status_code=409,
            detail=f"cost cap reached: spent {spent:.4f} >= cap {cap:g} USD",
        )

    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    job_id = str(job_row["id"])
    await jobs_queue.enqueue_job(job_id, kind=AiJobKind.ORCHESTRATE.value)
    return AssignAiResponse(job_id=job_id)
