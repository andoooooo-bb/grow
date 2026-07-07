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

    # Cloud Tasks が worker エンドポイントへ push する際の自サービスURL
    self_url: str = "http://localhost:8000"

    # /internal/jobs/run の保護トークン（#16）。本番は --allow-unauthenticated のため、
    # 設定時のみ X-Internal-Jobs-Token ヘッダの一致を検証する（enqueue 側が同ヘッダを付与）。
    # 未設定（既定・ローカル/テスト）は従来通り検証なしで素通し。
    internal_jobs_token: str = ""

    # Cloud Run が注入する待受ポート（ローカル既定 8000 / Cloud Run 既定 8080）
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
