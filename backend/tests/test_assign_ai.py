"""POST /api/tasks/{id}/assign-ai（§1.5 / §5.3 assignAI）のテスト。

enqueue はフェイクに差し替え、同期部分（retrieval・状態遷移・着手コメント・
applied++・rule_applications・ai_jobs 作成・SSE）だけを検証する。
ジョブ実行そのものは test_execute_job.py で検証する。
"""

import httpx
import pytest

from app.events import bus
from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_events

START_COMMENT_T104 = (
    "承知しました。あなた／チームのルール"
    "「絵文字は使わない。文体は簡潔・断定調に統一する」ほか計4件を前提に着手します。"
)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue_job をフェイク化してジョブを自動実行させない（jobId を記録）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


async def test_assign_ai_with_rules(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """T-104（仕事,調査）: K-01〜K-04 の4件適用・着手コメント・ai_work/progressレーン末尾。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202
    job_id = res.json()["jobId"]
    assert captured_jobs == [job_id]

    conn = await db_connect()
    try:
        # ai_work・progress 0・progress レーン末尾（T-098, T-101 の後ろ = 2）
        task = await conn.fetchrow("select * from tasks where human_id = 'T-104'")
        assert task["status"] == "ai_work"
        assert task["progress"] == 0
        assert task["lane_key"] == "progress"
        assert task["order_in_lane"] == 2

        # 着手コメント（Grow.dc.html 準拠。先頭ルール = confidence/applied 順で K-02）
        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert len(comments) == 1
        assert comments[0]["author"] == "ai"
        assert comments[0]["text"] == START_COMMENT_T104

        # applied++ と last_applied_at（適用4件のみ。K-05 は不変）
        rules = {
            r["human_id"]: r for r in await conn.fetch("select * from rules order by human_id")
        }
        assert rules["K-01"]["applied"] == 7
        assert rules["K-02"]["applied"] == 15
        assert rules["K-03"]["applied"] == 3
        assert rules["K-04"]["applied"] == 10
        assert rules["K-05"]["applied"] == 5
        assert all(
            rules[k]["last_applied_at"] is not None for k in ("K-01", "K-02", "K-03", "K-04")
        )
        assert rules["K-05"]["last_applied_at"] is None

        # rule_applications 4行
        applications = await conn.fetch(
            "select rule_id from rule_applications where task_id = $1", task["id"]
        )
        assert {row["rule_id"] for row in applications} == {
            rules[k]["id"] for k in ("K-01", "K-02", "K-03", "K-04")
        }

        # ai_jobs: kind=execute / queued / applied_rule_ids は retrieval 順
        job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert job["kind"] == "execute"
        assert job["status"] == "queued"
        assert job["task_id"] == task["id"]
        assert list(job["applied_rule_ids"]) == [
            rules[k]["id"] for k in ("K-02", "K-04", "K-01", "K-03")
        ]
    finally:
        await conn.close()

    # SSE: comment.created → task.updated（status/progress/laneKey/commentCount 同期）
    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["comment.created", "task.updated"]
    assert events[0]["payload"]["text"] == START_COMMENT_T104
    task_payload = events[1]["payload"]
    assert task_payload["id"] == "T-104"
    assert task_payload["status"] == "ai_work"
    assert task_payload["progress"] == 0
    assert task_payload["laneKey"] == "progress"
    assert task_payload["commentCount"] == 1


async def test_assign_ai_single_rule_wording(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """適用が1件のときは「ほか計N件」が付かない（プロト準拠）。"""
    conn = await db_connect()
    try:
        await conn.execute("delete from rules where human_id <> 'K-03'")
    finally:
        await conn.close()

    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202

    conn = await db_connect()
    try:
        text = await conn.fetchval(
            "select text from comments c join tasks t on t.id = c.task_id "
            "where t.human_id = 'T-104'"
        )
        assert text == (
            "承知しました。あなた／チームのルール"
            "「競合調査は料金を表形式にし、各項目に出典URLを付ける」を前提に着手します。"
        )
    finally:
        await conn.close()


async def test_assign_ai_without_rules(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """該当ルールなし: 「承知しました。着手します。」・applied_rule_ids 空。"""
    conn = await db_connect()
    try:
        await conn.execute("delete from rules")
    finally:
        await conn.close()

    res = await api_client.post("/api/tasks/T-121/assign-ai")
    assert res.status_code == 202

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-121'")
        assert task["status"] == "ai_work"
        text = await conn.fetchval(
            "select text from comments where task_id = $1", task["id"]
        )
        assert text == "承知しました。着手します。"
        job = await conn.fetchrow(
            "select * from ai_jobs where id = $1::uuid", res.json()["jobId"]
        )
        assert list(job["applied_rule_ids"]) == []
        count = await conn.fetchval("select count(*) from rule_applications")
        assert count == 0
    finally:
        await conn.close()


async def test_assign_ai_invalid_transition_makes_no_job(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """breakdown → ai_work は不正遷移（409）。ジョブもコメントも applied++ も発生しない。"""
    res = await api_client.post("/api/tasks/T-130/assign-ai")
    assert res.status_code == 409
    assert captured_jobs == []

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-130'")
        assert task["status"] == "breakdown"
        assert task["lane_key"] == "backlog"
        assert await conn.fetchval("select count(*) from ai_jobs") == 0
        assert await conn.fetchval("select count(*) from comments") == 0
        assert await conn.fetchval("select count(*) from rule_applications") == 0
    finally:
        await conn.close()


async def test_assign_ai_task_not_found(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    res = await api_client.post("/api/tasks/T-999/assign-ai")
    assert res.status_code == 404
    assert captured_jobs == []
