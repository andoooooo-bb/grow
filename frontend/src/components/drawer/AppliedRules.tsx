// 適用ルールセクション（§3.3.2b — この製品の主戦場のUI, #10）:
// 「◈ AIが着手時に前提にするルール {N}」＋ scopeミニバッジ＋ルール文で、
// AIが着手時に注入するルールを必ず可視化する（§6.3: ブラックボックスにしない）。
// retrieval 0件なら非表示（§5.5。「まだルールがありません」等は出さない）。

import { relevantRules } from '../../lib/retrieval.ts';
import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import './AppliedRules.css';

interface AppliedRulesProps {
  task: Task;
}

export function AppliedRules({ task }: AppliedRulesProps) {
  const rules = useBoardStore((s) => s.rules);
  // FE ミラー（src/lib/retrieval.ts）で BE と同一の順序・上限を決定的に再現する
  const applied = relevantRules(rules, task);
  if (applied.length === 0) return null;

  return (
    <section className="applied-rules">
      <div className="applied-rules__heading">
        <span className="applied-rules__title">◈ AIが着手時に前提にするルール</span>
        <span className="applied-rules__count">{applied.length}</span>
      </div>
      <div className="applied-rules__list">
        {applied.map((rule) => (
          <div key={rule.id} className="applied-rules__row">
            <span
              className={`applied-rules__scope applied-rules__scope--${rule.scope}`}
            >
              {rule.scope === 'team' ? 'チーム' : '個人'}
            </span>
            <span className="applied-rules__text">{rule.text}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
