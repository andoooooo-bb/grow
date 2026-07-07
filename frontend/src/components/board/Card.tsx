// カード（§3.2）。owner/tone/label は STATUS_META から導出（§5.1）。
// クリックで select(id) → 詳細ドロワーが開く（#7）。
// DnD（#8）: useDraggable(id=taskId)。ドラッグ活性化後のクリックは dnd-kit が
// capture で抑止するため、select と競合しない（活性化は Board の distance 制約）。

import { useDraggable } from '@dnd-kit/core';
import type { Task } from '../../types/domain.ts';
import { STATUS_META } from '../../types/domain.ts';
import { useBoardStore } from '../../store/board.ts';
import { Avatar } from './Avatar';
import { StatusBadge } from './StatusBadge';
import './Card.css';

/** 親タグは親タイトル先頭12字＋…（§3.2） */
const PARENT_TAG_MAX = 12;

function truncateParentTitle(title: string): string {
  return title.length > PARENT_TAG_MAX ? `${title.slice(0, PARENT_TAG_MAX)}…` : title;
}

interface CardProps {
  task: Task;
}

export function Card({ task }: CardProps) {
  const select = useBoardStore((s) => s.select);
  const parentTitle = useBoardStore((s) =>
    task.parentId ? s.cards[task.parentId]?.title : undefined,
  );
  // サブタスク進捗 = childIds のうち done の数 / 総数（§5.1）
  const childDoneCount = useBoardStore(
    (s) => (task.childIds ?? []).filter((id) => s.cards[id]?.status === 'done').length,
  );

  const meta = STATUS_META[task.status];
  // 左色バー（§3.2）: done は owner に関わらず緑。未完了は owner で色分け。
  const barTone = task.status === 'done' ? 'done' : meta.owner;
  const childTotal = task.childIds?.length ?? 0;

  // DnD（#8）: id=taskId が onDragEnd の active.id になる。
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({ id: task.id });

  return (
    <div
      ref={setNodeRef}
      className={`card${isDragging ? ' card--dragging' : ''}`}
      style={
        transform !== null
          ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
          : undefined
      }
      onClick={() => select(task.id)}
      {...listeners}
      {...attributes}
    >
      <div className={`card__bar card__bar--${barTone}`} aria-hidden="true" />

      <div className="card__top">
        <div className="card__who">
          <Avatar owner={meta.owner} />
          <span className="card__id">{task.id}</span>
        </div>
        <span className="card__comments">
          <span className="card__comments-dot" aria-hidden="true" />
          {task.commentCount}
        </span>
      </div>

      <div className="card__title">{task.title}</div>

      {task.parentId != null && parentTitle !== undefined && (
        <span className="card__parent">親: {truncateParentTitle(parentTitle)}</span>
      )}

      <div>
        <StatusBadge status={task.status} />
      </div>

      {typeof task.progress === 'number' && (
        <div className="card__meter">
          <div className="card__meter-meta">
            <span>progress</span>
            <span>{task.progress}%</span>
          </div>
          <div className="card__meter-track">
            <div className="card__meter-fill" style={{ width: `${task.progress}%` }} />
          </div>
        </div>
      )}

      {childTotal > 0 && (
        <div className="card__meter">
          <div className="card__meter-meta">
            <span>サブタスク</span>
            <span>
              {childDoneCount}/{childTotal}
            </span>
          </div>
          <div className="card__meter-track">
            <div
              className="card__meter-fill"
              style={{ width: `${(childDoneCount / childTotal) * 100}%` }}
            />
          </div>
        </div>
      )}

      {task.labels.length > 0 && (
        <div className="card__labels">
          {task.labels.map((label) => (
            <span key={label} className="card__label">
              {label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
