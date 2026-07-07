// fetch ラッパ。API コントラクトは types/api.ts（backend/app/domain/dto.py と鏡写し）。

import type {
  BoardResponse,
  CommentCreate,
  TaskCreate,
  TaskPatch,
} from '../types/api.ts';
import type { Comment, Task } from '../types/domain.ts';

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
