"""書き込みレート制限（assert_write_rate, #security）とデモDB自動リセット
（POST /internal/demo/reset）のテスト。

書き込みガード:
- IP 単位のスライディングウィンドウ。同一 IP（X-Forwarded-For 固定）で上限+1 回目に 429、
  別 IP は影響を受けない。ウィンドウ経過で回復。ai_guard_enabled=False で素通し。
- エンドポイント経由（artifacts POST）でも 429 が HTTP に乗ることを確認する。

デモリセット:
- INTERNAL_JOBS_TOKEN 設定時、トークン不一致は 403。一致すると 200 で truncate → seed.sql
  再投入が走り、seed の件数（tasks/rules）が返る（seed.sql のパス解決が効くことの確認）。
"""

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import guard
from app.config import get_settings
from app.guard import (
    WRITE_RATE_LIMIT_DETAIL,
    assert_write_rate,
    reset_write_rate_state,
)


def _make_request(*, xff: str | None = None, client_host: str = "10.0.0.1") -> Request:
    """テスト用の最小 ASGI Request（X-Forwarded-For / client.host を持つ）。"""
    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": headers,
        "client": (client_host, 12345),
        "query_string": b"",
    }
    return Request(scope)


# ---- 書き込みレート（プロセス内 IP 単位スライディングウィンドウ） --------------------


def test_write_rate_per_ip_limit_and_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 IP は上限+1 回目で 429。別 IP は独立にカウントされ影響を受けない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "write_rate_max", 3)
    monkeypatch.setattr(settings, "write_rate_window_sec", 100)
    reset_write_rate_state()

    req_a = _make_request(xff="1.1.1.1")
    req_b = _make_request(xff="2.2.2.2")

    # IP-A は上限ちょうど（3回）まで素通し
    for _ in range(3):
        assert_write_rate(req_a)

    # IP-A の 4回目（上限+1）は 429
    with pytest.raises(HTTPException) as exc_info:
        assert_write_rate(req_a)
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == WRITE_RATE_LIMIT_DETAIL

    # IP-B は別カウント → まだ通る
    for _ in range(3):
        assert_write_rate(req_b)
    with pytest.raises(HTTPException):
        assert_write_rate(req_b)


def test_write_rate_recovers_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウィンドウ経過で古い記録が抜けて回復する（monotonic は _now を差し替え）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "write_rate_max", 2)
    monkeypatch.setattr(settings, "write_rate_window_sec", 100)
    reset_write_rate_state()

    clock = {"t": 1000.0}
    monkeypatch.setattr(guard, "_now", lambda: clock["t"])

    req = _make_request(xff="3.3.3.3")
    assert_write_rate(req)
    assert_write_rate(req)
    with pytest.raises(HTTPException) as exc_info:
        assert_write_rate(req)
    assert exc_info.value.status_code == 429

    # +100s でウィンドウを抜け、記録が掃除されて回復する
    clock["t"] = 1000.0 + 100
    assert_write_rate(req)  # 例外が出なければ回復している


def test_write_rate_disabled_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """ai_guard_enabled=False なら上限を無視して素通しする。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", False)
    monkeypatch.setattr(settings, "write_rate_max", 1)
    reset_write_rate_state()

    req = _make_request(xff="4.4.4.4")
    for _ in range(10):
        assert_write_rate(req)  # 何回呼んでも 429 にならない


def test_write_rate_falls_back_to_client_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Forwarded-For が無ければ request.client.host 単位で数える。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "write_rate_max", 1)
    monkeypatch.setattr(settings, "write_rate_window_sec", 100)
    reset_write_rate_state()

    req1 = _make_request(client_host="192.0.2.1")
    req2 = _make_request(client_host="192.0.2.2")

    assert_write_rate(req1)
    with pytest.raises(HTTPException):
        assert_write_rate(req1)  # 同一 host は上限超過
    assert_write_rate(req2)  # 別 host は独立


def test_write_rate_empty_ip_keys_are_pruned(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウィンドウを抜けて空になった IP のキーは dict から削除される（メモリ肥大の防止）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "write_rate_max", 5)
    monkeypatch.setattr(settings, "write_rate_window_sec", 100)
    reset_write_rate_state()

    clock = {"t": 1000.0}
    monkeypatch.setattr(guard, "_now", lambda: clock["t"])

    assert_write_rate(_make_request(xff="9.9.9.9"))
    assert "9.9.9.9" in guard._write_events

    # 別 IP のアクセスがウィンドウ経過後に来ると、古い IP のキーが掃除される
    clock["t"] = 1000.0 + 100
    assert_write_rate(_make_request(xff="8.8.8.8"))
    assert "9.9.9.9" not in guard._write_events
    assert "8.8.8.8" in guard._write_events


# ---- 統合: 書き込みエンドポイント（artifacts POST）に 429 が乗る -----------------------


async def test_write_rate_blocks_via_endpoint(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一 IP の書き込みが上限を超えると HTTP 429。別 IP は影響を受けない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_guard_enabled", True)
    monkeypatch.setattr(settings, "write_rate_max", 2)
    monkeypatch.setattr(settings, "write_rate_window_sec", 100)
    reset_write_rate_state()

    headers_a = {"X-Forwarded-For": "203.0.113.10"}
    for _ in range(2):
        res = await api_client.post(
            "/api/tasks/T-104/artifacts", json={"contentMd": "x"}, headers=headers_a
        )
        assert res.status_code == 201

    # 3回目（上限+1）は 429
    blocked = await api_client.post(
        "/api/tasks/T-104/artifacts", json={"contentMd": "x"}, headers=headers_a
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == WRITE_RATE_LIMIT_DETAIL

    # 別 IP は独立 → まだ通る
    ok = await api_client.post(
        "/api/tasks/T-104/artifacts",
        json={"contentMd": "x"},
        headers={"X-Forwarded-For": "203.0.113.99"},
    )
    assert ok.status_code == 201


# ---- デモDB自動リセット（POST /internal/demo/reset） --------------------------------

TOKEN = "test-internal-jobs-token"


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch):
    """INTERNAL_JOBS_TOKEN を設定して settings キャッシュを更新する。"""
    monkeypatch.setenv("INTERNAL_JOBS_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_demo_reset_rejects_wrong_token(
    api_client: httpx.AsyncClient, token_env
) -> None:
    """トークン設定時: ヘッダ無し／不一致は 403（DB に触れる前に拒否）。"""
    no_header = await api_client.post("/internal/demo/reset")
    assert no_header.status_code == 403

    wrong = await api_client.post(
        "/internal/demo/reset", headers={"X-Internal-Jobs-Token": "wrong"}
    )
    assert wrong.status_code == 403


async def test_demo_reset_truncates_and_reseeds(
    api_client: httpx.AsyncClient, token_env
) -> None:
    """一致トークン: truncate → seed.sql 再投入が走り、seed の件数が返る。"""
    res = await api_client.post(
        "/internal/demo/reset", headers={"X-Internal-Jobs-Token": TOKEN}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reset"] is True
    # seed.sql の正準件数（tasks 11 件 / rules K-01..05 の 5 件）
    assert body["tasks"] == 11
    assert body["rules"] == 5


async def test_demo_reset_passthrough_without_token(
    api_client: httpx.AsyncClient,
) -> None:
    """トークン未設定（ローカル・テスト既定）ならヘッダ無しでも 200 で実行できる。"""
    res = await api_client.post("/internal/demo/reset")
    assert res.status_code == 200
    assert res.json()["reset"] is True
