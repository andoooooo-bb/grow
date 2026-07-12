"""#22 指揮者エージェント（オートパイロット）のテスト。

- POST /tasks/:id/autopilot: 202 → orchestrate ジョブ enqueue / 404 / L0・ai_work・done は 409
- 判断ループ（MockProvider の決定的な状態機械分岐）:
  (a) T-130（breakdown・chat空）→ hearing 判断 → 初期質問を自発投稿し spec で停止
  (a') T-130（壁打ち済み）→ breakdown 判断 → 候補を SSE 配信し人の承認待ちで停止（§1.6）
  (b) T-112（queued・L1）→ execute 判断 → 実行AI＋レビューAIの同期リレー（#23）
      → you_review で必ず停止
  (c) T-112 L3 → execute → done まで自動 → 次判断で終了宣言
  (d) 実行回数上限（MAX_AUTOPILOT_STEPS）で強制停止＋「人にお返しします」コメント
  (e) コスト上限: エンドポイントの 409（enqueue 前）とループ内チェックの停止の両方
  (f) 判断理由コメントが毎ステップ（理由: …）付き・conductor ロールで残る
  (g) レビュー未実施の成果物がある you_review タスク → review 判断（#23 _act_review）
既存 assign-ai / execute の外部挙動は変えない（test_assign_ai / test_execute_job が担保）。
"""

import httpx
import pytest

from app.ai.mock_provider import DECIDE_REASONS, GREETING_T130, SUBTASKS_T130
from app.domain.models import AiJobKind
from app.events import bus
from app.jobs import execute as execute_mod
from app.jobs import orchestrate as orchestrate_mod
from app.jobs import queue as jobs_queue
from app.jobs import registry
from app.jobs import review as review_mod
from app.jobs.orchestrate import run_orchestrate_job_row
from tests.helpers import db_connect, drain_events

REASON_HEARING = orchestrate_mod.REASON_COMMENT_TEMPLATE.format(
    label="ヒアリング", reason=DECIDE_REASONS["hearing"]
)
REASON_BREAKDOWN = orchestrate_mod.REASON_COMMENT_TEMPLATE.format(
    label="分解提案", reason=DECIDE_REASONS["breakdown"]
)
REASON_EXECUTE = orchestrate_mod.REASON_COMMENT_TEMPLATE.format(
    label="実行", reason=DECIDE_REASONS["execute"]
)
REASON_REVIEW = orchestrate_mod.REASON_COMMENT_TEMPLATE.format(
    label="セルフレビュー", reason=DECIDE_REASONS["review"]
)
REASON_DONE = orchestrate_mod.REASON_COMMENT_TEMPLATE.format(
    label="完了", reason=DECIDE_REASONS["done"]
)
REASON_HANDOFF = orchestrate_mod.REASON_COMMENT_TEMPLATE.format(
    label="人へのハンドオフ", reason=DECIDE_REASONS["handoff_review"]
)
COST_CAP_COMMENT_1USD = (
    "コスト上限 $1 に達したため停止しました。上限を変更するか、人が引き継いでください。"
)


@pytest.fixture
def zero_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execute_mod, "RETRY_BACKOFF_SEC", 0.0)


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化して jobId を捕捉する（ジョブは手動で worker を叩いて進める）。

    orchestrate の再 enqueue（ループ継続）もここに積まれるため、テストは
    _drain_jobs で連鎖を決定的に消化できる。
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


async def _drain_jobs(api_client: httpx.AsyncClient, captured_jobs: list[str]) -> None:
    """捕捉済みジョブを先頭から順に実行する（実行中に積まれた再 enqueue 分も消化）。"""
    ran = 0
    while ran < len(captured_jobs):
        res = await api_client.post(
            "/internal/jobs/run", json={"jobId": captured_jobs[ran]}
        )
        assert res.status_code == 200
        assert res.json() == {"status": "succeeded"}
        ran += 1


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


# ---- 登録（#18 registry） -----------------------------------------------------------


def test_orchestrate_kind_is_registered() -> None:
    """kind='orchestrate' は registry 経由で worker/local 両対応になっている。"""
    assert registry.get_handler(AiJobKind.ORCHESTRATE) is run_orchestrate_job_row


# ---- エンドポイント検証 --------------------------------------------------------------


async def test_autopilot_404_for_unknown_task(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    res = await api_client.post("/api/tasks/T-999/autopilot")
    assert res.status_code == 404
    assert captured_jobs == []


async def test_autopilot_409_for_l0_and_terminal_statuses(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """L0（計画のみ #21）と ai_work / done は起動できない（FE もボタン無効）。"""
    await api_client.patch("/api/tasks/T-112", json={"autonomy": "L0"})
    res = await api_client.post("/api/tasks/T-112/autopilot")
    assert res.status_code == 409
    assert "L0" in res.json()["detail"]

    assert (await api_client.post("/api/tasks/T-098/autopilot")).status_code == 409
    assert (await api_client.post("/api/tasks/T-080/autopilot")).status_code == 409
    assert captured_jobs == []


# ---- (a) hearing: 前提が未確認 → 初期質問を自発投稿して停止 ---------------------------


async def test_autopilot_hearing_posts_initial_question_and_stops(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """T-130（breakdown・chat空）: hearing 判断 → 理由コメント → 初期質問 → spec 停止。"""
    res = await api_client.post("/api/tasks/T-130/autopilot")
    assert res.status_code == 202
    job_id = res.json()["jobId"]
    assert captured_jobs == [job_id]
    drain_events(event_queue)  # 着手コメント分は捨て、ジョブ分だけを検証する

    await _drain_jobs(api_client, captured_jobs)
    assert len(captured_jobs) == 1  # 人の回答待ちなので再 enqueue しない

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-130'")
        assert task["status"] == "spec"

        chat = await conn.fetch(
            "select * from chat_messages where task_id = $1", task["id"]
        )
        assert len(chat) == 1
        assert chat[0]["author"] == "ai"
        assert chat[0]["text"] == GREETING_T130

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            orchestrate_mod.TAKEOVER_COMMENT,
            REASON_HEARING,
        ]
        # (f) 判断理由コメントは（理由: …）付き・指揮者AIロール
        assert "（理由: " in comments[1]["text"]
        assert all(c["agent_role"] == "conductor" for c in comments)

        job = await conn.fetchrow("select * from ai_jobs where id = $1::uuid", job_id)
        assert job["kind"] == "orchestrate"
        assert job["status"] == "succeeded"
        assert job["input_tokens"] > 0
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "comment.created",  # 判断理由（conductor）
        "task.updated",  # commentCount 同期
        "chat.message.created",  # 初期質問の自発投稿
        "task.updated",  # spec 遷移
    ]
    assert events[0]["payload"]["agentRole"] == "conductor"
    assert events[3]["payload"]["status"] == "spec"


# ---- (a') breakdown: 壁打ち済み → 候補を配信して人の承認待ちで停止 --------------------


async def test_autopilot_breakdown_proposes_and_waits_for_human(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays, event_queue
) -> None:
    """壁打ち済みの T-130: breakdown 判断 → subtask.proposal 配信 → 反映は人の承認のまま。"""
    await api_client.post("/api/tasks/T-130/chat/start")  # chat あり・spec へ
    res = await api_client.post("/api/tasks/T-130/autopilot")
    assert res.status_code == 202
    drain_events(event_queue)

    await _drain_jobs(api_client, captured_jobs)
    assert len(captured_jobs) == 1  # 反映承認は人のボール（§1.6 暴走防止を維持）

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == [
        "comment.created",  # 判断理由
        "task.updated",
        "subtask.proposal",  # 分解候補（サーバ非永続）
        "comment.created",  # 引き継ぎコメント
        "task.updated",
    ]
    proposal = events[2]["payload"]
    assert proposal["taskId"] == "T-130"
    assert [s["title"] for s in proposal["subtasks"]] == [s.title for s in SUBTASKS_T130]

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-130'")
        assert task["status"] == "spec"  # 勝手に ai_work へ進めない
        # 子タスクは作られない（confirmBreakdown は人の操作のまま）
        children = await conn.fetchval(
            "select count(*) from tasks where parent_id = $1", task["id"]
        )
        assert children == 0

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            orchestrate_mod.TAKEOVER_COMMENT,
            REASON_BREAKDOWN,
            orchestrate_mod.BREAKDOWN_HANDOFF_COMMENT,
        ]
        assert all(c["agent_role"] == "conductor" for c in comments)
    finally:
        await conn.close()


# ---- (b) execute 連鎖: L1 は you_review で必ず停止 -----------------------------------


async def test_autopilot_executes_then_stops_at_you_review_on_l1(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """T-112（queued・既定L1）: execute 判断 → 実行AI＋レビューAIの同期リレー（#23）
    → you_review 停止。"""
    res = await api_client.post("/api/tasks/T-112/autopilot")
    assert res.status_code == 202

    await _drain_jobs(api_client, captured_jobs)
    # L1: 下書きまで。再 enqueue しない（execute→review 連鎖は同期リレーで消化済み）
    assert len(captured_jobs) == 1

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-112'")
        assert task["status"] == "you_review"
        assert task["lane_key"] == "review"
        assert task["progress"] is None

        # 成果物は execute ジョブが通常どおり生成する（revise を経て2版 #23）
        assert await conn.fetchval("select count(*) from artifacts") == 2

        jobs = await conn.fetch(
            "select * from ai_jobs where task_id = $1 order by created_at", task["id"]
        )
        assert [j["kind"] for j in jobs] == [
            "orchestrate",
            "execute",
            "review",
            "execute",
            "review",
        ]
        assert all(j["status"] == "succeeded" for j in jobs)
        # execute には assign-ai と同じ retrieval 済みルールが注入される
        # （T-112 labels=['ブログ'] → K-01 + 全体ルール K-02 / K-04 の3件）
        assert len(jobs[1]["applied_rule_ids"]) == 3
        # revise 再実行は同じルールを引き継ぎ applied++ は重ねない（§6.3）
        assert jobs[3]["applied_rule_ids"] == jobs[1]["applied_rule_ids"]
        assert await conn.fetchval("select count(*) from rule_applications") == 3

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            orchestrate_mod.TAKEOVER_COMMENT,  # conductor
            REASON_EXECUTE,  # conductor（理由: …）
            execute_mod.PROGRESS_COMMENT,  # executor（リレー先の実行AI名義）
            comments[3]["text"],  # レビューAIの指摘（下で検証）
            execute_mod.PROGRESS_COMMENT,  # executor（修正）
            review_mod.APPROVE_COMMENT,  # reviewer
            execute_mod.COMPLETE_COMMENT,  # executor
        ]
        assert "【レビュー指摘】" in comments[3]["text"]
        assert [c["agent_role"] for c in comments] == [
            "conductor",
            "conductor",
            "executor",
            "reviewer",
            "executor",
            "reviewer",
            "executor",
        ]
    finally:
        await conn.close()


# ---- (b') L2: you_review 到達後も指揮者が次判断を続け、ハンドオフを自分で選ぶ ---------


async def test_autopilot_l2_continues_after_review_and_hands_off(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """L2: execute 完了後に自分を再 enqueue → 成果物レビューは人と判断して停止（#21/#22）。"""
    await api_client.patch("/api/tasks/T-112", json={"autonomy": "L2"})
    res = await api_client.post("/api/tasks/T-112/autopilot")
    assert res.status_code == 202

    await _drain_jobs(api_client, captured_jobs)
    assert len(captured_jobs) == 2  # 2周目 = handoff_human 判断で停止

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-112'")
        assert task["status"] == "you_review"  # 人のボールのまま据え置き
        assert task["lane_key"] == "review"

        jobs = await conn.fetch(
            "select * from ai_jobs where task_id = $1 order by created_at", task["id"]
        )
        assert [j["kind"] for j in jobs] == [
            "orchestrate",
            "execute",
            "review",
            "execute",
            "review",
            "orchestrate",
        ]
        assert all(j["status"] == "succeeded" for j in jobs)

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            orchestrate_mod.TAKEOVER_COMMENT,
            REASON_EXECUTE,
            execute_mod.PROGRESS_COMMENT,
            comments[3]["text"],  # レビューAIの指摘（#23）
            execute_mod.PROGRESS_COMMENT,
            review_mod.APPROVE_COMMENT,
            execute_mod.COMPLETE_COMMENT,
            REASON_HANDOFF,  # 2周目の判断理由（conductor。hasReview=True → 人へ）
            orchestrate_mod.HANDOFF_COMMENT,  # 引き継ぎコメント（conductor）
        ]
        assert comments[7]["agent_role"] == "conductor"
        assert comments[8]["agent_role"] == "conductor"
    finally:
        await conn.close()


# ---- (c) L3: execute → done まで自動 → 次判断で終了宣言 ------------------------------


async def test_autopilot_l3_runs_to_done_automatically(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """L3: execute が done まで連鎖 → 指揮者が再判断して終了宣言（全自動）。"""
    await api_client.patch("/api/tasks/T-112", json={"autonomy": "L3"})
    res = await api_client.post("/api/tasks/T-112/autopilot")
    assert res.status_code == 202

    await _drain_jobs(api_client, captured_jobs)
    assert len(captured_jobs) == 2  # チェーン完了後に自分を再 enqueue → 終了宣言で停止

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-112'")
        assert task["status"] == "done"
        assert task["lane_key"] == "done"
        assert await conn.fetchval("select count(*) from artifacts") == 2

        jobs = await conn.fetch(
            "select * from ai_jobs where task_id = $1 order by created_at", task["id"]
        )
        assert [j["kind"] for j in jobs] == [
            "orchestrate",
            "execute",
            "review",
            "execute",
            "review",
            "orchestrate",
        ]
        assert all(j["status"] == "succeeded" for j in jobs)

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            orchestrate_mod.TAKEOVER_COMMENT,
            REASON_EXECUTE,
            execute_mod.PROGRESS_COMMENT,
            comments[3]["text"],  # レビューAIの指摘（#23）
            execute_mod.PROGRESS_COMMENT,
            review_mod.APPROVE_COMMENT,
            execute_mod.COMPLETE_COMMENT,
            execute_mod.AUTO_APPROVE_COMMENT,  # L3 自動承認（executor, #21）
            REASON_DONE,  # 2周目の判断理由（conductor）
            orchestrate_mod.DONE_CLOSING_COMMENT,  # 終了宣言（conductor）
        ]
        assert comments[8]["agent_role"] == "conductor"
        assert comments[9]["agent_role"] == "conductor"
    finally:
        await conn.close()


# ---- (d) 実行回数上限: 強制停止＋人にお返しコメント ----------------------------------


async def test_autopilot_stops_at_step_limit(
    api_client: httpx.AsyncClient,
    captured_jobs: list[str],
    zero_delays,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """無限ループ防止: セッション内 orchestrate 実行数が上限を超えたら強制停止。"""
    monkeypatch.setattr(orchestrate_mod, "MAX_AUTOPILOT_STEPS", 1)
    await api_client.patch("/api/tasks/T-112", json={"autonomy": "L2"})
    res = await api_client.post("/api/tasks/T-112/autopilot")
    assert res.status_code == 202

    await _drain_jobs(api_client, captured_jobs)
    assert len(captured_jobs) == 2  # 1周目=execute / 2周目=上限到達で停止

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-112'")
        # you_review → you_todo は §5.6 で不許可のため現状維持（人のボールのまま）
        assert task["status"] == "you_review"

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert comments[-1]["text"] == orchestrate_mod.STEP_LIMIT_COMMENT
        assert "上限に達したため人にお返しします" in comments[-1]["text"]
        assert comments[-1]["agent_role"] == "conductor"

        jobs = await conn.fetch(
            "select * from ai_jobs where task_id = $1 order by created_at", task["id"]
        )
        assert [j["kind"] for j in jobs] == [
            "orchestrate",
            "execute",
            "review",
            "execute",
            "review",
            "orchestrate",
        ]
        assert all(j["status"] == "succeeded" for j in jobs)  # 上限停止も判断として成功
    finally:
        await conn.close()


# ---- (e) コスト上限（#21 policy.costCapUsd） ----------------------------------------


async def test_autopilot_endpoint_409_at_cost_cap(
    api_client: httpx.AsyncClient, captured_jobs: list[str], event_queue
) -> None:
    """enqueue 前の上限チェック: 409＋停止理由コメント（conductor）。ジョブは作らない。"""
    await _seed_job_cost("T-112", 1.2)
    await api_client.patch(
        "/api/tasks/T-112", json={"policy": {"allowWebSearch": True, "costCapUsd": 1.0}}
    )
    drain_events(event_queue)

    res = await api_client.post("/api/tasks/T-112/autopilot")
    assert res.status_code == 409
    assert "cost cap reached" in res.json()["detail"]
    assert captured_jobs == []

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-112'")
        assert task["status"] == "queued"  # queued → you_todo は不許可のため現状維持

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert len(comments) == 1  # 着手コメントは投稿されない（停止コメントのみ）
        assert comments[0]["text"] == COST_CAP_COMMENT_1USD
        assert comments[0]["agent_role"] == "conductor"

        # orchestrate ジョブは作られない（累計コスト用に挿入した execute 1件のみ）
        count = await conn.fetchval(
            "select count(*) from ai_jobs where task_id = $1", task["id"]
        )
        assert count == 1
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["comment.created", "task.updated"]
    assert events[0]["payload"]["text"] == COST_CAP_COMMENT_1USD


async def test_autopilot_loop_stops_at_cost_cap(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """ループ内の毎回チェック: 実行前に上限へ達していたら何もせず停止コメントを残す。"""
    await api_client.patch(
        "/api/tasks/T-112", json={"policy": {"allowWebSearch": True, "costCapUsd": 1.0}}
    )
    res = await api_client.post("/api/tasks/T-112/autopilot")
    assert res.status_code == 202  # 起動時点では累計 0 で上限未満

    await _seed_job_cost("T-112", 1.5)  # enqueue 後〜実行前に上限到達
    await _drain_jobs(api_client, captured_jobs)
    assert len(captured_jobs) == 1

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-112'")
        assert task["status"] == "queued"  # execute は起動されない
        assert await conn.fetchval("select count(*) from artifacts") == 0

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            orchestrate_mod.TAKEOVER_COMMENT,
            COST_CAP_COMMENT_1USD,
        ]
        assert comments[1]["agent_role"] == "conductor"

        jobs = await conn.fetch(
            "select kind, status from ai_jobs where task_id = $1 order by created_at",
            task["id"],
        )
        # コスト挿入用 execute（seed）＋ orchestrate のみ。新規 execute は作られない
        assert sorted(j["kind"] for j in jobs) == ["execute", "orchestrate"]
        orchestrate_job = next(j for j in jobs if j["kind"] == "orchestrate")
        assert orchestrate_job["status"] == "succeeded"
    finally:
        await conn.close()


# ---- (g) review: レビュー未実施の成果物 → レビューAIへリレー（#23 _act_review） -------


async def test_autopilot_reviews_unreviewed_artifact_then_stops(
    api_client: httpx.AsyncClient, captured_jobs: list[str], zero_delays
) -> None:
    """T-091（you_review・成果物あり・レビュー未実施）: review 判断 → レビューAIが
    revise で実行AIへ差し戻し → 修正 → approve → you_review で停止（L1）。"""
    # 人の編集版として成果物だけを置く（execute 履歴なし・レビュー未実施の状態を再現）
    res = await api_client.post(
        "/api/tasks/T-091/artifacts", json={"contentMd": "# 確定申告サマリー（下書き）"}
    )
    assert res.status_code == 201

    res = await api_client.post("/api/tasks/T-091/autopilot")
    assert res.status_code == 202

    await _drain_jobs(api_client, captured_jobs)
    assert len(captured_jobs) == 1  # L1: レビュー往復は同期リレーで消化し停止

    conn = await db_connect()
    try:
        task = await conn.fetchrow("select * from tasks where human_id = 'T-091'")
        assert task["status"] == "you_review"
        assert task["lane_key"] == "review"

        jobs = await conn.fetch(
            "select * from ai_jobs where task_id = $1 order by created_at", task["id"]
        )
        # review（revise）→ execute（修正）→ review（approve）
        assert [j["kind"] for j in jobs] == ["orchestrate", "review", "execute", "review"]
        assert all(j["status"] == "succeeded" for j in jobs)
        # execute 履歴が無いため retrieval で審査基準を組む（T-091 labels=['経理'] → 3件）
        assert len(jobs[1]["applied_rule_ids"]) == 3

        comments = await conn.fetch(
            "select * from comments where task_id = $1 order by created_at", task["id"]
        )
        assert [c["text"] for c in comments] == [
            orchestrate_mod.TAKEOVER_COMMENT,
            REASON_REVIEW,  # 「セルフレビュー」判断（conductor）
            comments[2]["text"],  # レビューAIの指摘（revise → ai_work へ差し戻し）
            execute_mod.PROGRESS_COMMENT,  # 実行AIの修正
            review_mod.APPROVE_COMMENT,  # 承認（reviewer）
            execute_mod.COMPLETE_COMMENT,  # 完了ハンドオフ（executor）
        ]
        assert "【レビュー指摘】" in comments[2]["text"]
        assert [c["agent_role"] for c in comments] == [
            "conductor",
            "conductor",
            "reviewer",
            "executor",
            "reviewer",
            "executor",
        ]

        # 修正版（v2）が最新。人の下書き（v1）は残る
        versions = await conn.fetch(
            "select version, job_id from artifacts where task_id = $1 order by version",
            task["id"],
        )
        assert [v["version"] for v in versions] == [1, 2]
        assert versions[0]["job_id"] is None  # 人の編集版
        assert versions[1]["job_id"] is not None  # 実行AIの修正版
    finally:
        await conn.close()
