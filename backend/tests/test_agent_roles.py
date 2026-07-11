"""#19 エージェント編成の見える化 — agent_role 保存・各投稿箇所の役割付与・jobs API。

- comments.agent_role の保存/取得（camelCase agentRole で API 往復）
- 役割付与: assign-ai・execute ジョブ=executor / breakdown 反映=planner / 蒸留採用=distiller
- GET /api/tasks/{human_id}/jobs（created_at 昇順のリレー履歴）
"""

import httpx
import pytest

from app.events import bus
from app.jobs import execute as execute_mod
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_events, drain_jobs


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（ジョブは必要なら /internal/jobs/run を手動で叩く）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str, *, kind: str | None = None) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


@pytest.fixture
def zero_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


async def _agent_roles(client_conn, human_id: str) -> list[tuple[str, str | None]]:
    """タスクのコメントを (author, agent_role) の時系列で返す。"""
    rows = await client_conn.fetch(
        "select c.author, c.agent_role from comments c "
        "join tasks t on t.id = c.task_id where t.human_id = $1 "
        "order by c.created_at, c.id",
        human_id,
    )
    return [(r["author"], r["agent_role"]) for r in rows]


# ---- agent_role の保存・API 往復 ---------------------------------------------------


async def test_comment_agent_role_roundtrip(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """POST /comments の agentRole が保存され、応答・一覧・SSE に載る。"""
    res = await api_client.post(
        "/api/tasks/T-104/comments",
        json={"author": "ai", "text": "編成テスト", "agentRole": "conductor"},
    )
    assert res.status_code == 201
    assert res.json()["agentRole"] == "conductor"

    listed = await api_client.get("/api/tasks/T-104/comments")
    assert [c["agentRole"] for c in listed.json()] == ["conductor"]

    conn = await db_connect()
    try:
        assert await _agent_roles(conn, "T-104") == [("ai", "conductor")]
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert events[0]["type"] == "comment.created"
    assert events[0]["payload"]["agentRole"] == "conductor"


async def test_comment_without_agent_role_defaults_to_null(
    api_client: httpx.AsyncClient,
) -> None:
    """agentRole 未指定（human・従来AI）は null のまま（「Grow」のみ表示の互換）。"""
    res = await api_client.post(
        "/api/tasks/T-104/comments", json={"author": "human", "text": "了解です"}
    )
    assert res.status_code == 201
    assert res.json()["agentRole"] is None


async def test_comment_rejects_unknown_agent_role(api_client: httpx.AsyncClient) -> None:
    """未知の役割は 422（AgentRole 列挙で検証）。"""
    res = await api_client.post(
        "/api/tasks/T-104/comments",
        json={"author": "ai", "text": "x", "agentRole": "hacker"},
    )
    assert res.status_code == 422


# ---- 各投稿箇所の役割付与 ----------------------------------------------------------


async def test_assign_ai_start_comment_is_executor(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """assign-ai の着手コメントは実行AI（executor）名義。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202

    conn = await db_connect()
    try:
        assert await _agent_roles(conn, "T-104") == [("ai", "executor")]
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert events[0]["type"] == "comment.created"
    assert events[0]["payload"]["agentRole"] == "executor"


async def test_execute_job_comments_are_executor(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """execute の進捗・完了は executor、セルフレビューの指摘・承認は reviewer 名義（#23）。"""
    await api_client.post("/api/tasks/T-104/assign-ai")
    await drain_jobs(api_client, captured_jobs)  # execute→review→execute→review を完走

    conn = await db_connect()
    try:
        # 着手(e) → 進捗(e) → 指摘(r) → 進捗(e) → 承認(r) → 完了(e)
        assert await _agent_roles(conn, "T-104") == [
            ("ai", "executor"),
            ("ai", "executor"),
            ("ai", "reviewer"),
            ("ai", "executor"),
            ("ai", "reviewer"),
            ("ai", "executor"),
        ]
    finally:
        await conn.close()


async def test_execute_job_failure_comment_is_executor(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最終失敗時の人へのハンドオフコメントも実行AI（executor）名義。"""

    class _FailingProvider:
        async def execute(self, task: dict, rules: list, comments: list, **kwargs):
            raise RuntimeError("模擬的な失敗")

    monkeypatch.setattr(execute_mod, "get_provider", lambda: _FailingProvider())
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]
    run = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert run.json() == {"status": "failed"}

    conn = await db_connect()
    try:
        roles = await _agent_roles(conn, "T-104")
        # 着手・失敗ハンドオフ（#24: delta 未受信で失敗するため進捗コメントは無い）
        assert roles[0] == ("ai", "executor")
        assert roles[-1] == ("ai", "executor")
    finally:
        await conn.close()


async def test_confirm_breakdown_comments_are_planner(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """分解の反映コメント（親・最初のAI子）は計画AI（planner）名義。"""
    res = await api_client.post(
        "/api/tasks/T-104/breakdown/confirm",
        json={
            "subtasks": [
                {"title": "料金表の下書き", "owner": "ai"},
                {"title": "最終チェック", "owner": "human"},
            ]
        },
    )
    assert res.status_code == 200
    child_id = res.json()["children"][0]["id"]

    conn = await db_connect()
    try:
        assert await _agent_roles(conn, "T-104") == [("ai", "planner")]
        assert await _agent_roles(conn, child_id) == [("ai", "planner")]
    finally:
        await conn.close()


async def test_adopt_learn_comment_is_distiller(api_client: httpx.AsyncClient) -> None:
    """蒸留候補の採用コメントは学習AI（distiller）名義。"""
    res = await api_client.post(
        "/api/tasks/T-091/learn/adopt",
        json={
            "text": "確定申告サマリーは控除候補を先に提示する",
            "scope": "personal",
            "tags": ["経理"],
            "confidence": "med",
        },
    )
    assert res.status_code == 201

    conn = await db_connect()
    try:
        assert await _agent_roles(conn, "T-091") == [("ai", "distiller")]
    finally:
        await conn.close()


# ---- GET /api/tasks/{human_id}/jobs（リレー履歴） ----------------------------------


async def test_jobs_api_empty(api_client: httpx.AsyncClient) -> None:
    res = await api_client.get("/api/tasks/T-104/jobs")
    assert res.status_code == 200
    assert res.json() == {"taskId": "T-104", "jobs": []}


async def test_jobs_api_returns_relay_history_ascending(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """created_at 昇順のリレー履歴（breakdown → execute）を返す。"""
    # 先行する breakdown ジョブ（succeeded）を過去時刻で直接挿入
    conn = await db_connect()
    try:
        await conn.execute(
            "insert into ai_jobs (task_id, kind, status, created_at, finished_at) "
            "select id, 'breakdown', 'succeeded', now() - interval '1 hour', "
            "now() - interval '59 minutes' from tasks where human_id = 'T-104'"
        )
    finally:
        await conn.close()

    res = await api_client.post("/api/tasks/T-104/assign-ai")
    job_id = res.json()["jobId"]

    listed = await api_client.get("/api/tasks/T-104/jobs")
    assert listed.status_code == 200
    body = listed.json()
    assert body["taskId"] == "T-104"
    assert [(j["kind"], j["status"]) for j in body["jobs"]] == [
        ("breakdown", "succeeded"),
        ("execute", "queued"),
    ]
    assert body["jobs"][1]["id"] == job_id
    assert body["jobs"][0]["finishedAt"] is not None
    assert body["jobs"][1]["finishedAt"] is None
    assert all(j["taskId"] == "T-104" for j in body["jobs"])


async def test_jobs_api_task_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.get("/api/tasks/T-999/jobs")
    assert res.status_code == 404
