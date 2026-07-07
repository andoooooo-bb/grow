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
  TaskCreate,
  TaskPatch,
} from '../types/api.ts';
import type { Artifact, ChatMessage, Comment, Task } from '../types/domain.ts';

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

/** 人の編集を新版として保存する（#10 §00 #12）。作成された Artifact（201）を返す。 */
export function createArtifact(taskId: string, body: ArtifactCreate): Promise<Artifact> {
  return postJson<Artifact>(`/api/tasks/${taskId}/artifacts`, body);
}
