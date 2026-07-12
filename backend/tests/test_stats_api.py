"""学習・コストダッシュボード API（GET /api/stats, #25）のテスト。

シード直後のゼロ状態と、実作業（assign-ai 完走）＋差し戻し後の集計を検証する。
"""

import httpx
import pytest

from app.jobs import queue as jobs_queue
from app.repo.stats import RULE_APPLICATIONS_DAYS
from tests.helpers import db_connect, drain_jobs


@pytest.fixture
def captured_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """enqueue をフェイク化（ジョブは手動で /internal/jobs/run を叩く）。"""
    jobs: list[str] = []

    async def _fake_enqueue(job_id: str, *, kind: str | None = None) -> None:
        jobs.append(job_id)

    monkeypatch.setattr(jobs_queue, "enqueue_job", _fake_enqueue)
    return jobs


async def test_stats_initial_seed_state(api_client: httpx.AsyncClient) -> None:
    """シード直後: ジョブ・差し戻しゼロ、ルールはシードの5件（applied 合計 36）。"""
    res = await api_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["aiDoneCount"] == 0
    assert body["totalCostUsd"] == 0.0
    assert body["totalTokens"] == 0
    assert body["rejectCount"] == 0
    assert body["rulesCount"] == 5
    # シードの applied（K-01:6, K-02:14, K-03:2, K-04:9, K-05:5）の合計
    assert body["ruleApplicationsTotal"] == 36
    # 直近14日（今日を含む・古い順・欠損日は 0）
    points = body["ruleApplications"]
    assert len(points) == RULE_APPLICATIONS_DAYS
    assert all(p["count"] == 0 for p in points)
    dates = [p["date"] for p in points]
    assert dates == sorted(dates)


async def test_stats_after_execution_and_reject(
    api_client: httpx.AsyncClient, captured_jobs: list[str]
) -> None:
    """assign-ai 完走＋差し戻し後: AI完了・コスト・トークン・適用・差し戻しが動く。"""
    res = await api_client.post("/api/tasks/T-104/assign-ai")
    assert res.status_code == 202
    await drain_jobs(api_client, captured_jobs)

    # 人の構造化差し戻し（#23）→ 再実行チェーンも完走させる
    # （理由は mock の矛盾検出キーワード「ルール」「逆」を含めず、降格を発生させない）
    rej = await api_client.post(
        "/api/tasks/T-104/reject", json={"reason": "数値の出典を補強してください"}
    )
    assert rej.status_code == 202
    await drain_jobs(api_client, captured_jobs)

    res = await api_client.get("/api/stats")
    assert res.status_code == 200
    body = res.json()

    conn = await db_connect()
    try:
        # aiDoneCount = succeeded した execute ジョブ数（DB と一致）
        expected_done = await conn.fetchval(
            "select count(*) from ai_jobs where kind = 'execute' and status = 'succeeded'"
        )
        assert body["aiDoneCount"] == expected_done
        assert expected_done >= 2  # 初回チェーンで2回（revise 込み）＋差し戻し再実行

        # 累計コスト（実算定 #25）とトークンが DB 合計と一致して正になる
        totals = await conn.fetchrow(
            "select coalesce(sum(cost_usd), 0) as cost, "
            "coalesce(sum(coalesce(input_tokens,0)+coalesce(output_tokens,0)), 0) as tokens "
            "from ai_jobs"
        )
        assert body["totalCostUsd"] == pytest.approx(float(totals["cost"]))
        assert body["totalCostUsd"] > 0.0
        assert body["totalTokens"] == int(totals["tokens"])
        assert body["totalTokens"] > 0

        # 適用の学習曲線: 今日の点に rule_applications 全件が乗る（テストは全て当日実行）
        applications = await conn.fetchval("select count(*) from rule_applications")
        assert applications > 0
        points = body["ruleApplications"]
        assert len(points) == RULE_APPLICATIONS_DAYS
        assert points[-1]["count"] == applications
        assert sum(p["count"] for p in points) == applications

        # 適用回数の累計 = rules.applied 合計（シード 36 + 今回の適用分）
        applied_sum = await conn.fetchval("select sum(applied) from rules")
        assert body["ruleApplicationsTotal"] == int(applied_sum)
        assert body["ruleApplicationsTotal"] > 36
    finally:
        await conn.close()

    # 差し戻しは【差し戻し理由】コメント数で数える（#23 reject 1回分）
    assert body["rejectCount"] == 1
    assert body["rulesCount"] == 5
