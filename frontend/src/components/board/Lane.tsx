// レーン（§3.2）: 幅272px固定・レーンヘッダ＋カードリスト＋「＋ カードを追加」破線ボタン。
// レーン全体がドロップ先（§3.2 / #8: useDroppable(id=laneKey)）。
// 「＋ カードを追加」は addCard（§5.3, #8）: breakdown カード生成＋AI初期コメント＋ドロワー。

import { useDroppable } from '@dnd-kit/core';
import type { LaneDto } from '../../types/api.ts';
import { useBoardStore } from '../../store/board.ts';
import { Card } from './Card';
import './Lane.css';

interface LaneProps {
  lane: LaneDto;
}

export function Lane({ lane }: LaneProps) {
  const cards = useBoardStore((s) => s.cards);
  const addCard = useBoardStore((s) => s.addCard);
  const tasks = lane.cardIds
    .map((id) => cards[id])
    .filter((task) => task !== undefined);

  // レーン全体をドロップ先にする（#8）。id=laneKey が onDragEnd の over.id になる。
  const { setNodeRef, isOver } = useDroppable({ id: lane.key });

  return (
    <section
      ref={setNodeRef}
      className={`lane${isOver ? ' lane--over' : ''}`}
    >
      <div className="lane__header">
        <span className="lane__name">{lane.name}</span>
        <span className="lane__count">{tasks.length}</span>
      </div>
      <div className="lane__cards">
        {tasks.map((task) => (
          <Card key={task.id} task={task} />
        ))}
        <button
          type="button"
          className="lane__add"
          onClick={() => void addCard(lane.key)}
        >
          ＋ カードを追加
        </button>
      </div>
    </section>
  );
}
