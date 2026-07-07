// アクティビティスレッド（§3.3.2e）: 見出し「アクティビティ」＋コメント行（msgin）。
// アバターは ai=ティール"AI" / human=アンバー"YK"（24×24, variant="thread"）、
// 名義は「Grow | あなた」。送信/読込失敗時は簡易エラーを表示（§5.4）。

import { useBoardStore } from '../../store/board.ts';
import { Avatar } from '../board/Avatar';
import './ActivityThread.css';

interface ActivityThreadProps {
  taskId: string;
}

export function ActivityThread({ taskId }: ActivityThreadProps) {
  const comments = useBoardStore((s) => s.comments[taskId]);
  const error = useBoardStore((s) => s.commentError[taskId]);

  return (
    <div className="activity">
      <div className="activity__heading">アクティビティ</div>
      {(comments ?? []).map((comment) => (
        <div key={comment.id} className="activity__item">
          <Avatar owner={comment.author} variant="thread" />
          <div className="activity__body">
            <span className="activity__name">
              {comment.author === 'ai' ? 'Grow' : 'あなた'}
            </span>
            <div className="activity__bubble">{comment.text}</div>
          </div>
        </div>
      ))}
      {error != null && (
        <div className="activity__error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
