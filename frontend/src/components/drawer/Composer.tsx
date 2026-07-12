// コンポーザ（§3.3.2f / §3.3.3）: Enter=送信 / Shift+Enter=改行（§00 #11）。
// IME 変換確定の Enter では送信しない。
// variant='comment'（既定）: placeholder「コメントで依頼・指示を残す…」＋「送信」（ダーク）。
//   送信は store.postComment（§5.4 楽観的更新: 即UI反映 → API → 失敗ならロールバック）。
// variant='chat'（#12 壁打ち）: placeholder「前提や要望を伝える…」＋「送信」（パープル #5d4eb0）。
//   送信は store.sendChat。入力は chatDrafts（detail のコンポーザとは別領域）。

import type { KeyboardEvent } from 'react';
import { useBoardStore } from '../../store/board.ts';
import './Composer.css';

/** 入力欄の表示行数の上限（改行数に応じて 1〜4 行まで広がる） */
const MAX_ROWS = 4;

interface ComposerProps {
  taskId: string;
  variant?: 'comment' | 'chat';
}

export function Composer({ taskId, variant = 'comment' }: ComposerProps) {
  const isChat = variant === 'chat';
  const draft = useBoardStore((s) =>
    isChat ? (s.chatDrafts[taskId] ?? '') : (s.drafts[taskId] ?? ''),
  );
  const setDraft = useBoardStore((s) => (isChat ? s.setChatDraft : s.setDraft));
  const post = useBoardStore((s) => (isChat ? s.sendChat : s.postComment));

  const submit = () => void post(taskId, draft);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter=送信 / Shift+Enter=改行（§00 #11）。IME 変換確定の Enter は無視
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer">
      <textarea
        className="composer__input"
        rows={Math.min(MAX_ROWS, draft.split('\n').length)}
        value={draft}
        placeholder={isChat ? '前提や要望を伝える…' : 'コメントで依頼・指示を残す…'}
        onChange={(e) => setDraft(taskId, e.target.value)}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        className={`composer__send${isChat ? ' composer__send--chat' : ''}`}
        onClick={submit}
      >
        送信
      </button>
    </div>
  );
}
