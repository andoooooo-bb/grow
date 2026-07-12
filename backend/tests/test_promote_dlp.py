"""チーム昇格DLPガードレール API（#29 §6.7）のテスト。

- POST /api/rules/{id}/promote: 機微情報検出で 409 {detail, findings}、クリーンで昇格
- POST /api/rules/{id}/generalize: 決定的な一般化文案 {original, generalized}
- POST /api/rules/{id}/promote {text}: 文案を再スキャン → 通過で更新して昇格

seed の K-01（personal・クリーン文）に機微情報を仕込んで検出させ、AI_PROVIDER=mock の
正規表現スタブ＋決定的一般化で完結させる（実 GCP は呼ばない）。
"""

import httpx
import pytest

from app.events import bus
from tests.helpers import db_connect, drain_events

# 機微情報を含むルール文（人名・メール・電話）。昇格ガードで 409 になるはず
DIRTY_TEXT = "田中様のレポートは hiroki@example.com か 03-1234-5678 へ送る"


@pytest.fixture
def event_queue():
    queue = bus.subscribe()
    yield queue
    bus.unsubscribe(queue)


async def _set_rule_text(human_id: str, text: str) -> None:
    conn = await db_connect()
    try:
        await conn.execute(
            "update rules set text = $2 where human_id = $1", human_id, text
        )
    finally:
        await conn.close()


# ---- promote ガード（409 + findings） ----------------------------------------------


async def test_promote_blocked_returns_409_with_findings(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """機微情報を含む personal ルールの昇格は 409。findings に種別・該当文字列。scope 不変。"""
    await _set_rule_text("K-01", DIRTY_TEXT)

    res = await api_client.post("/api/rules/K-01/promote")
    assert res.status_code == 409
    body = res.json()
    assert body["detail"] == "機微情報が含まれるためチーム昇格できません"
    info_types = {f["infoType"] for f in body["findings"]}
    assert info_types == {"PERSON_NAME", "EMAIL_ADDRESS", "PHONE_NUMBER"}
    quotes = {f["quote"] for f in body["findings"]}
    assert "hiroki@example.com" in quotes

    # 昇格していない（scope=personal のまま）・SSE も飛ばない
    conn = await db_connect()
    try:
        row = await conn.fetchrow("select scope from rules where human_id = 'K-01'")
        assert row["scope"] == "personal"
    finally:
        await conn.close()
    assert drain_events(event_queue) == []


async def test_promote_clean_rule_succeeds(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """機微情報を含まないクリーンな K-01（seed 文）は従来どおり昇格する（200・SSE）。"""
    res = await api_client.post("/api/rules/K-01/promote")
    assert res.status_code == 200
    assert res.json()["scope"] == "team"
    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["rule.updated"]


# ---- generalize（決定的文案） ------------------------------------------------------


async def test_generalize_returns_deterministic_draft(
    api_client: httpx.AsyncClient,
) -> None:
    """generalize は findings の quote を伏字化＋「（一般化済み）」を付けた文案を返す。"""
    await _set_rule_text("K-01", DIRTY_TEXT)

    res = await api_client.post("/api/rules/K-01/generalize")
    assert res.status_code == 200
    body = res.json()
    assert body["original"] == DIRTY_TEXT
    # mock: quote を「◯◯」へ置換し末尾に「（一般化済み）」
    assert body["generalized"].endswith("（一般化済み）")
    assert "hiroki@example.com" not in body["generalized"]
    assert "田中" not in body["generalized"]
    assert "03-1234-5678" not in body["generalized"]
    assert "◯◯" in body["generalized"]


async def test_generalize_not_found(api_client: httpx.AsyncClient) -> None:
    res = await api_client.post("/api/rules/K-99/generalize")
    assert res.status_code == 404


# ---- text 付き promote（更新 → 再スキャン → 昇格） --------------------------------


async def test_promote_with_clean_text_updates_and_promotes(
    api_client: httpx.AsyncClient, event_queue
) -> None:
    """text 付き promote: 機微情報のないクリーン文案なら text を更新してから昇格する。"""
    await _set_rule_text("K-01", DIRTY_TEXT)
    clean = "担当者のレポートは所定の宛先へ送る（一般化済み）"

    res = await api_client.post("/api/rules/K-01/promote", json={"text": clean})
    assert res.status_code == 200
    rule = res.json()
    assert rule["scope"] == "team"
    assert rule["text"] == clean

    conn = await db_connect()
    try:
        row = await conn.fetchrow("select scope, text from rules where human_id = 'K-01'")
        assert row["scope"] == "team"
        assert row["text"] == clean
    finally:
        await conn.close()

    events = drain_events(event_queue)
    assert [e["type"] for e in events] == ["rule.updated"]
    assert events[0]["payload"]["text"] == clean


async def test_promote_with_dirty_text_still_blocked(
    api_client: httpx.AsyncClient,
) -> None:
    """text 付きでも、その文案に機微情報が残っていれば 409（text も更新しない）。"""
    await _set_rule_text("K-01", "クリーンな元文")
    res = await api_client.post(
        "/api/rules/K-01/promote", json={"text": "山田様へ連絡する"}
    )
    assert res.status_code == 409
    assert {f["infoType"] for f in res.json()["findings"]} == {"PERSON_NAME"}

    conn = await db_connect()
    try:
        row = await conn.fetchrow("select scope, text from rules where human_id = 'K-01'")
        # 更新も昇格もされていない
        assert row["scope"] == "personal"
        assert row["text"] == "クリーンな元文"
    finally:
        await conn.close()
