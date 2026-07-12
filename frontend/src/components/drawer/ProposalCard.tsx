// 分解候補カード（§3.3.3 / #12）: proposal[taskId] がある時のみ表示。
// ヘッダ「提案された分解 — {N} 件」＋候補行（担当ミニバッジ AI/あなた＋名称）＋
// 「この内容でボードに反映する」プライマリ（幅いっぱい #5d4eb0）＋補足文。
// confirm 送信中はボタン無効化。409/422 失敗は boardError（Board 側表示）で候補は残る。

import { useBoardStore } from '../../store/board.ts';
import './ProposalCard.css';

interface ProposalCardProps {
  taskId: string;
}

export function ProposalCard({ taskId }: ProposalCardProps) {
  const proposal = useBoardStore((s) => s.proposal[taskId]);
  const confirming = useBoardStore((s) => s.confirming[taskId] ?? false);
  const confirmBreakdown = useBoardStore((s) => s.confirmBreakdown);

  // 候補が無ければ何も出さない（§3.3.3「AIが提示したら」）
  if (proposal === undefined || proposal.length === 0) return null;

  return (
    <div className="proposal-card">
      <div className="proposal-card__header">提案された分解 — {proposal.length} 件</div>
      <div className="proposal-card__body">
        {proposal.map((item, index) => (
          // 候補はサーバ非永続で id を持たない（title 重複もあり得るため index キー）
          <div key={index} className="proposal-card__row">
            <span
              className={`proposal-card__owner proposal-card__owner--${item.owner}`}
            >
              {item.owner === 'ai' ? 'AI' : 'あなた'}
            </span>
            <span className="proposal-card__title">{item.title}</span>
          </div>
        ))}
        <button
          type="button"
          className="proposal-card__confirm"
          disabled={confirming}
          onClick={() => void confirmBreakdown(taskId)}
        >
          この内容でボードに反映する
        </button>
        <div className="proposal-card__note">
          反映後、AIが着手できるサブタスクから自動で進めます
        </div>
      </div>
    </div>
  );
}
