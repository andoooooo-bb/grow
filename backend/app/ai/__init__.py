"""AI プロバイダ層（§7.0）。

呼び出し側（ジョブ・API）は必ず get_provider() 経由で AiProvider を取得し、
mock / gemini のどちらが動いているかを意識しない。
切替は環境変数 AI_PROVIDER=mock|gemini の1変数のみ（00 §0.1）。
"""

from app.ai.provider import AiProvider
from app.config import get_settings

__all__ = ["AiProvider", "get_provider"]


def get_provider() -> AiProvider:
    """config.AI_PROVIDER に応じた AiProvider 実装を返すファクトリ。"""
    settings = get_settings()
    if settings.ai_provider == "gemini":
        # Vertex AI Gemini 実装（#15）。google-genai は import が重いので遅延 import。
        from app.ai.gemini_provider import GeminiProvider

        return GeminiProvider()

    from app.ai.mock_provider import MockProvider

    return MockProvider()
