// サブタスクセクション（§3.3.2d / #12, detail モード）: childIds がある時のみ表示。
// 見出し「サブタスク {done}/{total}」＋子行（担当アバター17px＋タイトル12px＋
// 右にステータスラベル9.5px）。子行クリックで子カードのドロワーへ（§5.3 select）。

import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import { STATUS_META } from '../../types/domain.ts';
import { Avatar } from '../board/Avatar';
import './SubtaskSection.css';

interface SubtaskSectionProps {
  task: Task;
}

export function SubtaskSection({ task }: SubtaskSectionProps) {
  // selector は安定参照（cards 辞書）を返し、派生（§5.1）は render 内で計算する
  const cards = useBoardStore((s) => s.cards);
  const select = useBoardStore((s) => s.select);

  const childIds = task.childIds ?? [];
  if (childIds.length === 0) return null;

  const children = childIds
    .map((id) => cards[id])
    .filter((child) => child !== undefined);
  // done/total は childIds から集計（§3.2 カード側の巻き上げと同じ導出, §5.1）
  const done = children.filter((child) => child.status === 'done').length;

  return (
    <div className="subtask-section">
      <div className="subtask-section__heading">
        サブタスク {done}/{childIds.length}
      </div>
      <div className="subtask-section__list">
        {children.map((child) => (
          <button
            key={child.id}
            type="button"
            className="subtask-section__row"
            onClick={() => select(child.id)}
          >
            <Avatar owner={STATUS_META[child.status].owner} variant="subtask" />
            <span className="subtask-section__title">{child.title}</span>
            <span className="subtask-section__status">
              {STATUS_META[child.status].label}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
