"""#28 信頼グラデュエーション縮退版（差し戻し時の自律性自動降格）のテスト。

- 全ステータス遷移が task_transitions に actor 付きで記録される（#26 と同一フック）
- 人の差し戻し（reject / done→you_todo 再オープン）で autonomy が1段降格し、
  指揮者AI名義の理由コメントが残り、既存の task.updated に新しい autonomy が乗る
- 下限は L1（L1/L0 は下げない）。連続差し戻しでも1遷移1段まで
- 承認遷移（→done）では降格しない。AI起因の遷移（actor='ai'）でも降格しない
"""

import httpx
import pytest

from app.domain.models import TaskStatus
from app.events import bus
from app.jobs import queue as jobs_queue
from app.repo import tasks as tasks_repo
from tests.helpers import db_connect, drain_events

DOWNGRADE_L3_L2_COMMENT = (
    "差し戻しを受けて、このタスクの自律性を L3→L2 に下げました。信頼は実績で回復します。"
)
DOWNGRADE_L2_L1_COMMENT = (
    "差し戻しを受けて、このタスクの自律性を L2→L1 に下げました。信頼は実績で回復します。"
)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（reject が積む execute ジョブは実行しない）。"""
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


async def _transitions(conn, human_id: str) -> list[tuple[str, str, str]]:
    rows = await conn.fetch(
        "select t.from_status, t.to_status, t.actor from task_transitions t "
        "join tasks k on k.id = t.task_id where k.human_id = $1 "
        "order by t.created_at, t.id",
        human_id,
    )
    return [(r["from_status"], r["to_status"], r["actor"]) for r in rows]


async def _conductor_comments(conn, human_id: str) -> list:
    return await conn.fetch(
        "select c.author, c.text from comments c "
        "join tasks k on k.id = c.task_id "
        "where k.human_id = $1 and c.agent_role = 'conductor' "
        "order by c.created_at, c.id",
        human_id,
    )


# ---- task_transitions の記録（全遷移・actor 付き） -----------------------------------


async def test_status_transitions_are_recorded(api_client: httpx.AsyncClient) -> None:
    """人の PATCH による遷移が順に記録される。ステータス以外の変更は記録されない。"""
    res = await api_client.patch("/api/tasks/T-091", json={"status": "reviewing"})
    assert res.status_code == 200
    res = await api_client.patch("/api/tasks/T-091", json={"status": "done"})
    assert res.status_code == 200
    res = await api_client.patch("/api/tasks/T-091", json={"title": "改題のみ"})
    assert res.status_code == 200

    conn = await db_connect()
    try:
        assert await _transitions(conn, "T-091") == [
            ("you_review", "reviewing", "human"),
            ("reviewing", "done", "human"),
        ]
    finally:
        await conn.close()


# ---- 人の差し戻し（reject）で1段降格 -------------------------------------------------


async def test_human_reject_downgrades_l3_to_l2(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """L3 タスクの reject: L2 へ降格＋指揮者コメント＋task.updated に新 autonomy。"""
    await api_client.patch("/api/tasks/T-091", json={"autonomy": "L3"})
    drain_events(event_queue)

    res = await api_client.post(
        "/api/tasks/T-091/reject", json={"reason": "数値の出典を追記してください"}
    )
    assert res.status_code == 202

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-091'")
        assert task["status"] == "ai_work"
        assert task["autonomy"] == "L2"  # L3 → L2（1遷移1段）

        conductor = await _conductor_comments(conn, "T-091")
        assert len(conductor) == 1
        assert conductor[0]["author"] == "ai"
        assert conductor[0]["text"] == DOWNGRADE_L3_L2_COMMENT

        assert await _transitions(conn, "T-091") == [
            ("you_review", "ai_work", "human"),
        ]
    finally:
        await conn.close()

    # SSE: 既存の task.updated（ai_work 遷移）に降格後の autonomy が乗る（追加配信なし）
    events = drain_events(event_queue)
    ai_work_updates = [
        e
        for e in events
        if e["type"] == "task.updated" and e["payload"]["status"] == "ai_work"
    ]
    assert len(ai_work_updates) == 1
    assert ai_work_updates[0]["payload"]["autonomy"] == "L2"


async def test_reject_keeps_l1_floor(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """L1（既定）は下限なので降格されず、降格コメントも出ない。"""
    res = await api_client.post(
        "/api/tasks/T-091/reject", json={"reason": "体裁を整えてください"}
    )
    assert res.status_code == 202

    conn = await db_connect()
    try:
        assert (
            await conn.fetchval("select autonomy from tasks where human_id = 'T-091'")
            == "L1"
        )
        assert await _conductor_comments(conn, "T-091") == []
        # 遷移自体は記録される
        assert await _transitions(conn, "T-091") == [
            ("you_review", "ai_work", "human"),
        ]
    finally:
        await conn.close()


# ---- 降格しないケース ----------------------------------------------------------------


async def test_approval_transition_does_not_downgrade(
    api_client: httpx.AsyncClient,
) -> None:
    """承認（you_review→done）は通常遷移: 記録はされるが降格しない。"""
    await api_client.patch("/api/tasks/T-091", json={"autonomy": "L3"})
    res = await api_client.patch("/api/tasks/T-091", json={"status": "done"})
    assert res.status_code == 200
    assert res.json()["autonomy"] == "L3"

    conn = await db_connect()
    try:
        assert (
            await conn.fetchval("select autonomy from tasks where human_id = 'T-091'")
            == "L3"
        )
        assert await _conductor_comments(conn, "T-091") == []
        assert await _transitions(conn, "T-091") == [
            ("you_review", "done", "human"),
        ]
    finally:
        await conn.close()


async def test_ai_transition_does_not_downgrade(api_client: httpx.AsyncClient) -> None:
    """AI起因（actor='ai'）の差し戻し遷移では降格しない（#23 セルフレビューの差し戻し等）。"""
    await api_client.patch("/api/tasks/T-091", json={"autonomy": "L3"})

    conn = await db_connect()
    try:
        async with conn.transaction():
            row = await tasks_repo.get_task_row(conn, "T-091", for_update=True)
            await tasks_repo.apply_patch(
                conn, row, {"status": TaskStatus.AI_WORK}, actor="ai"
            )
        assert (
            await conn.fetchval("select autonomy from tasks where human_id = 'T-091'")
            == "L3"
        )
        assert await _conductor_comments(conn, "T-091") == []
        assert await _transitions(conn, "T-091") == [
            ("you_review", "ai_work", "ai"),
        ]
    finally:
        await conn.close()


# ---- 再オープン（done→you_todo）と下限 L1 -------------------------------------------


async def test_reopen_downgrades_one_step_per_transition_until_l1(
    api_client: httpx.AsyncClient,
) -> None:
    """再オープンも差し戻し扱い: L3→L2→L1 と1遷移1段で下がり、L1 で止まる。"""
    await api_client.patch("/api/tasks/T-080", json={"autonomy": "L3"})

    # 1回目の再オープン: L3→L2（PATCH 応答の DTO にも降格後の値が乗る）
    res = await api_client.patch("/api/tasks/T-080", json={"status": "you_todo"})
    assert res.status_code == 200
    assert res.json()["autonomy"] == "L2"

    # done に戻して 2回目: L2→L1
    await api_client.patch("/api/tasks/T-080", json={"status": "done"})
    res = await api_client.patch("/api/tasks/T-080", json={"status": "you_todo"})
    assert res.json()["autonomy"] == "L1"

    # 3回目: L1 が下限（L0 = 実行停止までは自動で下げない）
    await api_client.patch("/api/tasks/T-080", json={"status": "done"})
    res = await api_client.patch("/api/tasks/T-080", json={"status": "you_todo"})
    assert res.json()["autonomy"] == "L1"

    conn = await db_connect()
    try:
        conductor = await _conductor_comments(conn, "T-080")
        assert [c["text"] for c in conductor] == [
            DOWNGRADE_L3_L2_COMMENT,
            DOWNGRADE_L2_L1_COMMENT,
        ]
        assert await _transitions(conn, "T-080") == [
            ("done", "you_todo", "human"),
            ("you_todo", "done", "human"),
            ("done", "you_todo", "human"),
            ("you_todo", "done", "human"),
            ("done", "you_todo", "human"),
        ]
    finally:
        await conn.close()
