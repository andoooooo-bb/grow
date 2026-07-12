# infra/ — Cloud Run デプロイ手順（#16）

Grow を GCP へデプロイするための冪等スクリプト集。構成は
[`docs/design_handoff_baton/00_decisions_and_platform.md`](../docs/design_handoff_baton/00_decisions_and_platform.md) §0.2 が正:
**Cloud Run 1サービス（API + SPA 静的配信）/ Cloud SQL for PostgreSQL 最小ティア / Cloud Tasks / Vertex AI Gemini / Secret Manager / Artifact Registry**。

## 前提

- `gcloud` CLI がインストール済みで、`gcloud auth login` 済みであること
- スキーマ適用（20）にはさらに `gcloud auth application-default login`（cloud-sql-proxy が ADC を使う）
- 対象の GCP プロジェクトが存在し、**課金が有効**であること
- ローカルに `cloud-sql-proxy` と `psql` があること（20 のみ。無ければスクリプトが案内を出す）
- 実行前に `export PROJECT_ID=<プロジェクトID>` を設定すること

## 実行順

```bash
export PROJECT_ID=<GCPプロジェクトID>

bash infra/00_setup.sh            # API有効化 / Artifact Registry / SA+IAM / Cloud Tasksキュー / トークン  （〜5分）
bash infra/10_database.sh         # Cloud SQL 最小構成 + DB/ユーザー + DATABASE_URL シークレット      （10〜15分）
bash infra/20_migrate.sh --seed   # cloud-sql-proxy 経由で schema.sql + seed.sql を適用               （〜2分）
bash infra/30_deploy.sh           # Cloud Build → Cloud Run デプロイ → SELF_URL 反映                  （5〜10分）
```

すべて冪等（再実行可）。コード更新後の再デプロイは `bash infra/30_deploy.sh` のみでよい。
全リソースの削除は `bash infra/90_teardown.sh`（確認プロンプト付き）。

## 環境変数（スクリプト入力）

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `PROJECT_ID` | （必須） | GCP プロジェクトID |
| `REGION` | `asia-northeast1` | 全リソースのリージョン（Cloud Run / SQL / Tasks / Vertex 共通） |
| `SERVICE_NAME` | `grow` | Cloud Run サービス名。AR リポジトリ・SA・SQL インスタンス名の接頭辞にもなる |
| `CLOUD_TASKS_QUEUE` | `grow-jobs` | AI ジョブ用キュー名 |
| `SQL_INSTANCE` | `${SERVICE_NAME}-pg` | Cloud SQL インスタンス名 |
| `MEMORY` | `512Mi` | Cloud Run メモリ（不足時 `MEMORY=1Gi` で再デプロイ） |
| `IMAGE_TAG` | git short SHA | コンテナイメージタグ（30 のみ） |
| `PROXY_PORT` | `54330` | cloud-sql-proxy のローカルポート（20 のみ） |

アプリ側の環境変数（`AI_PROVIDER=gemini` / `JOB_RUNNER=cloud_tasks` / `GCP_PROJECT` / `GCP_LOCATION` /
`CLOUD_TASKS_QUEUE` / `SELF_URL`）とシークレット（`DATABASE_URL` / `INTERNAL_JOBS_TOKEN`）は
`30_deploy.sh` が自動で設定する。

## セキュリティ（MVP の割り切り）

- サービス全体は `--allow-unauthenticated`（個人利用・認証はフェーズ3）。
- worker エンドポイント `POST /internal/jobs/run` のみ `INTERNAL_JOBS_TOKEN` で保護
  （Cloud Tasks が enqueue 時に `X-Internal-Jobs-Token` ヘッダを付与し、worker が一致検証。不一致は 403）。
- Cloud SQL はパブリック IP を持つが、接続は Cloud Run からの unix socket
  （`--add-cloudsql-instances`）と cloud-sql-proxy（IAM 認証）のみ。パスワードは Secret Manager 管理。

## コスト

| リソース | 課金の性質 | 目安 |
| --- | --- | --- |
| **Cloud SQL（主コスト）** | インスタンス稼働時間に対して常時課金 | db-f1-micro + 10GB SSD で月 $10 前後 |
| Cloud Run | リクエスト処理中のみ（**ゼロスケール**、待機コスト0） | 個人利用ならほぼ無料枠内 |
| Vertex AI Gemini | リクエスト（トークン）課金 | 実行回数に比例。Flash 系は安価、execute の Pro 系が支配的 |
| Cloud Tasks / Secret Manager / Artifact Registry | ごく少額 | 無料枠内〜数十円 |

**未使用時は Cloud SQL を停止するとコストをほぼゼロにできる**（ディスク分のみ課金）:

```bash
# 停止（Cloud Run は起動したままでもDB接続エラーになるだけで課金はほぼ発生しない）
gcloud sql instances patch grow-pg --activation-policy NEVER --project "$PROJECT_ID"
# 再開
gcloud sql instances patch grow-pg --activation-policy ALWAYS --project "$PROJECT_ID"
```

※ コスト最小化のため自動バックアップは無効で作成している。データを守りたくなったら
`gcloud sql instances patch grow-pg --backup` で有効化する。

## トラブルシュート

- **Vertex AI のモデルが `GCP_LOCATION`（=REGION）で使えない**（404 / model not found）:
  `gemini-2.5-pro` / `gemini-2.5-flash` が `asia-northeast1` で未提供の場合がある。
  注意点として、現在の実装（`backend/app/config.py`）は `GCP_LOCATION` を **Cloud Tasks のキュー参照と
  Vertex AI の両方**に使うため、`GCP_LOCATION=global` にするとキューが見つからなくなる。対処は次のいずれか:
  1. モデルが提供されているリージョンにスタックごと寄せる（例: `REGION=us-central1` で 00 から再実行）
  2. `GEMINI_MODEL_EXECUTE` / `GEMINI_MODEL_LIGHT` を提供中のモデルIDに変更して再デプロイ
  3. Vertex 用ロケーションを分離する小改修（`GCP_LOCATION` とは別に `VERTEX_LOCATION=global` を導入）を入れる
- **`gcloud builds submit` が権限エラーになる**: Cloud Build の実行 SA（プロジェクトにより
  `PROJECT_NUMBER@cloudbuild.gserviceaccount.com` または Compute default SA）に
  `roles/artifactregistry.writer` を付与する。
- **デプロイ直後に 500 / DB 接続エラー**: `10_database.sh` を再実行した場合、パスワードが
  ローテーションされるため `30_deploy.sh` の再実行（新リビジョンがシークレットを読み直す）が必要。
- **AIジョブが動かない**: `gcloud tasks queues describe grow-jobs --location $REGION` でキュー状態、
  `gcloud run services logs read grow --region $REGION` で `/internal/jobs/run` の応答を確認。
  403 が出る場合は INTERNAL_JOBS_TOKEN のローテーション後に再デプロイしていない可能性。
- **キュー削除後に再作成できない**: Cloud Tasks は削除後 約7日間 同名キューを再作成できない。
  別名（例 `grow-jobs2`）を `CLOUD_TASKS_QUEUE` に指定し、同じ値で再デプロイする。

## デプロイ後

[`VERIFICATION.md`](./VERIFICATION.md) の本番動作検証チェックリスト（§6.8 受け入れ基準）を実施すること。
