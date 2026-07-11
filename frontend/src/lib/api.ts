// fetch ラッパ。API コントラクトは types/api.ts（backend/app/domain/dto.py と鏡写し）。

import type {
  ArtifactCreate,
  ArtifactResponse,
  AssignAiResponse,
  BoardResponse,
  BreakdownConfirmRequest,
  BreakdownConfirmResponse,
  ChatSendRequest,
  CommentCreate,
  JobsResponse,
  LearnDecisionRequest,
  RuleProposalDto,
  TaskCreate,
  TaskPatch,
} from '../types/api.ts';
import type { Artifact, ChatMessage, Comment, Rule, Task } from '../types/domain.ts';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const method = init?.method ?? 'GET';
    throw new ApiError(res.status, `${method} ${path} failed with status ${res.status}`);
  }
  return (await res.json()) as T;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function patchJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** ボード全体（正規化形: lanes / cards / rules）を取得する。 */
export function getBoard(): Promise<BoardResponse> {
  return request<BoardResponse>('/api/board');
}

/** タスクを新規作成する（#8 addCard）。status 省略時はサーバが 'breakdown' を採用（§5.3）。 */
export function createTask(body: TaskCreate): Promise<Task> {
  return postJson<Task>('/api/tasks', body);
}

/** タスクを部分更新する（#8 move/markDone）。更新後の Task を返す。不正遷移は 409。 */
export function patchTask(taskId: string, body: TaskPatch): Promise<Task> {
  return patchJson<Task>(`/api/tasks/${taskId}`, body);
}

/** タスクのアクティビティ（コメント）を作成時刻の昇順で取得する（#7）。 */
export function getComments(taskId: string): Promise<Comment[]> {
  return request<Comment[]>(`/api/tasks/${taskId}/comments`);
}

/** コメントを投稿する（#7 コンポーザ）。作成された Comment を返す。 */
export function createComment(taskId: string, body: CommentCreate): Promise<Comment> {
  return postJson<Comment>(`/api/tasks/${taskId}/comments`, body);
}

/** 「AIにまかせる」を起動する（#10 assignAI）。202 {jobId}。不正遷移は 409。 */
export function assignAi(taskId: string): Promise<AssignAiResponse> {
  return request<AssignAiResponse>(`/api/tasks/${taskId}/assign-ai`, {
    method: 'POST',
  });
}

/**
 * オートパイロット（#22 指揮者AI）を起動する。202 {jobId}。
 * ai_work/done・オートノミーL0・コスト上限は 409。以降の進行はすべて SSE が届ける。
 */
export function autopilot(taskId: string): Promise<AssignAiResponse> {
  return request<AssignAiResponse>(`/api/tasks/${taskId}/autopilot`, {
    method: 'POST',
  });
}

/** 壁打ちメッセージ一覧を作成時刻の昇順で取得する（#12）。 */
export function getChatMessages(taskId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/api/tasks/${taskId}/chat`);
}

/**
 * 壁打ちを開始する（#12 §5.3 startChat）。冪等 — chat が空のときだけ
 * AI 初期質問を生成し spec 遷移。更新後のメッセージ一覧を返す。
 */
export function startChat(taskId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/api/tasks/${taskId}/chat/start`, {
    method: 'POST',
  });
}

/**
 * 壁打ちに人メッセージを送信する（#12 §5.3 sendChat step1）。201 で確定版を返す。
 * AI応答＋分解候補は +0.85s 後に SSE（chat.message.created / subtask.proposal）で届く。
 */
export function sendChatMessage(
  taskId: string,
  body: ChatSendRequest,
): Promise<ChatMessage> {
  return postJson<ChatMessage>(`/api/tasks/${taskId}/chat`, body);
}

/**
 * 分解候補をボードに反映する（#12 §1.6 step5 / §5.3 confirmBreakdown）。
 * {parent, children} を返す。breakdown/done 親は 409、空配列は 422。
 */
export function confirmBreakdown(
  taskId: string,
  body: BreakdownConfirmRequest,
): Promise<BreakdownConfirmResponse> {
  return postJson<BreakdownConfirmResponse>(
    `/api/tasks/${taskId}/breakdown/confirm`,
    body,
  );
}

/** 成果物の全版を version 昇順で取得する（#10。末尾が最新）。 */
export function getArtifacts(taskId: string): Promise<ArtifactResponse> {
  return request<ArtifactResponse>(`/api/tasks/${taskId}/artifacts`);
}

/** AIジョブ履歴を createdAt 昇順で取得する（#19 リレー・タイムライン）。 */
export function getJobs(taskId: string): Promise<JobsResponse> {
  return request<JobsResponse>(`/api/tasks/${taskId}/jobs`);
}

/** 人の編集を新版として保存する（#10 §00 #12）。作成された Artifact（201）を返す。 */
export function createArtifact(taskId: string, body: ArtifactCreate): Promise<Artifact> {
  return postJson<Artifact>(`/api/tasks/${taskId}/artifacts`, body);
}

/**
 * 「✧ 学ぶ」でルール候補を生成する（#14 §5.3 learnFrom / §6.4a）。
 * 完了系（you_review/reviewing/done）以外は 409。候補はサーバ非永続 —
 * 採用/却下の判断ごとに adopt / dismiss へ内容を送り返す（subtask.proposal と同型）。
 */
export function getLearnProposals(taskId: string): Promise<RuleProposalDto[]> {
  return request<RuleProposalDto[]>(`/api/tasks/${taskId}/learn`);
}

/**
 * 蒸留候補を採用する（#14 §5.3 adoptLearn / §6.8 基準①）。201 で確定 Rule（K-xx）を返す。
 * カードへのAIコメントは SSE（comment.created / task.updated）で届く。
 */
export function adoptLearn(taskId: string, body: LearnDecisionRequest): Promise<Rule> {
  return postJson<Rule>(`/api/tasks/${taskId}/learn/adopt`, body);
}

/** 蒸留候補を却下する（#14 §5.3 dismissLearn）。feedback 記録のみで 204（body なし）。 */
export async function dismissLearn(
  taskId: string,
  body: LearnDecisionRequest,
): Promise<void> {
  const path = `/api/tasks/${taskId}/learn/dismiss`;
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, `POST ${path} failed with status ${res.status}`);
  }
}

/** 個人ルールをチームへ昇格する（#14 §1.8 promoteRule）。200 で scope=team の Rule（冪等）。 */
export function promoteRule(ruleId: string): Promise<Rule> {
  return request<Rule>(`/api/rules/${ruleId}/promote`, { method: 'POST' });
}
