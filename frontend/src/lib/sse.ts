// SSE 購読（§5.4 / #7）: GET /api/events を EventSource で購読し、
// task.updated / comment.created をストアへ適用する。
// ワイヤ形式（backend/app/routers/events.py と鏡写し）:
//   event: <type>
//   data: {"type": <type>, "payload": <DTO の camelCase>}

import { useBoardStore } from '../store/board.ts';
import type { Artifact, Comment, Task } from '../types/domain.ts';

export const EVENTS_URL = '/api/events';

// イベント種別（backend/app/events.py の定数と鏡写し。後続 Wave もここに追加する）
export const TASK_UPDATED = 'task.updated';
export const COMMENT_CREATED = 'comment.created';
export const ARTIFACT_CREATED = 'artifact.created'; // #10（backend/app/repo/artifacts.py）

interface SseEnvelope<T> {
  type: string;
  payload: T;
}

/**
 * /api/events へ接続し、受信イベントをストアへ適用する（App 起動時に一度呼ぶ）。
 * - task.updated → applyTaskUpdated（レーン移動・commentCount 同期を含むカード差し替え）
 * - comment.created → applyCommentCreated（開いているドロワーのスレッドへ追記。id で重複排除）
 * - artifact.created → applyArtifactCreated（成果物の新版を version 昇順で追記。id で重複排除）
 * 切断時の再接続は EventSource が自動で行う。戻り値は切断用のクリーンアップ。
 */
export function connectEvents(): () => void {
  // EventSource が無い環境（jsdom 等）では何もしない（テストは vi.stubGlobal でモックする）
  if (typeof EventSource === 'undefined') return () => {};

  const source = new EventSource(EVENTS_URL);
  // zustand のアクション参照は安定なので接続時に一度だけ取得すればよい
  const { applyTaskUpdated, applyCommentCreated, applyArtifactCreated } =
    useBoardStore.getState();

  source.addEventListener(TASK_UPDATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Task>;
    applyTaskUpdated(payload);
  });
  source.addEventListener(COMMENT_CREATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Comment>;
    applyCommentCreated(payload);
  });
  source.addEventListener(ARTIFACT_CREATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Artifact>;
    applyArtifactCreated(payload);
  });

  return () => source.close();
}
