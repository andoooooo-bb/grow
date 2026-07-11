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

POST /api/tasks/{human_id}/reject（#23 人の構造化差し戻し）:
理由を human コメント（【差し戻し理由】…）で保存 → 前回適用ルールとの矛盾を
check_rule_conflicts で判定して confidence を1段降格 → assign-ai と同じ準備
（ステップ1〜5 を _start_execution で共用）で execute を再 enqueue → 202。
"""

from fastapi import APIRouter, HTTPException

from app.ai import get_provider
from app.ai.provider import REJECT_REASON_PREFIX
from app.db import get_pool
from app.domain.dto import AssignAiResponse, CommentCreate, RejectRequest
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


# #23 差し戻し理由と矛盾したルールの確度降格を知らせるコメント（学習AI名義）
RULE_DOWNGRADE_COMMENT_TEMPLATE = (
    "差し戻し理由がルール「{rule}」と矛盾する可能性があるため、"
    "確度を下げました（{before}→{after}）。今後の適用優先度が下がります。"
)


async def _start_execution(conn, row):
    """assign-ai ステップ1〜5（retrieval → 着手コメント → applied++ → ai_work → ジョブ作成）。

    reject（#23 差し戻し再実行）と共用する。呼び出し側のトランザクション内で、
    ai_work への遷移検証を済ませた行（for update）を渡すこと。
    返り値: (着手コメント, 更新後 Task, applied 反映済み Rule DTO 列, ジョブ行)。
    """
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
    return comment, task, applied_rules, job_row


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
                # ステップ1〜5（#23 reject と共用の _start_execution に抽出）
                comment, task, applied_rules, job_row = await _start_execution(conn, row)

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


# ---- 人の構造化差し戻し（#23） -------------------------------------------------------


@router.post("/tasks/{human_id}/reject", status_code=202)
async def reject(human_id: str, payload: RejectRequest) -> AssignAiResponse:
    """理由付きの差し戻し（you_review / reviewing → ai_work）→ execute 再実行。

    1. 理由を「【差し戻し理由】{reason}」の human コメントで保存する
       （再実行ジョブには provider.execute の「# 差し戻し理由（最優先で対処）」節として注入される）
    2. 前回 execute の適用ルールと理由の矛盾を check_rule_conflicts で判定し、
       該当ルールの confidence を1段降格（RULE_UPDATED 配信＋学習AI名義のコメント）
    3. assign-ai と同じ準備（_start_execution）で execute を再 enqueue → 202 {jobId}
    """
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason must not be blank")

    pool = await get_pool()

    # 1) 遷移検証 ＋ 理由コメントの保存（先にコミットして SSE で見せる）
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
            if row is None:
                raise HTTPException(status_code=404, detail=f"task not found: {human_id}")
            current_status = TaskStatus(row["status"])
            if current_status not in (
                TaskStatus.YOU_REVIEW,
                TaskStatus.REVIEWING,
            ) or not can_transition(current_status, TaskStatus.AI_WORK):
                raise HTTPException(
                    status_code=409,
                    detail=f"invalid status for reject: {current_status} -> ai_work",
                )
            reject_comment = await comments_repo.create_comment(
                conn,
                row,
                CommentCreate(
                    author=Author.HUMAN, text=f"{REJECT_REASON_PREFIX}{reason}"
                ),
            )
            reject_task = await tasks_repo.task_from_row(conn, row)  # commentCount 同期
            # 矛盾判定の対象 = 前回 execute ジョブが注入したルール（#23）
            prev_rule_ids = await conn.fetchval(
                "select applied_rule_ids from ai_jobs "
                "where task_id = $1 and kind = 'execute' "
                "order by created_at desc limit 1",
                row["id"],
            )
            prev_rule_rows = await rules_repo.get_rules_by_uuids(
                conn, list(prev_rule_ids or [])
            )
    publish_event(COMMENT_CREATED, reject_comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, reject_task.model_dump(mode="json", by_alias=True))

    # 2) 矛盾検出（provider 呼び出しはトランザクション外）→ confidence 1段降格
    if prev_rule_rows:
        conflict = await get_provider().check_rule_conflicts(
            reason, [rules_repo.rule_prompt_dict(r) for r in prev_rule_rows]
        )
        if conflict.rule_ids:
            await _downgrade_conflicting_rules(human_id, conflict.rule_ids)

    # 3) 再実行（assign-ai ステップ1〜5 を共用。降格を反映した retrieval で組み直す）
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
            if row is None or not can_transition(
                TaskStatus(row["status"]), TaskStatus.AI_WORK
            ):
                # 並行操作で状態が変わった（極めて稀）。理由コメントは残る
                raise HTTPException(
                    status_code=409, detail="task state changed during reject"
                )
            comment, task, applied_rules, job_row = await _start_execution(conn, row)

    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    for rule in applied_rules:
        publish_event(RULE_UPDATED, rule.model_dump(mode="json", by_alias=True))
    job_id = str(job_row["id"])
    await jobs_queue.enqueue_job(job_id)
    return AssignAiResponse(job_id=job_id)


async def _downgrade_conflicting_rules(human_id: str, rule_human_ids: list[str]) -> None:
    """矛盾ルールの confidence を1段降格し、RULE_UPDATED と学習AIコメントを配信する。

    降格（high→med→low）は repo/rules.downgrade_confidence（夜間ナレッジCI #26 と共用）。
    既に low のルールは何もしない。
    """
    pool = await get_pool()
    downgraded = []  # 更新後 Rule DTO（RULE_UPDATED の payload）
    comments = []
    async with pool.acquire() as conn, conn.transaction():
        task_row = await tasks_repo.get_task_row(conn, human_id, for_update=True)
        if task_row is None:
            return
        for rule_human_id in rule_human_ids:
            rule_row = await rules_repo.get_rule_by_human_id(
                conn, rule_human_id, for_update=True
            )
            if rule_row is None:
                continue
            before = rule_row["confidence"]
            updated = await rules_repo.downgrade_confidence(conn, rule_row)
            if updated is None:
                continue  # 既に low（下限）
            downgraded.append(updated)
            comments.append(
                await comments_repo.create_comment(
                    conn,
                    task_row,
                    CommentCreate(
                        author=Author.AI,
                        text=RULE_DOWNGRADE_COMMENT_TEMPLATE.format(
                            rule=rule_row["text"],
                            before=before,
                            after=updated.confidence.value,
                        ),
                        agent_role=AgentRole.DISTILLER,  # 確度の管理は学習AIの名義（#19）
                    ),
                )
            )
        task = await tasks_repo.task_from_row(conn, task_row)  # commentCount 同期
    for rule in downgraded:
        publish_event(RULE_UPDATED, rule.model_dump(mode="json", by_alias=True))
    for comment in comments:
        publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    if comments:
        publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


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
