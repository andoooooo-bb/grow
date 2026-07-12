"""セルフレビュー（review）ジョブ本体（#23 — AIがAIの成果物を突き返す）。

execute ジョブは成果物を保存すると（タスクは ai_work のまま）本ジョブを enqueue する。
レビューAIが適用ルールを審査基準に最新版を検査し、approve / revise を自分で判定する:

    revise  → findings を REVIEWER 名義のコメント（【レビュー指摘】…）で投稿し、
              execute を再 enqueue（同一 applied_rule_ids）。最大 MAX_REVIEW_CYCLES 周。
    approve → 現行どおりの完了ハンドオフ（実行AIの完了コメント → you_review・review レーン。
              L3 は done まで連鎖適用 #21）。周回上限到達時は approve 扱いにして
              「自動修正上限に達したためレビューをお願いします」を明示する。

見かけの最終状態（you_review・完了コメント）は #23 以前と同じで、途中に
レビューAIとのやり取り（指摘 → 修正 → 承認）が挟まるだけ（§00 外部挙動の維持）。

周回のカウントはコメント履歴を新しい順に走査し、実行チェーン内に現れるコメント
（実行AIの進捗・レビューAIの指摘）だけを辿って数える — 人のコメント・着手コメント・
指揮者コメントが現れた時点でチェーン境界とみなす（セッションを跨いで累積しない）。

リトライ設計: 判定は軽量（Flash / mock）なのでジョブ内リトライはせず、失敗は
ai_jobs=failed ＋ REVIEWER 名義のコメントで人へハンドオフする（成果物は保存済みなので
you_review へ渡し、人のレビューで補完できるようにする）。
"""

import logging
from typing import Any

import asyncpg

from app.ai import get_provider
from app.ai.provider import REVIEW_FINDINGS_MARKER, ReviewResult
from app.costs import calc_cost_usd
from app.db import get_pool
from app.domain.dto import CommentCreate
from app.domain.models import (
    AgentRole,
    AiJobKind,
    AiJobStatus,
    Author,
    AutonomyLevel,
    LaneKey,
    TaskStatus,
)
from app.domain.state_machine import can_transition
from app.events import COMMENT_CREATED, TASK_UPDATED, publish_event
from app.jobs import queue as jobs_queue
from app.jobs.execute import (
    AUTO_APPROVE_COMMENT,
    COMPLETE_COMMENT,
    PROGRESS_COMMENT,
    JobNotFoundError,
)
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo

logger = logging.getLogger(__name__)

# revise → execute 再実行の上限（#21: 品質ループも暴走させない。上限到達で人へ）
MAX_REVIEW_CYCLES = 2

# --- コメント文言（レビューAI名義 #19） ---

# revise: findings を実行AIへの修正指示として投稿する（マーカーは周回カウントにも使う）
REVISE_COMMENT_TEMPLATE = (
    "セルフレビューの結果、修正が必要と判断しました。実行AIに差し戻します。\n"
    f"{REVIEW_FINDINGS_MARKER}\n"
    "{findings}"
)
# approve: 検査済みであることを人に見える形で残す
APPROVE_COMMENT = "セルフレビューを実施しました。適用ルールに照らして問題ありません。"
# 周回上限到達: approve 扱いで人へ（停止理由の明示）
CYCLE_LIMIT_COMMENT = "自動修正上限に達したためレビューをお願いします。"
# レビュー自体の失敗: 成果物は保存済みなので人のレビューへ渡す
FAILURE_COMMENT_TEMPLATE = (
    "セルフレビュー中にエラーが発生しました。成果物はそのまま、"
    "人によるレビューをお願いします。（理由: {reason}）"
)


async def run_review_job_row(
    job_row: asyncpg.Record,
    *,
    max_retries: int | None = None,
    handoff_on_failure: bool = True,
) -> bool:
    """kind='review' の登録ハンドラ（app/jobs/registry.py の統一シグネチャ, #18）。"""
    return await run_review_job(
        str(job_row["id"]), max_retries=max_retries, handoff_on_failure=handoff_on_failure
    )


async def run_review_job(
    job_id: str,
    *,
    max_retries: int | None = None,  # 判定は軽量（Flash/mock）のためジョブ内リトライなし
    handoff_on_failure: bool = True,
    enqueue_next: bool = True,
) -> bool:
    """review ジョブを実行する（成功 True / 失敗 False）。

    enqueue_next=False は #22 指揮者の同期リレー用: revise 時の execute ジョブ行は
    作るが enqueue しない（orchestrate 側が queued の行を直接消化する）。
    """
    del max_retries  # 統一シグネチャ（#18）の互換のため受け取るのみ
    try:
        await _review_attempt(job_id, enqueue_next=enqueue_next)
        return True
    except JobNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001 — 失敗は人へのハンドオフに集約する（§7.2）
        logger.warning("review job %s failed: %s", job_id, exc)
        if handoff_on_failure:
            await _handle_failure(job_id, exc)
        return False


# ---- 1判定分の実行 -----------------------------------------------------------------


async def _review_attempt(job_id: str, *, enqueue_next: bool) -> None:
    pool = await get_pool()

    # 0) ジョブ・タスク・最新成果物・審査基準（実行時と同じ適用ルール）をロード
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
        artifact_md = await conn.fetchval(
            "select content_md from artifacts where task_id = $1 "
            "order by version desc limit 1",
            task_row["id"],
        )
        rule_rows = await rules_repo.get_rules_by_uuids(conn, job_row["applied_rule_ids"])
        cycles = await _revise_cycle_count(conn, task_row)
    if artifact_md is None:
        raise RuntimeError(f"no artifact to review for task {task_row['human_id']}")

    # 1) レビューAIの判定（適用ルール = 審査基準, #23）
    result = await get_provider().review_artifact(
        _task_prompt_dict(task_row),
        artifact_md,
        [rules_repo.rule_prompt_dict(row) for row in rule_rows],
    )

    # 2) 分岐: revise（上限内）は実行AIへ差し戻し / それ以外は人へのハンドオフ確定
    if result.verdict == "revise" and cycles < MAX_REVIEW_CYCLES:
        await _revise(job_id, task_row, job_row, result, enqueue_next=enqueue_next)
    else:
        await _finalize_handoff(job_id, task_row, result, cycle_capped=cycles >= MAX_REVIEW_CYCLES)


# ---- revise: findings を投稿して execute を再 enqueue --------------------------------


async def _revise(
    job_id: str,
    task_row: asyncpg.Record,
    job_row: asyncpg.Record,
    result: ReviewResult,
    *,
    enqueue_next: bool,
) -> None:
    """REVIEWER 名義の指摘コメント → タスクを ai_work に保ち execute を再 enqueue。

    再実行ジョブは同じ applied_rule_ids を引き継ぐ（applied++ は重ねない —
    ルール適用のカウントはセッション単位 §6.3）。指摘はコメント経由で
    provider.execute の履歴に自然に流れる。
    """
    findings_md = "\n".join(f"- {finding}" for finding in result.findings)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        current = TaskStatus(row["status"])
        if current is not TaskStatus.AI_WORK and not can_transition(
            current, TaskStatus.AI_WORK
        ):
            raise RuntimeError(f"invalid transition on review revise: {current} -> ai_work")
        comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI,
                text=REVISE_COMMENT_TEMPLATE.format(findings=findings_md),
                agent_role=AgentRole.REVIEWER,  # 指摘はレビューAIの名義（#19）
            ),
        )
        fields: dict[str, Any] = {}
        if current is not TaskStatus.AI_WORK:
            # #22 指揮者起点で you_review 等から review が走った場合も差し戻せる
            fields = {
                "status": TaskStatus.AI_WORK,
                "progress": 0,
                "lane_key": LaneKey.PROGRESS,
            }
        task = await tasks_repo.apply_patch(conn, row, fields, actor="ai")
        exec_job_row = await ai_jobs_repo.create_job(
            conn,
            row,
            kind=AiJobKind.EXECUTE,
            applied_rule_ids=list(job_row["applied_rule_ids"]),
        )
        await _mark_succeeded(conn, job_id, result)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    if enqueue_next:
        await jobs_queue.enqueue_job(
            str(exec_job_row["id"]), kind=AiJobKind.EXECUTE.value
        )


# ---- approve / 上限到達: 現行どおりの完了ハンドオフ ----------------------------------


async def _finalize_handoff(
    job_id: str,
    task_row: asyncpg.Record,
    result: ReviewResult,
    *,
    cycle_capped: bool,
) -> None:
    """人へのハンドオフを確定する（#23 以前の execute 完了処理と同じ最終状態）。

    - REVIEWER 名義の判定コメント（approve / 上限到達）を残す
    - ai_work からの完了なら実行AIの完了コメント → you_review・review レーンへ
      （§5.6 不変条件: progress は null に）
    - L3（#21）は you_review → done を連鎖適用（自動承認コメント付き）
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        current = TaskStatus(row["status"])
        if current is not TaskStatus.YOU_REVIEW and not can_transition(
            current, TaskStatus.YOU_REVIEW
        ):
            # reviewing 等 you_review へ動かせない status は現状維持で判定コメントのみ
            keep_status = True
        else:
            keep_status = False
        review_comment = await comments_repo.create_comment(
            conn,
            row,
            CommentCreate(
                author=Author.AI,
                text=CYCLE_LIMIT_COMMENT if cycle_capped else APPROVE_COMMENT,
                agent_role=AgentRole.REVIEWER,  # 判定はレビューAIの名義（#19）
            ),
        )
        complete_comment = None
        if current is TaskStatus.AI_WORK:
            # 実行チェーンの完了: 従来どおり実行AIの完了ハンドオフコメントを残す
            complete_comment = await comments_repo.create_comment(
                conn,
                row,
                CommentCreate(
                    author=Author.AI,
                    text=COMPLETE_COMMENT,
                    agent_role=AgentRole.EXECUTOR,  # 完了ハンドオフは実行AIの名義（#19）
                ),
            )
        fields: dict[str, Any] = {}
        if not keep_status and current is not TaskStatus.YOU_REVIEW:
            # §5.6 不変条件: ai_work 以外では progress は null
            fields = {
                "status": TaskStatus.YOU_REVIEW,
                "progress": None,
                "lane_key": LaneKey.REVIEW,
            }
        task = await tasks_repo.apply_patch(conn, row, fields, actor="ai")
        # L3（#21）: you_review → done を連鎖適用（§5.6 の承認遷移をそのまま使う）
        auto_comment = None
        done_task = None
        if AutonomyLevel(row["autonomy"]) is AutonomyLevel.L3:
            row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
            if can_transition(TaskStatus(row["status"]), TaskStatus.DONE):
                auto_comment = await comments_repo.create_comment(
                    conn,
                    row,
                    CommentCreate(
                        author=Author.AI,
                        text=AUTO_APPROVE_COMMENT,
                        agent_role=AgentRole.EXECUTOR,  # 自動承認も実行AIの名義（#21）
                    ),
                )
                done_task = await tasks_repo.apply_patch(
                    conn,
                    row,
                    {
                        "status": TaskStatus.DONE,
                        "progress": None,
                        "lane_key": LaneKey.DONE,
                    },
                    actor="ai",
                )
        await _mark_succeeded(conn, job_id, result)
    publish_event(COMMENT_CREATED, review_comment.model_dump(mode="json", by_alias=True))
    if complete_comment is not None:
        publish_event(
            COMMENT_CREATED, complete_comment.model_dump(mode="json", by_alias=True)
        )
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    if auto_comment is not None and done_task is not None:
        # L3 の連鎖（you_review → done）も順に配信し、FEフィードに経緯を残す
        publish_event(COMMENT_CREATED, auto_comment.model_dump(mode="json", by_alias=True))
        publish_event(TASK_UPDATED, done_task.model_dump(mode="json", by_alias=True))


# ---- 失敗: 成果物ごと人のレビューへ --------------------------------------------------


async def _handle_failure(job_id: str, error: Exception) -> None:
    """ai_jobs=failed で確定し、成果物はそのまま人のレビューへ渡す（§7.2 同型）。"""
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
                agent_role=AgentRole.REVIEWER,  # 失敗ハンドオフはレビューAIの名義（#19）
            ),
        )
        fields: dict[str, Any] = {}
        if can_transition(TaskStatus(row["status"]), TaskStatus.YOU_REVIEW):
            # 成果物は保存済み: レビュー未実施のまま人のレビューで補完してもらう
            fields = {
                "status": TaskStatus.YOU_REVIEW,
                "progress": None,
                "lane_key": LaneKey.REVIEW,
            }
        task = await tasks_repo.apply_patch(conn, row, fields, actor="ai")
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- ヘルパ -----------------------------------------------------------------------


async def _revise_cycle_count(conn: asyncpg.Connection, task_row: asyncpg.Record) -> int:
    """現在の実行チェーン内の revise 周回数（コメント履歴を新しい順に走査）。

    チェーン内に現れるのは実行AIの進捗コメントとレビューAIの指摘コメントのみ。
    それ以外のコメント（着手・人・指揮者など）が現れた時点でチェーン境界とみなし、
    そこまでの指摘コメント数を返す（セッションを跨いで累積させない）。
    """
    rows = await conn.fetch(
        "select agent_role, text from comments where task_id = $1 "
        "order by created_at desc, id desc",
        task_row["id"],
    )
    count = 0
    for row in rows:
        text = row["text"] or ""
        if row["agent_role"] == AgentRole.REVIEWER.value and REVIEW_FINDINGS_MARKER in text:
            count += 1
        elif row["agent_role"] == AgentRole.EXECUTOR.value and text == PROGRESS_COMMENT:
            continue  # チェーン内の進捗コメントは読み飛ばす
        else:
            break  # チェーン境界（着手・人・指揮者・完了など）
    return count


async def _mark_succeeded(
    conn: asyncpg.Connection, job_id: str, result: ReviewResult
) -> None:
    """review ジョブを成功確定する（トークン記録＋コスト実算定 #25。Flash 単価）。"""
    await ai_jobs_repo.mark_succeeded(
        conn,
        job_id,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_usd=calc_cost_usd(AiJobKind.REVIEW, result.usage),
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
