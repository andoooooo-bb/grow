"""§02 データモデルのスキーマ/シード検証（実DBを使用）。

DATABASE_URL（既定 postgresql://grow:grow@localhost:54329/grow）に接続できない場合は
skip する。実行手順: scripts/devdb.sh start → make migrate && make seed →
cd backend && uv run pytest tests/test_schema.py
"""

import asyncpg
import pytest

from app.config import get_settings

EXPECTED_TABLES = {
    "workspaces",
    "users",
    "boards",
    "lanes",
    "tasks",
    "comments",
    "chat_messages",
    "rules",
    "rule_applications",
    "ai_jobs",
    "artifacts",
}


@pytest.fixture
async def conn():
    """実DBへの接続。接続できなければテストを skip する。"""
    try:
        connection = await asyncpg.connect(get_settings().database_url, timeout=3)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"DATABASE_URL に接続できないため skip: {exc}")
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def seeded(conn):
    """シード投入済みDB。スキーマ未適用/シード未投入なら skip する。"""
    try:
        count = await conn.fetchval("select count(*) from workspaces")
    except asyncpg.PostgresError as exc:
        pytest.skip(f"スキーマ未適用のため skip（make migrate を実行）: {exc}")
    if count == 0:
        pytest.skip("シード未投入のため skip（make seed を実行）")
    return conn


# ---- スキーマ（DoD: マイグレーション適用でスキーマが再現できる） ----------------


async def test_all_tables_exist(conn):
    rows = await conn.fetch(
        "select table_name from information_schema.tables "
        "where table_schema = 'public' and table_type = 'BASE TABLE'"
    )
    tables = {r["table_name"] for r in rows}
    missing = EXPECTED_TABLES - tables
    assert not missing, f"存在しないテーブル: {missing}"


async def test_artifacts_unique_task_id_version(conn):
    rows = await conn.fetch(
        """
        select c.conname, array_agg(a.attname order by k.ord) as cols
        from pg_constraint c
        join lateral unnest(c.conkey) with ordinality as k(attnum, ord) on true
        join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
        where c.conrelid = 'artifacts'::regclass and c.contype = 'u'
        group by c.conname
        """
    )
    unique_column_sets = [tuple(r["cols"]) for r in rows]
    assert ("task_id", "version") in unique_column_sets


async def test_artifacts_latest_version_index(conn):
    rows = await conn.fetch("select indexdef from pg_indexes where tablename = 'artifacts'")
    indexdefs = [r["indexdef"] for r in rows]
    assert any("(task_id, version DESC)" in d for d in indexdefs), indexdefs


async def test_ai_jobs_token_and_cost_columns(conn):
    rows = await conn.fetch(
        "select column_name, data_type from information_schema.columns "
        "where table_schema = 'public' and table_name = 'ai_jobs'"
    )
    cols = {r["column_name"]: r["data_type"] for r in rows}
    assert cols.get("input_tokens") == "integer"
    assert cols.get("output_tokens") == "integer"
    assert cols.get("cost_usd") == "numeric"


# ---- シード（DoD: §2.5 のタスク/ルールが SELECT で確認できる） ------------------


async def test_seed_counts(seeded):
    assert await seeded.fetchval("select count(*) from tasks") == 11
    assert await seeded.fetchval("select count(*) from rules") == 5
    assert await seeded.fetchval("select count(*) from lanes") == 5


async def test_seed_lanes_in_position_order(seeded):
    rows = await seeded.fetch("select key from lanes order by position")
    assert [r["key"] for r in rows] == ["backlog", "todo", "progress", "review", "done"]


async def test_seed_t098_is_ai_work_in_progress_lane(seeded):
    row = await seeded.fetchrow(
        "select lane_key, status, progress, labels from tasks where human_id = 'T-098'"
    )
    assert row is not None
    assert row["lane_key"] == "progress"
    assert row["status"] == "ai_work"
    assert row["progress"] == 60
    assert row["labels"] == ["仕事", "調査"]


async def test_seed_k01_applied_six_times(seeded):
    row = await seeded.fetchrow(
        "select scope, confidence, applied, tags from rules where human_id = 'K-01'"
    )
    assert row is not None
    assert row["scope"] == "personal"
    assert row["confidence"] == "high"
    assert row["applied"] == 6
    assert row["tags"] == ["調査", "ブログ"]
