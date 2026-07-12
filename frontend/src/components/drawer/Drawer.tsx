// カード詳細ドロワー（§3.3, #7/#10/#12/#14）: 選択時のみ表示。
// detail モード（ヘッダ・アクションバー・適用ルール(b)・学習(c)・成果物(c-2)・
// サブタスク(d)・アクティビティスレッド・コンポーザ）と chat モード（§3.3.3 壁打ち）を
// panelMode で切替。

import { useEffect } from 'react';
import { useBoardStore } from '../../store/board.ts';
import { taskAutonomy } from '../../types/domain.ts';
import { ActionBar } from './ActionBar';
import { ActivityThread } from './ActivityThread';
import { AgentTimeline } from './AgentTimeline';
import { AppliedRules } from './AppliedRules';
import { ChatMode } from './ChatMode';
import { Composer } from './Composer';
import { DrawerHeader } from './DrawerHeader';
import { LearnSection } from './LearnSection';
import { SubtaskSection } from './SubtaskSection';
import { TraceSection } from './TraceSection';
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
  const autopilot = useBoardStore((s) => s.autopilot);
  const reject = useBoardStore((s) => s.reject);
  const startChat = useBoardStore((s) => s.startChat);
  const assigning = useBoardStore((s) =>
    s.selectedId !== null ? (s.assigning[s.selectedId] ?? false) : false,
  );
  const rejecting = useBoardStore((s) =>
    s.selectedId !== null ? (s.rejecting[s.selectedId] ?? false) : false,
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
  // 「オートパイロット」（#22 指揮者AI）: assign-ai と同条件 ＋ L0（計画のみ #21）は無効
  const isPlanOnly = taskAutonomy(task) === 'L0';
  const canAutopilot = canAssignAi && !isPlanOnly;
  // 「差し戻す」（#23）: 人のレビュー局面（you_review / reviewing）でのみ表示
  const canReject = task.status === 'you_review' || task.status === 'reviewing';

  return (
    <aside className="drawer">
      <DrawerHeader task={task} />
      {panelMode === 'chat' ? (
        // chat モード（§3.3.3, #12）: ヘッダ帯＋チャット領域＋コンポーザのみ
        <ChatMode task={task} />
      ) : (
        <div className="drawer__detail">
          {/* アクションバーは上部にピン留め（長いスレッドでもレビュー操作＝差し戻す/
              完了にする が常に見える）。markDone 結線（#8）・startChat 結線（#12）。
              done カードでは「完了にする」を出さない（§03） */}
          <ActionBar
            onAssignAi={() => void assignAi(task.id)}
            assignAiDisabled={!canAssignAi}
            onAutopilot={() => void autopilot(task.id)}
            autopilotDisabled={!canAutopilot}
            autopilotTitle={isPlanOnly ? 'L0では提案のみです' : undefined}
            onStartChat={() => void startChat(task.id)}
            onMarkDone={
              task.status !== 'done' ? () => void markDone(task.id) : undefined
            }
            onReject={
              canReject ? (reason) => void reject(task.id, reason) : undefined
            }
            rejectDisabled={rejecting}
          />
          {/* 中央のみスクロール。ヘッダ・アクションバー・コンポーザは固定して
              「レビューできない/コメント欄が見つからない」を防ぐ（§3.3.2） */}
          <div className="drawer__scroll">
            {/* (b) 適用ルール（§3.3.2b, #10）: retrieval 0件時はコンポーネント側で非表示 */}
            <AppliedRules task={task} />
            {/* (c) 学習（§3.3.2c, #14）: 完了系（you_review/reviewing/done）以外は非表示 */}
            <LearnSection task={task} />
            {/* (d) サブタスク（§3.3.2d, #12）: childIds が無ければコンポーネント側で非表示 */}
            <SubtaskSection task={task} />
            {/* (d-2) リレー・タイムライン（#19）: ジョブ0件時はコンポーネント側で非表示 */}
            <AgentTimeline task={task} />
            {/* (d-3) 意思決定トレース（#25）: 版0件時はコンポーネント側で非表示（既定は閉） */}
            <TraceSection task={task} />
            {/* アクティビティ＝会話。成果物の各版もこの中にメッセージとして時系列で
                差し込まれ、最新版（レビュー対象）が会話の最後に出る（#10/#20/#24 統合） */}
            <ActivityThread task={task} canAssignAi={canAssignAi} />
          </div>
          {/* コンポーザは最下部にピン留め（常に入力できる, §3.3.2f） */}
          <Composer taskId={task.id} />
        </div>
      )}
    </aside>
  );
}
