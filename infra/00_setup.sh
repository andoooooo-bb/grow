#!/usr/bin/env bash
# infra/00_setup.sh — GCP 基盤の初期セットアップ（冪等・再実行可）
#
# やること:
#   1. 必要な API の有効化
#   2. Artifact Registry リポジトリ作成（コンテナ置き場）
#   3. Cloud Run 実行サービスアカウント作成 + IAM ロール付与
#   4. Cloud Tasks キュー作成（AIジョブ用、再試行設定込み）
#   5. INTERNAL_JOBS_TOKEN シークレット作成（worker エンドポイント保護, #16）
#
# 使い方:
#   export PROJECT_ID=<GCPプロジェクトID>
#   bash infra/00_setup.sh
#
# 参照: docs/design_handoff_baton/00_decisions_and_platform.md §0.2（確定GCPスタック）

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID を設定してください（例: export PROJECT_ID=my-gcp-project）}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-grow}"

AR_REPO="${AR_REPO:-${SERVICE_NAME}}"
SA_NAME="${SA_NAME:-${SERVICE_NAME}-run}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
QUEUE_NAME="${CLOUD_TASKS_QUEUE:-grow-jobs}"

echo "== [1/5] API 有効化（数分かかることがあります）"
gcloud services enable \
  aiplatform.googleapis.com \
  run.googleapis.com \
  sqladmin.googleapis.com \
  cloudtasks.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --project "$PROJECT_ID"

echo "== [2/5] Artifact Registry リポジトリ: ${AR_REPO} (${REGION})"
if gcloud artifacts repositories describe "$AR_REPO" \
    --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  既に存在します（スキップ）"
else
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format docker \
    --location "$REGION" \
    --description "Grow container images" \
    --project "$PROJECT_ID"
fi

echo "== [3/5] Cloud Run 実行サービスアカウント: ${SA_EMAIL}"
if gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  既に存在します（スキップ）"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name "Grow Cloud Run runtime" \
    --project "$PROJECT_ID"
fi

# 必要ロール（#15 からの引き継ぎ + Secret 参照）:
#   aiplatform.user           — Vertex AI Gemini（ADC 認証、APIキー不要）
#   cloudtasks.enqueuer       — AIジョブの enqueue（app/jobs/queue.py）
#   cloudsql.client           — Cloud SQL unix socket 接続（--add-cloudsql-instances）
#   secretmanager.secretAccessor — DATABASE_URL / INTERNAL_JOBS_TOKEN の参照
# SA 作成直後は結果整合性で "does not exist" になることがあるためリトライする
for role in \
  roles/aiplatform.user \
  roles/cloudtasks.enqueuer \
  roles/cloudsql.client \
  roles/secretmanager.secretAccessor; do
  for attempt in 1 2 3 4 5; do
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member "serviceAccount:${SA_EMAIL}" \
      --role "$role" \
      --condition None \
      --quiet >/dev/null 2>&1; then
      echo "  bind: ${role}"
      break
    fi
    if [ "$attempt" = 5 ]; then
      echo "  ERROR: ${role} のバインドに失敗しました" >&2
      exit 1
    fi
    echo "  retry(${attempt}): ${role}（SA伝播待ち…）"
    sleep 10
  done
done

echo "== [4/5] Cloud Tasks キュー: ${QUEUE_NAME} (${REGION})"
# max-attempts=4（初回+3再試行）は backend の CLOUD_TASKS_MAX_RETRY_COUNT=3
# （app/routers/internal_jobs.py）と揃えること。変更する場合は両方を更新する。
QUEUE_FLAGS=(
  --location "$REGION"
  --project "$PROJECT_ID"
  --max-attempts 4
  --min-backoff 10s
  --max-backoff 300s
  --max-doublings 4
  --max-dispatches-per-second 5
  --max-concurrent-dispatches 5
)
if gcloud tasks queues describe "$QUEUE_NAME" \
    --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  既に存在します（設定を再適用）"
  gcloud tasks queues update "$QUEUE_NAME" "${QUEUE_FLAGS[@]}" >/dev/null
else
  gcloud tasks queues create "$QUEUE_NAME" "${QUEUE_FLAGS[@]}" >/dev/null
fi

echo "== [5/5] INTERNAL_JOBS_TOKEN シークレット（worker エンドポイント保護, #16）"
# 再実行時はローテーションしない（稼働中リビジョンとの不一致を避けるため）。
# ローテーションしたい場合は versions add 後に infra/30_deploy.sh を再実行すること。
if gcloud secrets describe INTERNAL_JOBS_TOKEN --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  既に存在します（スキップ）"
else
  gcloud secrets create INTERNAL_JOBS_TOKEN \
    --replication-policy automatic \
    --project "$PROJECT_ID"
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add INTERNAL_JOBS_TOKEN \
    --data-file=- --project "$PROJECT_ID" >/dev/null
  echo "  生成・登録しました"
fi

echo ""
echo "完了。次は infra/10_database.sh（Cloud SQL 作成、10〜15分程度）へ。"
