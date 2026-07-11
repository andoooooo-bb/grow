"""#21 タスク別オートノミー（L0-L3 ダイヤル）と行動範囲ポリシーのテスト。

- PATCH /tasks/:id での autonomy / policy（jsonb）の往復
- execute ジョブの分岐: L0（計画のみ→you_todo）/ L2（現状 L1 同挙動）/
  L3（done まで連鎖適用＋自動承認コメント）
- provider へのポリシー伝搬（allowWebSearch / plan_only）
- コスト上限（policy.costCapUsd）到達時の assign-ai 409 ＋ you_todo 戻し
"""

import json

import httpx
import pytest

from app.ai.mock_provider import MockProvider
from app.events import bus
from app.jobs import execute as execute_mod
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_events, drain_jobs

COST_CAP_COMMENT_1USD = (
    "コスト上限 $1 に達したため停止しました。上限を変更するか、人が引き継いでください。"
)


@pytest.fixture
def zero_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_mod, "PROGRESS_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "COMPLETE_DELAY_SEC", 0.0)
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（ジョブは手動で /internal/jobs/run を叩く。#23 連鎖も捕捉）。"""
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


class _RecordingProvider(MockProvider):
    """execute に渡された policy / plan_only を記録するモック（伝搬検証用）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, task, rules, comments, *, policy=None, plan_only=False):
        self.calls.append({"policy": policy, "plan_only": plan_only})
        return await super().execute(
            task, rules, comments, policy=policy, plan_only=plan_only
        )


# ---- PATCH: autonomy / policy（jsonb）の往復 ---------------------------------------


async def test_patch_autonomy_and_policy_roundtrip(api_client: httpx.AsyncClient) -> None:
    """PATCH で L0-L3 とポリシーを保存でき、DB（jsonb）とボード応答に反映される。"""
    res = await api_client.patch(
        "/api/tasks/T-104",
        json={"autonomy": "L3", "policy": {"allowWebSearch": False, "costCapUsd": 2.5}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["autonomy"] == "L3"
    assert body["policy"] == {"allowWebSearch": False, "costCapUsd": 2.5}

    conn = await db_connect()
    try:
        row = await conn.fetchrow(
            "select autonomy, policy from tasks where human_id = 'T-104'"
        )
        assert row["autonomy"] == "L3"
        assert json.loads(row["policy"]) == {"allowWebSearch": False, "costCapUsd": 2.5}
    finally:
        await conn.close()

    board = (await api_client.get("/api/board")).json()
    card = board["cards"]["T-104"]
    assert card["autonomy"] == "L3"
    assert card["policy"] == {"allowWebSearch": False, "costCapUsd": 2.5}


async def test_board_returns_defaults_for_untouched_tasks(
    api_client: httpx.AsyncClient,
) -> None:
    """未設定タスクは既定（L1・Web検索可・上限なし）で返る（policy jsonb '{}'）。"""
    board = (await api_client.get("/api/board")).json()
    card = board["cards"]["T-104"]
    assert card["autonomy"] == "L1"
    assert card["policy"] == {"allowWebSearch": True, "costCapUsd": None}


async def test_patch_rejects_invalid_autonomy_and_policy(
    api_client: httpx.AsyncClient,
) -> None:
    res = await api_client.patch("/api/tasks/T-104", json={"autonomy": "L9"})
    assert res.status_code == 422
    res = await api_client.patch("/api/tasks/T-104", json={"policy": {"costCapUsd": -1}})
    assert res.status_code == 422


# ---- execute 分岐: L0（計画のみ） ---------------------------------------------------


async def test_execute_l0_hands_off_plan_to_you_todo(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """L0: 成果物は作らず、実行プランのコメントで ai_work→you_todo へハンドオフ。"""
    await api_client.patch("/api/tasks/T-104", json={"autonomy": "L0"})
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    drain_events(event_queue)  # patch/assign 分のイベントは捨て、ジョブ分だけを検証する

    run = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert run.status_code == 200
    assert run.json() == {"status": "succeeded"}

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_todo"
        assert task["progress"] is None
        assert task["lane_key"] == "progress"  # レーンは動かさない（人のボールに変わるだけ）

        assert await conn.fetchval("select count(*) from artifacts") == 0  # 成果物なし

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert len(comments) == 3  # 着手 → 中間 → プランハンドオフ
        assert comments[2]["text"].startswith(
            "実行プランを作成しました。オートノミーL0（計画のみ）のため、実行せずここで停止します。"
        )
        assert "実行プラン" in comments[2]["text"]
        assert "## 進め方（案）" in comments[2]["text"]
        assert comments[2]["agent_role"] == "executor"

        job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert job["status"] == "succeeded"
        assert job["input_tokens"] > 0
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "comment.created",  # 中間
        "task.updated",  # progress 45
        "comment.created",  # プランハンドオフ
        "task.updated",  # you_todo
    ]
    assert events[-1]["payload"]["status"] == "you_todo"
    assert events[-1]["payload"]["autonomy"] == "L0"
    assert events[-1]["payload"]["progress"] is None


# ---- execute 分岐: L2 は現段階では L1 と同挙動（#22 の接続点） ----------------------


async def test_execute_l2_behaves_like_l1_for_now(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """L2: assign-ai 起点では L1 と同じ（you_review・review レーン・成果物あり）。"""
    await api_client.patch("/api/tasks/T-104", json={"autonomy": "L2"})
    await api_client.post("/api/tasks/T-104/assign-ai")
    await drain_jobs(api_client, captured_jobs)  # execute→review 連鎖（#23）を完走

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "you_review"
        assert task["lane_key"] == "review"
        # 1回の revise を経て v1/v2 の2版（#23）
        assert await conn.fetchval("select count(*) from artifacts") == 2
    finally:
        await conn.close()


# ---- execute 分岐: L3（全自動・事後レビュー） ---------------------------------------


async def test_execute_l3_chains_to_done_with_auto_approve_comment(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """L3: セルフレビュー（#23）の approve 後に you_review→done を連鎖適用（自動承認）。"""
    await api_client.patch("/api/tasks/T-104", json={"autonomy": "L3"})
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    drain_events(event_queue)

    await drain_jobs(api_client, captured_jobs)  # execute→review→execute→review

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "done"
        assert task["lane_key"] == "done"
        assert task["progress"] is None

        # 成果物は通常どおり保存される（事後レビューできる。revise を経て2版 #23）
        artifacts = await conn.fetch(
            "select * from artifacts where task_id = $1 order by version", task["id"]
        )
        assert len(artifacts) == 2
        assert str(artifacts[0]["job_id"]) == job_id

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        # 着手 → 中間 → 指摘(r) → 中間 → 承認(r) → 完了 → 自動承認
        assert len(comments) == 7
        assert comments[6]["text"] == (
            "ポリシーL3により自動承認しました。内容は事後確認できます。"
        )
        assert comments[6]["agent_role"] == "executor"

        job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert job["status"] == "succeeded"
    finally:
        await conn.close()

    # SSE: 承認（reviewer）→ 完了 → you_review → 自動承認 → done の順で経緯が残る
    events = drain_events(event_queue)
    assert [e["type"] for e in events][-5:] == [
        "comment.created",  # 承認（reviewer）
        "comment.created",  # 完了（executor）
        "task.updated",  # you_review
        "comment.created",  # 自動承認（executor）
        "task.updated",  # done
    ]
    assert events[-3]["payload"]["status"] == "you_review"
    assert events[-1]["payload"]["status"] == "done"
    assert events[-1]["payload"]["laneKey"] == "done"


# ---- provider へのポリシー伝搬 ------------------------------------------------------


async def test_execute_passes_policy_and_plan_only_to_provider(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute ジョブは tasks.policy を camelCase dict で provider へ渡す（L0 は plan_only）。"""
    provider = _RecordingProvider()
    monkeypatch.setattr(execute_mod, "get_provider", lambda: provider)

    await api_client.patch(
        "/api/tasks/T-104",
        json={"autonomy": "L0", "policy": {"allowWebSearch": False, "costCapUsd": 3.0}},
    )
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    await api_client.post("/internal/jobs/run", json={"jobId": res.json()["jobId"]})

    assert provider.calls == [
        {"policy": {"allowWebSearch": False, "costCapUsd": 3.0}, "plan_only": True}
    ]


# ---- コスト上限（policy.costCapUsd） -----------------------------------------------


async def _seed_job_cost(human_id: str, cost_usd: float) -> None:
    """既存ジョブの累計コストを作る（mock は cost 0.0 のため直接挿入）。"""
    conn = await db_connect()
    try:
        task_id = await conn.fetchval(
            "select id from tasks where human_id = $1", human_id
        )
        await conn.execute(
            "insert into ai_jobs (task_id, kind, status, cost_usd, finished_at) "
            "values ($1, 'execute', 'succeeded', $2, now())",
            task_id,
            cost_usd,
        )
    finally:
        await conn.close()


async def test_assign_ai_stops_at_cost_cap_and_hands_off(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """上限到達: 409＋停止理由コメント＋you_todo 戻し。ジョブは作らない。

    T-089 は reviewing（→ai_work 可・→you_todo 可）なので「人へ戻す」が観測できる。
    """
    await _seed_job_cost("T-089", 1.2)
    await api_client.patch(
        "/api/tasks/T-089", json={"policy": {"allowWebSearch": True, "costCapUsd": 1.0}}
    )
    drain_events(event_queue)

    res = await api_client.post("/api/tasks/T-089/assign-ai")
    assert res.status_code == 409
    assert "cost cap reached" in res.json()["detail"]
    assert captured_jobs == []

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-089'")
        assert task["status"] == "you_todo"  # reviewing → you_todo（人へハンドオフ）
        assert task["progress"] is None

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert len(comments) == 1  # 着手コメントは投稿されない（停止コメントのみ）
        assert comments[0]["text"] == COST_CAP_COMMENT_1USD
        assert comments[0]["agent_role"] == "executor"

        # 新規ジョブは作られない（累計コスト用に挿入した1件のみ）
        count = await conn.fetchval(
            "select count(*) from ai_jobs where task_id = $1", task["id"]
        )
        assert count == 1
        # retrieval / applied++ も発生しない
        assert await conn.fetchval("select count(*) from rule_applications") == 0
    finally:
        await conn.close()

    # SSE: 停止理由コメント → you_todo 戻し（既存の失敗ハンドオフと同型）
    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["comment.created", "task.updated"]
    assert events[0]["payload"]["text"] == COST_CAP_COMMENT_1USD
    assert events[1]["payload"]["status"] == "you_todo"


async def test_cost_cap_keeps_status_when_you_todo_not_reachable(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """spec など you_todo へ遷移できない status では現状維持で停止する（§7.2 と同型）。"""
    await _seed_job_cost("T-104", 2.0)
    await api_client.patch(
        "/api/tasks/T-104", json={"policy": {"allowWebSearch": True, "costCapUsd": 1.0}}
    )

    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 409
    assert captured_jobs == []

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "spec"  # spec → you_todo は不許可なので現状維持
        text = await conn.fetchval(
            "select text from comments where task_id = $1", task["id"]
        )
        assert text == COST_CAP_COMMENT_1USD
    finally:
        await conn.close()


async def test_assign_ai_proceeds_when_under_cost_cap(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """累計コストが上限未満なら通常どおり 202 で enqueue する。"""
    await _seed_job_cost("T-104", 0.4)
    await api_client.patch(
        "/api/tasks/T-104", json={"policy": {"allowWebSearch": True, "costCapUsd": 1.0}}
    )
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202
    assert len(captured_jobs) == 1
