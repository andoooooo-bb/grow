#!/usr/bin/env bash
# infra/10_database.sh — Cloud SQL for PostgreSQL の最小構成セットアップ（冪等・再実行可）
#
# やること:
#   1. Cloud SQL インスタンス作成（コスト最小: Enterprise / db-f1-micro / zonal / 10GB）
#   2. データベース grow 作成
#   3. アプリユーザー grow 作成（パスワード自動生成。再実行時は再生成して更新）
#   4. DATABASE_URL を Secret Manager へ登録（unix socket 形式）
#
# 使い方:
#   export PROJECT_ID=<GCPプロジェクトID>
#   bash infra/10_database.sh
#
# 注意:
#   - インスタンス作成に 10〜15 分程度かかる。
#   - 本プロジェクトの主コスト源。未使用時は README の「コスト」節を参照して停止できる。
#   - DATABASE_URL は Cloud Run の unix socket 接続形式:
#       postgresql://grow:<pass>@/grow?host=/cloudsql/<PROJECT>:<REGION>:<INSTANCE>
#     asyncpg は `host` クエリパラメータの unix socket ディレクトリ指定に対応しており、
#     app/db.py はこの DSN をそのまま asyncpg.create_pool へ渡すため接続可能
#     （asyncpg がディレクトリに /.s.PGSQL.5432 を補完する）。
#   - パスワードは 16 進文字のみで生成するため URL エンコード不要。

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID を設定してください（例: export PROJECT_ID=my-gcp-project）}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-grow}"

SQL_INSTANCE="${SQL_INSTANCE:-${SERVICE_NAME}-pg}"
DB_NAME="${DB_NAME:-grow}"
DB_USER="${DB_USER:-grow}"
CONNECTION_NAME="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"

echo "== [1/4] Cloud SQL インスタンス: ${SQL_INSTANCE}（作成には 10〜15 分かかります）"
if gcloud sql instances describe "$SQL_INSTANCE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  既に存在します（スキップ）"
else
  # コスト最小の順当構成（§0.2: 最小ティア / 未使用時停止）:
  #   PostgreSQL 16 / Enterprise エディション / db-f1-micro（共有コア・最安ティア）
  #   zonal（HA なし）/ SSD 10GB + 自動増加 / 自動バックアップ無効
  # バックアップが必要になったら: gcloud sql instances patch $SQL_INSTANCE --backup
  gcloud sql instances create "$SQL_INSTANCE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --database-version POSTGRES_16 \
    --edition enterprise \
    --tier db-f1-micro \
    --availability-type zonal \
    --storage-type SSD \
    --storage-size 10GB \
    --storage-auto-increase \
    --no-backup
fi

echo "== [2/4] データベース: ${DB_NAME}"
if gcloud sql databases describe "$DB_NAME" \
    --instance "$SQL_INSTANCE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  既に存在します（スキップ）"
else
  gcloud sql databases create "$DB_NAME" \
    --instance "$SQL_INSTANCE" --project "$PROJECT_ID"
fi

echo "== [3/4] アプリユーザー: ${DB_USER}（パスワード生成）"
# 16進文字のみ（URLエンコード不要）。再実行時もパスワードを再生成し、
# DB とシークレットの両方を更新して常に整合させる。
DB_PASSWORD="$(openssl rand -hex 24)"
EXISTING_USERS="$(gcloud sql users list \
  --instance "$SQL_INSTANCE" --project "$PROJECT_ID" --format 'value(name)')"
if grep -qx "$DB_USER" <<<"$EXISTING_USERS"; then
  echo "  既存ユーザーのパスワードを更新します"
  gcloud sql users set-password "$DB_USER" \
    --instance "$SQL_INSTANCE" --project "$PROJECT_ID" --password "$DB_PASSWORD"
else
  gcloud sql users create "$DB_USER" \
    --instance "$SQL_INSTANCE" --project "$PROJECT_ID" --password "$DB_PASSWORD"
fi

echo "== [4/4] DATABASE_URL を Secret Manager へ登録"
DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CONNECTION_NAME}"
if ! gcloud secrets describe DATABASE_URL --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets create DATABASE_URL \
    --replication-policy automatic \
    --project "$PROJECT_ID"
fi
printf '%s' "$DATABASE_URL" | gcloud secrets versions add DATABASE_URL \
  --data-file=- --project "$PROJECT_ID" >/dev/null
echo "  登録しました（インスタンス接続名: ${CONNECTION_NAME}）"

echo ""
echo "完了。次は infra/20_migrate.sh（スキーマ適用）へ。"
echo "※ パスワードを更新した場合、稼働中の Cloud Run は再デプロイ（infra/30_deploy.sh）で新しい"
echo "   シークレットを読み直すまで旧パスワードのままなので、続けて 30 まで実行すること。"
