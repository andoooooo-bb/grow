"""デモDB自動リセットの内部エンドポイント（POST /internal/demo/reset, #security）。

無認証の公開 Cloud Run デモで、荒らし・実験の痕跡を定期的に消して正準シードへ戻すための
内部エンドポイント。Cloud Scheduler（infra/50_demo_reset_scheduler.sh — 5分ごと）の
push ターゲット。/api prefix の外に置く（main.py で直接 include。internal_jobs.py と同型）。

保護は internal_jobs.py / internal_knowledge.py と同じ X-Internal-Jobs-Token 検証（#16）:
本番は --allow-unauthenticated のため、INTERNAL_JOBS_TOKEN 設定時のみヘッダの一致を
検証して外部からの直接叩き込みを拒否する。未設定（ローカル・テスト既定）は素通し。

処理は単一トランザクション:
1. 全データテーブルを truncate（infra/20_migrate.sh --seed の truncate 対象一覧と同じ）
2. backend/db/seed.sql を実行して正準シードを再投入

asyncpg は simple query protocol で `;` 区切りの複数ステートメントを 1 回の execute で
流せるため、truncate も seed.sql（複数文）も await conn.execute(...) 1 回で実行できる。
"""

import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.db import get_pool

router = APIRouter(tags=["internal"])

# backend ルート = app/routers/internal_demo.py から2階層上（app/ → backend/）。
# コンテナ（/srv/backend）でもローカル（backend/）でも同じ相対で seed.sql を解決できる。
_SEED_FILE = Path(__file__).resolve().parents[2] / "db" / "seed.sql"

# infra/20_migrate.sh --seed の truncate 対象一覧と同じ（全データテーブルを cascade）。
_TRUNCATE_SQL = (
    "truncate workspaces, users, boards, lanes, tasks, comments, chat_messages, "
    "rules, rule_applications, rule_feedback, rule_proposals, rule_signals, "
    "task_transitions, knowledge_ci_runs, ai_jobs, artifacts cascade"
)


def _seed_body() -> str:
    """seed.sql の中身から外側のトランザクション制御文（begin;/commit;）を取り除く。

    seed.sql は `begin; ... commit;` で自身を囲むが、ここでは truncate と同じ
    `conn.transaction()` の中で 1 トランザクションとして流すため、埋め込みの
    begin/commit を残すと外側トランザクションと二重管理になり asyncpg が壊れる。
    行単位で begin/commit（末尾セミコロン含む）だけを落とす（他の文は保持する）。
    """
    lines = []
    for line in _SEED_FILE.read_text().splitlines():
        token = line.strip().rstrip(";").strip().lower()
        if token in ("begin", "commit"):
            continue
        lines.append(line)
    return "\n".join(lines)


@router.post("/internal/demo/reset")
async def reset_demo(request: Request) -> dict[str, object]:
    """デモDBを truncate → seed.sql 再投入で正準状態へ戻す（トークン保護・冪等）。"""
    settings = get_settings()
    if settings.internal_jobs_token and not secrets.compare_digest(
        request.headers.get("X-Internal-Jobs-Token", ""), settings.internal_jobs_token
    ):
        raise HTTPException(status_code=403, detail="invalid internal jobs token")

    seed_body = _seed_body()
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # asyncpg は simple query protocol で ; 区切りの複数文を 1 回の execute で流せる
        await conn.execute(_TRUNCATE_SQL)
        await conn.execute(seed_body)
        tasks = await conn.fetchval("select count(*) from tasks")
        rules = await conn.fetchval("select count(*) from rules")
    return {"reset": True, "tasks": tasks, "rules": rules}
