// fetch ラッパ。API コントラクトは types/api.ts（backend/app/domain/dto.py と鏡写し）。

import type {
  ArtifactCreate,
  ArtifactResponse,
  AssignAiResponse,
  BoardResponse,
  CommentCreate,
  TaskCreate,
  TaskPatch,
} from '../types/api.ts';
import type { Artifact, Comment, Task } from '../types/domain.ts';

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

/** 成果物の全版を version 昇順で取得する（#10。末尾が最新）。 */
export function getArtifacts(taskId: string): Promise<ArtifactResponse> {
  return request<ArtifactResponse>(`/api/tasks/${taskId}/artifacts`);
}

/** 人の編集を新版として保存する（#10 §00 #12）。作成された Artifact（201）を返す。 */
export function createArtifact(taskId: string, body: ArtifactCreate): Promise<Artifact> {
  return postJson<Artifact>(`/api/tasks/${taskId}/artifacts`, body);
}
