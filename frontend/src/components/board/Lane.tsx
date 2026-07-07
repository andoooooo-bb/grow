// レーン（§3.2）: 幅272px固定・レーンヘッダ＋カードリスト＋「＋ カードを追加」破線ボタン。
// レーン全体のドロップ受け（onDragOver/onDrop）は #8 で実装。

import type { LaneDto } from '../../types/api.ts';
import { useBoardStore } from '../../store/board.ts';
import { Card } from './Card';
import './Lane.css';

interface LaneProps {
  lane: LaneDto;
}

export function Lane({ lane }: LaneProps) {
  const cards = useBoardStore((s) => s.cards);
  const tasks = lane.cardIds
    .map((id) => cards[id])
    .filter((task) => task !== undefined);

  return (
    <section className="lane">
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
          onClick={() => {
            // TODO(後続Issue): addCard（§5.3）。本Issueでは見た目のみ実装。
          }}
        >
          ＋ カードを追加
        </button>
      </div>
    </section>
  );
}
