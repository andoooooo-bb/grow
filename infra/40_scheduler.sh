#!/usr/bin/env bash
# infra/40_scheduler.sh — 夜間ナレッジCIの Cloud Scheduler 設定（#26。冪等・再実行可）
#
# やること:
#   1. Cloud Scheduler API の有効化
#   2. 毎日 JST 03:00 に POST {SERVICE_URL}/internal/knowledge/ci を叩く http ジョブ作成
#      （X-Internal-Jobs-Token ヘッダで保護 — worker エンドポイントと同じ #16 の方式）
#
# 使い方:
#   export PROJECT_ID=<GCPプロジェクトID>
#   bash infra/40_scheduler.sh
#
# 前提: infra/00_setup.sh（INTERNAL_JOBS_TOKEN シークレット作成）と
#       infra/30_deploy.sh（Cloud Run デプロイ = SERVICE_URL の確定）が完了していること。
# 注意: トークンをローテーションした場合は 30_deploy.sh の再実行後に本スクリプトも
#       再実行すること（ジョブのヘッダは作成時の値で固定されるため）。

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID を設定してください（例: export PROJECT_ID=my-gcp-project）}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-grow}"

JOB_NAME="${SCHEDULER_JOB_NAME:-grow-knowledge-ci}"
# 毎日 JST 03:00（人が寝ている間にナレッジを棚卸しし、朝の受信箱に提案が貯まる）
SCHEDULE="${SCHEDULER_CRON:-0 3 * * *}"
TIME_ZONE="${SCHEDULER_TZ:-Asia/Tokyo}"

echo "== [1/2] Cloud Scheduler API 有効化"
gcloud services enable cloudscheduler.googleapis.com --project "$PROJECT_ID"

echo "== [2/2] Scheduler ジョブ: ${JOB_NAME}（${SCHEDULE} ${TIME_ZONE}）"
# SERVICE_URL は Cloud Run から取得（30_deploy.sh 完了済みが前提）
SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
if [[ -z "$SERVICE_URL" ]]; then
  echo "エラー: Cloud Run サービス ${SERVICE_NAME} の URL を取得できません。infra/30_deploy.sh を先に実行してください。" >&2
  exit 1
fi

# 保護トークンはシークレットから取得（値はログに出さない #16）
INTERNAL_JOBS_TOKEN="$(gcloud secrets versions access latest \
  --secret INTERNAL_JOBS_TOKEN --project "$PROJECT_ID")"
if [[ -z "$INTERNAL_JOBS_TOKEN" ]]; then
  echo "エラー: シークレット INTERNAL_JOBS_TOKEN を取得できません。infra/00_setup.sh を先に実行してください。" >&2
  exit 1
fi

# バッチは冪等寄り（同内容の pending 提案は重複保存されない）なので再試行は少なめでよい
JOB_FLAGS=(
  --location "$REGION"
  --project "$PROJECT_ID"
  --schedule "$SCHEDULE"
  --time-zone "$TIME_ZONE"
  --uri "${SERVICE_URL}/internal/knowledge/ci"
  --http-method POST
  --headers "Content-Type=application/json,X-Internal-Jobs-Token=${INTERNAL_JOBS_TOKEN}"
  --message-body '{}'
  --attempt-deadline 540s
  --max-retry-attempts 1
)
if gcloud scheduler jobs describe "$JOB_NAME" \
    --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  既に存在します（設定を再適用）"
  gcloud scheduler jobs update http "$JOB_NAME" "${JOB_FLAGS[@]}" >/dev/null
else
  gcloud scheduler jobs create http "$JOB_NAME" "${JOB_FLAGS[@]}" >/dev/null
fi

echo ""
echo "完了。毎日 ${TIME_ZONE} ${SCHEDULE} に ${SERVICE_URL}/internal/knowledge/ci を実行します。"
echo "手動テスト: gcloud scheduler jobs run ${JOB_NAME} --location ${REGION} --project ${PROJECT_ID}"
