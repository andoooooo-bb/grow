// カード詳細ドロワー（§3.3, #7）: 選択時のみ表示。detail モード（ヘッダ・アクションバー・
// アクティビティスレッド・コンポーザ）を実装。chat モード（§3.3.3）は #12 で実装。
// 適用ルール(b)・学習(c)・成果物(c-2)・サブタスク(d)の各セクションは後続 Issue（#10/#13/#14）。

import { useEffect } from 'react';
import { useBoardStore } from '../../store/board.ts';
import { ActionBar } from './ActionBar';
import { ActivityThread } from './ActivityThread';
import { Composer } from './Composer';
import { DrawerHeader } from './DrawerHeader';
import './Drawer.css';

export function Drawer() {
  const selectedId = useBoardStore((s) => s.selectedId);
  const task = useBoardStore((s) =>
    s.selectedId !== null ? s.cards[s.selectedId] : undefined,
  );
  const panelMode = useBoardStore((s) => s.panelMode);
  const loadComments = useBoardStore((s) => s.loadComments);
  const markDone = useBoardStore((s) => s.markDone);

  // ドロワーを開いたら GET /tasks/:id/comments でスレッドを読み込む（#7）
  useEffect(() => {
    if (selectedId !== null) void loadComments(selectedId);
  }, [selectedId, loadComments]);

  if (task === undefined) return null;

  return (
    <aside className="drawer">
      <DrawerHeader task={task} />
      {panelMode === 'detail' && (
        <div className="drawer__detail">
          {/* markDone 結線（#8）。done カードでは「完了にする」を出さない（§03） */}
          <ActionBar
            onMarkDone={
              task.status !== 'done' ? () => void markDone(task.id) : undefined
            }
          />
          <ActivityThread taskId={task.id} />
          <Composer taskId={task.id} />
        </div>
      )}
    </aside>
  );
}
