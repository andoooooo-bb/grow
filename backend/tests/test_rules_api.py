"""ルール API（#13 §1.8 promote）と applied 同期イベントのテスト。

- POST /api/rules/{human_id}/promote: scope=team 化・SSE rule.updated・冪等
- assign-ai の適用時に rule.updated が適用件数分 publish される（applied 表示鮮度 #13）
"""

import httpx
import pytest

from app.events import bus
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_events


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue_job をフェイク化してジョブを自動実行させない（jobId を記録）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


# ---- 昇格（POST /rules/:id/promote §1.8） -----------------------------------------


async def test_promote_rule_to_team(api_client: httpx.AsyncClient, event_queue) -> None:
    """K-01（personal）→ scope=team。応答は Rule DTO、SSE rule.updated が届く。"""
    res = await api_client.post("/api/rules/K-01/promote")
    assert res.status_code == 200
    rule = res.json()
    assert rule["id"] == "K-01"
    assert rule["scope"] == "team"
    assert rule["sourceTaskId"] == "T-098"  # source_task の human_id 表現を維持

    conn = await db_connect()
    try:
        row = await conn.fetchrow("select * from rules where human_id = 'K-01'")
        assert row["scope"] == "team"
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["rule.updated"]
    assert events[0]["payload"]["id"] == "K-01"
    assert events[0]["payload"]["scope"] == "team"


async def test_promote_rule_is_idempotent(api_client: httpx.AsyncClient, event_queue) -> None:
    """既に team のルール（K-04）でも 200 で Rule DTO を返す。SSE は配信しない。"""
    res = await api_client.post("/api/rules/K-04/promote")
    assert res.status_code == 200
    assert res.json()["id"] == "K-04"
    assert res.json()["scope"] == "team"
    assert drain_events(event_queue) == []

    # personal → team → 再 promote も冪等
    first = await api_client.post("/api/rules/K-01/promote")
    second = await api_client.post("/api/rules/K-01/promote")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["scope"] == "team"
    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["rule.updated"]  # 1回目のみ


async def test_promote_rule_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post("/api/rules/K-99/promote")
    assert res.status_code == 404


# ---- assign-ai 適用時の rule.updated（applied 表示鮮度 #13） -----------------------


async def test_assign_ai_publishes_rule_updated_per_applied_rule(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """T-104 の assign-ai で、適用4件（K-01〜K-04）の rule.updated が applied++ 後の値で届く。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202

    events = drain_events(event_queue)
    rule_events = [e for e in events if e["type"] == "rule.updated"]
    assert len(rule_events) == 4
    payloads = {e["payload"]["id"]: e["payload"] for e in rule_events}
    assert set(payloads) == {"K-01", "K-02", "K-03", "K-04"}
    # シード値 +1（K-01: 6→7, K-02: 14→15, K-03: 2→3, K-04: 9→10）
    assert payloads["K-01"]["applied"] == 7
    assert payloads["K-02"]["applied"] == 15
    assert payloads["K-03"]["applied"] == 3
    assert payloads["K-04"]["applied"] == 10
    assert all(p["lastAppliedAt"] is not None for p in payloads.values())
