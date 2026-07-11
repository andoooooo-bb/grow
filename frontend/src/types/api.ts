// API コントラクト（DTO）。backend/app/domain/dto.py と鏡写し。
// §2.3 の正規化ストア形（cards: id辞書 / lanes: cardIds 配列で順序保持）に忠実。

import type {
  AgentRole,
  AiJob,
  AiJobKind,
  AiJobStatus,
  Artifact,
  Author,
  AutonomyLevel,
  Confidence,
  LaneKey,
  Owner,
  Rule,
  RuleScope,
  Task,
  TaskPolicy,
  TaskStatus,
} from './domain.ts';

// ---- ボード取得 ----
export interface LaneDto {
  key: LaneKey;
  name: string; // backlog=バックログ, todo=ToDo, progress=進行中, review=レビュー, done=完了
  cardIds: string[]; // 並び順を保持
}

export interface BoardResponse {
  lanes: LaneDto[];
  cards: Record<string, Task>; // id -> Task（サブタスク含む全カード）
  rules: Rule[];
}

// ---- タスク ----
// PATCH /tasks/:id（部分更新。指定フィールドのみ変更）
export interface TaskPatch {
  laneKey?: LaneKey;
  orderInLane?: number;
  title?: string;
  status?: TaskStatus;
  labels?: string[];
  progress?: number | null; // null で明示クリア（ai_work 以外は null が不変条件）
  parentId?: string | null;
  autonomy?: AutonomyLevel; // #21 オートノミー・ダイヤル（L0-L3）
  policy?: TaskPolicy; // #21 行動範囲ポリシー（全体置換）
}

// POST /boards/:id/tasks
export interface TaskCreate {
  laneKey: LaneKey;
  title: string;
  status?: TaskStatus; // 省略時 'breakdown'（§5.3 addCard）
  labels?: string[];
  parentId?: string | null;
}

// ---- コメント / 壁打ちチャット ----
// POST /tasks/:id/comments
export interface CommentCreate {
  author: Author;
  authorUserId?: string; // human のとき
  text: string;
  agentRole?: AgentRole; // AIコメントの役割バッジ（#19。省略時 null）
}

// POST /tasks/:id/chat
export interface ChatMessageCreate {
  author: Author;
  text: string;
}

// POST /tasks/:id/chat の実リクエスト（#11。送信者は常に human）
export interface ChatSendRequest {
  text: string;
}

// ---- 壁打ち → 分解（§1.6 / §5.3 confirmBreakdown, #11/#12） ----
// confirmBreakdown の1項目（クライアントが subtask.proposal の候補を送り返す）
export interface BreakdownConfirmItem {
  title: string;
  owner: Owner; // ai → queued（先頭のみ ai_work）/ human → you_todo
}

// POST /tasks/:id/breakdown/confirm（1件以上。空配列は 422）
export interface BreakdownConfirmRequest {
  subtasks: BreakdownConfirmItem[];
}

// confirmBreakdown の応答（親は childIds 込み・子は生成順）
export interface BreakdownConfirmResponse {
  parent: Task;
  children: Task[];
}

// subtask.proposal イベントの payload（#11。候補はサーバ非永続 —
// SSE で届いた候補を proposal[taskId] に保持し、confirm で送り返す）
export interface SubtaskProposalEvent {
  taskId: string;
  subtasks: SubtaskProposal[];
}

// ---- 成果物 ----
// GET /tasks/:id/artifacts（版の一覧。最大 version が最新）
export interface ArtifactResponse {
  taskId: string;
  artifacts: Artifact[];
}

// artifact.delta イベントの payload（#24 ライブ実況。サーバ非永続 —
// backend/app/events.py ARTIFACT_DELTA と鏡写し）。
// delta は生成テキストの増分（累積ではない）。seq は 1 始まりの受信連番で、
// seq=1 は新しいストリームの開始（liveDraft をリセットして連結し直す）
export interface ArtifactDeltaEvent {
  taskId: string;
  delta: string;
  seq: number;
}

// POST /tasks/:id/artifacts（人の編集を新版として保存, #10 §00 #12）
export interface ArtifactCreate {
  contentMd: string;
}

// ---- AIジョブ（§7.2） ----
// POST /tasks/:id/assign-ai の応答（202: enqueue したジョブの ID）
export interface AssignAiResponse {
  jobId: string;
}

// POST /tasks/:id/reject（#23 人の構造化差し戻し）。理由は必須
export interface RejectRequest {
  reason: string;
}

// GET /tasks/:id/jobs（#19 リレー・タイムライン。createdAt 昇順 = リレー履歴）
export interface JobsResponse {
  taskId: string;
  jobs: AiJob[];
}

// ---- 意思決定トレース（#25） ----
// 成果物1版ぶんのトレース行（GET /tasks/:id/trace）。
// 「どのジョブが・どのルール（K-xx）を前提に・何トークン/$いくらで生成したか」。
// 人の編集版（jobId なし）は kind 以下がすべて null/空 = 「あなたが編集」と表示する
export interface TraceEntry {
  version: number;
  jobId?: string | null;
  kind?: AiJobKind | null; // null = 人の編集版
  status?: AiJobStatus | null;
  appliedRuleIds: string[]; // ルールの human_id（例 ["K-01","K-03"]。注入順）
  inputTokens?: number | null;
  outputTokens?: number | null;
  costUsd?: number | null;
  createdAt: string; // この版の保存時刻
  finishedAt?: string | null; // 生成ジョブの完了時刻
}

// GET /tasks/:id/trace（version 昇順。末尾が最新版）
export interface TraceResponse {
  taskId: string;
  entries: TraceEntry[];
}

// ---- 学習ダッシュボード統計（#25） ----
// ルール適用回数の日別1点（学習曲線スパークラインの素材）
export interface RuleApplicationPoint {
  date: string; // YYYY-MM-DD
  count: number;
}

// GET /api/stats（ワークスペース横断の学習・コスト集計）
export interface StatsResponse {
  aiDoneCount: number; // succeeded した execute ジョブ数（AIが完遂した実作業）
  totalCostUsd: number; // ai_jobs.cost_usd の累計（実算定 #25）
  totalTokens: number; // input+output トークンの累計
  ruleApplications: RuleApplicationPoint[]; // 直近14日・古い順（欠損日は 0）
  ruleApplicationsTotal: number; // 適用回数の累計（rules.applied の合計）
  rejectCount: number; // 人の差し戻し回数（【差し戻し理由】コメント数）
  rulesCount: number; // ナレッジのルール総数
}

// ---- ルール（ナレッジ） ----
// POST /rules（蒸留候補の採用等）
export interface RuleCreate {
  scope: RuleScope;
  ownerUserId?: string; // personal のとき
  text: string;
  tags: string[];
  source: string;
  sourceTaskId?: string | null;
  confidence: Confidence;
}

// PATCH /rules/:id（例: promoteRule は {scope:'team'}）
export interface RulePatch {
  scope?: RuleScope;
  text?: string;
  tags?: string[];
  confidence?: Confidence;
}

// ---- AI 構造化出力（§7.4b / §7.5） ----
// propose_subtasks の1項目（分解候補）
export interface SubtaskProposal {
  title: string;
  owner: Owner; // AIが実行可能なら ai、人の判断/作業が必須なら human
  rationale?: string;
}

// propose_rules の1項目（蒸留候補）。永続化時は tempId/taskId を付与（§2.2 RuleProposal）
export interface RuleProposalDto {
  tempId: string;
  taskId: string;
  text: string;
  scope: RuleScope;
  tags: string[];
  confidence: Confidence;
  source?: string; // 抽出根拠（§7.5。BE は返すが表示は任意なので optional）
}

// ---- 手動蒸留（#13/#14 §6.4a / §5.3 adoptLearn・dismissLearn） ----
// POST /tasks/:id/learn/adopt・/learn/dismiss の body（候補1件の内容）
export interface LearnDecisionRequest {
  text: string;
  scope: RuleScope;
  tags: string[];
  confidence: Confidence;
}
