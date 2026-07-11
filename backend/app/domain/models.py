"""共有ドメイン型（§2.2）。frontend/src/types/domain.ts と鏡写し。

API 表現は camelCase（alias）、Python 内部は snake_case。
populate_by_name=True なのでどちらの名前でも構築できる。
STATUS_META は shared/contracts/status_meta.json、AUTONOMY_META は
shared/contracts/autonomy_levels.json と一致することをテストで担保する。
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


# ---- 列挙 ----
class LaneKey(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    PROGRESS = "progress"
    REVIEW = "review"
    DONE = "done"


class TaskStatus(StrEnum):
    QUEUED = "queued"  # AI待機中
    BREAKDOWN = "breakdown"  # 分解しましょう
    SPEC = "spec"  # 壁打ち中
    AI_WORK = "ai_work"  # AI作業中
    YOU_TODO = "you_todo"  # あなたの作業待ち
    YOU_REVIEW = "you_review"  # あなたのレビュー待ち
    REVIEWING = "reviewing"  # レビュー中
    DONE = "done"  # 完了


class Owner(StrEnum):
    AI = "ai"
    HUMAN = "human"


class Tone(StrEnum):
    WORK = "work"
    SPEC = "spec"
    ATTENTION = "attention"
    NEUTRAL = "neutral"
    DONE = "done"


class Author(StrEnum):
    AI = "ai"
    HUMAN = "human"


class RuleScope(StrEnum):
    PERSONAL = "personal"
    TEAM = "team"


class Confidence(StrEnum):
    HIGH = "high"
    MED = "med"
    LOW = "low"


class AiJobKind(StrEnum):
    EXECUTE = "execute"
    BREAKDOWN = "breakdown"
    DISTILL = "distill"


class AgentRole(StrEnum):
    """AIコメントの役割バッジ（#19 エージェント編成の見える化）。

    後続エージェント（#22 指揮者 / #23 レビュー）はここに値を足すだけで
    コメント役割バッジ（FE の AGENT_ROLE_META と鏡写し）に乗る。
    """

    PLANNER = "planner"  # 計画AI（壁打ち・分解・初期質問）
    EXECUTOR = "executor"  # 実行AI（着手・進捗・完了・失敗）
    REVIEWER = "reviewer"  # レビューAI（#23）
    DISTILLER = "distiller"  # 学習AI（蒸留の採用）
    CONDUCTOR = "conductor"  # 指揮者AI（#22）


class AiJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AutonomyLevel(StrEnum):
    """タスク別オートノミー（#21 L0-L3 ダイヤル）。

    AIが「自分で進めてよいか、人の承認を待つか」を判断する権限設定。
    #22 指揮者エージェントは tasks.autonomy（この値）と tasks.policy を参照して
    プラン承認ゲート（L2）や自動リレーの範囲を決める。
    """

    L0 = "L0"  # 計画のみ（実行プランを提案して人へハンドオフ）
    L1 = "L1"  # 下書きまで（既定・現行挙動: you_review で人のレビューを待つ）
    L2 = "L2"  # プラン承認後は完了まで自動（#22 指揮者が実現。現段階は L1 と同挙動）
    L3 = "L3"  # 全自動（done まで連鎖適用し自動承認。事後レビュー可）


# ---- 基底（camelCase alias） ----
class CamelModel(BaseModel):
    """API 用に camelCase alias を持つ基底モデル。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---- STATUS_META ----
class StatusMeta(CamelModel):
    label: str
    owner: Owner
    tone: Tone


# status のメタ定義（UIとバリデーションで共有する単一の真実）
STATUS_META: dict[TaskStatus, StatusMeta] = {
    TaskStatus.QUEUED: StatusMeta(label="AI待機中", owner=Owner.AI, tone=Tone.NEUTRAL),
    TaskStatus.BREAKDOWN: StatusMeta(label="分解しましょう", owner=Owner.HUMAN, tone=Tone.SPEC),
    TaskStatus.SPEC: StatusMeta(label="壁打ち中", owner=Owner.HUMAN, tone=Tone.SPEC),
    TaskStatus.AI_WORK: StatusMeta(label="AI作業中", owner=Owner.AI, tone=Tone.WORK),
    TaskStatus.YOU_TODO: StatusMeta(
        label="あなたの作業待ち", owner=Owner.HUMAN, tone=Tone.ATTENTION
    ),
    TaskStatus.YOU_REVIEW: StatusMeta(
        label="あなたのレビュー待ち", owner=Owner.HUMAN, tone=Tone.ATTENTION
    ),
    TaskStatus.REVIEWING: StatusMeta(label="レビュー中", owner=Owner.HUMAN, tone=Tone.NEUTRAL),
    TaskStatus.DONE: StatusMeta(label="完了", owner=Owner.AI, tone=Tone.DONE),
}


# ---- AUTONOMY_META（#21） ----
class AutonomyMeta(CamelModel):
    label: str
    description: str


# オートノミー・ダイヤルのメタ定義（UIツールチップと説明の単一の真実）。
# shared/contracts/autonomy_levels.json と一致することをテストで担保する。
AUTONOMY_META: dict[AutonomyLevel, AutonomyMeta] = {
    AutonomyLevel.L0: AutonomyMeta(
        label="計画のみ",
        description="実行プランだけを提案し、作業は行わない。進め方はあなたが決める",
    ),
    AutonomyLevel.L1: AutonomyMeta(
        label="下書きまで",
        description="成果物の下書きまで作成し、あなたのレビューを待つ（既定）",
    ),
    AutonomyLevel.L2: AutonomyMeta(
        label="承認後は自動",
        description="実行プランの承認後は、完了まで自動で進める",
    ),
    AutonomyLevel.L3: AutonomyMeta(
        label="全自動",
        description="完了まで自動で進めて自動承認する。内容は事後レビューできる",
    ),
}


class TaskPolicy(CamelModel):
    """行動範囲ポリシー（#21）。tasks.policy（jsonb）と鏡写し。

    省略キーは既定値（Web検索可・コスト上限なし）で解釈する。
    #22 指揮者・将来の自動リレーもこの型を通して権限を読む。
    """

    allow_web_search: bool = True  # False: provider は検索ツールを使わない
    cost_cap_usd: float | None = Field(default=None, ge=0)  # None = 上限なし


# ---- エンティティ ----
class Task(CamelModel):
    id: str  # 例 "T-098"（表示用の人間可読ID）。DB主キーは別にUUID
    workspace_id: str
    board_id: str
    lane_key: LaneKey  # 現在のレーン
    order_in_lane: int  # レーン内の並び順
    title: str
    status: TaskStatus
    owner_user_id: str  # このタスクの人側担当
    labels: list[str]  # 例 ["仕事","調査"]。retrieval のタグ照合に使う
    progress: int | None = None  # 0..100（AI作業中のみ）
    parent_id: str | None = None  # サブタスクなら親のid
    child_ids: list[str] | None = None  # 親なら子のid配列（進捗巻き上げ表示）
    # タスク別オートノミー（#21 L0-L3 ダイヤル）。既定 L1 = 現行挙動（下書きまで）
    autonomy: AutonomyLevel = AutonomyLevel.L1
    # 行動範囲ポリシー（#21）。省略キーは既定値（Web検索可・コスト上限なし）
    policy: TaskPolicy = Field(default_factory=TaskPolicy)
    # コメント件数（§3.2 カード右上の表示用）。repo が comments を集計して詰める派生値。
    # コメント作成時は task.updated イベントでも配信され、クライアントの件数が同期される（#7）。
    comment_count: int = 0
    created_at: str
    updated_at: str


class Comment(CamelModel):
    """カードのアクティビティ（人とAIの共有スレッド）。"""

    id: str
    task_id: str
    author: Author
    author_user_id: str | None = None  # human のとき
    text: str
    # AIコメントの役割バッジ（#19）。null = 役割なし（従来通り「Grow」のみ表示）
    agent_role: AgentRole | None = None
    created_at: str


class ChatMessage(CamelModel):
    """壁打ちチャット（Commentとは別スレッド）。"""

    id: str
    task_id: str
    author: Author
    text: str
    created_at: str


class Rule(CamelModel):
    """ナレッジ = 蒸留した働き方のルール。"""

    id: str  # 例 "K-01"
    workspace_id: str
    scope: RuleScope
    owner_user_id: str | None = None  # personal のとき所有者
    text: str  # ルール本文（AIへ注入する自然文）
    tags: list[str]  # 空配列 = 全体ルール。非空 = そのラベルのタスクに適用
    source: str  # 出典（例 "T-098 で2回同じ修正"）
    source_task_id: str | None = None
    confidence: Confidence
    applied: int  # 適用回数（retrievalで採用されるたび++）
    last_applied_at: str | None = None
    is_new: bool | None = None  # 採用直後の NEW バッジ表示用（クライアント表示状態）
    created_at: str
    updated_at: str


class RuleProposal(CamelModel):
    """学習フローの「候補」（採用前）。"""

    temp_id: str
    task_id: str
    text: str
    scope: RuleScope
    tags: list[str]
    confidence: Confidence


class AiJob(CamelModel):
    """AI実行ジョブ（§07）。"""

    id: str
    task_id: str
    kind: AiJobKind
    status: AiJobStatus
    applied_rule_ids: list[str]  # execute 時にretrievalで注入したルール
    error: str | None = None
    # コスト可視化（§00 #16 / §07.6）: MVPから記録だけ仕込む
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None  # 概算コスト
    created_at: str
    finished_at: str | None = None


class Artifact(CamelModel):
    """AI実作業の成果物（§00 #2）= Markdownレポート。版を重ねる。"""

    id: str
    task_id: str
    job_id: str | None = None  # どの execute ジョブが生成したか
    # 生成ジョブが注入したルールの human_id（例 ["K-01","K-03"]。#20 差分リプレイの由来表示）。
    # 人の編集版（job_id なし）は空配列。UUID は API 境界に出さない（§00 #9）。
    applied_rule_ids: list[str] = []
    version: int  # 1,2,3… タスク内で単調増加。最大版が「最新」
    content_md: str  # Markdown 本文（3行サマリー→本文→比較表→出典URL）
    created_at: str
