"""API コントラクト（DTO）。frontend/src/types/api.ts と鏡写し。

§2.3 の正規化ストア形（cards: id辞書 / lanes: cardIds 配列で順序保持）に忠実。
"""

from pydantic import Field

from app.domain.models import (
    AgentRole,
    AiJob,
    AiJobKind,
    AiJobStatus,
    Artifact,
    Author,
    AutonomyLevel,
    CamelModel,
    Confidence,
    LaneKey,
    Owner,
    Rule,
    RuleScope,
    Task,
    TaskPolicy,
    TaskStatus,
)


# ---- ボード取得 ----
class LaneDto(CamelModel):
    key: LaneKey
    name: str  # backlog=バックログ, todo=ToDo, progress=進行中, review=レビュー, done=完了
    card_ids: list[str]  # 並び順を保持


class BoardResponse(CamelModel):
    lanes: list[LaneDto]
    cards: dict[str, Task]  # id -> Task（サブタスク含む全カード）
    rules: list[Rule]


# ---- タスク ----
class TaskPatch(CamelModel):
    """PATCH /tasks/:id（部分更新。指定フィールドのみ変更）。"""

    lane_key: LaneKey | None = None
    order_in_lane: int | None = None
    title: str | None = None
    status: TaskStatus | None = None
    labels: list[str] | None = None
    progress: int | None = None  # null で明示クリア（ai_work 以外は null が不変条件）
    parent_id: str | None = None
    autonomy: AutonomyLevel | None = None  # #21 オートノミー・ダイヤル（L0-L3）
    policy: TaskPolicy | None = None  # #21 行動範囲ポリシー（全体置換）


class TaskCreate(CamelModel):
    """POST /boards/:id/tasks。"""

    lane_key: LaneKey
    title: str
    status: TaskStatus = TaskStatus.BREAKDOWN  # 省略時 'breakdown'（§5.3 addCard）
    labels: list[str] = []
    parent_id: str | None = None


# ---- コメント / 壁打ちチャット ----
class CommentCreate(CamelModel):
    """POST /tasks/:id/comments。"""

    author: Author
    author_user_id: str | None = None  # human のとき
    text: str
    # AIコメントの役割バッジ（#19）。省略時 null = 役割なし
    agent_role: AgentRole | None = None


class ChatMessageCreate(CamelModel):
    """POST /tasks/:id/chat。"""

    author: Author
    text: str


class ChatSendRequest(CamelModel):
    """POST /tasks/:id/chat の実リクエスト（#11）。送信者は常に human。"""

    text: str


# ---- 壁打ち → 分解（§1.6 / §5.3 confirmBreakdown, #11） ----
class BreakdownConfirmItem(CamelModel):
    """confirmBreakdown の1項目（クライアントが subtask.proposal の候補を送り返す）。"""

    title: str
    owner: Owner  # ai → queued（先頭のみ ai_work）/ human → you_todo


class BreakdownConfirmRequest(CamelModel):
    """POST /tasks/:id/breakdown/confirm（1件以上）。"""

    subtasks: list[BreakdownConfirmItem] = Field(min_length=1)


class BreakdownConfirmResponse(CamelModel):
    """confirmBreakdown の応答（親は childIds 込み・子は生成順）。"""

    parent: Task
    children: list[Task]


# ---- 成果物 ----
class ArtifactResponse(CamelModel):
    """GET /tasks/:id/artifacts（版の一覧。最大 version が最新）。"""

    task_id: str
    artifacts: list[Artifact]


class ArtifactCreate(CamelModel):
    """POST /tasks/:id/artifacts（人の編集を新版として保存, #10 レビュー画面）。"""

    content_md: str


# ---- AIジョブ（§7.2） ----
class AssignAiResponse(CamelModel):
    """POST /tasks/:id/assign-ai の応答（enqueue したジョブの ID）。"""

    job_id: str


class RejectRequest(CamelModel):
    """POST /tasks/:id/reject（#23 人の構造化差し戻し）。理由は必須。"""

    reason: str = Field(min_length=1)


class JobsResponse(CamelModel):
    """GET /tasks/:id/jobs（#19 リレー・タイムライン。created_at 昇順 = リレー履歴）。"""

    task_id: str
    jobs: list[AiJob]


class JobRunRequest(CamelModel):
    """POST /internal/jobs/run（Cloud Tasks / local ランナーのターゲット）。"""

    job_id: str


# ---- 意思決定トレース（#25） ----
class TraceEntry(CamelModel):
    """成果物1版ぶんのトレース行（GET /tasks/:id/trace）。

    「どのジョブが・どのルール（K-xx）を前提に・何トークン/$いくらで生成したか」。
    人の編集版（job_id なし）は kind 以下がすべて null/空 = FE は「あなたが編集」と表示。
    """

    version: int
    job_id: str | None = None
    kind: AiJobKind | None = None  # None = 人の編集版
    status: AiJobStatus | None = None
    applied_rule_ids: list[str] = []  # ルールの human_id（例 ["K-01","K-03"]。注入順）
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    created_at: str  # この版の保存時刻
    finished_at: str | None = None  # 生成ジョブの完了時刻


class TraceResponse(CamelModel):
    """GET /tasks/:id/trace（version 昇順。末尾が最新版）。"""

    task_id: str
    entries: list[TraceEntry]


# ---- 学習ダッシュボード統計（#25） ----
class RuleApplicationPoint(CamelModel):
    """ルール適用回数の日別1点（学習曲線スパークラインの素材）。"""

    date: str  # YYYY-MM-DD
    count: int


class StatsResponse(CamelModel):
    """GET /api/stats（ワークスペース横断の学習・コスト集計）。"""

    ai_done_count: int  # succeeded した execute ジョブ数（AIが完遂した実作業）
    total_cost_usd: float  # ai_jobs.cost_usd の累計（実算定 #25）
    total_tokens: int  # input+output トークンの累計
    rule_applications: list[RuleApplicationPoint]  # 直近14日・古い順（欠損日は 0）
    rule_applications_total: int  # 適用回数の累計（rules.applied の合計）
    reject_count: int  # 人の差し戻し回数（【差し戻し理由】コメント数）
    rules_count: int  # ナレッジのルール総数


# ---- ルール（ナレッジ） ----
class RuleCreate(CamelModel):
    """POST /rules（蒸留候補の採用等）。"""

    scope: RuleScope
    owner_user_id: str | None = None  # personal のとき
    text: str
    tags: list[str]
    source: str
    source_task_id: str | None = None
    confidence: Confidence


class RulePatch(CamelModel):
    """PATCH /rules/:id（例: promoteRule は {scope:'team'}）。"""

    scope: RuleScope | None = None
    text: str | None = None
    tags: list[str] | None = None
    confidence: Confidence | None = None


# ---- AI 構造化出力（§7.4b / §7.5） ----
class SubtaskProposal(CamelModel):
    """propose_subtasks の1項目（分解候補）。"""

    title: str
    owner: Owner  # AIが実行可能なら ai、人の判断/作業が必須なら human
    rationale: str | None = None


class SubtaskProposalEvent(CamelModel):
    """subtask.proposal イベントの payload（#11）。

    候補はサーバ側に永続化しない。SSE で届いた候補をクライアントが保持し、
    confirmBreakdown 時に POST /tasks/:id/breakdown/confirm へ送り返す。
    """

    task_id: str
    subtasks: list[SubtaskProposal]


class RuleProposalDto(CamelModel):
    """propose_rules の1項目（蒸留候補）。永続化時は tempId/taskId を付与（§2.2）。"""

    temp_id: str
    task_id: str
    text: str
    scope: RuleScope
    tags: list[str]
    confidence: Confidence
    source: str = ""  # 抽出根拠（§7.5。表示は任意なので FE 型には必須で置かない）


# ---- 手動蒸留（#13 §6.4a / §5.3 adoptLearn・dismissLearn） ----
class LearnDecisionRequest(CamelModel):
    """POST /tasks/:id/learn/adopt・/learn/dismiss の body（候補1件の内容）。"""

    text: str
    scope: RuleScope
    tags: list[str]
    confidence: Confidence
