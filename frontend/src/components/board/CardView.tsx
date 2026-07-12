// カードの見た目（§3.2）。dnd 非依存の presentational コンポーネント。
// Card（draggable ラッパ）と Board の DragOverlay（ドラッグ中の浮遊コピー）の
// 両方から使う。ドラッグ中もレーンの overflow に切り取られないよう、浮遊表示は
// DragOverlay 側がポータルで最前面に描画する（#8 の clipping バグ修正）。

import { forwardRef, type HTMLAttributes } from 'react';
import { relevantRules } from '../../lib/retrieval.ts';
import type { Task } from '../../types/domain.ts';
import {
  AUTONOMY_META,
  DEFAULT_AUTONOMY,
  STATUS_META,
  taskAutonomy,
} from '../../types/domain.ts';
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
    // #20: このカードに適用予定のルール数（◈N ミニチップ。0件は非表示）。
    // FE ミラー retrieval（BE と同一ロジック）で決定的に算出でき API 不要
    const ruleCount = useBoardStore((s) => relevantRules(s.rules, task).length);

    const meta = STATUS_META[task.status];
    // 左色バー（§3.2）: done は owner に関わらず緑。未完了は owner で色分け。
    const barTone = task.status === 'done' ? 'done' : meta.owner;
    const childTotal = task.childIds?.length ?? 0;
    // #21: オートノミーのミニバッジ。既定 L1 は表示しない（L0/L2/L3 のみ）
    const autonomy = taskAutonomy(task);

    return (
      <div ref={ref} className={`card${className ? ` ${className}` : ''}`} {...rest}>
        <div className={`card__bar card__bar--${barTone}`} aria-hidden="true" />

        <div className="card__top">
          <div className="card__who">
            <Avatar owner={meta.owner} />
            <span className="card__id">{task.id}</span>
          </div>
          <span className="card__meta-right">
            {/* #21: オートノミーのミニバッジ（L1=既定は非表示） */}
            {autonomy !== DEFAULT_AUTONOMY && (
              <span
                className={`card__autonomy card__autonomy--${autonomy.toLowerCase()}`}
                title={`${AUTONOMY_META[autonomy].label} — ${AUTONOMY_META[autonomy].description}`}
              >
                {autonomy}
              </span>
            )}
            {/* #20: 適用予定ルール数の ◈N ミニチップ（0件は非表示） */}
            {ruleCount > 0 && (
              <span className="card__rules" title={`適用予定のルール ${ruleCount}件`}>
                ◈{ruleCount}
              </span>
            )}
            <span className="card__comments">
              <span className="card__comments-dot" aria-hidden="true" />
              {task.commentCount}
            </span>
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
