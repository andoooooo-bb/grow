"""実作業（execute）ジョブ本体（§7.2 / §7.3 — この製品の主戦場）。

進行はプロトタイプ assignAI の擬似タイミング（§4.4: 1600ms→中間 / 4200ms→完了）を踏襲:

    running → (1.6s) progress=45 ＋ 中間コメント → (2.6s) AiProvider.execute
    → artifacts 新版保存 → you_review・review レーンへ ＋ 完了コメント
    → ai_jobs succeeded（トークン/コスト記録 §00 #16）

演出ディレイ・リトライ間隔はモジュール定数で注入可能（テストでは 0 秒に差し替える）。

リトライ設計（§7.2）:
- local ランナー: 本コルーチン内で最大 MAX_RETRIES 回リトライ（指数バックオフ）。
  最終失敗で ai_jobs=failed、タスクを you_todo へ戻し、失敗コメントで人へハンドオフ。
- cloud_tasks ランナー: ジョブ内リトライはせず（max_retries=0）、worker エンドポイント
  （app/routers/internal_jobs.py）が 5xx を返して Cloud Tasks の再試行に任せる。
  最終試行（X-CloudTasks-TaskRetryCount が上限到達）でのみ人へのハンドオフを行う。
AIの実行権限は読み取り専用（§00 #3）なので、途中失敗のリトライで副作用は残らない。
"""

import asyncio
import logging
from typing import Any

import asyncpg

from app.ai import get_provider
from app.db import get_pool
from app.domain.dto import CommentCreate
from app.domain.models import (
    AgentRole,
    AiJobStatus,
    Author,
    AutonomyLevel,
    LaneKey,
    TaskStatus,
)
from app.domain.state_machine import can_transition
from app.events import ARTIFACT_CREATED, COMMENT_CREATED, TASK_UPDATED, publish_event
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo
from app.repo.artifacts import create_artifact

logger = logging.getLogger(__name__)

# --- 演出ディレイ（§4.4 疑似タイミング。テストでは monkeypatch で 0 にする） ---
PROGRESS_DELAY_SEC = 1.6  # 着手 → 中間進捗（プロト 1600ms）
COMPLETE_DELAY_SEC = 2.6  # 中間進捗 → 完了（プロト 4200ms - 1600ms）
INTERMEDIATE_PROGRESS = 45  # 中間進捗の値（プロト準拠）

# --- local ランナーのリトライ設定（§7.2） ---
MAX_RETRIES = 2  # 初回 + 最大2回リトライ = 最大3試行
RETRY_BACKOFF_SEC = 0.5  # 指数バックオフの基準（0.5s → 1.0s）

# --- コメント文言（Grow.dc.html assignAI 準拠） ---
PROGRESS_COMMENT = "作業を進めています…（途中経過を共有します）"
COMPLETE_COMMENT = "完了しました。学習済みのルールに沿って仕上げています。レビューをお願いします。"
FAILURE_COMMENT_TEMPLATE = (
    "作業中にエラーが発生しました。内容を確認のうえ、再度お任せください。（理由: {reason}）"
)
# --- #21 オートノミー分岐のコメント文言 ---
# L0: 実行プランだけを作り、成果物は作らず人へハンドオフする（停止理由の明示）
PLAN_HANDOFF_COMMENT_TEMPLATE = (
    "実行プランを作成しました。オートノミーL0（計画のみ）のため、実行せずここで停止します。"
    "プランを確認のうえ、あなたが進めるか、レベルを上げて再度お任せください。\n\n{plan}"
)
# L3: done まで連鎖適用したことを事後レビュー可能な形で明示する
AUTO_APPROVE_COMMENT = "ポリシーL3により自動承認しました。内容は事後確認できます。"


class JobNotFoundError(Exception):
    """指定 ID の ai_jobs 行が存在しない。"""


async def run_execute_job_row(
    job_row: asyncpg.Record,
    *,
    max_retries: int | None = None,
    handoff_on_failure: bool = True,
) -> bool:
    """kind='execute' の登録ハンドラ（app/jobs/registry.py の統一シグネチャ, #18）。

    行の状態は試行ごとに変わり得るため job_row からは id のみを使い、
    実行本体（run_execute_job）が毎試行 ai_jobs 行を再取得する。
    """
    return await run_execute_job(
        str(job_row["id"]), max_retries=max_retries, handoff_on_failure=handoff_on_failure
    )


async def run_execute_job(
    job_id: str,
    *,
    max_retries: int | None = None,
    handoff_on_failure: bool = True,
) -> bool:
    """execute ジョブを実行する（成功で True / 最終失敗で False）。

    - max_retries: ジョブ内リトライ回数（None は MAX_RETRIES）。cloud_tasks 経由は 0。
    - handoff_on_failure: 失敗し切ったとき人へのハンドオフ（you_todo 戻し＋失敗コメント）
      を行うか。cloud_tasks では Cloud Tasks の再試行が残っている間は False にする。
    """
    retries = MAX_RETRIES if max_retries is None else max_retries
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            await _execute_attempt(job_id)
            return True
        except JobNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 — 失敗種別はリトライ方針に影響しない
            last_error = exc
            logger.warning("execute job %s attempt %d failed: %s", job_id, attempt + 1, exc)
            if attempt < retries:
                await asyncio.sleep(RETRY_BACKOFF_SEC * (2**attempt))
    if handoff_on_failure and last_error is not None:
        await _handle_final_failure(job_id, last_error)
    return False


# ---- 1試行分の実行 ---------------------------------------------------------------


async def _execute_attempt(job_id: str) -> None:
    pool = await get_pool()

    # 0) ジョブと対象タスクをロードして running へ
    async with pool.acquire() as conn:
        job_row = await ai_jobs_repo.get_job_row(conn, job_id)
        if job_row is None:
            raise JobNotFoundError(f"ai_job not found: {job_id}")
        if job_row["status"] in (AiJobStatus.SUCCEEDED, AiJobStatus.FAILED):
            return  # 二重配信（Cloud Tasks の at-least-once）への冪等ガード
        # 既に running = 前回試行の失敗後の再実行。中間演出は重複させない
        is_retry = job_row["status"] == AiJobStatus.RUNNING
        task_row = await _get_task_row(conn, job_row["task_id"])
        await ai_jobs_repo.mark_running(conn, job_id)

    # 1) 演出ディレイ → 中間進捗 45% ＋ 中間コメント（§1.5 step5。再試行時はスキップ）
    if not is_retry:
        await asyncio.sleep(PROGRESS_DELAY_SEC)
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
                comment = await comments_repo.create_comment(
                    conn,
                    row,
                    CommentCreate(
                        author=Author.AI,
                        text=PROGRESS_COMMENT,
                        agent_role=AgentRole.EXECUTOR,  # 進捗は実行AIの名義（#19）
                    ),
                )
                task = await tasks_repo.apply_patch(
                    conn, row, {"progress": INTERMEDIATE_PROGRESS}
                )
        publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
        publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))

    # 2) AiProvider.execute（retrieval 済みルール＋コメント履歴を注入, §7.3）
    #    #21: タスクのオートノミー（L0-L3）と行動範囲ポリシーを読み、provider へ渡す
    autonomy = AutonomyLevel(task_row["autonomy"])
    policy = tasks_repo.policy_from_row(task_row)
    await asyncio.sleep(COMPLETE_DELAY_SEC)
    async with pool.acquire() as conn:
        rule_rows = await rules_repo.get_rules_by_uuids(conn, job_row["applied_rule_ids"])
        history = await comments_repo.list_comments(conn, task_row)
    result = await get_provider().execute(
        _task_prompt_dict(task_row),
        [rules_repo.rule_prompt_dict(row) for row in rule_rows],
        [{"who": c.author.value, "text": c.text} for c in history],
        policy=policy.model_dump(by_alias=True),
        plan_only=autonomy is AutonomyLevel.L0,
    )

    # 3) 完了処理はオートノミーで分岐する（#21）:
    #    - L0: 成果物は作らず、実行プランをコメントで渡して you_todo へハンドオフ
    #    - L1: 現行どおり you_review へ（下書きまで）
    #    - L2: 現段階では L1 と同挙動。#22 指揮者が「プラン承認後は完了まで自動」を
    #      実現する（指揮者は you_review 到達時に autonomy=L2 を見て自動リレーへ接続）
    #    - L3: you_review 適用後、同一トランザクションで done まで連鎖適用（自動承認）
    if autonomy is AutonomyLevel.L0:
        await _handoff_plan(job_id, task_row, result)
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
            current = TaskStatus(row["status"])
            if not can_transition(current, TaskStatus.YOU_REVIEW):
                raise RuntimeError(
                    f"invalid transition on job completion: {current} -> you_review"
                )
            artifact = await create_artifact(conn, row, result.content_md, job_id=job_id)
            comment = await comments_repo.create_comment(
                conn,
                row,
                CommentCreate(
                    author=Author.AI,
                    text=COMPLETE_COMMENT,
                    agent_role=AgentRole.EXECUTOR,  # 完了ハンドオフも実行AIの名義（#19）
                ),
            )
            # §5.6 不変条件: ai_work 以外では progress は null
            task = await tasks_repo.apply_patch(
                conn,
                row,
                {
                    "status": TaskStatus.YOU_REVIEW,
                    "progress": None,
                    "lane_key": LaneKey.REVIEW,
                },
            )
            # L3（#21）: ai_work→you_review→done を連鎖適用（ALLOWED_TRANSITIONS は不変。
            # you_review→done は §5.6 の「承認」遷移をそのまま使う）
            auto_comment = None
            done_task = None
            if autonomy is AutonomyLevel.L3:
                row = await tasks_repo.get_task_row(
                    conn, task_row["human_id"], for_update=True
                )
                if not can_transition(TaskStatus(row["status"]), TaskStatus.DONE):
                    raise RuntimeError(
                        f"invalid transition on auto-approve: {row['status']} -> done"
                    )
                auto_comment = await comments_repo.create_comment(
                    conn,
                    row,
                    CommentCreate(
                        author=Author.AI,
                        text=AUTO_APPROVE_COMMENT,
                        agent_role=AgentRole.EXECUTOR,  # 自動承認も実行AIの名義（#19）
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
                )
            # mock は usage をそのまま記録し cost 0.0（実コスト算定は Gemini 実装 #15 で）
            await ai_jobs_repo.mark_succeeded(
                conn,
                job_id,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=0.0,
            )
    publish_event(ARTIFACT_CREATED, artifact.model_dump(mode="json", by_alias=True))
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))
    if auto_comment is not None and done_task is not None:
        # L3 の連鎖（you_review → done）も順に配信し、FEフィードに経緯を残す
        publish_event(COMMENT_CREATED, auto_comment.model_dump(mode="json", by_alias=True))
        publish_event(TASK_UPDATED, done_task.model_dump(mode="json", by_alias=True))


# ---- L0: 実行プランを渡して人へハンドオフ（#21） -----------------------------------


async def _handoff_plan(job_id: str, task_row: asyncpg.Record, result: Any) -> None:
    """L0（計画のみ）: 成果物は作らず、プランをコメント投稿して you_todo へ渡す。

    ai_work→you_todo は §5.6 で許可済みの遷移（§7.2 の失敗ハンドオフと同じ）。
    レーンは動かさない（進行中のまま「あなたの作業待ち」= 人のボール）。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
            current = TaskStatus(row["status"])
            if not can_transition(current, TaskStatus.YOU_TODO):
                raise RuntimeError(
                    f"invalid transition on plan handoff: {current} -> you_todo"
                )
            comment = await comments_repo.create_comment(
                conn,
                row,
                CommentCreate(
                    author=Author.AI,
                    text=PLAN_HANDOFF_COMMENT_TEMPLATE.format(plan=result.content_md),
                    agent_role=AgentRole.EXECUTOR,  # プラン提示も実行AIの名義（#19）
                ),
            )
            # §5.6 不変条件: ai_work 以外では progress は null
            task = await tasks_repo.apply_patch(
                conn, row, {"status": TaskStatus.YOU_TODO, "progress": None}
            )
            await ai_jobs_repo.mark_succeeded(
                conn,
                job_id,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=0.0,
            )
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- 最終失敗: 人へ戻す（§7.2） ---------------------------------------------------


async def _handle_final_failure(job_id: str, error: Exception) -> None:
    """ai_jobs=failed で確定し、タスクを you_todo へ戻して失敗コメントを投稿する。"""
    reason = _summarize_error(error)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
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
                    agent_role=AgentRole.EXECUTOR,  # 失敗ハンドオフも実行AIの名義（#19）
                ),
            )
            fields: dict[str, Any] = {}
            if can_transition(TaskStatus(row["status"]), TaskStatus.YOU_TODO):
                # 再試行導線: you_todo に戻れば「AIにまかせる」を再度押せる（§7.2）
                fields = {"status": TaskStatus.YOU_TODO, "progress": None}
            task = await tasks_repo.apply_patch(conn, row, fields)
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


# ---- ヘルパ -----------------------------------------------------------------------


async def _get_task_row(conn: asyncpg.Connection, task_uuid: Any) -> asyncpg.Record:
    row = await conn.fetchrow("select * from tasks where id = $1", task_uuid)
    if row is None:  # ai_jobs.task_id は FK なので通常は起きない
        raise JobNotFoundError(f"task not found for job: {task_uuid}")
    return row


def _task_prompt_dict(task_row: asyncpg.Record) -> dict[str, Any]:
    """AiProvider へ渡すタスク dict（provider.py の想定キー: id/humanId/title/labels）。"""
    return {
        "id": str(task_row["id"]),
        "humanId": task_row["human_id"],
        "title": task_row["title"],
        "labels": list(task_row["labels"]),
    }


def _summarize_error(error: Exception) -> str:
    """失敗コメント向けの短い要約（先頭行・最大80文字）。"""
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    return text[:80]
