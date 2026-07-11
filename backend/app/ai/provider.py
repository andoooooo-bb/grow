"""AiProvider 抽象（docs/design_handoff_baton/07 §7.0）。

LLM を叩く箇所は必ずこのインターフェイス越しにする。呼び出し側（ジョブ・API）は
実装（mock / gemini）を知らない。依存を薄く保つため、入力は素の dict / プリミティブ、
出力は本モジュールで定義する dataclass のみとする。

task dict は少なくとも id / humanId / title / labels を持つ想定。
rules / comments / chat は DB 行やチャットメッセージを素の dict にしたもの
（rules は text、chat は who / text 程度を想定）。
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
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
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ExecuteResult:
        """実作業（§7.3）: ルールと履歴を前提に Markdown 成果物を生成する。

        - policy: 行動範囲ポリシー（#21）。camelCase の素の dict
          （例 {"allowWebSearch": False, "costCapUsd": 2.0}）。None/省略キーは既定値
          （Web検索可）。allowWebSearch=False のとき実装は検索ツールを使わず、
          既知情報のみで作成して要確認事項を明記する。
        - plan_only: True なら L0（計画のみ）。成果物本文ではなく「実行プラン」を
          Markdown で返す（呼び出し側はコメントとして人へハンドオフする）。
        - on_delta: ライブ実況コールバック（#24）。指定時、実装は生成テキストの
          「増分」（累積ではない）を受信順に await で渡す。全増分の連結は返り値
          content_md の本文と一致すること（gemini の出典付加など後処理は除く）。
          plan_only=True のときは使わない（実行プランは実況しない）。
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

    @abstractmethod
    async def reconcile_rules(
        self,
        existing_rules: list[dict],
        recent_tasks: list[dict],
        feedback: list[dict],
        signals: list[dict],
    ) -> "ReconcileResult":
        """夜間ナレッジCI（#26 §6.4b/c・§6.6 / §7.5 reconcile_rules）。

        既存ルール全件・直近の完了タスク群・人の採用/却下ログ（rule_feedback）・
        暗黙評価（rule_signals）をまとめて読み、ナレッジのメンテナンス提案
        （新規蒸留 distill / 重複統合 merge / 矛盾検出 conflict / 棚卸し demote）を返す。

        - existing_rules: {"id"(K-xx), "text", "scope", "tags", "confidence", "source",
          "applied", "lastAppliedAt", "createdAt"} のリスト（archived 除外済み）
        - recent_tasks: {"humanId", "title", "labels", "status", "distilled"(bool)} のリスト
        - feedback: {"action"("adopt"|"dismiss"), "text", "scope", "tags", "confidence"}
          — 人の判断のお手本（few-shot 材料 §6.4a）
        - signals: {"ruleId"(K-xx), "signal"("positive"|"negative")} — 承認/差し戻しの暗黙評価
        提案の適用（採用/却下）は人が受信箱で判断する。ここでは提案のみを返す。
        """

    @abstractmethod
    async def assess_task(self, task: dict, rules: list[dict]) -> "AssessResult":
        """受付判定（#27 intake）: 作成直後のタスクの進め方ルートを1つ選ぶ。

        - execute: 内容が具体的でそのまま実行AIに任せられる
        - hearing: 前提が不明。questions（初期質問）で人に確認してから進める
        - breakdown: 大きい/抽象的。壁打ちでの分解から始める
        reason は判定理由コメント（受付AIの可視化）の材料として必ず返す。
        """

    @abstractmethod
    async def deep_dive(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> "DeepDiveResult":
        """深掘り自己判定（#27）: 壁打ちの毎ターン「情報は十分か」を自分で判断する。

        - ask: まだ足りない。text は深掘り質問（subtasks は空）
        - propose: 十分揃った。text は応答文、subtasks は分解候補
          （呼び出し側が subtask.proposal を配信する）
        実装は既存の chat_reply / propose_subtasks を内部で合成してもよい。
        """


# ---- 夜間ナレッジCI（#26）の構造化出力 ---------------------------------------------
# 並行開発（#27 が同ファイル上部に追記する）とのコンフリクトを避けるため、
# 本ブロックはファイル末尾に置く。AiProvider.reconcile_rules の annotation は
# 文字列参照（"ReconcileResult"）で解決される。

# 提案の種別: 新規蒸留 / 重複統合 / 矛盾検出 / 不使用ルールの棚卸し（§6.4b/c・§6.6）
CiProposalKind = Literal["distill", "merge", "conflict", "demote"]


@dataclass(frozen=True, slots=True)
class CiProposal:
    """ナレッジCIの提案1件（rule_proposals 受信箱の1行になる）。

    - distill: text/scope/tags/confidence/source が新規ルール案。source_task_id は由来タスク
    - merge: target_rule_ids（2件以上）を統合した新ルール案を text に持つ
    - conflict: target_rule_ids（矛盾する既存ルール群）の置き換え文案を text に持つ
    - demote: target_rule_ids をアーカイブ提案（text は空でよい）
    note は AI の判断説明（受信箱カードに表示。全 kind 共通で必須）。
    target_rule_ids / source_task_id は human_id（K-xx / T-xx）で表す（§00 #9）。
    """

    kind: CiProposalKind
    text: str
    scope: Literal["personal", "team"]
    tags: list[str]
    confidence: Literal["high", "med", "low"]
    source: str
    target_rule_ids: list[str]
    note: str
    source_task_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """reconcile_rules（#26）の構造化出力。"""

    proposals: list[CiProposal]
    usage: TokenUsage


# ---- 受付・深掘りエージェント（#27。並行 Wave との衝突回避のため末尾追記） ------------

# 受付エージェント（intake）が選べる進め方ルート
IntakeRoute = Literal["execute", "hearing", "breakdown"]


@dataclass(frozen=True, slots=True)
class AssessResult:
    """受付判定（#27 assess_task）の構造化出力。

    - route: execute（即実行可）| hearing（前提ヒアリングが要る）| breakdown（分解が要る）
    - questions: hearing のとき人へ投げる初期質問（それ以外は空リスト）
    - reason: 判定理由（受付AIの判定理由コメントに可視化する）
    """

    route: IntakeRoute
    questions: list[str]
    reason: str
    usage: TokenUsage


# 深掘りエージェントの自己判定モード
DeepDiveMode = Literal["ask", "propose"]


@dataclass(frozen=True, slots=True)
class DeepDiveResult:
    """深掘り自己判定（#27 deep_dive）の構造化出力。

    - mode=ask: text は深掘り質問。subtasks は空（提案しない）
    - mode=propose: text は応答文。subtasks は分解候補（subtask.proposal の材料）
    """

    mode: DeepDiveMode
    text: str
    subtasks: list[SubtaskProposal]
    usage: TokenUsage
