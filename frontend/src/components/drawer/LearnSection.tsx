// 学習セクション（§3.3.2c / §1.7 / #14）: you_review・reviewing・done のカードのみ。
// 「このやり取りから働き方のルールを学べます」＋「✧ 学ぶ」→ 候補カード
// （ヘッダ帯「AIが見つけたルール候補 — 採用でナレッジに追加」＋ scopeミニバッジ＋
// 候補文＋✕却下＋「採用」）。採用/却下で行が消え、全て捌けたら見出し行だけに戻る
// （プロト Grow.dc.html の hasLearn 準拠）。学ぶ実行中はボタン無効化。

import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import './LearnSection.css';

// 「✧ 学ぶ」が出る完了系ステータス（§1.7 step1。BE の LEARNABLE_STATUSES と鏡写し）
const LEARNABLE_STATUSES: ReadonlySet<Task['status']> = new Set([
  'you_review',
  'reviewing',
  'done',
]);

interface LearnSectionProps {
  task: Task;
}

export function LearnSection({ task }: LearnSectionProps) {
  const proposals = useBoardStore((s) => s.learn[task.id]);
  const learning = useBoardStore((s) => s.learning[task.id] ?? false);
  const learnFrom = useBoardStore((s) => s.learnFrom);
  const adoptLearn = useBoardStore((s) => s.adoptLearn);
  const dismissLearn = useBoardStore((s) => s.dismissLearn);

  // 完了系（you_review/reviewing/done）以外では描画しない（§1.7）
  if (!LEARNABLE_STATUSES.has(task.status)) return null;

  return (
    <section className="learn-section">
      <div className="learn-section__head">
        <div className="learn-section__lead">
          このやり取りから<b>働き方のルール</b>を学べます
        </div>
        <button
          type="button"
          className="learn-section__learn"
          disabled={learning}
          onClick={() => void learnFrom(task.id)}
        >
          ✧ 学ぶ
        </button>
      </div>
      {proposals !== undefined && proposals.length > 0 && (
        <div className="learn-section__card">
          <div className="learn-section__band">
            AIが見つけたルール候補 — 採用でナレッジに追加
          </div>
          <div className="learn-section__list">
            {proposals.map((p) => (
              <div key={p.tempId} className="learn-section__row">
                <span
                  className={`learn-section__scope learn-section__scope--${p.scope}`}
                >
                  {p.scope === 'team' ? 'チーム' : '個人'}
                </span>
                <span className="learn-section__text">{p.text}</span>
                <button
                  type="button"
                  className="learn-section__dismiss"
                  aria-label="却下"
                  onClick={() => void dismissLearn(task.id, p.tempId)}
                >
                  ✕
                </button>
                <button
                  type="button"
                  className="learn-section__adopt"
                  onClick={() => void adoptLearn(task.id, p.tempId)}
                >
                  採用
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
