"""共有ドメイン型（§2.2）。frontend/src/types/domain.ts と鏡写し。

API 表現は camelCase（alias）、Python 内部は snake_case。
populate_by_name=True なのでどちらの名前でも構築できる。
STATUS_META は shared/contracts/status_meta.json と一致することをテストで担保する。
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
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


class AiJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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
    created_at: str
    updated_at: str


class Comment(CamelModel):
    """カードのアクティビティ（人とAIの共有スレッド）。"""

    id: str
    task_id: str
    author: Author
    author_user_id: str | None = None  # human のとき
    text: str
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
    version: int  # 1,2,3… タスク内で単調増加。最大版が「最新」
    content_md: str  # Markdown 本文（3行サマリー→本文→比較表→出典URL）
    created_at: str
