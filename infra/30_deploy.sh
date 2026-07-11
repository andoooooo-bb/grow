#!/usr/bin/env bash
# infra/30_deploy.sh — コンテナビルド & Cloud Run デプロイ（冪等・再実行可）
#
# やること:
#   1. gcloud builds submit（ルート Dockerfile: frontend build → backend + 静的配信）
#   2. Cloud Run デプロイ（scale-to-zero / Cloud SQL unix socket / secrets 注入 / 認証なし公開）
#   3. サービス URL を取得して SELF_URL を反映（2段階デプロイ。Cloud Tasks の push 先）
#
# 使い方:
#   export PROJECT_ID=<GCPプロジェクトID>
#   bash infra/30_deploy.sh
#
# 前提: infra/00_setup.sh と infra/10_database.sh が完了していること。
# 注意: MVP は --allow-unauthenticated（認証なし・個人利用）。worker エンドポイント
#       /internal/jobs/run のみ INTERNAL_JOBS_TOKEN で保護される（#16）。

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID を設定してください（例: export PROJECT_ID=my-gcp-project）}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-grow}"

AR_REPO="${AR_REPO:-${SERVICE_NAME}}"
SA_EMAIL="${SA_NAME:-${SERVICE_NAME}-run}@${PROJECT_ID}.iam.gserviceaccount.com"
QUEUE_NAME="${CLOUD_TASKS_QUEUE:-grow-jobs}"
SQL_INSTANCE="${SQL_INSTANCE:-${SERVICE_NAME}-pg}"
CONNECTION_NAME="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
MEMORY="${MEMORY:-512Mi}"  # 足りない場合は 1Gi へ（MEMORY=1Gi bash infra/30_deploy.sh）

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:${IMAGE_TAG}"

echo "== [1/3] コンテナビルド（Cloud Build, 5〜10分）: ${IMAGE}"
gcloud builds submit "$ROOT_DIR" \
  --tag "$IMAGE" \
  --project "$PROJECT_ID"

echo "== [2/3] Cloud Run デプロイ: ${SERVICE_NAME} (${REGION})"
# GCP_LOCATION は Cloud Tasks キューと Vertex AI の両方で使われる（app/config.py）ため、
# デプロイリージョンと同一にする。モデル可用性の問題は infra/README.md のトラブルシュート参照。
# --max-instances 1 は必須（#24）: SSE のイベントバス（backend/app/events.py）はプロセス内
# シングルトンのため、複数インスタンスだと SSE 購読とジョブ実行（Cloud Tasks の push 先）が
# 別インスタンスに分かれ、artifact.delta / task.updated が購読者に届かない。
# スケールさせる場合は Redis pub/sub 等のプロセス外バスに置き換えてから増やすこと。
gcloud run deploy "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA_EMAIL" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --memory "$MEMORY" \
  --cpu 1 \
  --timeout 600 \
  --add-cloudsql-instances "$CONNECTION_NAME" \
  --set-env-vars "AI_PROVIDER=gemini,JOB_RUNNER=cloud_tasks,GCP_PROJECT=${PROJECT_ID},GCP_LOCATION=${REGION},CLOUD_TASKS_QUEUE=${QUEUE_NAME}" \
  --set-secrets "DATABASE_URL=DATABASE_URL:latest,INTERNAL_JOBS_TOKEN=INTERNAL_JOBS_TOKEN:latest"

echo "== [3/3] SELF_URL の反映（2段階デプロイ）"
# --set-env-vars は列挙外の環境変数を消すため、SELF_URL はデプロイ後に毎回セットし直す。
SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
gcloud run services update "$SERVICE_NAME" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --update-env-vars "SELF_URL=${SERVICE_URL}" >/dev/null
echo "  SELF_URL=${SERVICE_URL}"

echo ""
echo "== ヘルスチェック"
if curl -fsS --max-time 30 "${SERVICE_URL}/healthz" >/dev/null; then
  echo "  OK: ${SERVICE_URL}/healthz"
else
  echo "  警告: /healthz に到達できません。gcloud run services logs read ${SERVICE_NAME} --region ${REGION} で確認してください。" >&2
fi

echo ""
echo "デプロイ完了: ${SERVICE_URL}"
echo "次は infra/VERIFICATION.md の本番動作検証チェックリストを実施してください。"
