"""夜間ナレッジCI（#26 §6.4b/c・§6.6）のテスト。

- シグナル自動記録: 承認（you_review/reviewing→done）= positive /
  差し戻し（→ai_work）・再オープン（done→you_todo）= negative
- mock reconcile の4種提案（conflict / merge / demote / distill）
- CIバッチ: rule_proposals 保存・knowledge_ci_runs 記録・確度の自動昇降格・SSE
- 受信箱: adopt 4種（archive・新規作成・feedback）と dismiss
- 手動実行 API（POST /api/knowledge/ci/run）と /internal/knowledge/ci のトークン検証
"""

import asyncio

import httpx
import pytest

from app.ai.mock_provider import MockProvider
from app.config import get_settings
from app.events import (
    KNOWLEDGE_CI_COMPLETED,
    RULE_PROPOSAL_CREATED,
    RULE_UPDATED,
    bus,
)
from tests.helpers import db_connect, drain_events

WS = "11111111-1111-4111-8111-111111111111"
K01 = "bbbbbbbb-0000-4000-8000-000000000001"
K03 = "bbbbbbbb-0000-4000-8000-000000000003"
K04 = "bbbbbbbb-0000-4000-8000-000000000004"
T091 = "aaaaaaaa-0000-4000-8000-000000000091"  # you_review
T080 = "aaaaaaaa-0000-4000-8000-000000000080"  # done


async def _insert_execute_job(conn, task_uuid: str, rule_uuids: list[str]) -> None:
    """直近 execute ジョブ（applied_rule_ids 付き）を作る（シグナル記録の材料）。"""
    await conn.execute(
        "insert into ai_jobs (task_id, kind, status, applied_rule_ids) "
        "values ($1, 'execute', 'succeeded', $2::uuid[])",
        task_uuid,
        rule_uuids,
    )


async def _signals(conn) -> list[tuple[str, str]]:
    rows = await conn.fetch(
        "select r.human_id, s.signal from rule_signals s "
        "join rules r on r.id = s.rule_id order by r.human_id, s.signal"
    )
    return [(row["human_id"], row["signal"]) for row in rows]


# ---- シグナル自動記録（repo/tasks.py apply_patch の遷移フック） ----------------------


async def test_approve_records_positive_signals(api_client: httpx.AsyncClient) -> None:
    """you_review→done（承認）で直近 execute の applied_rule_ids に positive。"""
    conn = await db_connect()
    try:
        await _insert_execute_job(conn, T091, [K01, K03])
        res = await api_client.patch(
            "/api/tasks/T-091", json={"status": "done", "laneKey": "done"}
        )
        assert res.status_code == 200
        assert await _signals(conn) == [("K-01", "positive"), ("K-03", "positive")]
    finally:
        await conn.close()


async def test_reject_records_negative_signals(api_client: httpx.AsyncClient) -> None:
    """you_review→ai_work（差し戻し）で negative。"""
    conn = await db_connect()
    try:
        await _insert_execute_job(conn, T091, [K01])
        res = await api_client.patch(
            "/api/tasks/T-091",
            json={"status": "ai_work", "laneKey": "progress", "progress": 0},
        )
        assert res.status_code == 200
        assert await _signals(conn) == [("K-01", "negative")]
    finally:
        await conn.close()


async def test_reopen_records_negative_signals(api_client: httpx.AsyncClient) -> None:
    """done→you_todo（再オープン）で negative。"""
    conn = await db_connect()
    try:
        await _insert_execute_job(conn, T080, [K04])
        res = await api_client.patch(
            "/api/tasks/T-080", json={"status": "you_todo", "laneKey": "todo"}
        )
        assert res.status_code == 200
        assert await _signals(conn) == [("K-04", "negative")]
    finally:
        await conn.close()


async def test_no_signals_without_execute_job(api_client: httpx.AsyncClient) -> None:
    """execute ジョブが無い承認・評価対象外の遷移（spec 開始等）は何も記録しない。"""
    conn = await db_connect()
    try:
        res = await api_client.patch(
            "/api/tasks/T-091", json={"status": "done", "laneKey": "done"}
        )
        assert res.status_code == 200
        res = await api_client.patch("/api/tasks/T-130", json={"status": "spec"})
        assert res.status_code == 200
        assert await _signals(conn) == []
    finally:
        await conn.close()


# ---- mock reconcile の4種提案（決定的） ----------------------------------------------


def _rule_dict(rule_id: str, text: str, **overrides) -> dict:
    base = {
        "id": rule_id,
        "text": text,
        "scope": "personal",
        "tags": [],
        "confidence": "med",
        "source": "",
        "applied": 1,
        "lastAppliedAt": "2026-07-01T00:00:00",
        "createdAt": "2026-07-01T00:00:00",
    }
    return {**base, **overrides}


async def test_mock_reconcile_emits_four_kinds() -> None:
    """conflict（敬体/常体）・merge（同tags＋先頭10字一致）・demote・distill が全て出る。"""
    rules = [
        _rule_dict("K-04", "社外向け文書は敬体で書く", scope="team", confidence="high"),
        _rule_dict("K-06", "報告書は常体で書く", createdAt="2026-07-10T00:00:00"),
        _rule_dict("K-07", "料金は必ず税抜/税込を明記する", tags=["経理"]),
        _rule_dict("K-08", "料金は必ず税抜/税込を明記し、通貨単位も書く", tags=["経理"]),
        _rule_dict("K-09", "未使用のルール", applied=0, lastAppliedAt=None),
    ]
    tasks = [
        {"humanId": "T-080", "title": "経費の分類", "labels": ["経理"],
         "status": "done", "distilled": False},
    ]
    result = await MockProvider().reconcile_rules(rules, tasks, [], [])

    by_kind = {p.kind: p for p in result.proposals}
    assert set(by_kind) == {"conflict", "merge", "demote", "distill"}
    # conflict: 両方を target に、新しい方（K-06 常体）の置き換え文案付き
    assert by_kind["conflict"].target_rule_ids == ["K-04", "K-06"]
    assert by_kind["conflict"].text == "報告書は常体で書く"
    # merge: 同一 tags・text 類似の2件。文案は情報量の多い方
    assert by_kind["merge"].target_rule_ids == ["K-07", "K-08"]
    assert "通貨単位" in by_kind["merge"].text
    # demote: applied==0・シグナルなしの K-09 のみ（conflict/merge 対象は除外）
    assert by_kind["demote"].target_rule_ids == ["K-09"]
    # distill: done かつ未蒸留のタスクから1件
    assert by_kind["distill"].source_task_id == "T-080"
    assert by_kind["distill"].tags == ["経理"]
    assert all(p.note for p in result.proposals)  # note（判断説明）は全 kind 必須


async def test_mock_reconcile_skips_signalled_and_distilled() -> None:
    """シグナルのあるルールは demote 対象外・蒸留済みタスクは distill 対象外。"""
    rules = [_rule_dict("K-09", "未使用のルール", applied=0, lastAppliedAt=None)]
    tasks = [
        {"humanId": "T-080", "title": "済", "labels": [], "status": "done",
         "distilled": True},
    ]
    result = await MockProvider().reconcile_rules(
        rules, tasks, [], [{"ruleId": "K-09", "signal": "negative"}]
    )
    assert result.proposals == []


# ---- CIバッチ（手動実行 API 経由） ---------------------------------------------------


async def test_manual_ci_run_stores_proposals_and_run(
    api_client: httpx.AsyncClient,
) -> None:
    """POST /api/knowledge/ci/run: 提案保存＋実行記録＋SSE 配信。"""
    queue = bus.subscribe()
    try:
        res = await api_client.post("/api/knowledge/ci/run")
        assert res.status_code == 200
        body = res.json()
        # シードは全ルール applied>0・シグナルなし → distill（T-077 done/未蒸留）のみ
        assert body["proposalsCreated"] == 1

        conn = await db_connect()
        try:
            rows = await conn.fetch("select * from rule_proposals")
            assert len(rows) == 1
            assert rows[0]["kind"] == "distill"
            assert rows[0]["status"] == "pending"
            run = await conn.fetchrow(
                "select * from knowledge_ci_runs where id = $1", body["runId"]
            )
            assert run["trigger"] == "manual"
            assert run["proposals_created"] == 1
            assert run["rules_scanned"] == 5
            assert run["tasks_scanned"] == 2  # done の T-080 / T-077
            assert run["finished_at"] is not None
            assert run["input_tokens"] > 0 and run["output_tokens"] > 0
            assert float(run["cost_usd"]) > 0  # Flash 単価で実算定（#25）
        finally:
            await conn.close()

        events = drain_events(queue)
        types = [e["type"] for e in events]
        assert RULE_PROPOSAL_CREATED in types
        assert KNOWLEDGE_CI_COMPLETED in types
        proposal_event = next(e for e in events if e["type"] == RULE_PROPOSAL_CREATED)
        assert proposal_event["payload"]["count"] == 1
        assert proposal_event["payload"]["proposals"][0]["kind"] == "distill"
        completed = next(e for e in events if e["type"] == KNOWLEDGE_CI_COMPLETED)
        assert completed["payload"]["trigger"] == "manual"
        assert completed["payload"]["proposalsCreated"] == 1
    finally:
        bus.unsubscribe(queue)


async def test_ci_run_is_idempotent_for_pending_duplicates(
    api_client: httpx.AsyncClient,
) -> None:
    """同内容の pending 提案は再実行で重複しない（Scheduler 再試行・多重配信対策）。"""
    res = await api_client.post("/api/knowledge/ci/run")
    assert res.json()["proposalsCreated"] == 1
    res = await api_client.post("/api/knowledge/ci/run")
    assert res.json()["proposalsCreated"] == 0
    conn = await db_connect()
    try:
        assert await conn.fetchval("select count(*) from rule_proposals") == 1
        assert await conn.fetchval("select count(*) from knowledge_ci_runs") == 2
    finally:
        await conn.close()


async def test_ci_detects_conflict_between_keitai_and_joutai(
    api_client: httpx.AsyncClient,
) -> None:
    """デモの矛盾シナリオ: 「敬体」(K-04) と「常体」(新規) の併存 → conflict 提案。"""
    conn = await db_connect()
    try:
        # 人が手動蒸留で「常体」ルールを採用した状態を作る（seed には入れない）
        await conn.execute(
            "insert into rules (human_id, workspace_id, scope, text, tags, confidence) "
            "values ('K-06', $1, 'personal', '報告書は常体で書く', '{}', 'med')",
            WS,
        )
        res = await api_client.post("/api/knowledge/ci/run")
        assert res.status_code == 200
        row = await conn.fetchrow(
            "select * from rule_proposals where kind = 'conflict'"
        )
        assert row is not None
        assert row["text"] == "報告書は常体で書く"  # 新しい方を優先した置き換え文案
        targets = await conn.fetch(
            "select human_id from rules where id = any($1::uuid[]) order by human_id",
            list(row["target_rule_ids"]),
        )
        assert [r["human_id"] for r in targets] == ["K-04", "K-06"]
    finally:
        await conn.close()


async def test_ci_applies_confidence_lifecycle(api_client: httpx.AsyncClient) -> None:
    """§6.6: positive≥2 で1段昇格 / negative≥2 で1段降格（提案を介さず自動適用）。"""
    conn = await db_connect()
    try:
        # K-03（med）に positive×2 → high へ昇格 / K-01（high）に negative×2 → med へ降格
        await conn.execute(
            "insert into rule_signals (rule_id, task_id, signal) values "
            "($1, $3, 'positive'), ($1, $3, 'positive'), "
            "($2, $3, 'negative'), ($2, $3, 'negative')",
            K03,
            K01,
            T091,
        )
        queue = bus.subscribe()
        try:
            res = await api_client.post("/api/knowledge/ci/run")
            assert res.status_code == 200
            events = drain_events(queue)
        finally:
            bus.unsubscribe(queue)

        assert await conn.fetchval(
            "select confidence from rules where human_id = 'K-03'"
        ) == "high"
        assert await conn.fetchval(
            "select confidence from rules where human_id = 'K-01'"
        ) == "med"
        # 昇降格は rule.updated で配信される（確度バッジのライブ更新）
        updated = [
            e["payload"]["id"] for e in events if e["type"] == RULE_UPDATED
        ]
        assert set(updated) == {"K-01", "K-03"}
    finally:
        await conn.close()


# ---- /internal/knowledge/ci（Cloud Scheduler ターゲット）のトークン検証 ---------------

TOKEN = "test-knowledge-ci-token"


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERNAL_JOBS_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_internal_ci_rejects_without_token(token_env) -> None:
    """設定時: ヘッダ無しは 403（DB に触れる前に拒否するため DB 不要）。"""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/internal/knowledge/ci")
    assert res.status_code == 403


async def test_internal_ci_accepts_matching_token(
    api_client: httpx.AsyncClient, token_env
) -> None:
    """設定時: 一致ヘッダでバッチ実行に到達する（trigger=scheduled で記録）。"""
    res = await api_client.post(
        "/internal/knowledge/ci", headers={"X-Internal-Jobs-Token": TOKEN}
    )
    assert res.status_code == 200
    assert res.json()["proposalsCreated"] == 1
    conn = await db_connect()
    try:
        assert await conn.fetchval(
            "select trigger from knowledge_ci_runs limit 1"
        ) == "scheduled"
    finally:
        await conn.close()


async def test_internal_ci_passes_through_without_token_config(
    api_client: httpx.AsyncClient,
) -> None:
    """未設定時（ローカル・テスト既定）: ヘッダ無しでも実行される。"""
    res = await api_client.post("/internal/knowledge/ci")
    assert res.status_code == 200


# ---- 受信箱（adopt / dismiss） -------------------------------------------------------


async def _insert_proposal(conn, *, kind: str, text: str = "", targets: list[str] | None = None,
                           source_task: str | None = None) -> str:
    row = await conn.fetchrow(
        "insert into rule_proposals (workspace_id, kind, text, scope, tags, confidence, "
        "source, target_rule_ids, note, source_task_id) "
        "values ($1, $2, $3, 'personal', '{}', 'med', 'テスト出典', $4::uuid[], "
        "'テストの判断説明', $5) returning id",
        WS,
        kind,
        text,
        targets or [],
        source_task,
    )
    return str(row["id"])


async def test_adopt_distill_creates_rule_and_feedback(
    api_client: httpx.AsyncClient,
) -> None:
    """distill 採用: 新ルール作成（K-{seq}・source_task 紐付き）＋feedback＋SSE。"""
    conn = await db_connect()
    try:
        proposal_id = await _insert_proposal(
            conn, kind="distill", text="完了レポートは要点サマリーを先頭に置く",
            source_task=T080,
        )
        res = await api_client.post(f"/api/knowledge/proposals/{proposal_id}/adopt")
        assert res.status_code == 200
        body = res.json()
        assert body["proposal"]["status"] == "adopted"
        assert body["rule"]["id"] == "K-06"  # シード K-05 の次
        assert body["rule"]["sourceTaskId"] == "T-080"
        assert body["archivedRuleIds"] == []

        rule = await conn.fetchrow("select * from rules where human_id = 'K-06'")
        assert rule["text"] == "完了レポートは要点サマリーを先頭に置く"
        assert rule["applied"] == 0 and rule["archived"] is False
        feedback = await conn.fetchrow("select * from rule_feedback")
        assert feedback["action"] == "adopt"
        assert feedback["task_id"] is not None  # source_task がある distill は紐付く
    finally:
        await conn.close()


async def test_adopt_merge_archives_targets_and_creates_rule(
    api_client: httpx.AsyncClient,
) -> None:
    """merge 採用: 対象2件を archived 化＋統合ルールを新規作成。"""
    conn = await db_connect()
    try:
        proposal_id = await _insert_proposal(
            conn, kind="merge", text="統合されたルール", targets=[K01, K03]
        )
        res = await api_client.post(f"/api/knowledge/proposals/{proposal_id}/adopt")
        assert res.status_code == 200
        body = res.json()
        assert body["rule"]["text"] == "統合されたルール"
        assert body["archivedRuleIds"] == ["K-01", "K-03"]
        archived = await conn.fetch(
            "select human_id from rules where archived order by human_id"
        )
        assert [r["human_id"] for r in archived] == ["K-01", "K-03"]
    finally:
        await conn.close()


async def test_adopt_conflict_replaces_rules(api_client: httpx.AsyncClient) -> None:
    """conflict 採用: 矛盾する対象を archived 化＋置き換えルールを新規作成。"""
    conn = await db_connect()
    try:
        proposal_id = await _insert_proposal(
            conn, kind="conflict", text="報告書は常体で書く", targets=[K04]
        )
        res = await api_client.post(f"/api/knowledge/proposals/{proposal_id}/adopt")
        assert res.status_code == 200
        body = res.json()
        assert body["rule"]["text"] == "報告書は常体で書く"
        assert body["archivedRuleIds"] == ["K-04"]
        # 置き換え後は retrieval からも消えている（archived 除外）
        rows = await conn.fetch(
            "select human_id from rules where archived"
        )
        assert [r["human_id"] for r in rows] == ["K-04"]
    finally:
        await conn.close()


async def test_adopt_demote_archives_only(api_client: httpx.AsyncClient) -> None:
    """demote 採用: 対象を archived 化するのみ（新ルールは作らない）。"""
    conn = await db_connect()
    try:
        proposal_id = await _insert_proposal(conn, kind="demote", targets=[K01])
        res = await api_client.post(f"/api/knowledge/proposals/{proposal_id}/adopt")
        assert res.status_code == 200
        body = res.json()
        assert body["rule"] is None
        assert body["archivedRuleIds"] == ["K-01"]
        assert await conn.fetchval("select count(*) from rules") == 5  # 増えない
        feedback = await conn.fetchrow("select * from rule_feedback")
        assert feedback["action"] == "adopt"
        assert feedback["task_id"] is None  # CI 由来（タスク紐付きなし）でも記録できる
        assert feedback["text"] == "テストの判断説明"  # text 空の demote は note を記録
    finally:
        await conn.close()


async def test_dismiss_records_feedback_only(api_client: httpx.AsyncClient) -> None:
    """dismiss: status=dismissed＋feedback 記録のみ（ルールは変更しない）。"""
    conn = await db_connect()
    try:
        proposal_id = await _insert_proposal(conn, kind="demote", targets=[K01])
        res = await api_client.post(f"/api/knowledge/proposals/{proposal_id}/dismiss")
        assert res.status_code == 200
        assert res.json()["status"] == "dismissed"
        assert await conn.fetchval("select count(*) from rules where archived") == 0
        feedback = await conn.fetchrow("select * from rule_feedback")
        assert feedback["action"] == "dismiss"
        # pending 一覧からは消える
        listing = await api_client.get("/api/knowledge/proposals")
        assert listing.json()["proposals"] == []
    finally:
        await conn.close()


async def test_decided_proposal_conflicts(api_client: httpx.AsyncClient) -> None:
    """決定済み提案への再操作は 409。未知 id は 404、不正 id は 422。"""
    conn = await db_connect()
    try:
        proposal_id = await _insert_proposal(conn, kind="demote", targets=[K01])
        assert (
            await api_client.post(f"/api/knowledge/proposals/{proposal_id}/dismiss")
        ).status_code == 200
        assert (
            await api_client.post(f"/api/knowledge/proposals/{proposal_id}/adopt")
        ).status_code == 409
        assert (
            await api_client.post(
                "/api/knowledge/proposals/00000000-0000-4000-8000-000000000000/adopt"
            )
        ).status_code == 404
        assert (
            await api_client.post("/api/knowledge/proposals/not-a-uuid/adopt")
        ).status_code == 422
    finally:
        await conn.close()


async def test_proposals_listing_is_pending_only_newest_first(
    api_client: httpx.AsyncClient,
) -> None:
    """GET /api/knowledge/proposals は pending のみ・新しい順・human_id 解決済み。"""
    conn = await db_connect()
    try:
        first = await _insert_proposal(conn, kind="demote", targets=[K01])
        await asyncio.sleep(0.01)  # created_at の順序を安定させる
        second = await _insert_proposal(conn, kind="conflict", text="置換案", targets=[K04])
        res = await api_client.get("/api/knowledge/proposals")
        proposals = res.json()["proposals"]
        assert [p["id"] for p in proposals] == [second, first]
        assert proposals[0]["targetRuleIds"] == ["K-04"]
        assert proposals[1]["targetRuleIds"] == ["K-01"]
    finally:
        await conn.close()
