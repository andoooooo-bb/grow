// ナレッジ・オーバーレイ（§3.4 / §1.8 / #14）: トップバー「◈ ナレッジ」で開く。
// fixed 全面・遮蔽 rgba(20,24,40,.42)・パネル720px/max84vh/radius14。
// ボディは「あなたのルール（personal）」「チームのルール（形式知, team）」の2セクション。
// 各ルールカード = 確度ドット（high緑/med琥珀/low灰）＋ルール文＋NEW＋出典/適用回数、
// personal のみ「チームへ昇格 ↑」（§5.3 promoteRule）。遮蔽クリックで閉じる
// （内側は stopPropagation で誤クローズ防止 — §5.3 stop）。

import { useBoardStore } from '../../store/board.ts';
import type { Rule } from '../../types/domain.ts';
import './KnowledgeOverlay.css';

// ナレッジ空のセクション文言（§5.5）
export const KNOWLEDGE_EMPTY_TEXT =
  'まだありません。タスクを完了して『✧ 学ぶ』で追加できます';

// ヘッダ説明文（§3.4）
const KNOWLEDGE_DESCRIPTION =
  'AIは作業を始める前に、ここのルールを自動で読み込んでから動きます。タスクの履歴から少しずつ蓄積され、あなた専用に賢くなっていきます。';

interface RuleCardProps {
  rule: Rule;
  /** personal セクションのみ「チームへ昇格 ↑」を出す（team は無し — §3.4） */
  onPromote?: (ruleId: string) => void;
}

function RuleCard({ rule, onPromote }: RuleCardProps) {
  return (
    <div className={`knowledge__rule knowledge__rule--${rule.scope}`}>
      <div className="knowledge__rule-main">
        <span
          className={`knowledge__conf knowledge__conf--${rule.confidence}`}
          aria-hidden="true"
        />
        <span className="knowledge__rule-text">{rule.text}</span>
        {rule.isNew === true && <span className="knowledge__new">NEW</span>}
      </div>
      <div className="knowledge__rule-meta">
        <span className="knowledge__source">出典: {rule.source}</span>
        <span className="knowledge__applied">適用 {rule.applied}回</span>
        {onPromote !== undefined && (
          <button
            type="button"
            className="knowledge__promote"
            onClick={() => onPromote(rule.id)}
          >
            チームへ昇格 ↑
          </button>
        )}
      </div>
    </div>
  );
}

export function KnowledgeOverlay() {
  const showKnowledge = useBoardStore((s) => s.showKnowledge);
  const rules = useBoardStore((s) => s.rules);
  const closeKnowledge = useBoardStore((s) => s.closeKnowledge);
  const promoteRule = useBoardStore((s) => s.promoteRule);

  if (!showKnowledge) return null;

  const personal = rules.filter((r) => r.scope === 'personal');
  const team = rules.filter((r) => r.scope === 'team');

  return (
    // 遮蔽クリックで閉じる（§3.4）。パネル側は stopPropagation（§5.3 stop）
    <div className="knowledge" onClick={closeKnowledge}>
      <div
        className="knowledge__panel"
        role="dialog"
        aria-label="ナレッジ — 学習した働き方"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="knowledge__header">
          <div>
            <div className="knowledge__title">ナレッジ — 学習した働き方</div>
            <div className="knowledge__description">{KNOWLEDGE_DESCRIPTION}</div>
          </div>
          <button
            type="button"
            className="knowledge__close"
            aria-label="閉じる"
            onClick={closeKnowledge}
          >
            ✕
          </button>
        </div>
        <div className="knowledge__body">
          <section>
            <div className="knowledge__section-head">
              <span className="knowledge__section-icon knowledge__section-icon--you">
                YK
              </span>
              <span className="knowledge__section-title">あなたのルール</span>
              <span className="knowledge__section-count">{personal.length}</span>
            </div>
            <div className="knowledge__rules">
              {personal.length === 0 ? (
                <div className="knowledge__empty">{KNOWLEDGE_EMPTY_TEXT}</div>
              ) : (
                personal.map((rule) => (
                  <RuleCard
                    key={rule.id}
                    rule={rule}
                    onPromote={(id) => void promoteRule(id)}
                  />
                ))
              )}
            </div>
          </section>
          <section>
            <div className="knowledge__section-head">
              <span className="knowledge__section-icon knowledge__section-icon--team">
                ◎
              </span>
              <span className="knowledge__section-title">
                チームのルール（形式知）
              </span>
              <span className="knowledge__section-count">{team.length}</span>
            </div>
            <div className="knowledge__rules">
              {team.length === 0 ? (
                <div className="knowledge__empty">{KNOWLEDGE_EMPTY_TEXT}</div>
              ) : (
                team.map((rule) => <RuleCard key={rule.id} rule={rule} />)
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
