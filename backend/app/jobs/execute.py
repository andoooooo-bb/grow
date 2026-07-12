"""実作業（execute）ジョブ本体（§7.2 / §7.3 — この製品の主戦場）。

進行は本物のストリーミング実況（#24。擬似タイミングの張りぼては廃止）:

    running → AiProvider.execute(on_delta=…) が増分テキストを届けるたびに
      - artifact.delta を SSE 配信（FE ドロワーのライブ描画）
      - 最初の delta で中間コメントを1回投稿（§1.5 step5 / review.py の走査互換）
      - 進捗 = 受信文字数ベースの推定を 5% 刻みで間引いて task.updated 配信
    → artifacts 新版保存 → ai_jobs succeeded（トークン/コスト記録 §00 #16）
    → review ジョブを enqueue（#23 セルフレビュー。タスクは ai_work のまま）

you_review への遷移と完了コメントは review ジョブ（app/jobs/review.py）が
approve 判定後に行う（#23）。revise なら本ジョブが再 enqueue され、修正版を作る。
最終状態（you_review・完了コメント・L3 の done 連鎖）は #23 以前と同じ。
L0（#21 計画のみ）は成果物を作らないためレビューを挟まず直接ハンドオフする
（実況もしない = on_delta を渡さない）。

リトライ間隔はモジュール定数で注入可能（テストでは 0 秒に差し替える）。

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
from app.costs import calc_cost_usd
from app.db import get_pool
from app.domain.dto import CommentCreate
from app.domain.models import (
    AgentRole,
    AiJobKind,
    AiJobStatus,
    Author,
    AutonomyLevel,
    TaskStatus,
)
from app.domain.state_machine import can_transition
from app.events import (
    ARTIFACT_CREATED,
    ARTIFACT_DELTA,
    COMMENT_CREATED,
    TASK_UPDATED,
    publish_event,
)
from app.jobs import queue as jobs_queue
from app.repo import ai_jobs as ai_jobs_repo
from app.repo import comments as comments_repo
from app.repo import rules as rules_repo
from app.repo import tasks as tasks_repo
from app.repo.artifacts import create_artifact

logger = logging.getLogger(__name__)

# --- ライブ実況（#24）: 進捗は受信文字数ベースの推定（擬似ディレイ・固定45%は廃止） ---
PROGRESS_CHARS_PER_PERCENT = 40  # 受信40文字 ≒ 1%（実レポート2〜4千字で終盤に達する推定）
PROGRESS_MAX = 95  # ストリーム中の進捗上限（最終確定は review 側のハンドオフ #23）
PROGRESS_STEP_PERCENT = 5  # task.updated の間引き幅（5%刻みが変わったときのみ配信）

# --- local ランナーのリトライ設定（§7.2） ---
MAX_RETRIES = 2  # 初回 + 最大2回リトライ = 最大3試行
RETRY_BACKOFF_SEC = 0.5  # 指数バックオフの基準（0.5s → 1.0s）

# --- コメント文言（Grow.dc.html assignAI 準拠） ---
PROGRESS_COMMENT = "作業を進めています…（途中経過を共有します）"
COMPLETE_COMMENT = (
    "完了しました。学習済みのルールに沿って仕上げています。"
    "下の成果物をご確認ください。問題なければ「完了にする」、"
    "直したい点があれば「差し戻す」で指示してください。"
)
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
    enqueue_next: bool = True,
) -> bool:
    """execute ジョブを実行する（成功で True / 最終失敗で False）。

    - max_retries: ジョブ内リトライ回数（None は MAX_RETRIES）。cloud_tasks 経由は 0。
    - handoff_on_failure: 失敗し切ったとき人へのハンドオフ（you_todo 戻し＋失敗コメント）
      を行うか。cloud_tasks では Cloud Tasks の再試行が残っている間は False にする。
    - enqueue_next: 成果物保存後の review ジョブ（#23）を enqueue するか。
      False は #22 指揮者の同期リレー用（ジョブ行は作るが enqueue せず、
      orchestrate 側が queued の行を直接消化する）。
    """
    retries = MAX_RETRIES if max_retries is None else max_retries
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            await _execute_attempt(job_id, enqueue_next=enqueue_next)
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


async def _execute_attempt(job_id: str, *, enqueue_next: bool = True) -> None:
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

    # 1) AiProvider.execute（retrieval 済みルール＋コメント履歴を注入, §7.3）
    #    #21: タスクのオートノミー（L0-L3）と行動範囲ポリシーを読み、provider へ渡す
    #    #24: L1-L3 は on_delta でライブ実況する（artifact.delta / 中間コメント /
    #    受信文字数ベースの進捗を間引き配信）。L0（計画のみ）は成果物を作らないため
    #    実況しない。再試行（is_retry）では中間コメントを重複投稿しない。
    autonomy = AutonomyLevel(task_row["autonomy"])
    policy = tasks_repo.policy_from_row(task_row)
    async with pool.acquire() as conn:
        rule_rows = await rules_repo.get_rules_by_uuids(conn, job_row["applied_rule_ids"])
        history = await comments_repo.list_comments(conn, task_row)
    stream = _LiveStream(task_row, skip_comment=is_retry)
    result = await get_provider().execute(
        _task_prompt_dict(task_row),
        [rules_repo.rule_prompt_dict(row) for row in rule_rows],
        [{"who": c.author.value, "text": c.text} for c in history],
        policy=policy.model_dump(by_alias=True),
        plan_only=autonomy is AutonomyLevel.L0,
        on_delta=None if autonomy is AutonomyLevel.L0 else stream.on_delta,
    )

    # 2) 完了処理はオートノミーで分岐する（#21/#23）:
    #    - L0: 成果物は作らず、実行プランをコメントで渡して you_todo へハンドオフ
    #    - L1-L3: 成果物を新版として保存し、タスクは ai_work のまま review ジョブへ
    #      リレーする（#23 セルフレビュー）。you_review への遷移・完了コメント・
    #      L3 の done 連鎖は review ジョブが approve 判定後に行う。
    if autonomy is AutonomyLevel.L0:
        await _handoff_plan(job_id, task_row, result)
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
            current = TaskStatus(row["status"])
            if not can_transition(current, TaskStatus.YOU_REVIEW):
                # 完了ハンドオフ（review ジョブが行う）が成立しない状態なら保存しない
                raise RuntimeError(
                    f"invalid transition on job completion: {current} -> you_review"
                )
            artifact = await create_artifact(conn, row, result.content_md, job_id=job_id)
            # セルフレビュー（#23）: 同じ適用ルールを審査基準として review ジョブに引き継ぐ
            review_job_row = await ai_jobs_repo.create_job(
                conn,
                row,
                kind=AiJobKind.REVIEW,
                applied_rule_ids=list(job_row["applied_rule_ids"]),
            )
            # コスト実算定（#25）: execute は Pro 単価。usage はストリーム経路でも
            # provider が返す最終累計（唯一の真実）。mock でも同じ式で $ が動く
            await ai_jobs_repo.mark_succeeded(
                conn,
                job_id,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=calc_cost_usd(AiJobKind.EXECUTE, result.usage),
            )
    publish_event(ARTIFACT_CREATED, artifact.model_dump(mode="json", by_alias=True))
    if enqueue_next:
        # コミット後に enqueue（review ジョブは別コネクションで行を読むため）
        await jobs_queue.enqueue_job(
            str(review_job_row["id"]), kind=AiJobKind.REVIEW.value
        )


# ---- ライブ実況（#24）: delta 受信ごとの SSE 配信・進捗の間引き・中間コメント ----------


class _LiveStream:
    """AiProvider.execute の on_delta 受け口（1 execute 試行につき1インスタンス）。

    - 受信増分をそのまま artifact.delta で SSE 配信する（seq は 1 始まりの連番。
      再試行では新インスタンス = seq が 1 に戻り、FE はそこでライブ描画をリセットする）
    - 最初の delta で中間コメント（PROGRESS_COMMENT）を1回だけ投稿する。
      文言・名義（executor）は review.py の周回走査（_revise_cycle_count）が
      読み飛ばす条件と対なので、変えるときは両方を更新すること。
      skip_comment=True（再試行）では投稿しない（重複防止）。
    - 進捗 = min(PROGRESS_MAX, 受信文字数 // PROGRESS_CHARS_PER_PERCENT)。
      task.updated は PROGRESS_STEP_PERCENT 刻みが変わったときのみ配信する（間引き）。
    """

    def __init__(self, task_row: asyncpg.Record, *, skip_comment: bool) -> None:
        self._task_row = task_row
        self._chars = 0
        self._seq = 0
        self._commented = skip_comment
        self._published_step = 0

    async def on_delta(self, delta: str) -> None:
        self._seq += 1
        self._chars += len(delta)
        publish_event(
            ARTIFACT_DELTA,
            {"taskId": self._task_row["human_id"], "delta": delta, "seq": self._seq},
        )
        progress = min(PROGRESS_MAX, self._chars // PROGRESS_CHARS_PER_PERCENT)
        step = progress // PROGRESS_STEP_PERCENT
        if not self._commented:
            # 最初の delta: 中間コメント＋現在進捗を同時に反映（commentCount も同期）
            self._commented = True
            self._published_step = step
            await _post_progress_comment(self._task_row, progress)
            return
        if step > self._published_step:
            self._published_step = step
            await _publish_progress(self._task_row, progress)


async def _post_progress_comment(task_row: asyncpg.Record, progress: int) -> None:
    """中間コメント（PROGRESS_COMMENT）と現在進捗を1トランザクションで反映・配信する。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
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
        # §5.6 不変条件: progress を持てるのは ai_work のみ（並行操作で離脱していたら触らない）
        fields: dict[str, Any] = (
            {"progress": progress} if TaskStatus(row["status"]) is TaskStatus.AI_WORK else {}
        )
        task = await tasks_repo.apply_patch(conn, row, fields, actor="ai")
    publish_event(COMMENT_CREATED, comment.model_dump(mode="json", by_alias=True))
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


async def _publish_progress(task_row: asyncpg.Record, progress: int) -> None:
    """間引き済みの進捗を保存して task.updated を配信する（ai_work 以外では何もしない）。"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await tasks_repo.get_task_row(conn, task_row["human_id"], for_update=True)
        if TaskStatus(row["status"]) is not TaskStatus.AI_WORK:
            return  # 並行操作で ai_work を離れた（§5.6: progress は ai_work のみ）
        task = await tasks_repo.apply_patch(conn, row, {"progress": progress}, actor="ai")
    publish_event(TASK_UPDATED, task.model_dump(mode="json", by_alias=True))


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
                conn, row, {"status": TaskStatus.YOU_TODO, "progress": None}, actor="ai"
            )
            # コスト実算定（#25）: L0 のプラン生成も execute ジョブ = Pro 単価
            await ai_jobs_repo.mark_succeeded(
                conn,
                job_id,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=calc_cost_usd(AiJobKind.EXECUTE, result.usage),
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
            task = await tasks_repo.apply_patch(conn, row, fields, actor="ai")
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
