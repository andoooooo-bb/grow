"""#23 セルフレビューと構造化差し戻しループのテスト。

- kind='review' の registry 登録（#18）
- 人の構造化差し戻し POST /tasks/:id/reject:
  理由コメント → 矛盾ルールの confidence 降格（RULE_UPDATED ＋ distiller コメント）
  → execute 再実行 → 成果物に理由が反映 → レビューAIが approve → you_review
- reject のバリデーション（404 / 409 / 422）
- revise 自動再実行の2周上限（上限到達で approve 扱い＋上限コメント）
- review ジョブ失敗時のハンドオフ（成果物ごと人のレビューへ）

execute→review の自動連鎖の基本形（revise 1回 → 修正 → approve）は
test_execute_job.py::test_execute_job_success が担う。
"""

import httpx
import pytest

from app.ai.mock_provider import MockProvider
from app.ai.provider import ReviewResult, TokenUsage
from app.domain.models import AiJobKind
from app.events import bus
from app.jobs import execute as execute_mod
from app.jobs import queue as jobs_queue
from app.jobs import registry
from app.jobs import review as review_mod
from app.jobs.review import run_review_job_row
from tests.helpers import db_connect, drain_events, drain_jobs

# 差し戻し理由（「ルール」「逆」を含む = mock の矛盾検出が先頭ルールを返す）
CONFLICT_REASON = "比較表は不要です。ルールとは逆に、箇条書きでまとめてください"
# 矛盾に触れない理由（降格は起きない）
PLAIN_REASON = "誤字が多いので修正してください"


@pytest.fixture
def zero_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_mod, "PROGRESS_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "COMPLETE_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（execute→review→execute の連鎖もここに積まれる）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str, *, kind: str | None = None) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


async def _complete_execution(
    api_client: httpx.AsyncClient, captured_jobs: list[str], human_id: str = "T-104"
) -> None:
    """assign-ai → execute/review 連鎖を完走させ you_review にする（reject の前提）。"""
    res = await api_client.post(f"/api/tasks/{human_id}/assign-ai")
    assert res.status_code == 202
    await drain_jobs(api_client, captured_jobs)


# ---- 登録（#18 registry） -----------------------------------------------------------


def test_review_kind_is_registered() -> None:
    """kind='review' は registry 経由で worker/local 両対応になっている。"""
    assert registry.get_handler(AiJobKind.REVIEW) is run_review_job_row


# ---- 人の構造化差し戻し（reject） ----------------------------------------------------


async def test_reject_reruns_execute_with_reason_and_downgrades_rule(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """差し戻し → 理由コメント → 矛盾ルール降格 → 再実行の成果物に理由反映 → you_review。"""
    await _complete_execution(api_client, captured_jobs)
    drain_events(event_queue)

    res = await api_client.post(
        "/api/tasks/T-104/reject", json={"reason": CONFLICT_REASON}
    )
    assert res.status_code == 202
    reject_job_id = res.json()["jobId"]
    assert captured_jobs[-1] == reject_job_id

    # SSE: 理由コメント → 降格（rule.updated + distiller コメント）→ 着手 → ai_work
    events = drain_events(event_queue)
    types = [e["type"] for e in events]
    assert types[:5] == [
        "comment.created",  # 【差し戻し理由】（human）
        "task.updated",  # commentCount 同期
        "rule.updated",  # K-02 の確度降格（#23）
        "comment.created",  # 降格の説明（distiller）
        "task.updated",
    ]
    assert events[0]["payload"]["author"] == "human"
    assert events[0]["payload"]["text"] == f"【差し戻し理由】{CONFLICT_REASON}"
    assert events[2]["payload"]["id"] == "K-02"
    assert events[2]["payload"]["confidence"] == "med"  # high → med（1段降格）
    assert events[3]["payload"]["agentRole"] == "distiller"
    assert "確度を下げました" in events[3]["payload"]["text"]
    # 再実行の開始（着手コメント → ai_work 遷移）も配信される
    ai_work_updates = [
        e for e in events if e["type"] == "task.updated" and e["payload"]["status"] == "ai_work"
    ]
    assert len(ai_work_updates) == 1

    # 再実行チェーン（execute → review）を完走させる
    await drain_jobs(api_client, captured_jobs)

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_review"  # 差し戻し既往ありは approve（mock）
        assert task["lane_key"] == "review"

        # 矛盾ルール（前回適用の先頭 = K-02）が high → med に降格
        assert (
            await conn.fetchval("select confidence from rules where human_id = 'K-02'")
            == "med"
        )

        # 再実行の成果物（最新版）に差し戻し理由の全文が反映されている（#23 DoD）
        latest_md = await conn.fetchval(
            "select content_md from artifacts where task_id = $1 "
            "order by version desc limit 1",
            task["id"],
        )
        assert "## 差し戻し対応" in latest_md
        assert CONFLICT_REASON in latest_md

        # コメント: … 完了 → 【差し戻し理由】(human) → 降格(distiller) → 着手(e)
        #          → 進捗(e) → 承認(r) → 完了(e)
        comments = await conn.fetch(
            "select author, agent_role, text from comments where task_id = $1 "
            "order by created_at, id",
            task["id"],
        )
        assert [(c["author"], c["agent_role"]) for c in comments[-6:]] == [
            ("human", None),  # 【差し戻し理由】
            ("ai", "distiller"),  # 確度降格の説明
            ("ai", "executor"),  # 再着手
            ("ai", "executor"),  # 進捗
            ("ai", "reviewer"),  # 承認（差し戻し対応済み）
            ("ai", "executor"),  # 完了ハンドオフ
        ]
        assert comments[-6]["text"] == f"【差し戻し理由】{CONFLICT_REASON}"
        assert comments[-5]["agent_role"] == "distiller"
        assert "絵文字は使わない" in comments[-5]["text"]  # 降格対象ルールの本文を引用
        assert comments[-2]["text"] == review_mod.APPROVE_COMMENT
        assert comments[-2]["agent_role"] == "reviewer"
        assert comments[-1]["text"] == execute_mod.COMPLETE_COMMENT

        # ジョブ列: (初回) execute, review, execute, review → (reject) execute, review
        jobs = await conn.fetch(
            "select kind, status from ai_jobs where task_id = $1 order by created_at",
            task["id"],
        )
        assert [j["kind"] for j in jobs] == [
            "execute",
            "review",
            "execute",
            "review",
            "execute",
            "review",
        ]
        assert all(j["status"] == "succeeded" for j in jobs)
    finally:
        await conn.close()


async def test_reject_without_conflict_keeps_rule_confidence(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """矛盾に触れない理由では降格しない（rule.updated は applied++ 分のみ）。"""
    await _complete_execution(api_client, captured_jobs)
    drain_events(event_queue)

    res = await api_client.post("/api/tasks/T-104/reject", json={"reason": PLAIN_REASON})
    assert res.status_code == 202

    events = drain_events(event_queue)
    # 降格の distiller コメントは出ない
    distiller = [
        e
        for e in events
        if e["type"] == "comment.created" and e["payload"].get("agentRole") == "distiller"
    ]
    assert distiller == []

    conn = await db_connect()
    try:
        confidences = await conn.fetch(
            "select human_id, confidence from rules order by human_id"
        )
        assert all(
            r["confidence"] in ("high", "med") for r in confidences
        )  # seed から変化なし
        assert (
            await conn.fetchval("select confidence from rules where human_id = 'K-02'")
            == "high"
        )
    finally:
        await conn.close()


async def test_reject_validation_404_409_422(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """reject は you_review / reviewing のみ。理由は必須。"""
    res = await api_client.post("/api/tasks/T-999/reject", json={"reason": "x"})
    assert res.status_code == 404

    # queued（T-112）はレビュー局面ではないので 409
    res = await api_client.post("/api/tasks/T-112/reject", json={"reason": "x"})
    assert res.status_code == 409

    # done（T-080）も 409（レビュー済みの差し戻しは再オープン操作で行う）
    res = await api_client.post("/api/tasks/T-080/reject", json={"reason": "x"})
    assert res.status_code == 409

    # 理由なし・空白のみは 422
    res = await api_client.post("/api/tasks/T-091/reject", json={})
    assert res.status_code == 422
    res = await api_client.post("/api/tasks/T-091/reject", json={"reason": "   "})
    assert res.status_code == 422

    assert captured_jobs == []  # どれもジョブは作られない


async def test_reject_from_reviewing_status(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """reviewing（T-089）からも差し戻せる（reviewing→ai_work は §5.6 の差し戻し遷移）。"""
    res = await api_client.post("/api/tasks/T-089/reject", json={"reason": PLAIN_REASON})
    assert res.status_code == 202

    await drain_jobs(api_client, captured_jobs)

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-089'")
        assert task["status"] == "you_review"  # 再実行 → approve → 人のレビューへ
        latest_md = await conn.fetchval(
            "select content_md from artifacts where task_id = $1 "
            "order by version desc limit 1",
            task["id"],
        )
        assert PLAIN_REASON in latest_md  # 理由は最優先制約として成果物に反映
    finally:
        await conn.close()


# ---- revise 自動再実行の2周上限 ------------------------------------------------------


class _AlwaysReviseProvider(MockProvider):
    """review_artifact が常に revise を返す（上限到達の検証用）。"""

    async def review_artifact(self, task, artifact_md, rules) -> ReviewResult:
        return ReviewResult(
            verdict="revise",
            findings=["まだ基準を満たしていません。再修正してください"],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


async def test_revise_loop_stops_at_cycle_cap(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revise が続いても最大2周で approve 扱いにし、上限コメントで人へ渡す。"""
    monkeypatch.setattr(review_mod, "get_provider", lambda: _AlwaysReviseProvider())

    await api_client.post("/api/tasks/T-104/assign-ai")
    await drain_jobs(api_client, captured_jobs)

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_review"  # 上限到達でも最終状態は人のレビュー待ち
        assert task["lane_key"] == "review"

        # execute×3（初回＋revise×2）/ review×3（revise, revise, 上限で approve 扱い）
        jobs = await conn.fetch(
            "select kind from ai_jobs where task_id = $1 order by created_at", task["id"]
        )
        assert [j["kind"] for j in jobs] == [
            "execute",
            "review",
            "execute",
            "review",
            "execute",
            "review",
        ]
        assert await conn.fetchval("select count(*) from artifacts") == 3

        comments = await conn.fetch(
            "select agent_role, text from comments where task_id = $1 "
            "order by created_at, id",
            task["id"],
        )
        # 指摘（【レビュー指摘】付き）はちょうど2回 = MAX_REVIEW_CYCLES
        findings = [c for c in comments if "【レビュー指摘】" in c["text"]]
        assert len(findings) == review_mod.MAX_REVIEW_CYCLES
        # 最後は上限コメント（reviewer）→ 完了ハンドオフ（executor）
        assert comments[-2]["text"] == review_mod.CYCLE_LIMIT_COMMENT
        assert comments[-2]["agent_role"] == "reviewer"
        assert comments[-1]["text"] == execute_mod.COMPLETE_COMMENT
        assert comments[-1]["agent_role"] == "executor"
    finally:
        await conn.close()


# ---- review ジョブの失敗ハンドオフ ---------------------------------------------------


class _BrokenReviewProvider(MockProvider):
    """review_artifact が常に失敗する（失敗ハンドオフの検証用）。"""

    async def review_artifact(self, task, artifact_md, rules) -> ReviewResult:
        raise RuntimeError("模擬的な失敗: レビューAIが応答しません")


async def test_review_failure_hands_artifact_to_human_review(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review 失敗: ai_jobs=failed ＋ REVIEWER コメント ＋ 成果物ごと you_review へ。"""
    monkeypatch.setattr(review_mod, "get_provider", lambda: _BrokenReviewProvider())

    res = await api_client.post("/api/tasks/T-104/assign-ai")
    exec_job_id = res.json()["jobId"]
    run = await api_client.post("/internal/jobs/run", json={"jobId": exec_job_id})
    assert run.json() == {"status": "succeeded"}  # execute 自体は成功（成果物保存済み）

    review_job_id = captured_jobs[-1]
    run = await api_client.post("/internal/jobs/run", json={"jobId": review_job_id})
    assert run.json() == {"status": "failed"}

    conn = await db_connect()
    try:
        job = await conn.fetchrow(
            "select * from ai_jobs where id = $1::uuid", review_job_id
        )
        assert job["kind"] == "review"
        assert job["status"] == "failed"
        assert "模擬的な失敗" in job["error"]

        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_review"  # 成果物は保存済みなので人のレビューで補完
        assert task["lane_key"] == "review"
        assert await conn.fetchval("select count(*) from artifacts") == 1

        last = await conn.fetchrow(
            "select agent_role, text from comments where task_id = $1 "
            "order by created_at desc, id desc limit 1",
            task["id"],
        )
        assert last["agent_role"] == "reviewer"
        assert "セルフレビュー中にエラーが発生しました" in last["text"]
    finally:
        await conn.close()
