// ボード（§3.2）: 横スクロール領域に5レーンを gap14px で横並び。

import { useBoardStore } from '../../store/board.ts';
import { Lane } from './Lane';
import './Board.css';

export function Board() {
  const lanes = useBoardStore((s) => s.lanes);

  return (
    <div className="board">
      <div className="board__lanes">
        {lanes.map((lane) => (
          <Lane key={lane.key} lane={lane} />
        ))}
      </div>
    </div>
  );
}
