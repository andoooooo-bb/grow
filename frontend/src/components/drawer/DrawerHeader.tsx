// ドロワーヘッダ（§3.3.1 共通）: 「{ID} · {レーン名}」・閉じる✕・タイトル・
// ステータスバッジ(lg)＋「担当: {Grow (AI) | あなた}」（owner は STATUS_META から導出）。

import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import { STATUS_META } from '../../types/domain.ts';
import { StatusBadge } from '../board/StatusBadge';
import './DrawerHeader.css';

interface DrawerHeaderProps {
  task: Task;
}

export function DrawerHeader({ task }: DrawerHeaderProps) {
  const laneName = useBoardStore(
    (s) => s.lanes.find((lane) => lane.key === task.laneKey)?.name ?? task.laneKey,
  );
  const closePanel = useBoardStore((s) => s.closePanel);
  const owner = STATUS_META[task.status].owner;

  return (
    <div className="drawer-header">
      <div className="drawer-header__top">
        <span className="drawer-header__meta">
          {task.id} · {laneName}
        </span>
        <button
          type="button"
          className="drawer-header__close"
          aria-label="閉じる"
          onClick={closePanel}
        >
          ✕
        </button>
      </div>
      <div className="drawer-header__title">{task.title}</div>
      <div className="drawer-header__status">
        <StatusBadge status={task.status} size="lg" />
        <span className="drawer-header__owner">
          担当: {owner === 'ai' ? 'Grow (AI)' : 'あなた'}
        </span>
      </div>
    </div>
  );
}
