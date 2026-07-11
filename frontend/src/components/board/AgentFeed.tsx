// AI活動ライブフィード（#19）: トップバーの「AI稼働」ピル（button化）から開く
// ドロップダウンパネル。store.activity（新しい順・上限100）を全カード横断で表示し、
// 行クリックで select(taskId) して該当カードのドロワーへジャンプ → 閉じる。
// 空状態は「AIの活動はまだありません」。

import { useState } from 'react';
import { deriveAiCount, useBoardStore } from '../../store/board.ts';
import { AgentBadge } from './AgentBadge';
import './AgentFeed.css';

function formatTime(at: number): string {
  const d = new Date(at);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

export function AgentFeed() {
  const [open, setOpen] = useState(false);
  const aiCount = useBoardStore((s) => deriveAiCount(s.cards));
  const activity = useBoardStore((s) => s.activity);
  const cards = useBoardStore((s) => s.cards);
  const select = useBoardStore((s) => s.select);

  return (
    <div className="agent-feed">
      {/* 既存の topbar__pill--ai の見た目を保った button（#19） */}
      <button
        type="button"
        className="topbar__pill topbar__pill--ai agent-feed__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="topbar__pill-dot topbar__pill-dot--ai" aria-hidden="true" />
        AI稼働 {aiCount}
      </button>
      {open && (
        <div className="agent-feed__panel">
          <div className="agent-feed__heading">AIの活動</div>
          {activity.length === 0 ? (
            <div className="agent-feed__empty">AIの活動はまだありません</div>
          ) : (
            <div className="agent-feed__list">
              {activity.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  className="agent-feed__row"
                  title={entry.taskTitle}
                  onClick={() => {
                    // 行クリックで該当カードへジャンプ（存在しないタスクは閉じるだけ）
                    if (cards[entry.taskId] !== undefined) select(entry.taskId);
                    setOpen(false);
                  }}
                >
                  <span className="agent-feed__row-head">
                    <span className="agent-feed__task">{entry.taskId}</span>
                    {entry.role !== undefined && <AgentBadge role={entry.role} />}
                    <span className="agent-feed__time">{formatTime(entry.at)}</span>
                  </span>
                  <span className="agent-feed__text">{entry.text}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
