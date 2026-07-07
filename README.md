# Grow — 人とAIの協働タスク管理（看板ボード）

Grow は「使うほど自分の働き方にフィットしていく」タスク管理ツールです。見た目は看板ボードですが、本質は (1) 人とAIが1枚のカード上でバトンを渡し合うこと、(2) やり取りの履歴から「働き方のルール」を蒸留してナレッジ化し、次からAIが**言われなくても前提として読んでから動く**ことで、AIの実作業アウトプットの質が上がり続けることにあります。

## アーキテクチャ

**単一の真実は [`docs/design_handoff_baton/00_decisions_and_platform.md`](docs/design_handoff_baton/00_decisions_and_platform.md)**（§0.2 に完全な構成図と選定理由）。要点:

```text
Frontend  : React + TS + Vite / Zustand / @dnd-kit / TanStack Query / SSE
Backend   : FastAPI (Python) on Cloud Run（scale-to-zero、SPA静的配信も兼任＝1サービス）
Job Runner: Cloud Tasks → Cloud Run worker（Redis不要でコスト最小）
Data      : Cloud SQL for PostgreSQL（最小ティア）＋ 将来 pgvector
AI        : Vertex AI Gemini（本番のみ）/ ローカルは Mock（AI_PROVIDER=mock|gemini で切替）
```

リポジトリ構成:

```text
frontend/   Vite + React + TypeScript の SPA
backend/    FastAPI（uv プロジェクト、パッケージ名 app）
scripts/    開発用スクリプト（devdb.sh: ローカルPostgres）
docs/       設計引き継ぎ書（design_handoff_baton/、00 が最初に読む文書）
Dockerfile  Cloud Run 用マルチステージ（frontend build → backend + 静的配信）
```

## ローカル開発（Mock 前提・費用ゼロ）

ローカルは `AI_PROVIDER=mock`（既定値）で動かすため、GCP・LLM の費用は一切かかりません。必要ツール: Node 22+ / npm 10+ / uv / PostgreSQL（`pg_ctl` が使えること。Docker があればそちらを自動利用）。

```bash
# 1. 依存インストール（frontend: npm / backend: uv）
make setup

# 2. 環境変数（既定値で動くので必須ではない。変える場合のみ）
cp .env.example .env

# 3. ローカルDB起動（port 54329, db=grow, user=grow）
#    Docker daemon があれば docker compose、無ければ pg_ctl で .pgdata/ に起動
make db-start

# 4. 開発サーバ（別ターミナルで並行起動）
make be-dev   # FastAPI  → http://localhost:8000（/healthz, /docs）
make fe-dev   # Vite SPA → http://localhost:5173（/api, /healthz は :8000 へプロキシ）

# 後片付け
make db-stop     # DB停止
make db-reset    # DBを作り直す
```

## テスト・Lint・ビルド

```bash
make test      # backend (pytest) + frontend (vitest)
make test-be   # cd backend && uv run pytest
make test-fe   # cd frontend && npm test
make lint      # ruff check + tsc --noEmit
make build     # cd frontend && npm run build（SPA本番ビルド）
```

`frontend/dist` が存在する状態で backend を起動すると、FastAPI が SPA を静的配信します（Cloud Run と同じ1サービス構成をローカルで再現可能）。

## 環境変数

`.env.example` を参照。主なもの:

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `AI_PROVIDER` | `mock` | `mock`（ローカル・テスト） / `gemini`（Vertex AI、本番のみ） |
| `DATABASE_URL` | `postgresql://grow:grow@localhost:54329/grow` | PostgreSQL 接続文字列 |
| `GCP_PROJECT` | （空） | GCP プロジェクトID（本番のみ） |
| `GCP_LOCATION` | `asia-northeast1` | GCP リージョン |
| `CLOUD_TASKS_QUEUE` | `grow-jobs` | AIジョブ用 Cloud Tasks キュー名 |
| `SELF_URL` | `http://localhost:8000` | Cloud Tasks が worker へ push する自サービスURL |
| `PORT` | `8000`（Cloud Run は `8080` を注入） | 待受ポート |

## デプロイ

Cloud Run へのデプロイ手順・IaC は `infra/` を参照（Wave6 で追加予定）。コンテナは本リポジトリの `Dockerfile`（マルチステージ: frontend build → python:3.13-slim + uv）でビルドする。
