// 共有ドメイン型（§2.2 データモデルの単一の真実）
// backend/app/domain/models.py と鏡写し。正準フィクスチャは shared/contracts/*.json。

// ---- 列挙 ----
export type LaneKey = 'backlog' | 'todo' | 'progress' | 'review' | 'done';

export type TaskStatus =
  | 'queued' // AI待機中
  | 'breakdown' // 分解しましょう
  | 'spec' // 壁打ち中
  | 'ai_work' // AI作業中
  | 'you_todo' // あなたの作業待ち
  | 'you_review' // あなたのレビュー待ち
  | 'reviewing' // レビュー中
  | 'done'; // 完了

export type Owner = 'ai' | 'human';
export type Tone = 'work' | 'spec' | 'attention' | 'neutral' | 'done';
export type Author = 'ai' | 'human';
export type RuleScope = 'personal' | 'team';
export type Confidence = 'high' | 'med' | 'low';

export type AiJobKind = 'execute' | 'breakdown' | 'distill' | 'orchestrate' | 'review';
export type AiJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

// AIコメントの役割バッジ（#19 エージェント編成の見える化）。
// backend/app/domain/models.py の AgentRole と鏡写し。
export type AgentRole =
  | 'planner' // 計画AI（壁打ち・分解・初期質問）
  | 'executor' // 実行AI（着手・進捗・完了・失敗）
  | 'reviewer' // レビューAI（#23）
  | 'distiller' // 学習AI（蒸留の採用）
  | 'conductor'; // 指揮者AI（#22）

// 役割 → 表示ラベル。色は CSS（agent-badge--{role} 等）が持つ:
// 計画/学習=パープル・実行=ティール・レビュー=アンバー・指揮者=ダーク（§04）
export const AGENT_ROLE_META = {
  planner: { label: '計画AI' },
  executor: { label: '実行AI' },
  reviewer: { label: 'レビューAI' },
  distiller: { label: '学習AI' },
  conductor: { label: '指揮者AI' },
} as const satisfies Record<AgentRole, { label: string }>;

// ジョブ種別 → 担当役割（#19 リレー・タイムライン）。
// 後続エージェント（#22/#23）は AiJobKind と本対応に1行足すだけでタイムラインに乗る
export const JOB_KIND_ROLE = {
  breakdown: 'planner',
  execute: 'executor',
  distill: 'distiller',
  orchestrate: 'conductor', // 指揮者AI（#22 オートパイロット）
  review: 'reviewer', // レビューAI（#23 セルフレビュー）
} as const satisfies Record<AiJobKind, AgentRole>;

// ---- STATUS_META ----
export interface StatusMeta {
  label: string;
  owner: Owner;
  tone: Tone;
}

// status のメタ定義（UIとバリデーションで共有する単一の真実）
// shared/contracts/status_meta.json と一致することをテストで担保する。
export const STATUS_META = {
  queued: { label: 'AI待機中', owner: 'ai', tone: 'neutral' },
  breakdown: { label: '分解しましょう', owner: 'human', tone: 'spec' },
  spec: { label: '壁打ち中', owner: 'human', tone: 'spec' },
  ai_work: { label: 'AI作業中', owner: 'ai', tone: 'work' },
  you_todo: { label: 'あなたの作業待ち', owner: 'human', tone: 'attention' },
  you_review: { label: 'あなたのレビュー待ち', owner: 'human', tone: 'attention' },
  reviewing: { label: 'レビュー中', owner: 'human', tone: 'neutral' },
  done: { label: '完了', owner: 'ai', tone: 'done' },
} as const satisfies Record<TaskStatus, StatusMeta>;

export const ALL_TASK_STATUSES = Object.keys(STATUS_META) as TaskStatus[];

// ---- オートノミー（#21 L0-L3 ダイヤル） ----
// backend/app/domain/models.py の AutonomyLevel / AUTONOMY_META と鏡写し。
export type AutonomyLevel = 'L0' | 'L1' | 'L2' | 'L3';

export interface AutonomyMeta {
  label: string;
  description: string; // ダイヤルの説明ツールチップに使う
}

// オートノミー・ダイヤルのメタ定義（UIツールチップと説明の単一の真実）
// shared/contracts/autonomy_levels.json と一致することをテストで担保する。
export const AUTONOMY_META = {
  L0: {
    label: '計画のみ',
    description: '実行プランだけを提案し、作業は行わない。進め方はあなたが決める',
  },
  L1: {
    label: '下書きまで',
    description: '成果物の下書きまで作成し、あなたのレビューを待つ（既定）',
  },
  L2: {
    label: '承認後は自動',
    description: '実行プランの承認後は、完了まで自動で進める',
  },
  L3: {
    label: '全自動',
    description: '完了まで自動で進めて自動承認する。内容は事後レビューできる',
  },
} as const satisfies Record<AutonomyLevel, AutonomyMeta>;

export const ALL_AUTONOMY_LEVELS = Object.keys(AUTONOMY_META) as AutonomyLevel[];

/** 既定レベル（BE tasks.autonomy default 'L1' と鏡写し。L1 = 現行挙動） */
export const DEFAULT_AUTONOMY: AutonomyLevel = 'L1';

// 行動範囲ポリシー（#21）。backend TaskPolicy / tasks.policy（jsonb）と鏡写し。
// 省略キーは既定値（Web検索可・コスト上限なし）で解釈する。
export interface TaskPolicy {
  allowWebSearch?: boolean; // 省略時 true
  costCapUsd?: number | null; // null/省略 = 上限なし
}

/** 省略時既定を補完して読む（#21。BE は常に返すが旧フィクスチャ互換のため optional） */
export function taskAutonomy(task: Task): AutonomyLevel {
  return task.autonomy ?? DEFAULT_AUTONOMY;
}

export function taskAllowWebSearch(task: Task): boolean {
  return task.policy?.allowWebSearch ?? true;
}

export function taskCostCapUsd(task: Task): number | null {
  return task.policy?.costCapUsd ?? null;
}

// ---- エンティティ ----
export interface Task {
  id: string; // 例 "T-098"（表示用の人間可読ID）。DB主キーは別にUUID
  workspaceId: string;
  boardId: string;
  laneKey: LaneKey; // 現在のレーン
  orderInLane: number; // レーン内の並び順
  title: string;
  status: TaskStatus;
  ownerUserId: string; // このタスクの人側担当
  labels: string[]; // 例 ["仕事","調査"]。retrieval のタグ照合に使う
  progress?: number; // 0..100（AI作業中のみ）
  parentId?: string | null; // サブタスクなら親のid
  childIds?: string[]; // 親なら子のid配列（進捗巻き上げ表示に使用）
  // タスク別オートノミー（#21 L0-L3 ダイヤル）。BE は常に返すが旧フィクスチャ互換のため
  // optional（省略時 L1 = 現行挙動）。読み出しは taskAutonomy() を使う
  autonomy?: AutonomyLevel;
  // 行動範囲ポリシー（#21）。省略キーは既定値（Web検索可・コスト上限なし）
  policy?: TaskPolicy;
  // コメント件数（§3.2 カード右上の表示用）。backend が comments を集計して返す派生値。
  // コメント作成時は task.updated イベントでも配信され件数が同期される（#7）。
  commentCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface Comment {
  // カードのアクティビティ（人とAIの共有スレッド）
  id: string;
  taskId: string;
  author: Author; // 'ai' | 'human'
  authorUserId?: string; // human のとき
  text: string;
  // AIコメントの役割バッジ（#19）。null/未指定 = 従来通り「Grow」のみ表示
  agentRole?: AgentRole | null;
  createdAt: string;
}

export interface ChatMessage {
  // 壁打ちチャット（Commentとは別スレッド）
  id: string;
  taskId: string;
  author: Author;
  text: string;
  createdAt: string;
}

export interface Rule {
  // ナレッジ = 蒸留した働き方のルール
  id: string; // 例 "K-01"
  workspaceId: string;
  scope: RuleScope; // 'personal' | 'team'
  ownerUserId?: string; // personal のとき所有者
  text: string; // ルール本文（AIへ注入する自然文）
  tags: string[]; // 空配列 = 全体ルール。非空 = そのラベルのタスクに適用
  source: string; // 出典（例 "T-098 で2回同じ修正"）
  sourceTaskId?: string | null;
  confidence: Confidence; // high | med | low
  applied: number; // 適用回数（retrievalで採用されるたび++）
  lastAppliedAt?: string | null;
  // 棚卸しアーカイブ（#26 §6.6）。true は retrieval・ナレッジ一覧から除外される。
  // BE は常に返すが旧フィクスチャ互換のため optional（省略時 false 扱い）
  archived?: boolean;
  isNew?: boolean; // 採用直後の NEW バッジ表示用（クライアント表示状態）
  createdAt: string;
  updatedAt: string;
}

// 学習フローの「候補」（採用前）
export interface RuleProposal {
  tempId: string;
  taskId: string;
  text: string;
  scope: RuleScope;
  tags: string[];
  confidence: Confidence;
}

// AI実行ジョブ（§07）
export interface AiJob {
  id: string;
  taskId: string;
  kind: AiJobKind;
  status: AiJobStatus;
  appliedRuleIds: string[]; // execute 時にretrievalで注入したルール
  error?: string | null;
  // コスト可視化（§00 #16 / §07.6）: MVPから記録だけ仕込む
  inputTokens?: number | null;
  outputTokens?: number | null;
  costUsd?: number | null; // 概算コスト
  createdAt: string;
  finishedAt?: string | null;
}

// AI実作業の成果物（§00 #2）= Markdownレポート。再生成・差し戻しで版が増える
export interface Artifact {
  id: string;
  taskId: string;
  jobId?: string | null; // どの execute ジョブが生成したか
  // 生成ジョブが注入したルールの human_id（例 ["K-01","K-03"]。#20 差分リプレイの由来表示）。
  // 人の編集版（jobId なし）は空配列。BE は常に返すが、旧レスポンス互換のため optional。
  appliedRuleIds?: string[];
  version: number; // 1,2,3… タスク内で単調増加。最大版が「最新」
  contentMd: string; // Markdown 本文（3行サマリー→本文→比較表→出典URL）
  createdAt: string;
}
