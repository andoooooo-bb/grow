// トップバー（§3.1）: ロゴ・ワークスペース表示と、派生カウンタ（§5.1）のピル群。
// youCount = owner human かつ status≠done / aiCount = ai_work or queued / ruleCount = rules 総数
// 「AI稼働」ピルは #19 で button 化し、AI活動ライブフィード（AgentFeed）のトグルになった。

import {
  deriveRuleCount,
  deriveYouCount,
  useBoardStore,
} from '../../store/board.ts';
import { AgentFeed } from './AgentFeed';
import './TopBar.css';

export function TopBar() {
  const youCount = useBoardStore((s) => deriveYouCount(s.cards));
  const ruleCount = useBoardStore((s) => deriveRuleCount(s.rules));
  const openKnowledge = useBoardStore((s) => s.openKnowledge);
  // #20: ルール適用の瞬間に「◈ ナレッジ」を明滅させる（最新の適用時刻で one-shot 再生）
  const justApplied = useBoardStore((s) => s.justApplied);
  const stamps = Object.values(justApplied);
  const flashStamp = stamps.length > 0 ? Math.max(...stamps) : undefined;

  return (
    <header className="topbar">
      <div className="topbar__left">
        <div className="topbar__logo" aria-hidden="true">
          G
        </div>
        <span className="topbar__brand">Grow</span>
        <span className="topbar__divider" aria-hidden="true" />
        <span className="topbar__workspace">workspace / 個人</span>
      </div>
      <div className="topbar__right">
        <span className="topbar__pill topbar__pill--you">
          <span className="topbar__pill-dot topbar__pill-dot--you" aria-hidden="true" />
          あなたの番 {youCount}
        </span>
        {/* #19: 「AI稼働 N」ピル＋ライブフィードのドロップダウン */}
        <AgentFeed />
        <button
          // #20: 適用時刻を key に使い、適用のたび明滅アニメを再マウントで再生する
          key={flashStamp ?? 'static'}
          type="button"
          className={`topbar__knowledge${
            flashStamp === undefined ? '' : ' topbar__knowledge--flash'
          }`}
          onClick={openKnowledge}
        >
          ◈ ナレッジ {ruleCount}
        </button>
        <span className="topbar__avatar">YK</span>
      </div>
    </header>
  );
}
