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


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """セルフレビュー（#23 review_artifact）の判定結果。

    - verdict: approve（人のレビューへ回してよい）| revise（実行AIへ差し戻す）
    - findings: revise のとき実行AIに伝える指摘（approve なら空でよい）
    """

    verdict: Literal["approve", "revise"]
    findings: list[str]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class RuleConflictResult:
    """差し戻し理由とルールの矛盾判定（#23 check_rule_conflicts）。

    rule_ids は矛盾が疑われるルールの id（rules dict の "id" = human_id 例 "K-01"）。
    矛盾なしは空リスト。
    """

    rule_ids: list[str]
    usage: TokenUsage


# 指揮者エージェント（#22/#23）が選べる次アクションの候補
NextAction = Literal["hearing", "breakdown", "execute", "review", "handoff_human", "done"]


@dataclass(frozen=True, slots=True)
class NextActionResult:
    """指揮者エージェント（#22 orchestrate）の次アクション判断。

    - hearing: 前提が不明。壁打ちの初期質問で人に確認する
    - breakdown: 前提が揃った。サブタスク分解を提案する（反映は人の承認 §1.6）
    - execute: 実行可能。実行AI（execute ジョブ）に作業を任せる
    - review: 成果物はあるがセルフレビュー未実施。レビューAIに検査させる（#23）
    - handoff_human: AIだけでは進められない。人へバトンを渡す
    - done: これ以上の作業はない（完了扱いは呼び出し側がオートノミーで分岐）
    """

    action: NextAction
    reason: str
    usage: TokenUsage


# ---- コメント本文の構造化マーカー（#23。ジョブ/ルーターと provider 実装の共有契約） ----
# 人の構造化差し戻し理由（routers/ai.py reject が human コメント先頭に付ける）
REJECT_REASON_PREFIX = "【差し戻し理由】"
# レビューAIの指摘（jobs/review.py が revise 時の REVIEWER コメントに含める）
REVIEW_FINDINGS_MARKER = "【レビュー指摘】"


def latest_reject_reason(comments: list[dict]) -> str | None:
    """コメント履歴から直近の差し戻し理由を取り出す（無ければ None）。

    execute 系プロンプトの「# 差し戻し理由（最優先で対処）」節の材料（#23）。
    comments は {"who", "text"} の時系列リスト。
    """
    for comment in reversed(comments):
        text = comment.get("text", "")
        if REJECT_REASON_PREFIX in text:
            return text.split(REJECT_REASON_PREFIX, 1)[1].strip()
    return None


class AiProvider(ABC):
    """ランタイム AI の抽象インターフェイス（AI_PROVIDER=mock|gemini で実装を切替）。"""

    @abstractmethod
    async def execute(
        self,
        task: dict,
        rules: list[dict],
        comments: list[dict],
        *,
        policy: dict | None = None,
        plan_only: bool = False,
    ) -> ExecuteResult:
        """実作業（§7.3）: ルールと履歴を前提に Markdown 成果物を生成する。

        - policy: 行動範囲ポリシー（#21）。camelCase の素の dict
          （例 {"allowWebSearch": False, "costCapUsd": 2.0}）。None/省略キーは既定値
          （Web検索可）。allowWebSearch=False のとき実装は検索ツールを使わず、
          既知情報のみで作成して要確認事項を明記する。
        - plan_only: True なら L0（計画のみ）。成果物本文ではなく「実行プラン」を
          Markdown で返す（呼び出し側はコメントとして人へハンドオフする）。
        """

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

    @abstractmethod
    async def review_artifact(
        self, task: dict, artifact_md: str, rules: list[dict]
    ) -> ReviewResult:
        """セルフレビュー（#23）: 適用ルールを審査基準に成果物を検査する。

        実行AIの成果物（artifact_md）がルール・タスクの趣旨を満たすかを
        レビューAIが自分で判定する。revise のとき findings に「何をどう直すか」を
        実行AIへの指示として返す（review ジョブがコメント投稿→再実行に使う）。
        """

    @abstractmethod
    async def check_rule_conflicts(
        self, reason: str, rules: list[dict]
    ) -> RuleConflictResult:
        """矛盾検出（#23）: 人の差し戻し理由と矛盾するルールを特定する。

        reason は人の差し戻し理由（自由文）、rules は前回 execute に注入した
        ルール（rule_prompt_dict）。理由がルールの内容を否定している場合に
        該当ルールの id を返す（呼び出し側が confidence を1段降格する）。
        """

    @abstractmethod
    async def decide_next_action(
        self, task: dict, history: list[dict], rules: list[dict]
    ) -> NextActionResult:
        """指揮者の次の一手（#22）: タスク現況から次アクションを1つ選ぶ。

        task には基本キー（id/humanId/title/labels）に加え、判断材料として
        status / autonomy / hasChat / hasArtifact / hasReview / childStatuses を
        含める（orchestrate ジョブが現況を集約して渡す）。history はコメント履歴
        （who/text）、rules は retrieval 済みルール。
        """
