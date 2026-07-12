"""学習ダッシュボード統計（#25 GET /api/stats）のリポジトリ層。

すべて既存テーブル（ai_jobs / rule_applications / rules / comments）で完結する。
MVP は単一 workspace（§02 冒頭）なので全体集計でよい。
"""

import asyncpg

from app.ai.provider import REJECT_REASON_PREFIX
from app.domain.dto import RuleApplicationPoint, StatsResponse
from app.domain.models import AiJobKind

# 学習曲線スパークラインの対象期間（今日を含む直近14日）
RULE_APPLICATIONS_DAYS = 14


async def fetch_stats(conn: asyncpg.Connection) -> StatsResponse:
    """ダッシュボードのスタット一式を集計する。"""
    ai_done_count = await conn.fetchval(
        "select count(*) from ai_jobs where kind = $1 and status = 'succeeded'",
        AiJobKind.EXECUTE.value,
    )
    totals = await conn.fetchrow(
        "select coalesce(sum(cost_usd), 0) as cost, "
        "coalesce(sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)), 0) as tokens "
        "from ai_jobs"
    )
    # 直近14日の日別ルール適用回数（欠損日は 0 で埋める。古い順）
    daily_rows = await conn.fetch(
        """
        select to_char(d.day, 'YYYY-MM-DD') as date, count(ra.id)::int as count
        from generate_series(
               current_date - ($1::int - 1) * interval '1 day',
               current_date,
               interval '1 day'
             ) as d(day)
        left join rule_applications ra on ra.applied_at::date = d.day::date
        group by d.day
        order by d.day
        """,
        RULE_APPLICATIONS_DAYS,
    )
    # 適用回数の累計はルールカードの「適用 N回」と同じ真実（rules.applied の合計）を使う
    rule_applications_total = await conn.fetchval(
        "select coalesce(sum(applied), 0) from rules"
    )
    reject_count = await conn.fetchval(
        "select count(*) from comments where author = 'human' and text like $1",
        f"{REJECT_REASON_PREFIX}%",
    )
    rules_count = await conn.fetchval("select count(*) from rules")
    return StatsResponse(
        ai_done_count=ai_done_count,
        total_cost_usd=float(totals["cost"]),
        total_tokens=int(totals["tokens"]),
        rule_applications=[
            RuleApplicationPoint(date=row["date"], count=row["count"]) for row in daily_rows
        ],
        rule_applications_total=int(rule_applications_total),
        reject_count=reject_count,
        rules_count=rules_count,
    )
