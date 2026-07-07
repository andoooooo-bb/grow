// ステータス・ステートマシン（§5.6 が正。§7.2 の ai_work→you_todo を補完）
// backend/app/domain/state_machine.py と鏡写し。正準フィクスチャは shared/contracts/transitions.json。

import type { Task, TaskStatus } from '../types/domain.ts';

export interface Transition {
  from: TaskStatus;
  to: TaskStatus;
}

// 許可遷移テーブル（shared/contracts/transitions.json と一致することをテストで担保）
// - done→任意 は再オープン（管理操作）として全statusへ展開済み
// - 同一statusへの遷移（from==to）は no-op として canTransition が常に許可
export const ALLOWED_TRANSITIONS: readonly Transition[] = [
  { from: 'breakdown', to: 'spec' }, // 壁打ち開始
  { from: 'spec', to: 'ai_work' }, // 分解反映 / AIにまかせる
  { from: 'queued', to: 'ai_work' }, // AIにまかせる/依頼
  { from: 'you_todo', to: 'done' }, // 人が着手・完了
  { from: 'you_todo', to: 'ai_work' }, // AIに委任
  { from: 'ai_work', to: 'you_review' }, // AI完了
  { from: 'ai_work', to: 'you_todo' }, // ジョブ最終失敗時の人戻し（§7.2）
  { from: 'you_review', to: 'reviewing' }, // レビュー開始
  { from: 'you_review', to: 'done' }, // 承認
  { from: 'you_review', to: 'ai_work' }, // 差し戻し
  { from: 'reviewing', to: 'done' }, // 承認
  { from: 'reviewing', to: 'ai_work' }, // 差し戻し
  { from: 'reviewing', to: 'you_todo' }, // 差し戻し
  { from: 'done', to: 'queued' }, // 再オープン（管理操作）
  { from: 'done', to: 'breakdown' },
  { from: 'done', to: 'spec' },
  { from: 'done', to: 'ai_work' },
  { from: 'done', to: 'you_todo' },
  { from: 'done', to: 'you_review' },
  { from: 'done', to: 'reviewing' },
];

const TRANSITION_SET: ReadonlySet<string> = new Set(
  ALLOWED_TRANSITIONS.map((t) => `${t.from}->${t.to}`),
);

/** from→to の遷移が許可されているか（§5.6）。from==to は常に許可（no-op）。 */
export function canTransition(from: TaskStatus, to: TaskStatus): boolean {
  if (from === to) return true;
  return TRANSITION_SET.has(`${from}->${to}`);
}

/** 不変条件: progress は ai_work のときのみ非null（0..100）。それ以外は null/undefined。 */
export function isProgressInvariantSatisfied(
  task: Pick<Task, 'status' | 'progress'>,
): boolean {
  const { status, progress } = task;
  if (progress === undefined || progress === null) return true;
  return status === 'ai_work' && progress >= 0 && progress <= 100;
}

/** 不変条件に違反していれば Error を投げる。 */
export function assertInvariants(task: Pick<Task, 'status' | 'progress'>): void {
  if (!isProgressInvariantSatisfied(task)) {
    throw new Error(
      `Invariant violation: progress=${String(task.progress)} is only allowed ` +
        `in status 'ai_work' with range 0..100 (status=${task.status})`,
    );
  }
}
