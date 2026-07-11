"""受付エージェント（intake）ジョブ本体（#27 — カード作成と同時の自律ルート判定）。

『AIが自分から足りないと判断して聞いてくる』自律判断の入口。POST /api/tasks の成功後に
kind='intake' の ai_jobs が enqueue され（routers/tasks.py。confirmBreakdown 経由の
サブタスクと seed 済みタスクは対象外）、受付エージェントが AiProvider.assess_task で
execute | hearing | breakdown のルートを自分で判定する:

    hearing   → 壁打ちの初期質問（assess の questions）を chat_messages へ自発投稿
                （routers/chat.py startChat と同ロジック: chat が空のときだけ投稿し、
                spec へ遷移可能なら status=spec。レーンは変えない §5.2）
                ＋「壁打ちで前提を伺います。」コメント
    breakdown → 「大きなタスクのようです。壁打ちで分解しましょう。」コメント
    execute   → 「このまま実行できます。『AIにまかせる』でおまかせください。」コメント
                （実行の開始は人の操作 or 指揮者 #22 に委ねる — 勝手に走らせない）

どのルートでも判定理由コメント（計画AI名義 #19）を必ず残して判断を可視化する。

リトライ設計: 判定は軽量（Flash / mock）なのでジョブ内リトライはせず、失敗は
ai_jobs=failed ＋ 計画AI名義のコメントのみ（タスクは作成直後の人のボールのままなので
状態は変えない — 受付が失敗しても従来どおり手動で進められる）。
コストは calc_cost_usd（execute 以外 = Flash 単価 #25）で実算定する。
"""

import logging
from typing import Any

import asyncpg

from app.ai import get_provider
from app.ai.provider import AssessResult, IntakeRoute, TokenUsage
from app.costs import calc_cost_usd
from app.db import get_pool
from app.domain.dto import CommentCreate
from app.domain.models import (
    AgentRole,
    AiJobKind,
    AiJobStatus,
    Author,
    ChatMessage,
    Comment,
    Task,
    TaskStatus,
)
from app.domain.state_machine import can_transition
from app.events import (
    CHAT_MESSAGE_CREATED,
    COMMENT_CREATED,
    TASK_UPDATED,
    publish_event,
)
from app.jobs.execute import JobNotFoundError
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import chat as chat_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo

logger = logging.getLogger(__name__)

# --- コメント文言（受付＝計画AI名義 #19。判定の可視化） ---

# 判定理由（ルートによらず必ず残す）
REASON_COMMENT_TEMPLATE = "受付AI: このタスクは{label}が適切と判断しました（理由: {reason}）"
ROUTE_LABELS: dict[IntakeRoute, str] = {
    "execute": "このまま実行",
    "hearing": "壁打ちでのヒアリング",
    "breakdown": "壁打ちでの分解",
}
# ルート別の案内コメント
HEARING_COMMENT = "壁打ちで前提を伺います。"
BREAKDOWN_COMMENT = "大きなタスクのようです。壁打ちで分解しましょう。"
EXECUTE_COMMENT = "このまま実行できます。『AIにまかせる』でおまかせください。"
ROUTE_COMMENTS: dict[IntakeRoute, str] = {
    "execute": EXECUTE_COMMENT,
    "hearing": HEARING_COMMENT,
    "breakdown": BREAKDOWN_COMMENT,
}
# 受付自体の失敗（タスクは人のボールのまま。手動で進められることを明示）
FAILURE_COMMENT_TEMPLATE = (
    "受付AIの判定が失敗しました。そのまま手動で進められます。（理由: {reason}）"
)

# 初期質問の番号記号（GREETING_T130 と同じ ①②③ 形式）
_CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨"
QUESTIONS_LEAD_LINE = "分解や実行の前に、いくつか確認させてください。"


def format_questions(questions: list[str]) -> str:
    """assess の初期質問を壁打ちメッセージ本文（①②③形式）へ整形する。"""
    lines = [QUESTIONS_LEAD_LINE]
    for index, question in enumerate(questions):
        prefix = _CIRCLED_DIGITS[index] if index < len(_CIRCLED_DIGITS) else "-"
        lines.append(f"{prefix} {question}")
    return "\n".join(lines)


async def run_intake_job_row(
    job_row: asyncpg.Record,
    *,
    max_retries: int | None = None,
    handoff_on_failure: bool = True,
) -> bool:
    """kind='intake' の登録ハンドラ（app/jobs/registry.py の統一シグネチャ, #18）。"""
    return await run_intake_job(
        str(job_row["id"]), max_retries=max_retries, handoff_on_failure=handoff_on_failure
    )


async def run_intake_job(
    job_id: str,
    *,
    max_retries: int | None = None,  # 判定は軽量（Flash/mock）のためジョブ内リトライなし
    handoff_on_failure: bool = True,
) -> bool:
    """intake ジョブを実行する（成功 True / 失敗 False）。"""
    del max_retries  # 統一シグネチャ（#18）の互換のため受け取るのみ
    try:
        await _intake_attempt(job_id)
        return True
    except JobNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001 — 失敗はコメントでの可視化に集約する（§7.2）
        logger.warning("intake job %s failed: %s", job_id, exc)
        if handoff_on_failure:
            await _handle_failure(job_id, exc)
        return False


# ---- 1判定分の実行 -----------------------------------------------------------------


async def _intake_attempt(job_id: str) -> None:
    pool = await get_pool()

    # 0) ジョブと対象タスクをロードして running へ ＋ retrieval ルール
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
        rule_rows = await rules_repo.relevant_rules(conn, task_row)

    # 1) 受付エージェントのルート判定（#27）
    result = await get_provider().assess_task(
        _task_prompt_dict(task_row),
        [rules_repo.rule_prompt_dict(row) for row in rule_rows],
    )

    # 2) 反映（判定理由コメント → ルート別アクション）。単一トランザクションで確定し、
    #    コミット後に SSE 配信する
    reason_comment: Comment | None = None
    route_comment: Comment | None = None
    chat_message: ChatMessage | None = None
    task: Task | None = None
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        if row is None:  # 判定中に削除された: ジョブだけ確定して静かに終了
            await _mark_succeeded(conn, job_id, result)
            return
        reason_comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI,
                text=REASON_COMMENT_TEMPLATE.format(
                    label=ROUTE_LABELS[result.route], reason=result.reason
                ),
                agent_role=AgentRole.PLANNER,  # 受付・ヒアリングは計画AIの名義（#19）
            ),
        )
        route_comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI,
                text=ROUTE_COMMENTS[result.route],
                agent_role=AgentRole.PLANNER,
            ),
        )
        fields: dict[str, Any] = {}
        if result.route == "hearing":
            # startChat（routers/chat.py）と同ロジック: chat が空のときだけ初期質問を
            # 自発投稿し、spec へ遷移可能なら遷移する（レーンは変えない §5.2）
            messages = await chat_repo.list_chat_messages(conn, row)
            if not messages and result.questions:
                chat_message = await chat_repo.create_chat_message(
                    conn, row, author=Author.AI, text=format_questions(result.questions)
                )
            current = TaskStatus(row["status"])
            if current is not TaskStatus.SPEC and can_transition(current, TaskStatus.SPEC):
                fields = {"status": TaskStatus.SPEC}
        task = await tasks_repo.apply_patch(conn, row, fields)  # commentCount 同期を兼ねる
        await _mark_succeeded(conn, job_id, result)

    # 3) SSE 配信（コミット後。判定理由 → 案内 → 初期質問 → task.updated の順）
    publish_event(COMMENT_CREATED, reason_comment.model_dump(mode="json", by_alias=True))
    publish_event(COMMENT_CREATED, route_comment.model_dump(mode="json", by_alias=True))
    if chat_message is not None:
        publish_event(
            CHAT_MESSAGE_CREATED, chat_message.model_dump(mode="json", by_alias=True)
        )
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- 失敗: コメントで可視化するだけ（タスク状態は変えない） ---------------------------


async def _handle_failure(job_id: str, error: Exception) -> None:
    """ai_jobs=failed で確定し、計画AI名義のコメントで可視化する。

    タスクは作成直後の人のボールのまま（受付が失敗しても手動で進められる）なので、
    status / lane は一切変更しない。
    """
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
                agent_role=AgentRole.PLANNER,
            ),
        )
        task = await tasks_repo.task_from_row(conn, row)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- ヘルパ -----------------------------------------------------------------------


async def _mark_succeeded(
    conn: asyncpg.Connection, job_id: str, result: AssessResult
) -> None:
    """intake ジョブを成功確定する（トークン記録＋コスト実算定 #25。Flash 単価）。"""
    usage: TokenUsage = result.usage
    await ai_jobs_repo.mark_succeeded(
        conn,
        job_id,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=calc_cost_usd(AiJobKind.INTAKE, usage),
    )


def _task_prompt_dict(task_row: asyncpg.Record) -> dict[str, Any]:
    """AiProvider へ渡すタスク dict（provider.py の想定キー: id/humanId/title/labels）。"""
    return {
        "id": str(task_row["id"]),
        "humanId": task_row["human_id"],
        "title": task_row["title"],
        "labels": list(task_row["labels"]),
    }


def _summarize_error(error: Exception) -> str:
    """失敗コメント向けの短い要約（先頭行・最大80文字。execute と同じ方針）。"""
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    return text[:80]
