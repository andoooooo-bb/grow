// ボード（§3.2）: 横スクロール領域に5レーンを gap14px で横並び。
// DnD（#8）: DndContext を導入し、カードのドロップで move(taskId, toLaneKey)。
// PointerSensor に distance 制約を付け、単純クリック（select）とドラッグを両立する。

import { useState } from 'react';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import type { LaneKey } from '../../types/domain.ts';
import { useBoardStore } from '../../store/board.ts';
import { Lane } from './Lane';
import { CardView } from './CardView';
import './Board.css';

/**
 * onDragEnd（#8）: レーン上でドロップされたら move(taskId, toLaneKey) を呼ぶ。
 * レーン外へのドロップ（over=null）は no-op。同一レーンの no-op は move 側が判定する。
 */
export function handleDragEnd(
  event: DragEndEvent,
  move: (taskId: string, toLaneKey: LaneKey) => Promise<void>,
): void {
  const { active, over } = event;
  if (over === null) return;
  void move(String(active.id), over.id as LaneKey);
}

export function Board() {
  const lanes = useBoardStore((s) => s.lanes);
  const cards = useBoardStore((s) => s.cards);
  const move = useBoardStore((s) => s.move);
  const boardError = useBoardStore((s) => s.boardError);
  // ドラッグ中のカード id（DragOverlay で最前面に浮遊コピーを描画する）
  const [activeId, setActiveId] = useState<string | null>(null);
  // distance 制約: 6px 動くまでドラッグを開始しない（クリック=ドロワーを開くを妨げない）
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const activeTask = activeId !== null ? cards[activeId] : undefined;

  return (
    <div className="board">
      <DndContext
        sensors={sensors}
        onDragStart={(e: DragStartEvent) => setActiveId(String(e.active.id))}
        onDragEnd={(e) => {
          setActiveId(null);
          handleDragEnd(e, move);
        }}
        onDragCancel={() => setActiveId(null)}
      >
        <div className="board__lanes">
          {lanes.map((lane) => (
            <Lane key={lane.key} lane={lane} />
          ))}
        </div>
        {/* ドラッグ中のカードはポータルで最前面に浮遊描画 → レーンの overflow に
            切り取られない（#8 clipping バグ修正） */}
        <DragOverlay dropAnimation={null}>
          {activeTask !== undefined ? (
            <CardView task={activeTask} className="card--overlay" />
          ) : null}
        </DragOverlay>
      </DndContext>
      {boardError !== null && (
        <div className="board__error" role="alert">
          {boardError}
        </div>
      )}
    </div>
  );
}
