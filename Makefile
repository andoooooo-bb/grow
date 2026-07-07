.PHONY: setup db-start db-stop db-reset migrate seed be-dev fe-dev test test-be test-fe lint build

DATABASE_URL ?= postgresql://grow:grow@localhost:54329/grow

## 初回セットアップ（frontend: npm / backend: uv）
setup:
	cd frontend && { [ -f package-lock.json ] && npm ci || npm install; }
	cd backend && uv sync

## ローカルDB（Docker があれば docker compose、無ければ pg_ctl。port 54329）
db-start:
	scripts/devdb.sh start

db-stop:
	scripts/devdb.sh stop

db-reset:
	scripts/devdb.sh reset

## マイグレーション / シード（空DB前提。リセットは make db-reset → migrate → seed）
migrate:
	psql "$(DATABASE_URL)" -v ON_ERROR_STOP=1 -f backend/db/schema.sql

seed:
	psql "$(DATABASE_URL)" -v ON_ERROR_STOP=1 -f backend/db/seed.sql

## 開発サーバ
be-dev:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

fe-dev:
	cd frontend && npm run dev

## テスト
test: test-be test-fe

test-be:
	cd backend && uv run pytest

test-fe:
	cd frontend && npm test

## Lint（ruff + tsc --noEmit）
lint:
	cd backend && uv run ruff check
	cd frontend && npm run lint

## 本番ビルド（SPA。backend は Dockerfile でビルド）
build:
	cd frontend && npm run build
