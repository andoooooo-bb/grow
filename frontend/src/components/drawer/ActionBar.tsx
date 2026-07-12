// アクションバー（§3.3.2a）: 「AIにまかせる」「AIと壁打ち / 分解」「完了にする」の器。
// 結線: onMarkDone → #8 markDone / onAssignAi → #10 assignAI（結線済み）。残りは後続 Issue:
//   onStartChat → #12 startChat
// が Drawer.tsx からコールバックを渡す（未指定時は no-op）。
// #23: onReject（差し戻す）は you_review / reviewing のときのみ Drawer が渡す。
// 押すと小さな理由入力が展開し、理由必須で送信する（構造化差し戻し）。

import { useState } from 'react';
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
  /**
   * #23 結線済み: reject（構造化差し戻し）。未指定（you_review / reviewing 以外）は
   * ボタンを出さない。理由テキストを引数に受け取る
   */
  onReject?: (reason: string) => void;
  /** #23: reject 送信中の無効化 */
  rejectDisabled?: boolean;
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
  onReject,
  rejectDisabled = false,
}: ActionBarProps) {
  // #23 差し戻し理由の入力フォーム（「差し戻す」で展開・送信/キャンセルで畳む）
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  const submitReject = () => {
    const reason = rejectReason.trim();
    if (reason === '' || onReject === undefined) return;
    onReject(reason);
    setRejectOpen(false);
    setRejectReason('');
  };

  return (
    <div className="action-bar">
      <div className="action-bar__buttons">
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
        {onReject !== undefined && (
          <button
            type="button"
            className="action-bar__button action-bar__button--reject"
            disabled={rejectDisabled}
            onClick={() => setRejectOpen((open) => !open)}
          >
            差し戻す
          </button>
        )}
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
      {onReject !== undefined && rejectOpen && (
        <div className="action-bar__reject-form">
          <textarea
            className="action-bar__reject-input"
            rows={2}
            placeholder="差し戻し理由（必須）: 何をどう直してほしいかを書くと、AIが最優先で対処します"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            aria-label="差し戻し理由"
          />
          <div className="action-bar__reject-actions">
            <button
              type="button"
              className="action-bar__button action-bar__button--reject-submit"
              disabled={rejectDisabled || rejectReason.trim() === ''}
              onClick={submitReject}
            >
              理由を付けて差し戻す
            </button>
            <button
              type="button"
              className="action-bar__button action-bar__button--reject-cancel"
              onClick={() => setRejectOpen(false)}
            >
              キャンセル
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
