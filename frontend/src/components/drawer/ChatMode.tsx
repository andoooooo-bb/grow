// chat モード（§3.3.3 / #12）: パープル系ヘッダ帯「● 壁打ちチャット — 分解の意識合わせ」＋
// 「← 戻る」、メッセージ領域（AIアバターはパープル #5d4eb0 "AI"、バブル背景 #f6f4fc、
// pre-wrap）、分解候補カード（ProposalCard）、コンポーザ（variant='chat'）。

import { useEffect, useRef } from 'react';
import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import { Composer } from './Composer';
import { ProposalCard } from './ProposalCard';
import './ChatMode.css';

interface ChatModeProps {
  task: Task;
}

export function ChatMode({ task }: ChatModeProps) {
  const messages = useBoardStore((s) => s.chat[task.id]);
  const proposal = useBoardStore((s) => s.proposal[task.id]);
  const error = useBoardStore((s) => s.chatError[task.id]);
  const setPanelMode = useBoardStore((s) => s.setPanelMode);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新着メッセージ・候補提示で最下部へ追従（+0.85s 後の AI 応答を見逃さないため）
  useEffect(() => {
    const el = scrollRef.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [messages, proposal]);

  return (
    <div className="chat-mode">
      {/* ヘッダ帯（背景#f1edfb, 下線#e7e0f7）＋「← 戻る」で detail へ（§5.3 backToDetail） */}
      <div className="chat-mode__band">
        <span className="chat-mode__band-title">
          <span className="chat-mode__band-dot" aria-hidden="true" />
          壁打ちチャット — 分解の意識合わせ
        </span>
        <button
          type="button"
          className="chat-mode__back"
          onClick={() => setPanelMode('detail')}
        >
          ← 戻る
        </button>
      </div>

      {/* メッセージ領域（flex:1, scroll, gap14px） */}
      <div ref={scrollRef} className="chat-mode__messages">
        {(messages ?? []).map((message) => (
          <div key={message.id} className="chat-mode__item">
            <span
              className={`chat-mode__avatar chat-mode__avatar--${message.author}`}
              aria-hidden="true"
            >
              {message.author === 'ai' ? 'AI' : 'YK'}
            </span>
            <div className="chat-mode__body">
              <span className="chat-mode__name">
                {message.author === 'ai' ? 'Grow' : 'あなた'}
              </span>
              <div className="chat-mode__bubble">{message.text}</div>
            </div>
          </div>
        ))}

        {/* 分解候補（AIが提示したら, §3.3.3） */}
        <ProposalCard taskId={task.id} />

        {error != null && (
          <div className="chat-mode__error" role="alert">
            {error}
          </div>
        )}
      </div>

      {/* コンポーザ: placeholder「前提や要望を伝える…」＋「送信」（パープル） */}
      <Composer taskId={task.id} variant="chat" />
    </div>
  );
}
