// カードの見た目（§3.2）。dnd 非依存の presentational コンポーネント。
// Card（draggable ラッパ）と Board の DragOverlay（ドラッグ中の浮遊コピー）の
// 両方から使う。ドラッグ中もレーンの overflow に切り取られないよう、浮遊表示は
// DragOverlay 側がポータルで最前面に描画する（#8 の clipping バグ修正）。

import { forwardRef, type HTMLAttributes } from 'react';
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

interface CardViewProps extends HTMLAttributes<HTMLDivElement> {
  task: Task;
}

export const CardView = forwardRef<HTMLDivElement, CardViewProps>(
  ({ task, className, ...rest }, ref) => {
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

    return (
      <div ref={ref} className={`card${className ? ` ${className}` : ''}`} {...rest}>
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
  },
);

CardView.displayName = 'CardView';
