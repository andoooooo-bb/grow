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

    # Cloud SQL for PostgreSQL（ローカルは scripts/devdb.sh のエフェメラルDB）
    database_url: str = "postgresql://grow:grow@localhost:54329/grow"

    # GCP（Cloud Run / Cloud Tasks / Vertex AI）
    gcp_project: str = ""
    gcp_location: str = "asia-northeast1"
    cloud_tasks_queue: str = "grow-jobs"

    # Cloud Tasks が worker エンドポイントへ push する際の自サービスURL
    self_url: str = "http://localhost:8000"

    # Cloud Run が注入する待受ポート（ローカル既定 8000 / Cloud Run 既定 8080）
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
