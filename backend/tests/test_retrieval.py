"""retrieval（app/repo/rules.py relevant_rules, §6.3 / §00 #8）の単体テスト。

シードの前提（db/seed.sql）:
- K-01 personal high applied=6  tags[調査,ブログ]
- K-02 personal high applied=14 tags[]（全体ルール）
- K-03 personal med  applied=2  tags[調査]
- K-04 team     high applied=9  tags[]（全体ルール）
- K-05 team     high applied=5  tags[経理]
"""

import httpx

from app.repo import rules as rules_repo
from tests.helpers import db_connect

WS = "11111111-1111-4111-8111-111111111111"


async def _task_row(conn, human_id: str):
    return await conn.fetchrow("select * from tasks where human_id = $1", human_id)


async def test_tag_intersection_and_global_rules(api_client: httpx.AsyncClient) -> None:
    """タグ交差＋全体ルールが対象になり、confidence降順→applied降順で並ぶ。"""
    conn = await db_connect()
    try:
        # T-104: labels [仕事, 調査] → K-01/K-02/K-03/K-04（K-05 経理は対象外）
        rows = await rules_repo.relevant_rules(conn, await _task_row(conn, "T-104"))
        assert [r["human_id"] for r in rows] == ["K-02", "K-04", "K-01", "K-03"]

        # T-121: labels [経理] → 全体ルール + K-05。personal/team 両方が対象
        rows = await rules_repo.relevant_rules(conn, await _task_row(conn, "T-121"))
        assert [r["human_id"] for r in rows] == ["K-02", "K-04", "K-05"]
        assert {r["scope"] for r in rows} == {"personal", "team"}
    finally:
        await conn.close()


async def test_confidence_order_beats_applied(api_client: httpx.AsyncClient) -> None:
    """applied がどれだけ多くても confidence が低ければ後ろに並ぶ。"""
    conn = await db_connect()
    try:
        await conn.execute(
            "insert into rules (human_id, workspace_id, scope, text, tags, confidence, applied) "
            "values ('K-90', $1, 'personal', 'med だが applied 最多', '{}', 'med', 100), "
            "       ('K-91', $1, 'personal', 'low ルール', '{}', 'low', 200)",
            WS,
        )
        rows = await rules_repo.relevant_rules(conn, await _task_row(conn, "T-104"))
        ids = [r["human_id"] for r in rows]
        # high 群（K-02, K-04, K-01）→ med 群（K-90 applied100 > K-03 applied2）→ low 群
        assert ids == ["K-02", "K-04", "K-01", "K-90", "K-03", "K-91"]
    finally:
        await conn.close()


async def test_limit_8(api_client: httpx.AsyncClient) -> None:
    """上限8件・confidence降順で足切りされる（§00 #8）。"""
    conn = await db_connect()
    try:
        for i in range(10):
            await conn.execute(
                "insert into rules (human_id, workspace_id, scope, text, tags, "
                "confidence, applied) values ($1, $2, 'team', $3, '{}', 'high', $4)",
                f"K-8{i}",
                WS,
                f"追加ルール {i}",
                1000 + i,
            )
        rows = await rules_repo.relevant_rules(conn, await _task_row(conn, "T-104"))
        assert len(rows) == rules_repo.RETRIEVAL_LIMIT == 8
        # 全て high・applied 降順（1009..1000 > 既存の K-02:14 等なので追加分が先頭）
        assert [r["human_id"] for r in rows[:3]] == ["K-89", "K-88", "K-87"]
        assert all(r["confidence"] == "high" for r in rows)
    finally:
        await conn.close()


async def test_no_matching_rules(api_client: httpx.AsyncClient) -> None:
    """該当ルールが無ければ空リスト。"""
    conn = await db_connect()
    try:
        await conn.execute("delete from rules")
        rows = await rules_repo.relevant_rules(conn, await _task_row(conn, "T-104"))
        assert rows == []
    finally:
        await conn.close()
