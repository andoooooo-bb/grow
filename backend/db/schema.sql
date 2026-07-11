-- Grow DB スキーマ（docs/design_handoff_baton/02_data_model.md §2.4 が正）
-- 適用は空DBが前提。リセットは make db-reset → make migrate → make seed の運用。
-- human_id（T-{seq} / K-{seq}）は workspace 内連番、DB主キーは UUID（§00 #9）。

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
  -- タスク別オートノミー（#21）: 'L0'(計画のみ) | 'L1'(下書きまで・既定) |
  -- 'L2'(プラン承認後は自動) | 'L3'(全自動・事後レビュー)。正準は shared/contracts/autonomy_levels.json
  autonomy text not null default 'L1',
  -- 行動範囲ポリシー（#21）: {"allowWebSearch": bool, "costCapUsd": number|null}。
  -- 省略キーは既定値（Web検索可・コスト上限なし）。domain/models.py TaskPolicy と鏡写し
  policy jsonb not null default '{}',
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
  -- AIコメントの役割バッジ（#19）: 'planner'|'executor'|'reviewer'|'distiller'|'conductor'。
  -- null = 役割なし（human コメント・旧AIコメントは従来通り「Grow」表示）
  agent_role text,
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

-- 手動蒸留での人の採用/却下ログ（§6.4a）。将来の半自動/自動化のお手本データ（#13）
create table rule_feedback (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references tasks(id) on delete cascade,
  action text not null,             -- 'adopt' | 'dismiss'
  text text not null,
  scope text not null,
  tags text[] not null default '{}',
  confidence text not null,
  created_at timestamptz not null default now()
);

create table ai_jobs (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references tasks(id) on delete cascade,
  kind text not null,               -- 'execute' | 'breakdown' | 'distill' | 'orchestrate'(#22) | 'review'(#23)
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
