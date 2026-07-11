"""#27 受付エージェント（intake ジョブ）のテスト。

- MockProvider.assess_task の決定的3分岐（hearing / breakdown / execute）
- MockProvider.deep_dive の決定的2分岐（1回目 ask / 2回目以降 propose）
- POST /api/tasks 成功後の自動 enqueue → ルート別の反映:
  (a) hearing: 判定理由コメント（planner）＋案内コメント＋初期質問の自発投稿＋spec 遷移
  (b) breakdown / execute: 判定理由コメント＋案内コメントのみ（状態は変えない）
  (c) parentId 付き作成・confirmBreakdown 経由のサブタスクには走らせない
  (d) 失敗時: ai_jobs=failed ＋ planner コメント（タスク状態は変えない）
壁打ち側の深掘り自己判定（ask → propose）は tests/test_chat_api.py が担保する。
"""

import httpx
import pytest

from app.ai.mock_provider import (
    DEEP_DIVE_QUESTION,
    INTAKE_QUESTIONS,
    MockProvider,
)
from app.domain.models import AiJobKind
from app.events import bus
from app.jobs import intake as intake_mod
from app.jobs import queue as jobs_queue
from app.jobs import registry
from app.jobs.intake import run_intake_job_row
from tests.helpers import db_connect, drain_events

# --- 期待文言（決定的であることをここで固定する） ---

REASON_HEARING = (
    "受付AI: このタスクは壁打ちでのヒアリングが適切と判断しました"
    "（理由: タイトルだけでは目的や制約が分からないため、先に前提を伺うのが適切です）"
)
REASON_BREAKDOWN = (
    "受付AI: このタスクは壁打ちでの分解が適切と判断しました"
    "（理由: ひとことで終わらない大きなテーマのため、分解から始めるのが適切です）"
)
REASON_EXECUTE = (
    "受付AI: このタスクはこのまま実行が適切と判断しました"
    "（理由: 作業内容が具体的なため、このまま実行AIに任せられます）"
)
HEARING_CHAT_MESSAGE = (
    "分解や実行の前に、いくつか確認させてください。\n"
    "① このタスクのゴールは？\n"
    "② 期限や制約は？\n"
    "③ 参考になる資料はありますか？"
)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化して jobId を捕捉する（ジョブは手動で worker を叩いて進める）。"""
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


async def _run_job(api_client: httpx.AsyncClient, job_id: str) -> None:
    res = await api_client.post("/internal/jobs/run", json={"jobId": job_id})
    assert res.status_code == 200
    assert res.json() == {"status": "succeeded"}


# ---- 登録（#18 registry） -----------------------------------------------------------


def test_intake_kind_is_registered() -> None:
    """kind='intake' は registry 経由で worker/local 両対応になっている。"""
    assert registry.get_handler(AiJobKind.INTAKE) is run_intake_job_row


# ---- MockProvider.assess_task の決定的3分岐 ------------------------------------------


@pytest.mark.parametrize(
    ("title", "labels", "expected_route"),
    [
        ("新しいタスク", [], "hearing"),  # addCard 既定タイトル
        ("メモ", [], "hearing"),  # 20字未満 & labels 空 = 判断材料なし
        ("ポートフォリオサイトのリニューアル", ["個人"], "breakdown"),  # 大きい語
        ("全社の業務プロセスを刷新", ["仕事"], "breakdown"),  # 大きい語
        (
            "四半期の営業データを集計してダッシュボードにまとめて共有する準備",
            ["仕事"],
            "breakdown",
        ),  # 30字超
        ("競合SaaS 5社の料金プランを調査", ["仕事", "調査"], "execute"),  # 具体的な作業
        ("確定申告に必要な書類リストを作成", ["経理"], "execute"),
    ],
)
async def test_assess_task_deterministic_routes(
    title: str, labels: list[str], expected_route: str
) -> None:
    provider = MockProvider()
    task = {"id": "uuid-x", "humanId": "T-900", "title": title, "labels": labels}
    result = await provider.assess_task(task, [])
    assert result.route == expected_route
    assert result.reason  # 判定理由は必ず付く（判定理由コメントの材料）
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    if expected_route == "hearing":
        assert result.questions == INTAKE_QUESTIONS
    else:
        assert result.questions == []
    # 決定的（同じ入力 → 同じ出力）
    assert await provider.assess_task(task, []) == result


# ---- MockProvider.deep_dive の決定的2分岐 --------------------------------------------


async def test_deep_dive_asks_on_first_human_turn() -> None:
    """人の発言が1回目 → ask（深掘り質問1つ・分解候補なし）。"""
    provider = MockProvider()
    task = {"id": "uuid-x", "humanId": "T-104", "title": "競合調査", "labels": ["調査"]}
    chat = [
        {"who": "ai", "text": "前提を教えてください"},
        {"who": "human", "text": "主要5社を比較したい"},
    ]
    result = await provider.deep_dive(task, chat, [])
    assert result.mode == "ask"
    assert result.text == DEEP_DIVE_QUESTION
    assert result.subtasks == []
    assert await provider.deep_dive(task, chat, []) == result  # 決定的


async def test_deep_dive_proposes_on_second_human_turn() -> None:
    """人の発言が2回目以降 → propose（既存の応答文＋分解候補の合成）。"""
    provider = MockProvider()
    task = {"id": "uuid-x", "humanId": "T-104", "title": "競合調査", "labels": ["調査"]}
    chat = [
        {"who": "ai", "text": "前提を教えてください"},
        {"who": "human", "text": "主要5社を比較したい"},
        {"who": "ai", "text": DEEP_DIVE_QUESTION},
        {"who": "human", "text": "料金と機能を重視します"},
    ]
    result = await provider.deep_dive(task, chat, [])
    assert result.mode == "propose"
    reply = await provider.chat_reply(task, chat, [])
    proposal = await provider.propose_subtasks(task, chat, [])
    assert result.text == reply.text  # 既存 chat_reply の合成
    assert result.subtasks == proposal.subtasks  # 既存 propose_subtasks の合成


# ---- (a) hearing: 作成 → 自動 enqueue → 初期質問の自発投稿＋spec 遷移 -----------------


async def test_post_task_triggers_intake_hearing_route(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """addCard 既定（「新しいタスク」）: intake が hearing と判定し、AIから先に質問する。"""
    res = await api_client.post("/api/tasks", json={"laneKey": "todo", "title": "新しいタスク"})
    assert res.status_code == 201
    assert res.json()["id"] == "T-131"
    assert res.json()["status"] == "breakdown"  # 作成レスポンスは従来どおり
    assert len(captured_jobs) == 1  # intake が自動 enqueue されている
    drain_events(event_queue)  # task.updated（作成）は捨て、ジョブ分だけを検証する

    await _run_job(api_client, captured_jobs[0])

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-131'")
        assert task["status"] == "spec"  # startChat と同ロジックで spec へ
        assert task["lane_key"] == "todo"  # レーンは変えない（§5.2）

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            REASON_HEARING,
            intake_mod.HEARING_COMMENT,
        ]
        assert all(c["agent_role"] == "planner" for c in comments)  # 受付は計画AI名義

        chat = await conn.fetch(
            "select * from chat_messages where task_id = $1 order by created_at",
            task["id"],
        )
        assert len(chat) == 1
        assert chat[0]["author"] == "ai"
        assert chat[0]["text"] == HEARING_CHAT_MESSAGE  # ①②③形式の初期質問

        job = await conn.fetchrow(
            "select * from ai_jobs where id = $1::uuid", captured_jobs[0]
        )
        assert job["kind"] == "intake"
        assert job["status"] == "succeeded"
        assert job["input_tokens"] > 0
        assert job["output_tokens"] > 0
        assert job["cost_usd"] is not None  # Flash 単価で実算定（#25）
        assert float(job["cost_usd"]) > 0
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "comment.created",  # 判定理由（planner）
        "comment.created",  # 「壁打ちで前提を伺います。」
        "chat.message.created",  # 初期質問の自発投稿
        "task.updated",  # spec 遷移＋commentCount 同期
    ]
    assert events[0]["payload"]["agentRole"] == "planner"
    assert events[2]["payload"]["text"] == HEARING_CHAT_MESSAGE
    assert events[3]["payload"]["status"] == "spec"


async def test_intake_hearing_is_idempotent_for_started_chat(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """ジョブ実行前に人が壁打ちを始めていたら、初期質問は二重投稿しない（startChat と同条件）。"""
    res = await api_client.post("/api/tasks", json={"laneKey": "todo", "title": "新しいタスク"})
    assert res.status_code == 201
    assert (await api_client.post("/api/tasks/T-131/chat/start")).status_code == 200

    await _run_job(api_client, captured_jobs[0])

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-131'")
        chat = await conn.fetch(
            "select * from chat_messages where task_id = $1", task["id"]
        )
        assert len(chat) == 1  # startChat の greeting のみ（intake は追い投稿しない）
        assert task["status"] == "spec"
    finally:
        await conn.close()


# ---- (b) breakdown / execute: 案内コメントのみ（状態は変えない） ----------------------


async def test_post_task_triggers_intake_breakdown_route(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """大きい語（リニューアル等）を含むタスク: breakdown 判定コメントを残して停止。"""
    res = await api_client.post(
        "/api/tasks",
        json={
            "laneKey": "backlog",
            "title": "ポートフォリオサイトのリニューアル",
            "labels": ["個人"],
        },
    )
    assert res.status_code == 201
    drain_events(event_queue)

    await _run_job(api_client, captured_jobs[0])

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-131'")
        assert task["status"] == "breakdown"  # 勝手に遷移しない（壁打ち開始は人の操作）

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            REASON_BREAKDOWN,
            intake_mod.BREAKDOWN_COMMENT,
        ]
        assert all(c["agent_role"] == "planner" for c in comments)
        # hearing ではないので chat への自発投稿はない
        assert (
            await conn.fetchval(
                "select count(*) from chat_messages where task_id = $1", task["id"]
            )
            == 0
        )
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "comment.created",
        "comment.created",
        "task.updated",  # commentCount 同期
    ]


async def test_post_task_triggers_intake_execute_route(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """具体的な作業（調査等）: execute 判定コメントのみ。実行は人の指示に委ねる。"""
    res = await api_client.post(
        "/api/tasks",
        json={
            "laneKey": "todo",
            "title": "競合SaaS 5社の料金プランを調査",
            "labels": ["仕事", "調査"],
        },
    )
    assert res.status_code == 201
    drain_events(event_queue)

    await _run_job(api_client, captured_jobs[0])

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-131'")
        assert task["status"] == "breakdown"  # 勝手に実行を始めない

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            REASON_EXECUTE,
            intake_mod.EXECUTE_COMMENT,
        ]
        assert all(c["agent_role"] == "planner" for c in comments)
        # intake 以外のジョブ（execute 等）は作られない
        kinds = await conn.fetch(
            "select kind from ai_jobs where task_id = $1", task["id"]
        )
        assert [k["kind"] for k in kinds] == ["intake"]
    finally:
        await conn.close()


# ---- (c) サブタスクには走らせない ----------------------------------------------------


async def test_post_task_with_parent_does_not_trigger_intake(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """parentId 付きの直接作成（親から生成されるサブタスク）は intake の対象外。"""
    res = await api_client.post(
        "/api/tasks", json={"laneKey": "todo", "title": "子タスク", "parentId": "T-104"}
    )
    assert res.status_code == 201
    assert captured_jobs == []

    conn = await db_connect()
    try:
        assert await conn.fetchval("select count(*) from ai_jobs") == 0
    finally:
        await conn.close()


async def test_confirm_breakdown_children_do_not_trigger_intake(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """confirmBreakdown 経由の子カード群にも intake は走らない（repo 直呼びのため）。"""
    assert (await api_client.post("/api/tasks/T-130/chat/start")).status_code == 200
    res = await api_client.post(
        "/api/tasks/T-130/breakdown/confirm",
        json={
            "subtasks": [
                {"title": "情報設計・サイトマップ作成", "owner": "ai"},
                {"title": "掲載する実績コンテンツの選定", "owner": "human"},
            ]
        },
    )
    assert res.status_code == 200
    assert len(res.json()["children"]) == 2
    assert captured_jobs == []  # intake は enqueue されない

    conn = await db_connect()
    try:
        assert (
            await conn.fetchval("select count(*) from ai_jobs where kind = 'intake'") == 0
        )
    finally:
        await conn.close()


# ---- (d) 失敗: ai_jobs=failed ＋ planner コメント（状態は変えない） --------------------


async def test_intake_failure_marks_job_failed_and_keeps_task_state(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判定失敗はコメントで可視化のみ。タスクは人のボールのまま手動で進められる。"""

    async def _boom(self, task: dict, rules: list[dict]):
        raise RuntimeError("mock assess failure")

    monkeypatch.setattr(MockProvider, "assess_task", _boom)

    res = await api_client.post("/api/tasks", json={"laneKey": "todo", "title": "新しいタスク"})
    assert res.status_code == 201

    run = await api_client.post("/internal/jobs/run", json={"jobId": captured_jobs[0]})
    assert run.status_code == 200
    assert run.json() == {"status": "failed"}

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-131'")
        assert task["status"] == "breakdown"  # 状態は変えない

        job = await conn.fetchrow(
            "select * from ai_jobs where id = $1::uuid", captured_jobs[0]
        )
        assert job["status"] == "failed"
        assert "mock assess failure" in job["error"]

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert len(comments) == 1
        assert "受付AIの判定が失敗しました" in comments[0]["text"]
        assert comments[0]["agent_role"] == "planner"

        # chat への自発投稿もない
        assert (
            await conn.fetchval(
                "select count(*) from chat_messages where task_id = $1", task["id"]
            )
            == 0
        )
    finally:
        await conn.close()
