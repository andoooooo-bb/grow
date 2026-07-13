#!/usr/bin/env bash
# infra/50_demo_reset_scheduler.sh — デモDB自動リセットの Cloud Scheduler 設定
#   （#security。冪等・再実行可。40_scheduler.sh と同型）
#
# やること:
#   1. Cloud Scheduler API の有効化
#   2. 5分ごと（*/5 * * * *）に POST {SERVICE_URL}/internal/demo/reset を叩く http ジョブ作成
#      （X-Internal-Jobs-Token ヘッダで保護 — worker/CI エンドポイントと同じ #16 の方式）
#
# 使い方:
#   export PROJECT_ID=<GCPプロジェクトID>
#   bash infra/50_demo_reset_scheduler.sh
#
# 前提: infra/00_setup.sh（INTERNAL_JOBS_TOKEN シークレット作成）と
#       infra/30_deploy.sh（Cloud Run デプロイ = SERVICE_URL の確定）が完了していること。
# 注意: トークンをローテーションした場合は 30_deploy.sh の再実行後に本スクリプトも
#       再実行すること（ジョブのヘッダは作成時の値で固定されるため）。

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID を設定してください（例: export PROJECT_ID=my-gcp-project）}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-grow}"

JOB_NAME="${DEMO_RESET_JOB_NAME:-grow-demo-reset}"
# 5分ごと（荒らし・実験の痕跡を短周期で正準シードへ戻す #security）
SCHEDULE="${DEMO_RESET_CRON:-*/5 * * * *}"
TIME_ZONE="${SCHEDULER_TZ:-Asia/Tokyo}"

echo "== [1/2] Cloud Scheduler API 有効化"
gcloud services enable cloudscheduler.googleapis.com --project "$PROJECT_ID"

echo "== [2/2] Scheduler ジョブ: ${JOB_NAME}（${SCHEDULE} ${TIME_ZONE}）"
# SERVICE_URL は Cloud Run から取得（30_deploy.sh 完了済みが前提。SELF_URL 上書きも許容）
SERVICE_URL="${SELF_URL:-$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')}"
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

# リセットは冪等（truncate → seed.sql 再投入で常に正準状態）なので再試行は少なめでよい
JOB_FLAGS=(
  --location "$REGION"
  --project "$PROJECT_ID"
  --schedule "$SCHEDULE"
  --time-zone "$TIME_ZONE"
  --uri "${SERVICE_URL}/internal/demo/reset"
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
echo "完了。${TIME_ZONE} ${SCHEDULE} に ${SERVICE_URL}/internal/demo/reset を実行します。"
echo "手動テスト: gcloud scheduler jobs run ${JOB_NAME} --location ${REGION} --project ${PROJECT_ID}"
