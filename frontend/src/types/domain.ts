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

export type AiJobKind = 'execute' | 'breakdown' | 'distill';
export type AiJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

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
