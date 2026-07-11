"""execute ジョブ（app/jobs/execute.py, §7.2/§7.3）のテスト。

リトライバックオフは 0 秒に差し替え、
worker エンドポイント POST /internal/jobs/run 経由で実行を検証する。
local ランナー（asyncio.create_task）経路は test_local_runner_completes_job で検証。
#23 以降、execute は成果物保存後に review ジョブを enqueue する（セルフレビュー連鎖）。
mock は初回成果物を必ず1回 revise するため、完走 = execute→review→execute→review。
#24 以降、進行は本物のストリーミング実況（artifact.delta ＋ 受信文字数ベースの進捗の
間引き配信）。擬似ディレイ・固定45%は廃止された。
"""

from uuid import uuid4

import httpx
import pytest

from app.config import get_settings
from app.events import bus
from app.jobs import execute as execute_mod
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_events, drain_jobs


@pytest.fixture
def zero_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（ジョブは手動で /internal/jobs/run を叩く）。

    execute → review → execute … の連鎖 enqueue（#23）もここに追記される。
    """
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


class _FailingProvider:
    """execute が常に失敗するモック（リトライ検証用）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task: dict, rules: list, comments: list, **kwargs):
        self.calls += 1
        raise RuntimeError("模擬的な失敗: provider が応答しません")


async def test_execute_job_success(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """完走（#23 セルフレビュー連鎖込み）: execute → review(revise) → execute → review(approve)。

    外部最終状態は #23 以前と同じ: you_review・review レーン・progress null・
    最後のコメントは実行AIの完了ハンドオフ。途中にレビューAIの指摘→修正が挟まる。
    """
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    drain_events(event_queue)  # assign 分のイベントは捨て、ジョブ分だけを検証する

    await drain_jobs(api_client, captured_jobs)
    # execute → review → execute（revise 再実行）→ review（approve）の4ジョブ連鎖
    assert len(captured_jobs) == 4

    conn = await db_connect()
    try:
        # you_review・progress null（§5.6 不変条件）・review レーン末尾（T-091, T-089 の後ろ）
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_review"
        assert task["progress"] is None
        assert task["lane_key"] == "review"
        assert task["order_in_lane"] == 2

        # コメント: 着手 → 中間 → レビュー指摘 → 中間（修正）→ 承認 → 完了
        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            comments[0]["text"],  # 着手（文言はルール数依存。role で検証）
            "作業を進めています…（途中経過を共有します）",
            comments[2]["text"],  # レビュー指摘（下で内容検証）
            "作業を進めています…（途中経過を共有します）",
            "セルフレビューを実施しました。適用ルールに照らして問題ありません。",
            "完了しました。学習済みのルールに沿って仕上げています。レビューをお願いします。",
        ]
        assert [c["agent_role"] for c in comments] == [
            "executor",
            "executor",
            "reviewer",  # AIがAIの成果物を突き返す（#23）
            "executor",
            "reviewer",
            "executor",
        ]
        # 指摘は適用ルールを審査基準として引用する（【レビュー指摘】マーカー付き。
        # 「出典」を含む先頭ルール = K-04 を引用する）
        assert "【レビュー指摘】" in comments[2]["text"]
        assert "社外向け文書は敬体。数値は必ず出典を明記する" in comments[2]["text"]

        # artifacts: v1（指摘前）と v2（修正版・レビュー対応セクション付き）
        artifacts = await conn.fetch(
            "select * from artifacts where task_id = $1 order by version", task["id"]
        )
        assert [a["version"] for a in artifacts] == [1, 2]
        assert str(artifacts[0]["job_id"]) == job_id
        assert "競合SaaS 5社の料金プランを調査" in artifacts[0]["content_md"]
        assert "絵文字は使わない。文体は簡潔・断定調に統一する" in artifacts[0]["content_md"]
        assert "## レビュー対応" not in artifacts[0]["content_md"]
        assert "## レビュー対応" in artifacts[1]["content_md"]

        # ai_jobs: execute → review → execute → review がすべて succeeded ＋ トークン記録
        jobs = await conn.fetch(
            "select * from ai_jobs where task_id = $1 order by created_at", task["id"]
        )
        assert [j["kind"] for j in jobs] == ["execute", "review", "execute", "review"]
        assert all(j["status"] == "succeeded" for j in jobs)
        assert str(jobs[0]["id"]) == job_id
        for job in jobs:
            assert job["input_tokens"] > 0
            assert job["output_tokens"] > 0
            # #25 コスト実算定: mock でも単価テーブル（execute=Pro / review=Flash）で $ が動く
            assert float(job["cost_usd"]) >= 0.0
            assert job["finished_at"] is not None
            assert job["error"] is None
        # execute（Pro 単価・数百トークン規模）は numeric(10,4) への丸め後も必ず正になる
        assert all(float(j["cost_usd"]) > 0.0 for j in jobs if j["kind"] == "execute")
        # review は execute と同じ適用ルールを審査基準として引き継ぐ
        assert jobs[1]["applied_rule_ids"] == jobs[0]["applied_rule_ids"]
        assert len(jobs[0]["applied_rule_ids"]) == 4  # K-01/K-02/K-03/K-04
    finally:
        await conn.close()

    # SSE（#24）: execute（中間コメント＋delta 実況＋進捗→v1）→ review 指摘
    #            → execute（中間＋実況→v2）→ 承認→完了→you_review
    events = drain_events(event_queue)

    # 骨格（実況イベント = artifact.delta と ai_work 中の進捗 task.updated を除く）は
    # #23 と同じ列で維持される
    skeleton = [
        e
        for e in events
        if e["type"] != "artifact.delta"
        and not (e["type"] == "task.updated" and e["payload"]["status"] == "ai_work")
    ]
    assert [e["type"] for e in skeleton] == [
        "comment.created",  # 中間（最初の delta 受信時）
        "artifact.created",  # v1
        "comment.created",  # レビュー指摘（reviewer）
        "comment.created",  # 中間（修正）
        "artifact.created",  # v2
        "comment.created",  # 承認（reviewer）
        "comment.created",  # 完了（executor）
        "task.updated",  # you_review
    ]
    assert skeleton[0]["payload"]["text"] == execute_mod.PROGRESS_COMMENT
    assert skeleton[1]["payload"]["version"] == 1
    assert skeleton[1]["payload"]["jobId"] == job_id
    assert skeleton[2]["payload"]["agentRole"] == "reviewer"
    assert "【レビュー指摘】" in skeleton[2]["payload"]["text"]
    assert skeleton[4]["payload"]["version"] == 2
    assert skeleton[5]["payload"]["agentRole"] == "reviewer"
    assert events[-1]["payload"]["status"] == "you_review"
    assert events[-1]["payload"]["progress"] is None
    assert events[-1]["payload"]["laneKey"] == "review"

    # ライブ実況（#24）: v1 / v2 それぞれのストリームで delta が seq 昇順（1始まり）で届き、
    # 増分の連結が確定版 contentMd と一致する（mock は出典付加なし = 本文が全増分）
    deltas = [e["payload"] for e in events if e["type"] == "artifact.delta"]
    assert all(d["taskId"] == "T-104" for d in deltas)
    restart = next(i for i, d in enumerate(deltas) if i > 0 and d["seq"] == 1)
    v1_deltas, v2_deltas = deltas[:restart], deltas[restart:]
    assert len(v1_deltas) >= 2  # 数チャンクに分割されている
    assert [d["seq"] for d in v1_deltas] == list(range(1, len(v1_deltas) + 1))
    assert [d["seq"] for d in v2_deltas] == list(range(1, len(v2_deltas) + 1))
    created = [e["payload"] for e in events if e["type"] == "artifact.created"]
    assert "".join(d["delta"] for d in v1_deltas) == created[0]["contentMd"]
    assert "".join(d["delta"] for d in v2_deltas) == created[1]["contentMd"]

    # 進捗（v1 ストリーム中の task.updated）: 受信文字数ベースで単調増加・上限 95・
    # delta ごとには配信しない（5% 刻みの間引き）
    first_created = next(i for i, e in enumerate(events) if e["type"] == "artifact.created")
    v1_progress = [
        e["payload"]["progress"]
        for e in events[:first_created]
        if e["type"] == "task.updated"
    ]
    assert len(v1_progress) >= 1  # 最初の delta で必ず1回配信される
    assert v1_progress == sorted(v1_progress)  # 単調増加（同値の再配信もない）
    assert len(set(v1_progress)) == len(v1_progress)
    assert all(0 <= p <= execute_mod.PROGRESS_MAX for p in v1_progress)
    assert len(v1_progress) < len(v1_deltas)  # 間引きされている


async def test_live_stream_throttles_progress_and_comments_once(
    event_queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_LiveStream 単体（#24）: artifact.delta は毎回・中間コメントは初回のみ・
    進捗の task.updated は5%刻みが変わったときのみ（同値スキップ）。"""
    comment_calls: list[int] = []
    progress_calls: list[int] = []

    async def fake_comment(task_row, progress: int) -> None:
        comment_calls.append(progress)

    async def fake_progress(task_row, progress: int) -> None:
        progress_calls.append(progress)

    monkeypatch.setattr(execute_mod, "_post_progress_comment", fake_comment)
    monkeypatch.setattr(execute_mod, "_publish_progress", fake_progress)

    # PROGRESS_CHARS_PER_PERCENT=40 / PROGRESS_STEP_PERCENT=5 → 5% = 200文字
    stream = execute_mod._LiveStream({"human_id": "T-104"}, skip_comment=False)
    await stream.on_delta("a" * 100)  # 2% → 初回: 中間コメント（進捗込み）
    await stream.on_delta("a" * 50)  # 3% → 同じ 0-5% 帯 → スキップ（同値スキップ）
    await stream.on_delta("a" * 100)  # 6% → 5% 帯を跨いだ → 配信
    await stream.on_delta("a" * 40)  # 7% → 同じ帯 → スキップ
    await stream.on_delta("a" * 4000)  # 107% → 上限 95 で配信
    await stream.on_delta("a" * 400)  # それ以上は 95 のまま → スキップ

    assert comment_calls == [2]  # 中間コメントは最初の delta で1回だけ
    assert progress_calls == [6, 95]  # 間引き＋上限クランプ

    # artifact.delta 自体は全 delta ぶん配信される（seq は 1 始まりの連番）
    deltas = [e for e in drain_events(event_queue) if e["type"] == "artifact.delta"]
    assert [d["payload"]["seq"] for d in deltas] == [1, 2, 3, 4, 5, 6]
    assert all(d["payload"]["taskId"] == "T-104" for d in deltas)
    assert deltas[0]["payload"]["delta"] == "a" * 100

    # 再試行（skip_comment=True）: 中間コメントは再投稿しない
    comment_calls.clear()
    progress_calls.clear()
    retry_stream = execute_mod._LiveStream({"human_id": "T-104"}, skip_comment=True)
    await retry_stream.on_delta("a" * 100)  # 2% → 0-5% 帯のまま → 何も配信しない
    await retry_stream.on_delta("a" * 200)  # 7% → 帯を跨いだ → 進捗のみ配信
    assert comment_calls == []
    assert progress_calls == [7]


async def test_local_runner_completes_job(
    api_client: httpx.AsyncClient, zero_delays
) -> None:
    """JOB_RUNNER=local: assign-ai だけで execute→review 連鎖が create_task 経由で完走する。"""
    assert get_settings().job_runner == "local"
    res = await api_client.post("/api/tasks/T-121/assign-ai")
    assert res.status_code == 202
    await jobs_queue.drain_local_jobs()

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-121'")
        assert task["status"] == "you_review"
        assert task["lane_key"] == "review"
        version = await conn.fetchval(
            "select max(version) from artifacts where task_id = $1", task["id"]
        )
        assert version == 2  # 1回の revise を経た修正版が最新（#23）
        statuses = await conn.fetch(
            "select kind, status from ai_jobs where task_id = $1 order by created_at",
            task["id"],
        )
        assert [(r["kind"], r["status"]) for r in statuses] == [
            ("execute", "succeeded"),
            ("review", "succeeded"),
            ("execute", "succeeded"),
            ("review", "succeeded"),
        ]
    finally:
        await conn.close()


async def test_execute_job_failure_retries_then_handoff(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    event_queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最終失敗: 最大2回リトライ（計3試行）→ you_todo ＋ 失敗コメント ＋ ai_jobs failed。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    provider = _FailingProvider()
    monkeypatch.setattr(execute_mod, "get_provider", lambda: provider)
    drain_events(event_queue)

    run = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert run.status_code == 200
    assert run.json() == {"status": "failed"}
    assert provider.calls == 3  # 初回 + 2回リトライ

    conn = await db_connect()
    try:
        # you_todo へ戻る（再試行導線 = 再度「AIにまかせる」できる状態）・progress null
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_todo"
        assert task["progress"] is None

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        # 着手 → 失敗（#24: 中間コメントは最初の delta 受信時に投稿されるため、
        # delta が届く前に失敗するプロバイダでは投稿されない）
        assert len(comments) == 2
        assert comments[1]["text"] == (
            "作業中にエラーが発生しました。内容を確認のうえ、再度お任せください。"
            "（理由: 模擬的な失敗: provider が応答しません）"
        )

        job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert job["status"] == "failed"
        assert "模擬的な失敗" in job["error"]
        assert job["finished_at"] is not None

        assert await conn.fetchval("select count(*) from artifacts") == 0
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert events[-2]["type"] == "comment.created"
    assert "作業中にエラーが発生しました" in events[-2]["payload"]["text"]
    assert events[-1]["type"] == "task.updated"
    assert events[-1]["payload"]["status"] == "you_todo"
    assert events[-1]["payload"]["progress"] is None


async def test_run_job_idempotent_after_success(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """確定済みジョブの再実行（Cloud Tasks の二重配信相当）は no-op。

    execute の再実行で review ジョブ・成果物が二重に作られないこと（#23）も含む。
    """
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    await drain_jobs(api_client, captured_jobs)  # 連鎖（execute×2 + review×2）を完走

    rerun = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert rerun.status_code == 200
    assert rerun.json() == {"status": "succeeded"}

    conn = await db_connect()
    try:
        assert await conn.fetchval("select count(*) from artifacts") == 2
        assert await conn.fetchval("select count(*) from comments") == 6
        assert (
            await conn.fetchval("select count(*) from ai_jobs where kind = 'review'") == 2
        )
    finally:
        await conn.close()


async def test_run_job_unknown_or_invalid_id(
    api_client: httpx.AsyncClient, zero_delays
) -> None:
    res = await api_client.post("/internal/jobs/run", json={"jobId": str(uuid4())})
    assert res.status_code == 404
    res = await api_client.post("/internal/jobs/run", json={"jobId": "not-a-uuid"})
    assert res.status_code == 422


async def test_cloud_tasks_mode_returns_5xx_until_last_attempt(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JOB_RUNNER=cloud_tasks: 失敗は 5xx（Cloud Tasks が再試行）。最終試行のみハンドオフ。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    provider = _FailingProvider()
    monkeypatch.setattr(execute_mod, "get_provider", lambda: provider)

    monkeypatch.setenv("JOB_RUNNER", "cloud_tasks")
    get_settings.cache_clear()
    try:
        # 再試行余地あり → 500 を返し、ジョブは failed 確定させない
        r1 = await api_client.post(
            "/internal/jobs/run",
            json={"jobId": job_id},
            headers={"X-CloudTasks-TaskRetryCount": "0"},
        )
        assert r1.status_code == 500
        assert provider.calls == 1  # ジョブ内リトライはしない

        conn = await db_connect()
        try:
            assert (
                await conn.fetchval("select status from ai_jobs where id = $1::uuid", job_id)
                == "running"
            )
        finally:
            await conn.close()

        # 最終試行（再試行上限到達）→ ハンドオフして 200
        r2 = await api_client.post(
            "/internal/jobs/run",
            json={"jobId": job_id},
            headers={"X-CloudTasks-TaskRetryCount": "3"},
        )
        assert r2.status_code == 200
        assert r2.json() == {"status": "failed"}

        conn = await db_connect()
        try:
            job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
            assert job["status"] == "failed"
            task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
            assert task["status"] == "you_todo"
        finally:
            await conn.close()
    finally:
        monkeypatch.delenv("JOB_RUNNER")
        get_settings.cache_clear()
