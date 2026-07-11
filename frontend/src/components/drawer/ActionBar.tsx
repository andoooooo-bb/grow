// アクションバー（§3.3.2a）: 「AIにまかせる」「AIと壁打ち / 分解」「完了にする」の器。
// 結線: onMarkDone → #8 markDone / onAssignAi → #10 assignAI（結線済み）。残りは後続 Issue:
//   onStartChat → #12 startChat
// が Drawer.tsx からコールバックを渡す（未指定時は no-op）。

import './ActionBar.css';

interface ActionBarProps {
  /** #10 結線済み: assignAI（§5.3） */
  onAssignAi?: () => void;
  /**
   * #10: 「AIにまかせる」の無効化。status が ai_work / done のとき、
   * および assign-ai 送信中は true（Grow.dc.html の活性制御に合わせる）
   */
  assignAiDisabled?: boolean;
  /** #22 結線済み: autopilot（指揮者AI）。assign-ai と同条件＋L0 で無効 */
  onAutopilot?: () => void;
  autopilotDisabled?: boolean;
  /** 無効理由のツールチップ（例: L0 のとき「L0では提案のみです」） */
  autopilotTitle?: string;
  /** #12 が結線: startChat（§5.3） */
  onStartChat?: () => void;
  /** #8 結線済み: markDone（§5.3）。未指定（done カード）はボタンを出さない（§03） */
  onMarkDone?: () => void;
}

const noop = () => {};

export function ActionBar({
  onAssignAi = noop,
  assignAiDisabled = false,
  onAutopilot = noop,
  autopilotDisabled = false,
  autopilotTitle,
  onStartChat = noop,
  onMarkDone,
}: ActionBarProps) {
  return (
    <div className="action-bar">
      <button
        type="button"
        className="action-bar__button action-bar__button--assign"
        disabled={assignAiDisabled}
        onClick={onAssignAi}
      >
        <span className="action-bar__assign-dot" aria-hidden="true" />
        AIにまかせる
      </button>
      <button
        type="button"
        className="action-bar__button action-bar__button--autopilot"
        disabled={autopilotDisabled}
        onClick={onAutopilot}
        title={autopilotTitle}
      >
        <span className="action-bar__autopilot-dot" aria-hidden="true" />
        オートパイロット
      </button>
      <button
        type="button"
        className="action-bar__button action-bar__button--chat"
        onClick={onStartChat}
      >
        AIと壁打ち / 分解
      </button>
      {onMarkDone !== undefined && (
        <button
          type="button"
          className="action-bar__button action-bar__button--done"
          onClick={onMarkDone}
        >
          完了にする
        </button>
      )}
    </div>
  );
}
