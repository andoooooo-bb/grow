"""夜間ナレッジCIの worker エンドポイント（POST /internal/knowledge/ci, #26）。

Cloud Scheduler（infra/40_scheduler.sh — 毎日 JST 03:00）の push ターゲット。
/api prefix の外に置く（main.py で直接 include。internal_jobs.py と同型）。

保護は internal_jobs.py と同じ X-Internal-Jobs-Token 検証（#16）:
本番は --allow-unauthenticated のため、INTERNAL_JOBS_TOKEN 設定時のみヘッダの
一致を検証して外部からの直接叩き込みを拒否する。未設定（ローカル・テスト既定）は素通し。

バッチは冪等寄り: 同内容の pending 提案は重複保存されないため（jobs/knowledge_ci.py）、
Scheduler の再試行・多重配信でも受信箱は膨れない。
"""

import secrets

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.domain.dto import KnowledgeCiRunResponse
from app.jobs.knowledge_ci import run_knowledge_ci

router = APIRouter(tags=["internal"])


@router.post("/internal/knowledge/ci")
async def run_knowledge_ci_endpoint(request: Request) -> KnowledgeCiRunResponse:
    settings = get_settings()
    if settings.internal_jobs_token and not secrets.compare_digest(
        request.headers.get("X-Internal-Jobs-Token", ""), settings.internal_jobs_token
    ):
        raise HTTPException(status_code=403, detail="invalid internal jobs token")
    outcome = await run_knowledge_ci(trigger="scheduled")
    return KnowledgeCiRunResponse(
        run_id=outcome.run_id, proposals_created=outcome.proposals_created
    )
