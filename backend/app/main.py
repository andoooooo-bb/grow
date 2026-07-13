"""Grow backend — FastAPI アプリ本体。

Cloud Run 1サービスで API と SPA 静的配信を兼ねる（docs/design_handoff_baton/00 §0.2）。
`frontend/dist` が存在する場合のみ静的マウント＋SPAフォールバックを有効化する
（ローカル開発では Vite dev server が別ポートで動くため通常は存在しない）。
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routers import api_router
from app.routers.internal_demo import router as internal_demo_router
from app.routers.internal_jobs import router as internal_jobs_router
from app.routers.internal_knowledge import router as internal_knowledge_router

# リポジトリルート = backend/app/main.py から2階層上。
# Dockerfile もこの相対配置（/srv/backend, /srv/frontend/dist）を再現している。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"

app = FastAPI(title="Grow API")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# 機能ルーターの拡張点（後続 Wave は app/routers/ 配下に追加する）
app.include_router(api_router, prefix="/api")

# worker エンドポイント（Cloud Tasks の push ターゲット, §7.2）。/api prefix の外。
app.include_router(internal_jobs_router)

# 夜間ナレッジCI（Cloud Scheduler の push ターゲット, #26）。/api prefix の外。
app.include_router(internal_knowledge_router)

# デモDB自動リセット（Cloud Scheduler の push ターゲット, #security）。/api prefix の外。
app.include_router(internal_demo_router)


def _mount_spa(application: FastAPI, dist_dir: Path) -> None:
    """SPA の静的配信: /assets は静的マウント、残りは index.html へフォールバック。"""
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_html = dist_dir / "index.html"

    @application.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = (dist_dir / full_path).resolve()
        # dist 配下の実ファイルのみ直接返す（パストラバーサル防止）
        if full_path and candidate.is_relative_to(dist_dir) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)


if _FRONTEND_DIST.is_dir():
    _mount_spa(app, _FRONTEND_DIST)
