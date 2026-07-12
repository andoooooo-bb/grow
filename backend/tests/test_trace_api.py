"""意思決定トレース API（GET /api/tasks/{human_id}/trace, #25）のテスト。

版ごとに「どのジョブが・どのルール（K-xx）を前提に・何トークン/$いくらで生成したか」を
返し、人の編集版はジョブ由来フィールドが null/空になることを検証する。
"""

import httpx
import pytest

from app.jobs import queue as jobs_queue
from tests.helpers import db_connect, drain_jobs


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（ジョブは手動で /internal/jobs/run を叩く）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str, *, kind: str | None = None) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


async def test_trace_empty_before_any_artifact(api_client: httpx.AsyncClient) -> None:
    """成果物ゼロのタスクは entries が空（200）。"""
    res = await api_client.get("/api/tasks/T-104/trace")
    assert res.status_code == 200
    assert res.json() == {"taskId": "T-104", "entries": []}


async def test_trace_unknown_task_returns_404(api_client: httpx.AsyncClient) -> None:
    res = await api_client.get("/api/tasks/T-999/trace")
    assert res.status_code == 404


async def test_trace_joins_jobs_rules_and_human_edit(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """完走（v1/v2 = execute 生成）＋人の編集（v3）のトレース結合形。

    - AI生成版: kind='execute'・appliedRuleIds は human_id（K-xx）解決・注入順を保持・
      tokens/costUsd が正・jobId が実ジョブと一致
    - 人の編集版: jobId/kind/status/tokens/costUsd が null・appliedRuleIds 空
    """
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    first_job_id = res.json()["jobId"]
    await drain_jobs(api_client, captured_jobs)  # execute→review→execute→review 完走

    # 人の編集を新版（v3）として保存する（#10 と同じ API）
    edited = await api_client.post(
        "/api/tasks/T-104/artifacts", json={"contentMd": "# 人が編集した最終版"}
    )
    assert edited.status_code == 201

    res = await api_client.get("/api/tasks/T-104/trace")
    assert res.status_code == 200
    body = res.json()
    assert body["taskId"] == "T-104"
    entries = body["entries"]
    assert [e["version"] for e in entries] == [1, 2, 3]

    # v1/v2: execute 生成（レビューAIの revise を挟んだ2版）
    for entry in entries[:2]:
        assert entry["kind"] == "execute"
        assert entry["status"] == "succeeded"
        # retrieval の注入順（confidence 降順 → applied 降順 → human_id）で K-xx 解決
        assert entry["appliedRuleIds"] == ["K-02", "K-04", "K-01", "K-03"]
        assert entry["inputTokens"] > 0
        assert entry["outputTokens"] > 0
        assert entry["costUsd"] > 0.0  # 実算定（#25）
        assert entry["createdAt"]
        assert entry["finishedAt"]
    assert entries[0]["jobId"] == first_job_id

    # v1 と v2 は別ジョブ（revise 再実行）
    assert entries[0]["jobId"] != entries[1]["jobId"]

    # v3: 人の編集版 = 「あなたが編集」（ジョブ由来フィールドはすべて null/空）
    human = entries[2]
    assert human["jobId"] is None
    assert human["kind"] is None
    assert human["status"] is None
    assert human["appliedRuleIds"] == []
    assert human["inputTokens"] is None
    assert human["outputTokens"] is None
    assert human["costUsd"] is None
    assert human["finishedAt"] is None
    assert human["createdAt"]

    # DB との突き合わせ: costUsd はジョブ行の記録値と一致する
    conn = await db_connect()
    try:
        job = await conn.fetchrow(
            "select * from ai_jobs where id = $1::uuid", first_job_id
        )
        assert entries[0]["costUsd"] == pytest.approx(float(job["cost_usd"]))
        assert entries[0]["inputTokens"] == job["input_tokens"]
        assert entries[0]["outputTokens"] == job["output_tokens"]
    finally:
        await conn.close()
