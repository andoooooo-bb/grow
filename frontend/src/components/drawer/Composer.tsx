// コンポーザ（§3.3.2f）: placeholder「コメントで依頼・指示を残す…」＋「送信」（ダーク）。
// Enter=送信 / Shift+Enter=改行（§00 #11）。IME 変換確定の Enter では送信しない。
// 送信は store.postComment（§5.4 楽観的更新: 即UI反映 → API → 失敗ならロールバック）。

import type { KeyboardEvent } from 'react';
import { useBoardStore } from '../../store/board.ts';
import './Composer.css';

/** 入力欄の表示行数の上限（改行数に応じて 1〜4 行まで広がる） */
const MAX_ROWS = 4;

interface ComposerProps {
  taskId: string;
}

export function Composer({ taskId }: ComposerProps) {
  const draft = useBoardStore((s) => s.drafts[taskId] ?? '');
  const setDraft = useBoardStore((s) => s.setDraft);
  const postComment = useBoardStore((s) => s.postComment);

  const submit = () => void postComment(taskId, draft);

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
        placeholder="コメントで依頼・指示を残す…"
        onChange={(e) => setDraft(taskId, e.target.value)}
        onKeyDown={onKeyDown}
      />
      <button type="button" className="composer__send" onClick={submit}>
        送信
      </button>
    </div>
  );
}
