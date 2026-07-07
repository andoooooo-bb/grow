# 02 · データモデル

プロトタイプの状態構造をそのまま本番のデータモデルに写像したもの。TypeScript 型（フロント/API 共有想定）と PostgreSQL スキーマの両方を示す。**チーム化を見据え、最初から `workspace_id` / `owner_user_id` を持たせる**（MVPでは単一ワークスペース・単一ユーザーの固定値で運用可）。

> **DB 前提（§00 で確定）:** 本番は **Cloud SQL for PostgreSQL**（最小ティア）。ローカルは Docker の Postgres 等でよい。以下 DDL はそのまま Cloud SQL に適用できる。`human_id`（`T-{seq}` / `K-{seq}`）は workspace 内連番、DB主キーは UUID（§00 #9）。

---

## 2.1 エンティティ相関（概念）

```
Workspace 1──∞ User
Workspace 1──∞ Board 1──∞ Lane 1──∞ Task
Task 1──∞ Comment          （アクティビティ/やり取りの履歴）
Task 1──∞ Task             （self参照: parent_id でサブタスク）
Task ∞──∞ Rule             （retrieval時の適用関係: rule_applications）
Workspace 1──∞ Rule        （ナレッジ。scope=personal は owner_user_id 紐付き、team は workspace 共有）
Task 1──∞ AiJob            （AI実作業/蒸留の実行ジョブ; §07）
Task 1──∞ ChatMessage      （壁打ちチャット。Commentとは別スレッド）
Task 1──∞ Artifact         （AI実作業の成果物=Markdownレポート。版を重ねる; §00 #2）
AiJob 1──0..1 Artifact      （execute ジョブが1版を生成）
```

> サブタスクは独立テーブルにせず、**Task の自己参照**（`parent_id`）で表現。プロトタイプと同じ。

---

## 2.2 TypeScript 型

```ts
// ---- 列挙 ----
export type LaneKey = 'backlog' | 'todo' | 'progress' | 'review' | 'done';

export type TaskStatus =
  | 'queued'      // AI待機中
  | 'breakdown'   // 分解しましょう
  | 'spec'        // 壁打ち中
  | 'ai_work'     // AI作業中
  | 'you_todo'    // あなたの作業待ち
  | 'you_review'  // あなたのレビュー待ち
  | 'reviewing'   // レビュー中
  | 'done';       // 完了

export type Owner = 'ai' | 'human';
export type Tone  = 'work' | 'spec' | 'attention' | 'neutral' | 'done';
export type Author = 'ai' | 'human';
export type RuleScope = 'personal' | 'team';
export type Confidence = 'high' | 'med' | 'low';

// status のメタ定義（UIとバリデーションで共有する単一の真実）
export const STATUS_META: Record<TaskStatus, { label: string; owner: Owner; tone: Tone }> = {
  queued:     { label: 'AI待機中',           owner: 'ai',    tone: 'neutral' },
  breakdown:  { label: '分解しましょう',       owner: 'human', tone: 'spec' },
  spec:       { label: '壁打ち中',            owner: 'human', tone: 'spec' },
  ai_work:    { label: 'AI作業中',            owner: 'ai',    tone: 'work' },
  you_todo:   { label: 'あなたの作業待ち',     owner: 'human', tone: 'attention' },
  you_review: { label: 'あなたのレビュー待ち',  owner: 'human', tone: 'attention' },
  reviewing:  { label: 'レビュー中',           owner: 'human', tone: 'neutral' },
  done:       { label: '完了',                owner: 'ai',    tone: 'done' },
};

// ---- エンティティ ----
export interface Task {
  id: string;                 // 例 "T-098"（表示用の人間可読ID）。DB主キーは別にUUID推奨
  workspaceId: string;
  boardId: string;
  laneKey: LaneKey;           // 現在のレーン
  orderInLane: number;        // レーン内の並び順
  title: string;
  status: TaskStatus;
  ownerUserId: string;        // このタスクの人側担当
  labels: string[];           // 例 ["仕事","調査"]。retrieval のタグ照合に使う
  progress?: number;          // 0..100（AI作業中のみ）
  parentId?: string | null;   // サブタスクなら親のid
  childIds?: string[];        // 親なら子のid配列（進捗巻き上げ表示に使用）
  createdAt: string;
  updatedAt: string;
}

export interface Comment {     // カードのアクティビティ（人とAIの共有スレッド）
  id: string;
  taskId: string;
  author: Author;             // 'ai' | 'human'
  authorUserId?: string;      // human のとき
  text: string;
  createdAt: string;
}

export interface ChatMessage { // 壁打ちチャット（Commentとは別スレッド）
  id: string;
  taskId: string;
  author: Author;
  text: string;
  createdAt: string;
}

export interface Rule {        // ナレッジ = 蒸留した働き方のルール
  id: string;                 // 例 "K-01"
  workspaceId: string;
  scope: RuleScope;           // 'personal' | 'team'
  ownerUserId?: string;       // personal のとき所有者
  text: string;               // ルール本文（AIへ注入する自然文）
  tags: string[];             // 空配列 = 全体ルール。非空 = そのラベルのタスクに適用
  source: string;             // 出典（例 "T-098 で2回同じ修正" / "T-098 から学習" / "チーム標準"）
  sourceTaskId?: string | null;
  confidence: Confidence;     // high | med | low
  applied: number;            // 適用回数（retrievalで採用されるたび++）
  lastAppliedAt?: string | null;
  isNew?: boolean;            // 採用直後の NEW バッジ表示用（クライアント表示状態）
  createdAt: string;
  updatedAt: string;
}

// 学習フローの「候補」（採用前。永続化は任意でも良いが、半自動フェーズでは保存推奨）
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
  kind: 'execute' | 'breakdown' | 'distill';
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  appliedRuleIds: string[];   // execute 時にretrievalで注入したルール
  error?: string | null;
  // コスト可視化（§00 #16 / §07.6）: MVPから記録だけ仕込む
  inputTokens?: number | null;
  outputTokens?: number | null;
  costUsd?: number | null;    // 概算コスト（将来のチーム課金・上限管理の土台）
  createdAt: string;
  finishedAt?: string | null;
}

// AI実作業の成果物（§00 #2）= Markdownレポート。再生成・差し戻しで版が増える
export interface Artifact {
  id: string;
  taskId: string;
  jobId?: string | null;      // どの execute ジョブが生成したか
  version: number;            // 1,2,3… タスク内で単調増加。最大版が「最新」
  contentMd: string;          // Markdown 本文（3行サマリー→本文→比較表→出典URL）
  createdAt: string;
}
```

---

## 2.3 フロント状態ストア形（正規化）

プロトタイプと同一の正規化。Zustand/Redux でこの形を推奨。

```ts
interface BoardState {
  cards: Record<string, Task>;                 // id -> Task（サブタスク含む全カード）
  lanes: { key: LaneKey; name: string; cardIds: string[] }[]; // 並び順を cardIds が保持
  rules: Rule[];
  // UI状態
  selectedId: string | null;                   // 開いているカード
  panelMode: 'detail' | 'chat';                // ドロワーのモード
  showKnowledge: boolean;                       // ナレッジ・オーバーレイ表示
  chat: Record<string, ChatMessage[]>;          // taskId -> 壁打ちメッセージ
  proposal: Record<string, RuleProposal[]>;     // 分解候補（taskId -> ）
  learn: Record<string, RuleProposal[]>;        // 蒸留候補（taskId -> ）
  artifacts: Record<string, Artifact[]>;        // taskId -> 成果物の版（§00 #2）
  drafts: Record<string, string>;               // コンポーザ入力
}
```

レーン名: backlog=バックログ, todo=ToDo, progress=進行中, review=レビュー, done=完了。

---

## 2.4 PostgreSQL スキーマ（DDL）

```sql
create table workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table users (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id),
  display_name text not null,
  initials text not null,           -- アバター表示（例 "YK"）
  created_at timestamptz not null default now()
);

create table boards (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id),
  name text not null default '個人ボード'
);

-- レーンは固定5種でも良いが、将来のカスタム列に備えテーブル化
create table lanes (
  id uuid primary key default gen_random_uuid(),
  board_id uuid not null references boards(id),
  key text not null,                -- 'backlog' | 'todo' | 'progress' | 'review' | 'done'
  name text not null,
  position int not null
);

create table tasks (
  id uuid primary key default gen_random_uuid(),
  human_id text not null,           -- 表示用 "T-098"（workspace内ユニーク）
  workspace_id uuid not null references workspaces(id),
  board_id uuid not null references boards(id),
  lane_key text not null,
  order_in_lane int not null default 0,
  title text not null,
  status text not null,             -- TaskStatus
  owner_user_id uuid references users(id),
  labels text[] not null default '{}',
  progress int,                     -- 0..100 nullable
  parent_id uuid references tasks(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on tasks (board_id, lane_key, order_in_lane);
create index on tasks (parent_id);
create index on tasks using gin (labels);

create table comments (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references tasks(id) on delete cascade,
  author text not null,             -- 'ai' | 'human'
  author_user_id uuid references users(id),
  text text not null,
  created_at timestamptz not null default now()
);
create index on comments (task_id, created_at);

create table chat_messages (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references tasks(id) on delete cascade,
  author text not null,
  text text not null,
  created_at timestamptz not null default now()
);

create table rules (
  id uuid primary key default gen_random_uuid(),
  human_id text not null,           -- "K-01"
  workspace_id uuid not null references workspaces(id),
  scope text not null,              -- 'personal' | 'team'
  owner_user_id uuid references users(id),
  text text not null,
  tags text[] not null default '{}',
  source text not null default '',
  source_task_id uuid references tasks(id),
  confidence text not null default 'med',
  applied int not null default 0,
  last_applied_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
  -- 将来: embedding vector(1536)  -- pgvector で意味検索（§06）
);
create index on rules using gin (tags);
create index on rules (workspace_id, scope);

create table rule_applications (   -- どのルールをどのタスクに適用したか（証跡・分析）
  id uuid primary key default gen_random_uuid(),
  rule_id uuid not null references rules(id) on delete cascade,
  task_id uuid not null references tasks(id) on delete cascade,
  applied_at timestamptz not null default now()
);

create table ai_jobs (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references tasks(id) on delete cascade,
  kind text not null,               -- 'execute' | 'breakdown' | 'distill'
  status text not null default 'queued',
  applied_rule_ids uuid[] not null default '{}',
  error text,
  input_tokens int,                 -- コスト可視化（§00 #16 / §07.6）
  output_tokens int,
  cost_usd numeric(10,4),           -- 概算コスト
  created_at timestamptz not null default now(),
  finished_at timestamptz
);

-- AI実作業の成果物（§00 #2）: Markdownレポート。版を重ねる（最大 version が最新）
create table artifacts (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references tasks(id) on delete cascade,
  job_id uuid references ai_jobs(id),
  version int not null,             -- タスク内で 1,2,3…
  content_md text not null,
  created_at timestamptz not null default now(),
  unique (task_id, version)
);
create index on artifacts (task_id, version desc);  -- 最新版の取得を高速に
```

> **アクセサで隠す（§00 #2）:** 成果物の読み書きは `getLatestArtifact(taskId)` / `saveArtifact(taskId, md, jobId)` のような小さな関数に閉じ込め、呼び出し側は保存形態（テーブル/版）を意識しない。将来オブジェクトストレージ（Cloud Storage）へ移す場合も中身の差し替えだけで済む。

---

## 2.5 シードデータ（プロトタイプと同一 — デモ/テストに使う）

**タスク（レーン別）:**
- backlog: `T-130` ポートフォリオサイトのリニューアル（breakdown, labels[個人,デザイン]）／`T-121` 確定申告に必要な書類リストを作成（queued, [経理]）
- todo: `T-104` 競合SaaS 5社の料金プランを調査（spec, [仕事,調査]）／`T-109` 週次レビューのテンプレートを記入（you_todo, [個人]）／`T-112` ブログ記事の構成案づくり（queued, [ブログ]）
- progress: `T-098` 競合調査レポートの下書き（ai_work, progress 60, [仕事,調査]）／`T-101` 新機能のリリースノート（you_todo, [ブログ]）
- review: `T-091` 確定申告サマリーの最終確認（you_review, [経理]）／`T-089` 記事『AI協働術』の校正（reviewing, [ブログ]）
- done: `T-080` 先週の経費を費目ごとに分類（done, [経理]）／`T-077` 名刺データをCSVに整理（done, [仕事]）

**ルール（ナレッジ）:**
| id | scope | text | tags | source | conf | applied |
|---|---|---|---|---|---|---|
| K-01 | personal | レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く | [調査,ブログ] | T-098 で2回同じ修正 | high | 6 |
| K-02 | personal | 絵文字は使わない。文体は簡潔・断定調に統一する | [] (全体) | 複数タスクで一貫 | high | 14 |
| K-03 | personal | 競合調査は料金を表形式にし、各項目に出典URLを付ける | [調査] | T-098 | med | 2 |
| K-04 | team | 社外向け文書は敬体。数値は必ず出典を明記する | [] (全体) | チーム標準 | high | 9 |
| K-05 | team | 経費の費目分類は自社の勘定科目マスタに合わせる | [経理] | 経理チーム | high | 5 |

**蒸留候補（学ぶ を押したとき提示する例）:**
- T-098 → 「競合ごとにセクションを分け、末尾に横断比較表を置くと差し戻しが減る」(personal, [調査], med) ／「料金は必ず税抜/税込を明記する」(personal, [調査,経理], med)
- T-091 → 「確定申告サマリーは控除候補を別セクションで先に提示する」(personal, [経理], med)
- その他 → 「このタスクで繰り返した指示を、今後の既定の進め方にする」(personal, カードのlabels, low)

**分解候補（壁打ち後に提示する例）:**
- T-130 → 情報設計・サイトマップ作成(AI) / ワイヤーフレーム作成(AI) / 掲載する実績コンテンツの選定(人) / デザイン方向性の決定(人) / コーディング・実装(AI)
- その他 → 要件・前提の整理(AI) / たたき台の作成(AI) / 内容の確認・決定(人) / 仕上げ(AI)
