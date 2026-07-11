"""指揮者エージェント（オートパイロット）ジョブ本体（#22 / §1.2 / §7.2）。

「次に何をすべきか」を AiProvider.decide_next_action で AI 自身が判断し、
既存の役割エージェント（計画=chat/分解・実行=execute）のジョブを連鎖させる上位層。
人がボタンを押すたびに 1 ステップ進む現行 UX を、「1クリックでエージェントチームが
完了までリレーする」体験に転換する（審査基準1: AIエージェントが価値の中心）。

1回の orchestrate ジョブ = 1判断ステップ:

    現況集約（status/autonomy/policy/コメント/成果物/子タスク）
    → decide_next_action → 判断理由コメント（指揮者AI名義 #19）
    → アクション実行:
       - hearing:       壁打ちの初期質問を自発投稿（routers/chat.py startChat と同ロジック）
       - breakdown:     propose_subtasks を生成し subtask.proposal を SSE 配信。
                        ボードへの反映は人の承認のまま（§1.6 の暴走防止を維持）
       - execute:       assign-ai と同じ準備（retrieval・ai_work 遷移・applied++）の上で
                        既存 execute ジョブを起動し、完了後に続行判定
       - handoff_human: 人のボール（you_review / you_todo）へ遷移＋バトンコメント
       - done:          L3 のみ自動完了。それ以外は you_review で人の承認を待つ

続行判定（execute 完了後）はオートノミー（#21）で分岐する:
    L0 = execute 自体が plan_only → you_todo で必ず停止（そもそも autopilot は 409）
    L1 = you_review で必ず停止（下書きまで）
    L2 = you_review 到達後も自分を再 enqueue し、指揮者が次判断を続ける
         （プラン承認ゲートは #31 の将来像。現段階は承認待ちをスキップ）
    L3 = 完了まで全自動（execute が done まで連鎖 → 次判断で終了宣言）

暴走防止（毎ループ実施）:
    - 1 autopilot セッション（指揮者引き受け〜停止）の実行回数上限 MAX_AUTOPILOT_STEPS
    - コスト上限 policy.costCapUsd（total_cost_usd の累計で判定, #21）

リトライ設計: 判断は軽量（Flash / mock）なのでジョブ内リトライはせず、失敗は
ai_jobs=failed ＋ 指揮者名義の中断コメントで人へハンドオフする（§7.2 と同型）。
cloud_tasks 経由では handoff_on_failure=False の間（再試行が残っている間）は
ハンドオフせず 5xx 再試行に任せる。
"""

import logging
from typing import Any

import asyncpg

from app.ai import get_provider
from app.ai.provider import NextActionResult, TokenUsage
from app.costs import calc_cost_usd
from app.db import get_pool
from app.domain.dto import CommentCreate, SubtaskProposal, SubtaskProposalEvent
from app.domain.models import (
    STATUS_META,
    AgentRole,
    AiJobKind,
    AiJobStatus,
    Author,
    AutonomyLevel,
    LaneKey,
    Owner,
    TaskStatus,
)
from app.domain.state_machine import can_transition
from app.events import (
    CHAT_MESSAGE_CREATED,
    COMMENT_CREATED,
    RULE_UPDATED,
    SUBTASK_PROPOSAL,
    TASK_UPDATED,
    publish_event,
)
from app.jobs import queue as jobs_queue
from app.jobs.execute import JobNotFoundError, run_execute_job
from app.jobs.review import run_review_job
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import chat as chat_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo

logger = logging.getLogger(__name__)

# 1 autopilot セッション（指揮者引き受け〜停止）での orchestrate 実行回数上限（無限ループ防止）
MAX_AUTOPILOT_STEPS = 5

# --- コメント文言（役割リレーの見える化 #19。指揮者AI名義） ---

# 着手（routers/ai.py autopilot が投稿。orchestrate はセッション境界の目印にも使う）
TAKEOVER_COMMENT = "指揮者AIがタスクを引き受けました。状況を判断して進めます。"
# 判断理由（毎ステップ必ず残す。（理由: …）付き）
REASON_COMMENT_TEMPLATE = "次のアクションを「{label}」と判断しました（理由: {reason}）"
ACTION_LABELS: dict[str, str] = {
    "hearing": "ヒアリング",
    "breakdown": "分解提案",
    "execute": "実行",
    "review": "セルフレビュー",  # レビューAIへのリレー（#23）
    "handoff_human": "人へのハンドオフ",
    "done": "完了",
}
# breakdown: 反映は人の承認のまま（§1.6 暴走防止）
BREAKDOWN_BATON_COMMENT = (
    "分解候補を提示しました。内容を確認のうえ、ボードへの反映を承認してください。"
)
# handoff_human: 人のボールへ
HANDOFF_COMMENT = "ここからは人の判断が必要です。内容を確認して進めてください。"
# done: 終了宣言（既に完了）/ L3 自動承認 / L3 以外は人の承認待ち
DONE_CLOSING_COMMENT = "タスクは完了しています。オートパイロットを終了します。"
DONE_AUTO_APPROVE_COMMENT = (
    "ポリシーL3により自動承認で完了にしました。内容は事後確認できます。"
)
DONE_WAIT_APPROVAL_COMMENT = (
    "完了と判断しましたが、最終承認はあなたにお任せします。レビューをお願いします。"
)
# 暴走防止の停止（理由の明示）
STEP_LIMIT_COMMENT = (
    "オートパイロットの実行回数が上限に達したため人にお返しします。"
    "状況を確認のうえ、必要なら再度お任せください。"
)
COST_CAP_COMMENT_TEMPLATE = (
    "コスト上限 ${cap} に達したため停止しました。上限を変更するか、人が引き継いでください。"
)
# execute/review 連鎖の失敗（各エージェント自身の失敗コメント・人戻しはジョブ側が行う）
EXECUTE_FAILED_COMMENT = "実行AIの作業が失敗したため、オートパイロットを停止します。"
# orchestrate 自体の失敗（判断不能）
FAILURE_COMMENT_TEMPLATE = (
    "オートパイロットが中断しました。人が引き継いでください。（理由: {reason}）"
)

_ZERO_USAGE = TokenUsage(input_tokens=0, output_tokens=0)


def _format_usd(value: float) -> str:
    """コメント表示用の USD 表記（1.0 → "1" / 2.5 → "2.5"。routers/ai.py と同じ）。"""
    return f"{value:g}"


async def run_orchestrate_job_row(
    job_row: asyncpg.Record,
    *,
    max_retries: int | None = None,
    handoff_on_failure: bool = True,
) -> bool:
    """kind='orchestrate' の登録ハンドラ（app/jobs/registry.py の統一シグネチャ, #18）。"""
    return await run_orchestrate_job(
        str(job_row["id"]), max_retries=max_retries, handoff_on_failure=handoff_on_failure
    )


async def run_orchestrate_job(
    job_id: str,
    *,
    max_retries: int | None = None,  # 判断は軽量（Flash/mock）のためジョブ内リトライなし
    handoff_on_failure: bool = True,
) -> bool:
    """orchestrate ジョブを1判断ステップ実行する（成功 True / 失敗 False）。"""
    del max_retries  # 統一シグネチャ（#18）の互換のため受け取るのみ
    try:
        await _orchestrate_attempt(job_id)
        return True
    except JobNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001 — 失敗は人へのハンドオフに集約する（§7.2）
        logger.warning("orchestrate job %s failed: %s", job_id, exc)
        if handoff_on_failure:
            await _handle_failure(job_id, exc)
        return False


# ---- 1判断ステップ ------------------------------------------------------------------


async def _orchestrate_attempt(job_id: str) -> None:
    pool = await get_pool()

    # 0) ジョブと対象タスクをロードして running へ ＋ 暴走防止チェックの材料集め
    async with pool.acquire() as conn:
        job_row = await ai_jobs_repo.get_job_row(conn, job_id)
        if job_row is None:
            raise JobNotFoundError(f"ai_job not found: {job_id}")
        if job_row["status"] in (AiJobStatus.SUCCEEDED, AiJobStatus.FAILED):
            return  # 二重配信（Cloud Tasks の at-least-once）への冪等ガード
        task_row = await conn.fetchrow(
            "select * from tasks where id = $1", job_row["task_id"]
        )
        if task_row is None:  # ai_jobs.task_id は FK なので通常は起きない
            raise JobNotFoundError(f"task not found for job: {job_id}")
        await ai_jobs_repo.mark_running(conn, job_id)
        step_count = await _session_step_count(conn, task_row)
        policy = tasks_repo.policy_from_row(task_row)
        spent = await ai_jobs_repo.total_cost_usd(conn, task_row["id"])

    # 1) 暴走防止（毎ループ実施）: 実行回数上限 → コスト上限（#21）
    if step_count > MAX_AUTOPILOT_STEPS:
        await _stop_with_comment(job_id, task_row, STEP_LIMIT_COMMENT)
        return
    if policy.cost_cap_usd is not None and spent >= policy.cost_cap_usd:
        await _stop_with_comment(
            job_id,
            task_row,
            COST_CAP_COMMENT_TEMPLATE.format(cap=_format_usd(policy.cost_cap_usd)),
        )
        return

    # 2) 現況の集約（status/autonomy/コメント履歴/壁打ち/成果物・レビュー有無/子タスク）
    #    → 次の一手
    async with pool.acquire() as conn:
        history = await comments_repo.list_comments(conn, task_row)
        chat_messages = await chat_repo.list_chat_messages(conn, task_row)
        artifact_count = await conn.fetchval(
            "select count(*) from artifacts where task_id = $1", task_row["id"]
        )
        review_count = await conn.fetchval(
            "select count(*) from ai_jobs "
            "where task_id = $1 and kind = $2 and status = 'succeeded'",
            task_row["id"],
            AiJobKind.REVIEW.value,
        )
        child_rows = await conn.fetch(
            "select status from tasks where parent_id = $1 order by created_at",
            task_row["id"],
        )
        rule_rows = await rules_repo.relevant_rules(conn, task_row)

    rule_dicts = [rules_repo.rule_prompt_dict(r) for r in rule_rows]
    chat_dicts = [{"who": m.author.value, "text": m.text} for m in chat_messages]
    task_ctx = {
        **_task_prompt_dict(task_row),
        # --- #22/#23 指揮者の判断材料 ---
        "status": task_row["status"],
        "autonomy": task_row["autonomy"],
        "hasChat": bool(chat_messages),
        "hasArtifact": artifact_count > 0,
        "hasReview": review_count > 0,  # セルフレビュー済みか（#23）
        "childStatuses": [r["status"] for r in child_rows],
    }
    decision = await get_provider().decide_next_action(
        task_ctx,
        [{"who": c.author.value, "text": c.text} for c in history],
        rule_dicts,
    )

    # 3) 判断理由コメント（毎ステップ・指揮者AI名義 #19）→ SSE
    await _post_conductor_comment(
        task_row,
        REASON_COMMENT_TEMPLATE.format(
            label=ACTION_LABELS[decision.action], reason=decision.reason
        ),
    )

    # 4) アクション実行（各アクションが ai_jobs の確定と続行判定まで行う）
    if decision.action == "hearing":
        await _act_hearing(job_id, task_row, chat_dicts, rule_dicts, decision)
    elif decision.action == "breakdown":
        await _act_breakdown(job_id, task_row, chat_dicts, rule_dicts, decision)
    elif decision.action == "execute":
        await _act_execute(job_id, task_row, decision)
    elif decision.action == "review":
        await _act_review(job_id, task_row, decision)
    elif decision.action == "handoff_human":
        await _act_handoff_human(job_id, task_row, decision)
    else:  # done
        await _act_done(job_id, task_row, decision)


# ---- アクション: hearing（壁打ちの初期質問を自発投稿） -------------------------------


async def _act_hearing(
    job_id: str,
    task_row: asyncpg.Record,
    chat_dicts: list[dict],
    rule_dicts: list[dict],
    decision: NextActionResult,
) -> None:
    """routers/chat.py startChat と同ロジック: AI の確認質問を chat に投入し spec へ。

    以降は人の回答待ち（人のボール）なのでオートパイロットはここで停止する。
    """
    reply = await get_provider().chat_reply(_task_prompt_dict(task_row), chat_dicts, rule_dicts)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        message = await chat_repo.create_chat_message(
            conn, row, author=Author.AI, text=reply.text
        )
        task = None
        current = TaskStatus(row["status"])
        if current is not TaskStatus.SPEC and can_transition(current, TaskStatus.SPEC):
            task = await tasks_repo.apply_patch(conn, row, {"status": TaskStatus.SPEC})
        await _mark_succeeded(conn, job_id, decision.usage, reply.usage)
    publish_event(CHAT_MESSAGE_CREATED, message.model_dump(mode="json", by_alias=True))
    if task is not None:
        publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- アクション: breakdown（分解候補の提示。反映は人の承認 §1.6） --------------------


async def _act_breakdown(
    job_id: str,
    task_row: asyncpg.Record,
    chat_dicts: list[dict],
    rule_dicts: list[dict],
    decision: NextActionResult,
) -> None:
    """propose_subtasks を生成し subtask.proposal を配信する（サーバ非永続, #11 と同型）。

    ボードへの反映（confirmBreakdown）は人の承認のまま = オートパイロットはここで
    停止して人にバトンを渡す（§1.6 の暴走防止を維持）。
    """
    proposal = await get_provider().propose_subtasks(
        _task_prompt_dict(task_row), chat_dicts, rule_dicts
    )
    event = SubtaskProposalEvent(
        task_id=task_row["human_id"],
        subtasks=[
            SubtaskProposal(title=s.title, owner=Owner(s.owner), rationale=s.rationale)
            for s in proposal.subtasks
        ],
    )
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI,
                text=BREAKDOWN_BATON_COMMENT,
                agent_role=AgentRole.CONDUCTOR,
            ),
        )
        task = await tasks_repo.apply_patch(conn, row, {})  # commentCount 同期用
        await _mark_succeeded(conn, job_id, decision.usage, proposal.usage)
    publish_event(SUBTASK_PROPOSAL, event.model_dump(mode="json", by_alias=True))
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- アクション: execute（実行AIへリレー → 完了後に続行判定） ------------------------


async def _act_execute(
    job_id: str, task_row: asyncpg.Record, decision: NextActionResult
) -> None:
    """assign-ai と同じ準備の上で既存 execute ジョブを起動する（外部挙動は不変, #10/#21）。

    #23: execute は成果物保存後に review ジョブ行を作る。指揮者は enqueue に頼らず
    そのチェーン（review → revise なら再 execute → …）を同期リレーで消化してから
    続行判定する（enqueue_next=False。キュー経由だと次判断と競合するため）。

    チェーン完了後の続行判定（#21 オートノミー）:
        L0/L1 → 停止（L0 はプランのみ・L1 は you_review で人のレビュー待ち）
        L2/L3 → 自分（orchestrate）を再 enqueue してループ継続
    execute/review が最終失敗した場合は各ジョブ自身が人へハンドオフ済みなので、
    指揮者は停止コメントだけ残してループを終了する。
    """
    pool = await get_pool()
    applied_rules: list[Any] = []
    comment = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
            current = TaskStatus(row["status"])
            if not can_transition(current, TaskStatus.AI_WORK):
                # 判断と現況が食い違った（並行操作等）。防御的に人へバトンを渡して停止
                comment = await comments_repo.create_comment(
                    conn,
                    row,
                    CommentCreate(
                        author=Author.AI,
                        text=HANDOFF_COMMENT,
                        agent_role=AgentRole.CONDUCTOR,
                    ),
                )
                task = await tasks_repo.apply_patch(conn, row, {})
                await _mark_succeeded(conn, job_id, decision.usage)
                exec_job_row = None
            else:
                # assign-ai ステップ1〜5 と同型（§6.3 retrieval → applied++ → ai_work →
                # execute ジョブ作成）。着手の説明は指揮者の判断理由コメントが兼ねる
                rule_rows = await rules_repo.relevant_rules(conn, row)
                await rules_repo.record_applications(conn, row, rule_rows)
                updated_rule_rows = await rules_repo.get_rules_by_uuids(
                    conn, [r["id"] for r in rule_rows]
                )
                applied_rules = [
                    await rules_repo.rule_dto_from_row(conn, r) for r in updated_rule_rows
                ]
                task = await tasks_repo.apply_patch(
                    conn,
                    row,
                    {
                        "status": TaskStatus.AI_WORK,
                        "progress": 0,
                        "lane_key": LaneKey.PROGRESS,
                    },
                )
                exec_job_row = await ai_jobs_repo.create_job(
                    conn,
                    row,
                    kind=AiJobKind.EXECUTE,
                    applied_rule_ids=[r["id"] for r in rule_rows],
                )
                comment = None

    if exec_job_row is None:
        publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
        publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
        return

    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    for rule in applied_rules:
        publish_event(RULE_UPDATED, rule.model_dump(mode="json", by_alias=True))

    # 既存 execute ジョブを起動（コミット後・進捗/成果物は execute が担う）。
    # 失敗時の人戻し・失敗コメントも execute 自身が行う（§7.2）。
    # #23: review 連鎖は enqueue させず（enqueue_next=False）、続けて同期リレーで消化する
    ok = await run_execute_job(str(exec_job_row["id"]), enqueue_next=False)
    if ok:
        ok = await _run_chain(task_row)
    await _continue_after_chain(job_id, task_row, decision, ok)


# ---- アクション: review（レビューAIへリレー #23） ------------------------------------


async def _act_review(
    job_id: str, task_row: asyncpg.Record, decision: NextActionResult
) -> None:
    """最新の成果物をレビューAIに検査させる（_act_execute と同型, #23）。

    審査基準は直近 execute ジョブの適用ルール（無ければ retrieval で組み直す）。
    revise なら review ジョブが execute を再起動する（ai_work へ差し戻し）ため、
    チェーンを同期リレーで消化してから続行判定する。
    """
    pool = await get_pool()
    comment = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
            artifact_count = await conn.fetchval(
                "select count(*) from artifacts where task_id = $1", row["id"]
            )
            if artifact_count == 0:
                # 判断と現況が食い違った（成果物なし）。防御的に人へバトンを渡して停止
                comment = await comments_repo.create_comment(
                    conn,
                    row,
                    CommentCreate(
                        author=Author.AI,
                        text=HANDOFF_COMMENT,
                        agent_role=AgentRole.CONDUCTOR,
                    ),
                )
                task = await tasks_repo.apply_patch(conn, row, {})
                await _mark_succeeded(conn, job_id, decision.usage)
                review_job_row = None
            else:
                prev_rule_ids = await conn.fetchval(
                    "select applied_rule_ids from ai_jobs "
                    "where task_id = $1 and kind = 'execute' "
                    "order by created_at desc limit 1",
                    row["id"],
                )
                rule_ids = list(prev_rule_ids or [])
                if not rule_ids:
                    # execute 履歴なし（人が成果物だけ置いた等）: retrieval で審査基準を組む
                    rule_ids = [r["id"] for r in await rules_repo.relevant_rules(conn, row)]
                review_job_row = await ai_jobs_repo.create_job(
                    conn, row, kind=AiJobKind.REVIEW, applied_rule_ids=rule_ids
                )

    if review_job_row is None:
        publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
        publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
        return

    ok = await run_review_job(str(review_job_row["id"]), enqueue_next=False)
    if ok:
        ok = await _run_chain(task_row)
    await _continue_after_chain(job_id, task_row, decision, ok)


async def _run_chain(task_row: asyncpg.Record) -> bool:
    """queued の execute/review ジョブを直列に消化する（#23 同期リレー）。

    enqueue はしない（enqueue_next=False で次のジョブ行だけが queued で積まれる）ため、
    cloud_tasks の二重配信・local の並行実行と競合しない。すべて成功で True。
    """
    pool = await get_pool()
    while True:
        async with pool.acquire() as conn:
            next_row = await conn.fetchrow(
                "select * from ai_jobs where task_id = $1 and status = 'queued' "
                "and kind in ('execute', 'review') order by created_at, id limit 1",
                task_row["id"],
            )
        if next_row is None:
            return True
        if next_row["kind"] == AiJobKind.REVIEW.value:
            ok = await run_review_job(str(next_row["id"]), enqueue_next=False)
        else:
            ok = await run_execute_job(str(next_row["id"]), enqueue_next=False)
        if not ok:
            return False


async def _continue_after_chain(
    job_id: str, task_row: asyncpg.Record, decision: NextActionResult, ok: bool
) -> None:
    """execute/review チェーン後の続行判定（#21/#23）。

    失敗時は各ジョブが人へハンドオフ済みなので停止コメントだけ残す。
    成功時はチェーン後の最新オートノミーで L2/L3 のみ次判断へ進む。
    """
    pool = await get_pool()
    if not ok:
        await _post_conductor_comment(task_row, EXECUTE_FAILED_COMMENT)
        async with pool.acquire() as conn:
            await _mark_succeeded(conn, job_id, decision.usage)
        return

    async with pool.acquire() as conn:
        autonomy = AutonomyLevel(
            await conn.fetchval(
                "select autonomy from tasks where id = $1", task_row["id"]
            )
        )
        await _mark_succeeded(conn, job_id, decision.usage)
    if autonomy in (AutonomyLevel.L2, AutonomyLevel.L3):
        await _enqueue_next_step(task_row)


# ---- アクション: handoff_human（人のボールへ） ---------------------------------------


async def _act_handoff_human(
    job_id: str, task_row: asyncpg.Record, decision: NextActionResult
) -> None:
    """人へバトンを渡して停止する。AI持ちの status なら人のボールへ遷移させる。

    成果物レビューが本筋なら you_review、それ以外は you_todo（遷移不可なら現状維持）。
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        current = TaskStatus(row["status"])
        fields: dict[str, Any] = {}
        if STATUS_META[current].owner is not Owner.HUMAN:
            has_artifact = (
                await conn.fetchval(
                    "select count(*) from artifacts where task_id = $1", row["id"]
                )
                > 0
            )
            if has_artifact and can_transition(current, TaskStatus.YOU_REVIEW):
                fields = {
                    "status": TaskStatus.YOU_REVIEW,
                    "progress": None,
                    "lane_key": LaneKey.REVIEW,
                }
            elif can_transition(current, TaskStatus.YOU_TODO):
                fields = {"status": TaskStatus.YOU_TODO, "progress": None}
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI, text=HANDOFF_COMMENT, agent_role=AgentRole.CONDUCTOR
            ),
        )
        task = await tasks_repo.apply_patch(conn, row, fields)
        await _mark_succeeded(conn, job_id, decision.usage)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- アクション: done（L3 のみ自動完了。それ以外は人の承認待ち） ----------------------


async def _act_done(
    job_id: str, task_row: asyncpg.Record, decision: NextActionResult
) -> None:
    """終了処理: 既に完了なら終了宣言のみ。L3 は自動承認で done、他は you_review 停止。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        current = TaskStatus(row["status"])
        autonomy = AutonomyLevel(row["autonomy"])
        fields: dict[str, Any] = {}
        if current is TaskStatus.DONE:
            text = DONE_CLOSING_COMMENT
        elif autonomy is AutonomyLevel.L3 and can_transition(current, TaskStatus.DONE):
            # L3 相当のみ自動完了（§5.6 の承認遷移をそのまま使う。事後レビュー可能）
            text = DONE_AUTO_APPROVE_COMMENT
            fields = {
                "status": TaskStatus.DONE,
                "progress": None,
                "lane_key": LaneKey.DONE,
            }
        else:
            # L3 以外は勝手に完了しない: you_review で人の承認を待つ（遷移不可なら現状維持）
            text = DONE_WAIT_APPROVAL_COMMENT
            if current is not TaskStatus.YOU_REVIEW and can_transition(
                current, TaskStatus.YOU_REVIEW
            ):
                fields = {
                    "status": TaskStatus.YOU_REVIEW,
                    "progress": None,
                    "lane_key": LaneKey.REVIEW,
                }
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(author=Author.AI, text=text, agent_role=AgentRole.CONDUCTOR),
        )
        task = await tasks_repo.apply_patch(conn, row, fields)
        await _mark_succeeded(conn, job_id, decision.usage)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- ヘルパ -----------------------------------------------------------------------


async def _session_step_count(conn: asyncpg.Connection, task_row: asyncpg.Record) -> int:
    """今回の autopilot セッション内の orchestrate ジョブ数（実行中の自分を含む）。

    セッション開始 = 最新の「指揮者引き受け」コメント（TAKEOVER_COMMENT。
    routers/ai.py autopilot がジョブ作成と同一トランザクションで投稿する）。
    見つからない場合（直接投入されたジョブ等）はタスク全体で数える（安全側）。
    """
    started_at = await conn.fetchval(
        "select max(created_at) from comments "
        "where task_id = $1 and agent_role = 'conductor' and text = $2",
        task_row["id"],
        TAKEOVER_COMMENT,
    )
    if started_at is None:
        return await conn.fetchval(
            "select count(*) from ai_jobs where task_id = $1 and kind = $2",
            task_row["id"],
            AiJobKind.ORCHESTRATE.value,
        )
    return await conn.fetchval(
        "select count(*) from ai_jobs "
        "where task_id = $1 and kind = $2 and created_at >= $3",
        task_row["id"],
        AiJobKind.ORCHESTRATE.value,
        started_at,
    )


async def _enqueue_next_step(task_row: asyncpg.Record) -> None:
    """次の判断ステップ（orchestrate ジョブ）を作成して enqueue する（ループ継続）。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        next_job_row = await ai_jobs_repo.create_job(
            conn, task_row, kind=AiJobKind.ORCHESTRATE
        )
    await jobs_queue.enqueue_job(str(next_job_row["id"]), kind=AiJobKind.ORCHESTRATE.value)


async def _post_conductor_comment(task_row: asyncpg.Record, text: str) -> None:
    """指揮者AI名義のコメントを投稿して SSE 配信する（commentCount も task.updated で同期）。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(author=Author.AI, text=text, agent_role=AgentRole.CONDUCTOR),
        )
        task = await tasks_repo.task_from_row(conn, row)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


async def _stop_with_comment(job_id: str, task_row: asyncpg.Record, text: str) -> None:
    """暴走防止の停止: 停止理由コメント＋人へハンドオフ（可能なら you_todo）＋成功確定。

    「上限で止まった」こと自体は判断として成功なので ai_jobs は succeeded にする
    （failed は判断不能・例外系に限る）。
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(author=Author.AI, text=text, agent_role=AgentRole.CONDUCTOR),
        )
        fields: dict[str, Any] = {}
        if can_transition(TaskStatus(row["status"]), TaskStatus.YOU_TODO):
            fields = {"status": TaskStatus.YOU_TODO, "progress": None}
        task = await tasks_repo.apply_patch(conn, row, fields)
        await _mark_succeeded(conn, job_id, _ZERO_USAGE)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


async def _mark_succeeded(
    conn: asyncpg.Connection, job_id: str, *usages: TokenUsage
) -> None:
    """orchestrate ジョブを成功確定する（判断＋生成のトークン合算＋コスト実算定 #25）。

    合算 usage に Flash 単価（orchestrate）を適用する。合算に含まれる生成呼び出し
    （chat_reply / propose_subtasks）も Flash 系のため単価は一致する。
    """
    total = TokenUsage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
    )
    await ai_jobs_repo.mark_succeeded(
        conn,
        job_id,
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        cost_usd=calc_cost_usd(AiJobKind.ORCHESTRATE, total),
    )


async def _handle_failure(job_id: str, error: Exception) -> None:
    """ai_jobs=failed で確定し、指揮者名義の中断コメントで人へハンドオフする（§7.2 同型）。"""
    reason = _summarize_error(error)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        job_row = await ai_jobs_repo.get_job_row(conn, job_id)
        if job_row is None:
            return
        await ai_jobs_repo.mark_failed(conn, job_id, error=reason)
        row = await conn.fetchrow(
            "select * from tasks where id = $1 for update", job_row["task_id"]
        )
        if row is None:
            return
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI,
                text=FAILURE_COMMENT_TEMPLATE.format(reason=reason),
                agent_role=AgentRole.CONDUCTOR,
            ),
        )
        fields: dict[str, Any] = {}
        if can_transition(TaskStatus(row["status"]), TaskStatus.YOU_TODO):
            fields = {"status": TaskStatus.YOU_TODO, "progress": None}
        task = await tasks_repo.apply_patch(conn, row, fields)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


def _task_prompt_dict(task_row: asyncpg.Record) -> dict[str, Any]:
    """AiProvider へ渡すタスク dict（provider.py の想定キー: id/humanId/title/labels）。"""
    return {
        "id": str(task_row["id"]),
        "humanId": task_row["human_id"],
        "title": task_row["title"],
        "labels": list(task_row["labels"]),
    }


def _summarize_error(error: Exception) -> str:
    """中断コメント向けの短い要約（先頭行・最大80文字。execute と同じ方針）。"""
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    return text[:80]
