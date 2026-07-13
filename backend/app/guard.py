"""AI 利用ガード（#security — 無認証の公開 Cloud Run での課金暴走・悪意アクセス対策）。

公開する AI 起動エンドポイント（assign-ai / autopilot / reject / chat / learn /
knowledge CI / タスク作成の intake）の先頭に、2 層の安全弁をかける:

1. レート制限（assert_ai_rate）— プロセス内スライディングウィンドウ。
   ai_rate_window_sec 内の AI 起動回数が ai_rate_max 以上なら 429。時刻は
   time.monotonic() ベース（壁時計の巻き戻り・NTP 補正の影響を受けない単調増加時計）。
   max-instances=1 前提でプロセス内状態のみで十分（インスタンスは常に1つ）。

2. 日次予算キルスイッチ（assert_ai_budget）— 当日（サーバ時刻 UTC の date）の
   sum(ai_jobs.cost_usd) が ai_daily_budget_usd 以上なら 429。翌 UTC 日付で自動回復する。

併用は guard_ai_action(conn)（rate → budget の順）。ai_guard_enabled=False なら
どちらも素通しする（テスト/ローカル）。#21 のタスク別コスト上限（policy.costCapUsd）とは
別レイヤーで、二重チェックになっても問題ない（全体の1日上限＋レート）。
"""

from __future__ import annotations

import time
from collections import deque

import asyncpg
from fastapi import HTTPException, Request

from app.config import get_settings

# 429 の日本語 detail（FE の fetch 失敗 → boardError にそのまま乗る）
RATE_LIMIT_DETAIL = "アクセスが集中しています。しばらくして再度お試しください。"
BUDGET_LIMIT_DETAIL = "本日のAI利用上限に達しました。明日また利用できます。"
WRITE_RATE_LIMIT_DETAIL = "書き込みが多すぎます。しばらくして再度お試しください。"

# AI 起動時刻（monotonic 秒）のスライディングウィンドウ。プロセス内で共有する
# （max-instances=1 なのでインスタンス間分散は不要）。
_rate_events: deque[float] = deque()

# 書き込み時刻（monotonic 秒）の IP 単位スライディングウィンドウ。IP ごとに deque を持つ
# （max-instances=1 前提のプロセス内状態）。空になった IP のキーは掃除して肥大を防ぐ。
_write_events: dict[str, deque[float]] = {}


def _now() -> float:
    """単調増加時計（テストで差し替えられるよう間接化）。time.monotonic() を使う。"""
    return time.monotonic()


def reset_rate_state() -> None:
    """スライディングウィンドウを空にする（テスト間で状態を持ち越さないためのフック）。"""
    _rate_events.clear()


def reset_write_rate_state() -> None:
    """IP 単位の書き込みウィンドウを空にする（テスト間で状態を持ち越さないためのフック）。"""
    _write_events.clear()


def _client_ip(request: Request) -> str:
    """クライアント IP を取得する。Cloud Run 等のプロキシ経由では X-Forwarded-For の
    先頭（最も外側のクライアント）を採用し、無ければ直接接続元にフォールバックする。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def assert_write_rate(request: Request) -> None:
    """書き込み系エンドポイントの IP 単位レート制限（プロセス内スライディングウィンドウ）。

    呼ばれるたびに当該 IP の現在時刻（monotonic）を記録する。window 内の書き込み回数が
    write_rate_max 以上なら HTTPException(429) を送出する。ai_guard_enabled=False なら
    何もしない。掃除: 呼び出しごとに全 IP の window 外エントリを捨て、空になったキーは
    dict から削除する（アクセスが途絶えた IP を残さず、dict を「window 内に活動のある
    IP」だけに抑える）。max-instances=1 前提のプロセス内状態（#24）。
    """
    settings = get_settings()
    if not settings.ai_guard_enabled:
        return
    now = _now()
    window = settings.write_rate_window_sec
    ip = _client_ip(request)

    # 全 IP の古いエントリを掃除し、空になったキーを削除する（メモリ肥大の防止）。
    for known_ip in list(_write_events.keys()):
        events = _write_events[known_ip]
        while events and now - events[0] >= window:
            events.popleft()
        if not events:
            del _write_events[known_ip]

    events = _write_events.setdefault(ip, deque())
    if len(events) >= settings.write_rate_max:
        raise HTTPException(status_code=429, detail=WRITE_RATE_LIMIT_DETAIL)
    events.append(now)


def assert_ai_rate() -> None:
    """プロセス内スライディングウィンドウでレート制限する。

    呼ばれるたびに現在時刻（monotonic）を記録する。window 内の起動回数が
    ai_rate_max 以上なら HTTPException(429) を送出する。ai_guard_enabled=False なら
    何もしない。
    """
    settings = get_settings()
    if not settings.ai_guard_enabled:
        return
    now = _now()
    window = settings.ai_rate_window_sec
    # ウィンドウから外れた古いタイムスタンプを捨てる
    while _rate_events and now - _rate_events[0] >= window:
        _rate_events.popleft()
    if len(_rate_events) >= settings.ai_rate_max:
        raise HTTPException(status_code=429, detail=RATE_LIMIT_DETAIL)
    _rate_events.append(now)


async def assert_ai_budget(conn: asyncpg.Connection) -> None:
    """当日（UTC）の sum(ai_jobs.cost_usd) が日次予算以上なら 429（キルスイッチ）。

    ai_guard_enabled=False なら何もしない。conn は読み取り専用に使う（トランザクション
    外で呼んでよい）。
    """
    settings = get_settings()
    if not settings.ai_guard_enabled:
        return
    spent = await conn.fetchval(
        "select coalesce(sum(cost_usd), 0) from ai_jobs "
        "where (created_at at time zone 'utc')::date = (now() at time zone 'utc')::date"
    )
    if float(spent) >= settings.ai_daily_budget_usd:
        raise HTTPException(status_code=429, detail=BUDGET_LIMIT_DETAIL)


async def guard_ai_action(conn: asyncpg.Connection) -> None:
    """AI 起動エンドポイントの先頭で呼ぶ併用ヘルパ（rate → budget の順でチェック）。"""
    assert_ai_rate()
    await assert_ai_budget(conn)
