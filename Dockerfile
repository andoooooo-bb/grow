# Grow — Cloud Run 用マルチステージビルド
# 1サービスで FastAPI (API) と SPA 静的配信を兼ねる（docs/design_handoff_baton/00 §0.2）。
# コンテナ内はリポジトリと同じ相対配置（backend/ と frontend/dist）を /srv 配下に再現する。

# ---- Stage 1: frontend build ----
FROM node:22-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: backend runtime ----
FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /srv/backend

# 依存レイヤーを先に解決してキャッシュを効かせる
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# アプリ本体
COPY backend/ ./
RUN uv sync --frozen --no-dev

# SPA ビルド成果物（app/main.py が /srv/frontend/dist を自動検出して配信する）
COPY --from=frontend-build /build/frontend/dist /srv/frontend/dist

ENV PATH="/srv/backend/.venv/bin:$PATH" \
    PORT=8080
EXPOSE 8080

# Cloud Run は $PORT を注入する（既定 8080）
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
