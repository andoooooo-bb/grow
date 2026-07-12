// アクティビティスレッド（§3.3.2e）: 見出し「アクティビティ」＋コメント行（msgin）。
// アバターは ai=ティール"AI" / human=アンバー"YK"（24×24, variant="thread"）、
// 名義は「Grow | あなた」。AIコメントに agentRole があれば役割ミニバッジを
// 名義の横に添える（#19。無指定は従来通り「Grow」のみ）。
// 送信/読込失敗時は簡易エラーを表示（§5.4）。

import { useBoardStore } from '../../store/board.ts';
import { AgentBadge } from '../board/AgentBadge';
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
            <div className="activity__meta">
              <span className="activity__name">
                {comment.author === 'ai' ? 'Grow' : 'あなた'}
              </span>
              {comment.author === 'ai' && comment.agentRole != null && (
                <AgentBadge role={comment.agentRole} />
              )}
            </div>
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
