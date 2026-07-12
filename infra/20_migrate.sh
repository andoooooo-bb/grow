#!/usr/bin/env bash
# infra/20_migrate.sh — cloud-sql-proxy 経由でローカルからスキーマ/シードを適用（冪等・再実行可）
#
# やること:
#   1. cloud-sql-proxy / psql の存在確認
#   2. Secret Manager の DATABASE_URL から接続情報を取得
#   3. proxy をローカルポートで起動し backend/db/schema.sql を適用
#      （適用済みならスキップ。--reset で public スキーマを作り直して再適用）
#   4. --seed 指定時のみ backend/db/seed.sql を投入（全テーブル truncate 後に投入）
#
# 使い方:
#   export PROJECT_ID=<GCPプロジェクトID>
#   bash infra/20_migrate.sh           # スキーマのみ
#   bash infra/20_migrate.sh --seed    # スキーマ + シードデータ（T-104 等）
#   bash infra/20_migrate.sh --reset --seed  # DB を作り直して両方適用

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID を設定してください（例: export PROJECT_ID=my-gcp-project）}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-grow}"

SQL_INSTANCE="${SQL_INSTANCE:-${SERVICE_NAME}-pg}"
DB_NAME="${DB_NAME:-grow}"
DB_USER="${DB_USER:-grow}"
CONNECTION_NAME="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
PROXY_PORT="${PROXY_PORT:-54330}"  # ローカル開発DB（54329）と衝突しないポート

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA_FILE="${ROOT_DIR}/backend/db/schema.sql"
SEED_FILE="${ROOT_DIR}/backend/db/seed.sql"

APPLY_SEED=false
RESET=false
for arg in "$@"; do
  case "$arg" in
    --seed) APPLY_SEED=true ;;
    --reset) RESET=true ;;
    *) echo "不明な引数: ${arg}（--seed / --reset のみ対応）" >&2; exit 1 ;;
  esac
done

echo "== [1/4] 前提コマンドの確認"
if ! command -v cloud-sql-proxy >/dev/null 2>&1; then
  cat >&2 <<'EOS'
エラー: cloud-sql-proxy が見つかりません。以下のいずれかでインストールしてください:
  - macOS: brew install cloud-sql-proxy
  - その他: https://cloud.google.com/sql/docs/postgres/connect-auth-proxy#install
EOS
  exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
  echo "エラー: psql が見つかりません（macOS: brew install libpq && brew link --force libpq）" >&2
  exit 1
fi
echo "  OK（cloud-sql-proxy / psql）"

echo "== [2/4] Secret Manager から DATABASE_URL を取得"
DATABASE_URL_SECRET="$(gcloud secrets versions access latest \
  --secret DATABASE_URL --project "$PROJECT_ID")"
# DSN は infra/10_database.sh が登録した固定形式:
#   postgresql://<user>:<pass>@/<db>?host=/cloudsql/<connection_name>
DB_PASSWORD="${DATABASE_URL_SECRET#postgresql://"${DB_USER}":}"
DB_PASSWORD="${DB_PASSWORD%%@*}"
if [ -z "$DB_PASSWORD" ] || [ "$DB_PASSWORD" = "$DATABASE_URL_SECRET" ]; then
  echo "エラー: DATABASE_URL シークレットからパスワードを抽出できませんでした。" >&2
  echo "       infra/10_database.sh を先に実行してください。" >&2
  exit 1
fi
echo "  OK"

echo "== [3/4] cloud-sql-proxy 起動（localhost:${PROXY_PORT} → ${CONNECTION_NAME}）"
# ADC に依存せず gcloud のアクセストークンで認可する（ADC が別アカウントでも安全）
cloud-sql-proxy --address 127.0.0.1 --port "$PROXY_PORT" \
  --token "$(gcloud auth print-access-token)" "$CONNECTION_NAME" &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT

export PGPASSWORD="$DB_PASSWORD"
PSQL=(psql -h 127.0.0.1 -p "$PROXY_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 --quiet)

# 接続可能になるまで待つ（最大30秒）
CONNECTED=false
for _ in $(seq 1 30); do
  if "${PSQL[@]}" -c 'select 1' >/dev/null 2>&1; then
    CONNECTED=true
    break
  fi
  sleep 1
done
if [ "$CONNECTED" != true ]; then
  echo "エラー: proxy 経由で DB に接続できませんでした（インスタンス起動状態と IAM を確認）" >&2
  exit 1
fi
echo "  接続 OK"

echo "== [4/4] スキーマ適用"
if [ "$RESET" = true ]; then
  echo "  --reset: public スキーマを作り直します"
  "${PSQL[@]}" -c "drop schema public cascade; create schema public; grant all on schema public to ${DB_USER};"
fi
# schema.sql は冪等（create table/index に if not exists）。常に適用してよい。
# デプロイ間でテーブルが追加されても差分が確実に反映される（スキップしない）。
"${PSQL[@]}" -f "$SCHEMA_FILE"
echo "  schema.sql を適用しました（冪等・既存オブジェクトはスキップ）"

if [ "$APPLY_SEED" = true ]; then
  echo "  --seed: 全テーブルを truncate してシードを投入します"
  "${PSQL[@]}" -c "truncate workspaces, users, boards, lanes, tasks, comments, chat_messages, rules, rule_applications, rule_feedback, rule_proposals, rule_signals, task_transitions, knowledge_ci_runs, ai_jobs, artifacts cascade"
  "${PSQL[@]}" -f "$SEED_FILE"
  echo "  seed.sql を投入しました（T-104 / T-098 など）"
fi

echo ""
echo "完了。次は infra/30_deploy.sh（ビルド & Cloud Run デプロイ）へ。"
