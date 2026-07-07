"""AiProvider 抽象（docs/design_handoff_baton/07 §7.0）。

LLM を叩く箇所は必ずこのインターフェイス越しにする。呼び出し側（ジョブ・API）は
実装（mock / gemini）を知らない。依存を薄く保つため、入力は素の dict / プリミティブ、
出力は本モジュールで定義する dataclass のみとする。

task dict は少なくとも id / humanId / title / labels を持つ想定。
rules / comments / chat は DB 行やチャットメッセージを素の dict にしたもの
（rules は text、chat は who / text 程度を想定）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """1回の呼び出しで消費したトークン数（§7.6: ai_jobs に記録する）。"""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    """実作業（execute §7.3）の結果。成果物は Markdown レポート。"""

    content_md: str
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class SubtaskProposal:
    """分解候補の1件（§7.4b tool propose_subtasks の items に対応）。"""

    title: str
    owner: Literal["ai", "human"]
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class ProposeSubtasksResult:
    """分解（breakdown §7.4）の構造化出力。"""

    subtasks: list[SubtaskProposal]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class RuleProposal:
    """蒸留候補の1件（§7.5 tool propose_rules の items に対応）。"""

    text: str
    scope: Literal["personal", "team"]
    tags: list[str]
    confidence: Literal["high", "med", "low"]
    source: str


@dataclass(frozen=True, slots=True)
class ProposeRulesResult:
    """蒸留（distill §7.5）の構造化出力。"""

    rules: list[RuleProposal]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ChatReplyResult:
    """壁打ちチャット応答（§7.4a）。"""

    text: str
    usage: TokenUsage


class AiProvider(ABC):
    """ランタイム AI の抽象インターフェイス（AI_PROVIDER=mock|gemini で実装を切替）。"""

    @abstractmethod
    async def execute(
        self, task: dict, rules: list[dict], comments: list[dict]
    ) -> ExecuteResult:
        """実作業（§7.3）: ルールと履歴を前提に Markdown 成果物を生成する。"""

    @abstractmethod
    async def propose_subtasks(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ProposeSubtasksResult:
        """分解（§7.4b）: 壁打ち内容に基づきサブタスク候補を構造化出力する。"""

    @abstractmethod
    async def propose_rules(
        self, task: dict, comments: list[dict], chat: list[dict]
    ) -> ProposeRulesResult:
        """蒸留（§7.5）: タスク履歴から再利用可能な働き方ルールを抽出する。"""

    @abstractmethod
    async def chat_reply(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ChatReplyResult:
        """壁打ち応答（§7.4a）: 初回は前提確認の質問、以降は分解へ誘導する応答。"""
