"""アプリケーション設定（環境変数 / .env から読み込む）。

切替は AI_PROVIDER=mock|gemini の1変数のみ（docs/design_handoff_baton/00 §0.1）。
ローカル開発は mock（費用ゼロ・ネットワーク不要・決定的）、本番のみ gemini。
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ランタイムAIの切替: mock（ローカル・テスト） / gemini（Vertex AI, 本番）
    ai_provider: Literal["mock", "gemini"] = "mock"

    # ジョブランナーの切替（§7.2）: local（asyncio.create_task, ローカル・テスト） /
    # cloud_tasks（Cloud Tasks → POST {SELF_URL}/internal/jobs/run, 本番）
    job_runner: Literal["local", "cloud_tasks"] = "local"

    # Cloud SQL for PostgreSQL（ローカルは scripts/devdb.sh のエフェメラルDB）
    database_url: str = "postgresql://grow:grow@localhost:54329/grow"

    # GCP（Cloud Run / Cloud Tasks / Vertex AI）
    gcp_project: str = ""
    gcp_location: str = "asia-northeast1"
    cloud_tasks_queue: str = "grow-jobs"

    # Vertex AI Gemini のモデル割当（00 §0.2 論点#6: コスト最小化）。
    # 実作業（execute）は品質要求が高いため Pro 系、分解/蒸留/壁打ちは Flash 系。
    # 環境変数 GEMINI_MODEL_EXECUTE / GEMINI_MODEL_LIGHT で上書き可。
    gemini_model_execute: str = "gemini-2.5-pro"
    gemini_model_light: str = "gemini-2.5-flash"

    # モデル単価テーブル（#25 コスト実算定。単位: USD / 100万トークン）。
    # execute = Pro 単価、review/orchestrate/breakdown/distill = Flash 単価
    # （app/costs.py calc_cost_usd がジョブ kind から導出する）。
    # 単価改定時は環境変数 PRICE_PRO_INPUT_USD_PER_MTOK 等で上書きできる。
    # mock プロバイダでも同じ式で算定する（デモでも $ が動く）。
    price_pro_input_usd_per_mtok: float = 1.25  # gemini-2.5-pro input
    price_pro_output_usd_per_mtok: float = 10.0  # gemini-2.5-pro output
    price_flash_input_usd_per_mtok: float = 0.30  # gemini-2.5-flash input
    price_flash_output_usd_per_mtok: float = 2.50  # gemini-2.5-flash output

    # Cloud Tasks が worker エンドポイントへ push する際の自サービスURL
    self_url: str = "http://localhost:8000"

    # /internal/jobs/run の保護トークン（#16）。本番は --allow-unauthenticated のため、
    # 設定時のみ X-Internal-Jobs-Token ヘッダの一致を検証する（enqueue 側が同ヘッダを付与）。
    # 未設定（既定・ローカル/テスト）は従来通り検証なしで素通し。
    internal_jobs_token: str = ""

    # Cloud Run が注入する待受ポート（ローカル既定 8000 / Cloud Run 既定 8080）
    port: int = 8000

    # ---- AI 利用ガード（#security — 無認証の公開デプロイでの課金暴走・悪意アクセス対策）----
    # app/guard.py が読む2層の安全弁。#21 のタスク別コスト上限（policy.costCapUsd）とは
    # 別レイヤーで、「全体の1日上限＋レート」を担う（二重チェックになっても問題ない）。

    # 1日の Gemini 想定コスト上限（USD）。当日 UTC の sum(ai_jobs.cost_usd) が
    # これ以上になったら AI 起動を止める（キルスイッチ）。AI_DAILY_BUDGET_USD で上書き。
    ai_daily_budget_usd: float = 5.0

    # プロセス内スライディングウィンドウのレート上限。
    # 既定は 10 分（ai_rate_window_sec）あたり AI 起動 30 回（ai_rate_max）まで。
    # AI_RATE_MAX / AI_RATE_WINDOW_SEC で上書き。max-instances=1 前提のプロセス内状態。
    ai_rate_max: int = 30
    ai_rate_window_sec: int = 600

    # ガードの有効/無効スイッチ（AI_GUARD_ENABLED）。テスト/ローカルは false で素通しできる。
    # AI_PROVIDER=mock でも自動では緩めない（このフラグでのみ制御する）。
    # 書き込みレート制限（assert_write_rate）もこのフラグで一緒に有効/無効を制御する。
    ai_guard_enabled: bool = True

    # 書き込み系エンドポイント（POST/PATCH）の IP 単位スライディングウィンドウ・レート上限。
    # 既定は 60 秒（write_rate_window_sec）あたり 1 IP から 60 回（write_rate_max）まで。
    # WRITE_RATE_MAX / WRITE_RATE_WINDOW_SEC で上書き。ai_guard_enabled=True のときのみ効く。
    # max-instances=1 前提のプロセス内状態（AI レートと同じ前提, #24）。
    write_rate_max: int = 60
    write_rate_window_sec: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
