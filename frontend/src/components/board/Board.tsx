// ボード（§3.2）: 横スクロール領域に5レーンを gap14px で横並び。
// DnD（#8）: DndContext を導入し、カードのドロップで move(taskId, toLaneKey)。
// PointerSensor に distance 制約を付け、単純クリック（select）とドラッグを両立する。

import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import type { LaneKey } from '../../types/domain.ts';
import { useBoardStore } from '../../store/board.ts';
import { Lane } from './Lane';
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
  const move = useBoardStore((s) => s.move);
  const boardError = useBoardStore((s) => s.boardError);
  // distance 制約: 6px 動くまでドラッグを開始しない（クリック=ドロワーを開くを妨げない）
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  return (
    <div className="board">
      <DndContext sensors={sensors} onDragEnd={(e) => handleDragEnd(e, move)}>
        <div className="board__lanes">
          {lanes.map((lane) => (
            <Lane key={lane.key} lane={lane} />
          ))}
        </div>
      </DndContext>
      {boardError !== null && (
        <div className="board__error" role="alert">
          {boardError}
        </div>
      )}
    </div>
  );
}
