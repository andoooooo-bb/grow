"""GeminiProvider — Vertex AI Gemini 実装のスタブ（Issue #15 で実装する）。

§7.1 の Gemini 実装（Function Calling / Google Search グラウンディング）が入る場所。
現時点では各メソッドが NotImplementedError を送出する。
"""

from app.ai.provider import (
    AiProvider,
    ChatReplyResult,
    ExecuteResult,
    ProposeRulesResult,
    ProposeSubtasksResult,
)

_NOT_IMPLEMENTED = "GeminiProvider は Issue #15 で実装予定です（現在は AI_PROVIDER=mock を使用）"


class GeminiProvider(AiProvider):
    """Vertex AI Gemini プロバイダ（未実装スタブ）。"""

    async def execute(
        self, task: dict, rules: list[dict], comments: list[dict]
    ) -> ExecuteResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def propose_subtasks(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ProposeSubtasksResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def propose_rules(
        self, task: dict, comments: list[dict], chat: list[dict]
    ) -> ProposeRulesResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def chat_reply(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ChatReplyResult:
        raise NotImplementedError(_NOT_IMPLEMENTED)
