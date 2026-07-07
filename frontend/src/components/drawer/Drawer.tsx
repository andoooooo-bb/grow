// カード詳細ドロワー（§3.3, #7/#10）: 選択時のみ表示。detail モード（ヘッダ・アクションバー・
// 適用ルール(b)・成果物(c-2)・アクティビティスレッド・コンポーザ）を実装。
// chat モード（§3.3.3）は #12、学習(c)・サブタスク(d)の各セクションは後続 Issue（#13/#14）。

import { useEffect } from 'react';
import { useBoardStore } from '../../store/board.ts';
import { ActionBar } from './ActionBar';
import { ActivityThread } from './ActivityThread';
import { AppliedRules } from './AppliedRules';
import { ArtifactSection } from './ArtifactSection';
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
  const loadArtifacts = useBoardStore((s) => s.loadArtifacts);
  const markDone = useBoardStore((s) => s.markDone);
  const assignAi = useBoardStore((s) => s.assignAi);
  const assigning = useBoardStore((s) =>
    s.selectedId !== null ? (s.assigning[s.selectedId] ?? false) : false,
  );

  // ドロワーを開いたらスレッド（#7）と成果物（#10）を読み込む
  useEffect(() => {
    if (selectedId !== null) {
      void loadComments(selectedId);
      void loadArtifacts(selectedId);
    }
  }, [selectedId, loadComments, loadArtifacts]);

  if (task === undefined) return null;

  // 「AIにまかせる」の活性（#10。Grow.dc.html の挙動に合わせる）:
  // ai_work（すでにAI作業中）と done は無効。送信中（assigning）も無効。
  // それ以外の不正遷移はサーバが 409 で拒否し boardError に出る（§5.4）
  const canAssignAi =
    task.status !== 'ai_work' && task.status !== 'done' && !assigning;

  return (
    <aside className="drawer">
      <DrawerHeader task={task} />
      {panelMode === 'detail' && (
        <div className="drawer__detail">
          {/* markDone 結線（#8）。done カードでは「完了にする」を出さない（§03） */}
          <ActionBar
            onAssignAi={() => void assignAi(task.id)}
            assignAiDisabled={!canAssignAi}
            onMarkDone={
              task.status !== 'done' ? () => void markDone(task.id) : undefined
            }
          />
          {/* (b) 適用ルール（§3.3.2b, #10）: retrieval 0件時はコンポーネント側で非表示 */}
          <AppliedRules task={task} />
          {/* (c-2) 成果物（§3.3.2 c-2, #10）: 学習(c)/サブタスク(d)実装後もこの間に置く */}
          <ArtifactSection task={task} canAssignAi={canAssignAi} />
          <ActivityThread taskId={task.id} />
          <Composer taskId={task.id} />
        </div>
      )}
    </aside>
  );
}
