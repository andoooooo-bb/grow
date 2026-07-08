// カード（§3.2）draggable ラッパ。見た目は CardView に委譲。
// クリックで select(id) → 詳細ドロワー（#7）。ドラッグ活性化後のクリックは
// dnd-kit が capture で抑止するため select と競合しない（活性化は Board の distance 制約）。
//
// #8 clipping バグ修正: ドラッグ中は transform で自分を動かさず、Board の
// DragOverlay が最前面のポータルに浮遊コピーを描画する。元カードはその場に
// プレースホルダ（減光）として残るだけなので、レーンの overflow に切り取られない。

import { useDraggable } from '@dnd-kit/core';
import type { Task } from '../../types/domain.ts';
import { useBoardStore } from '../../store/board.ts';
import { CardView } from './CardView';

interface CardProps {
  task: Task;
}

export function Card({ task }: CardProps) {
  const select = useBoardStore((s) => s.select);

  // DnD（#8）: id=taskId が onDragEnd の active.id になる。
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: task.id,
  });

  return (
    <CardView
      ref={setNodeRef}
      task={task}
      className={isDragging ? 'card--placeholder' : undefined}
      onClick={() => select(task.id)}
      {...listeners}
      {...attributes}
    />
  );
}
