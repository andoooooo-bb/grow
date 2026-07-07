#!/usr/bin/env bash
# infra/90_teardown.sh — Grow の GCP リソースを全削除（確認プロンプト付き）
#
# 削除対象:
#   - Cloud Run サービス
#   - Cloud Tasks キュー（※削除後 約7日間は同名キューを再作成できない点に注意）
#   - Cloud SQL インスタンス（データも消える）
#   - Secret Manager（DATABASE_URL / INTERNAL_JOBS_TOKEN）
#   - Artifact Registry リポジトリ（イメージ含む）
#   - サービスアカウント（IAM バインディング解除込み）
#
# API の有効化状態は残す（無効化は他ワークロードへ影響しうるため）。
#
# 使い方:
#   export PROJECT_ID=<GCPプロジェクトID>
#   bash infra/90_teardown.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID を設定してください（例: export PROJECT_ID=my-gcp-project）}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-grow}"

AR_REPO="${AR_REPO:-${SERVICE_NAME}}"
SA_NAME="${SA_NAME:-${SERVICE_NAME}-run}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
QUEUE_NAME="${CLOUD_TASKS_QUEUE:-grow-jobs}"
SQL_INSTANCE="${SQL_INSTANCE:-${SERVICE_NAME}-pg}"

cat <<EOS
以下のリソースを削除します（プロジェクト: ${PROJECT_ID} / リージョン: ${REGION}）:
  - Cloud Run サービス       : ${SERVICE_NAME}
  - Cloud Tasks キュー       : ${QUEUE_NAME}
  - Cloud SQL インスタンス   : ${SQL_INSTANCE}  ※データも消えます
  - シークレット             : DATABASE_URL, INTERNAL_JOBS_TOKEN
  - Artifact Registry        : ${AR_REPO}
  - サービスアカウント       : ${SA_EMAIL}
EOS
read -r -p "本当に削除しますか？ 確認のためプロジェクトID（${PROJECT_ID}）を入力: " CONFIRM
if [ "$CONFIRM" != "$PROJECT_ID" ]; then
  echo "入力が一致しないため中止しました。"
  exit 1
fi

echo "== Cloud Run サービス"
if gcloud run services describe "$SERVICE_NAME" \
    --region "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud run services delete "$SERVICE_NAME" \
    --region "$REGION" --project "$PROJECT_ID" --quiet
else
  echo "  存在しません（スキップ）"
fi

echo "== Cloud Tasks キュー"
if gcloud tasks queues describe "$QUEUE_NAME" \
    --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud tasks queues delete "$QUEUE_NAME" \
    --location "$REGION" --project "$PROJECT_ID" --quiet
else
  echo "  存在しません（スキップ）"
fi

echo "== Cloud SQL インスタンス"
if gcloud sql instances describe "$SQL_INSTANCE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud sql instances delete "$SQL_INSTANCE" --project "$PROJECT_ID" --quiet
else
  echo "  存在しません（スキップ）"
fi

echo "== シークレット"
for secret in DATABASE_URL INTERNAL_JOBS_TOKEN; do
  if gcloud secrets describe "$secret" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets delete "$secret" --project "$PROJECT_ID" --quiet
  else
    echo "  ${secret}: 存在しません（スキップ）"
  fi
done

echo "== Artifact Registry リポジトリ"
if gcloud artifacts repositories describe "$AR_REPO" \
    --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories delete "$AR_REPO" \
    --location "$REGION" --project "$PROJECT_ID" --quiet
else
  echo "  存在しません（スキップ）"
fi

echo "== サービスアカウント（IAM バインディング解除 → 削除）"
if gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  for role in \
    roles/aiplatform.user \
    roles/cloudtasks.enqueuer \
    roles/cloudsql.client \
    roles/secretmanager.secretAccessor; do
    gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
      --member "serviceAccount:${SA_EMAIL}" \
      --role "$role" \
      --condition None \
      --quiet >/dev/null 2>&1 || echo "  unbind 失敗（未付与の可能性）: ${role}"
  done
  gcloud iam service-accounts delete "$SA_EMAIL" --project "$PROJECT_ID" --quiet
else
  echo "  存在しません（スキップ）"
fi

echo ""
echo "削除が完了しました。"
