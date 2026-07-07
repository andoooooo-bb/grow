// アクションバー（§3.3.2a）: 「AIにまかせる」「AIと壁打ち / 分解」「完了にする」の器。
// 本 Issue（#7）はスタイルとハンドラの受け口のみ。結線は後続 Issue:
//   onAssignAi → #10 assignAI / onStartChat → #12 startChat / onMarkDone → #8 markDone
// が Drawer.tsx からコールバックを渡す（未指定時は no-op）。

import './ActionBar.css';

interface ActionBarProps {
  /** #10 が結線: assignAI（§5.3） */
  onAssignAi?: () => void;
  /** #12 が結線: startChat（§5.3） */
  onStartChat?: () => void;
  /** #8 が結線: markDone（§5.3） */
  onMarkDone?: () => void;
}

const noop = () => {};

export function ActionBar({
  onAssignAi = noop,
  onStartChat = noop,
  onMarkDone = noop,
}: ActionBarProps) {
  return (
    <div className="action-bar">
      <button
        type="button"
        className="action-bar__button action-bar__button--assign"
        onClick={onAssignAi}
      >
        <span className="action-bar__assign-dot" aria-hidden="true" />
        AIにまかせる
      </button>
      <button
        type="button"
        className="action-bar__button action-bar__button--chat"
        onClick={onStartChat}
      >
        AIと壁打ち / 分解
      </button>
      <button
        type="button"
        className="action-bar__button action-bar__button--done"
        onClick={onMarkDone}
      >
        完了にする
      </button>
    </div>
  );
}
