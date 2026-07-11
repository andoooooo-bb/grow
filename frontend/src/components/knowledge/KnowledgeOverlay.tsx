// ナレッジ・オーバーレイ（§3.4 / §1.8 / #14）: トップバー「◈ ナレッジ」で開く。
// fixed 全面・遮蔽 rgba(20,24,40,.42)・パネル720px/max84vh/radius14。
// ボディは「あなたのルール（personal）」「チームのルール（形式知, team）」の2セクション。
// 各ルールカード = 確度ドット（high緑/med琥珀/low灰）＋ルール文＋NEW＋出典/適用回数、
// personal のみ「チームへ昇格 ↑」（§5.3 promoteRule）。遮蔽クリックで閉じる
// （内側は stopPropagation で誤クローズ防止 — §5.3 stop）。
// #25: ヘッダ下に学習ダッシュボード — スタットタイル4枚（AI完了/ルール適用/差し戻し/累計コスト）
// ＋ 直近14日のルール適用スパークライン（素SVG・依存なし）＋ TOP3ルール（applied 降順）。
// #26: タブ（ルール一覧 / 受信箱 N）＋「⟳ 今すぐメンテナンス実行」— 夜間ナレッジCIの提案
// （新規蒸留/重複統合/矛盾検出/棚卸し）を受信箱で採用/却下する。アーカイブ済みルールは
// 一覧から除外（retrieval と同じ扱い §6.6）。

import { useEffect, useState } from 'react';
import { useBoardStore } from '../../store/board.ts';
import type {
  KnowledgeProposal,
  KnowledgeProposalKind,
  RuleApplicationPoint,
  StatsResponse,
} from '../../types/api.ts';
import type { Rule } from '../../types/domain.ts';
import './KnowledgeOverlay.css';

// ナレッジ空のセクション文言（§5.5）
export const KNOWLEDGE_EMPTY_TEXT =
  'まだありません。タスクを完了して『✧ 学ぶ』で追加できます';

// 受信箱が空のときの文言（#26）
export const INBOX_EMPTY_TEXT =
  '受信箱は空です。夜間メンテナンスAIがナレッジの提案をここに届けます';

// ヘッダ説明文（§3.4）
const KNOWLEDGE_DESCRIPTION =
  'AIは作業を始める前に、ここのルールを自動で読み込んでから動きます。タスクの履歴から少しずつ蓄積され、あなた専用に賢くなっていきます。';

// #26: 提案 kind の表示メタ（バッジ色は CSS knowledge__kind--{kind} が持つ:
// 新規=ティール / 統合=パープル / 矛盾=アンバー / 棚卸し=グレー）
export const PROPOSAL_KIND_META = {
  distill: { label: '新規' },
  merge: { label: '統合' },
  conflict: { label: '矛盾' },
  demote: { label: '棚卸し' },
} as const satisfies Record<KnowledgeProposalKind, { label: string }>;

/** タイルのコスト表示（$1 未満は 4桁・以上は 2桁。TraceSection と同規約） */
export function formatStatsCost(costUsd: number): string {
  return costUsd >= 1 ? `$${costUsd.toFixed(2)}` : `$${costUsd.toFixed(4)}`;
}

// ---- #25 学習ダッシュボード（スタットタイル＋スパークライン＋TOP3） ----------------

// スパークラインの描画領域（viewBox 座標。表示は CSS で width 100%）
const SPARK_WIDTH = 280;
const SPARK_HEIGHT = 44;
const SPARK_PAD = 5;

/** 直近14日のルール適用回数を素SVGの単系列ラインで描く（依存なし・単系列なので凡例なし） */
function Sparkline({ points }: { points: RuleApplicationPoint[] }) {
  if (points.length < 2) return null;
  const max = Math.max(...points.map((p) => p.count), 1);
  const stepX = (SPARK_WIDTH - SPARK_PAD * 2) / (points.length - 1);
  const toXY = (count: number, i: number): [number, number] => [
    SPARK_PAD + i * stepX,
    SPARK_HEIGHT - SPARK_PAD - (count / max) * (SPARK_HEIGHT - SPARK_PAD * 2),
  ];
  const xy = points.map((p, i) => toXY(p.count, i));
  const line = xy.map(([x, y]) => `${x},${y}`).join(' ');
  // 面ウォッシュ（系列色の10%）: ベースラインへ閉じたパス
  const area = `M ${SPARK_PAD},${SPARK_HEIGHT - SPARK_PAD} L ${line
    .split(' ')
    .join(' L ')} L ${SPARK_WIDTH - SPARK_PAD},${SPARK_HEIGHT - SPARK_PAD} Z`;
  const [endX, endY] = xy[xy.length - 1];
  const total = points.reduce((sum, p) => sum + p.count, 0);
  return (
    <svg
      className="knowledge__spark"
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      role="img"
      aria-label={`直近${points.length}日のルール適用 計${total}回`}
    >
      <path className="knowledge__spark-area" d={area} />
      <polyline className="knowledge__spark-line" points={line} />
      {/* 端点ドット（r4）＋サーフェスリング（2px）で現在値を示す */}
      <circle className="knowledge__spark-dot" cx={endX} cy={endY} r="4" />
    </svg>
  );
}

/** スタットタイル1枚（label + value。値はテキストトークン・mono） */
function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="knowledge__tile">
      <div className="knowledge__tile-value">{value}</div>
      <div className="knowledge__tile-label">{label}</div>
    </div>
  );
}

/** TOP3 に載せるルール（applied 降順 → human_id 昇順で安定ソート。0回は除く） */
export function topAppliedRules(rules: readonly Rule[], limit = 3): Rule[] {
  return [...rules]
    .filter((r) => r.applied > 0)
    .sort((a, b) => b.applied - a.applied || a.id.localeCompare(b.id))
    .slice(0, limit);
}

/** #25 学習ダッシュボード帯（stats 未取得時は非表示 = 既存レイアウトを崩さない） */
function LearningDashboard({ stats, rules }: { stats: StatsResponse; rules: Rule[] }) {
  const top = topAppliedRules(rules);
  return (
    <div className="knowledge__dashboard">
      <div className="knowledge__tiles">
        <StatTile label="AI完了" value={String(stats.aiDoneCount)} />
        <StatTile label="ルール適用" value={String(stats.ruleApplicationsTotal)} />
        <StatTile label="差し戻し" value={String(stats.rejectCount)} />
        <StatTile label="累計コスト" value={formatStatsCost(stats.totalCostUsd)} />
      </div>
      <div className="knowledge__learning">
        <div className="knowledge__learning-head">ルール適用の推移（直近14日）</div>
        <Sparkline points={stats.ruleApplications} />
      </div>
      {top.length > 0 && (
        <div className="knowledge__top-rules">
          <div className="knowledge__learning-head">よく効いているルール TOP3</div>
          {top.map((rule, i) => (
            <div key={rule.id} className="knowledge__top-rule">
              <span className="knowledge__top-rank">{i + 1}</span>
              <span className="knowledge__top-id">{rule.id}</span>
              <span className="knowledge__top-text">{rule.text}</span>
              <span className="knowledge__top-applied">適用 {rule.applied}回</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface RuleCardProps {
  rule: Rule;
  /** personal セクションのみ「チームへ昇格 ↑」を出す（team は無し — §3.4） */
  onPromote?: (ruleId: string) => void;
  /** #20: 適用直後（rule.updated applied++）の時刻。カードのフラッシュ＋適用回数バンプ */
  flashStamp?: number;
}

function RuleCard({ rule, onPromote, flashStamp }: RuleCardProps) {
  return (
    <div
      className={`knowledge__rule knowledge__rule--${rule.scope}${
        flashStamp === undefined ? '' : ' knowledge__rule--flash'
      }`}
    >
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
        {/* #20: 適用の瞬間にカウントアップを強調（時刻 key で one-shot アニメ再生） */}
        <span
          key={flashStamp}
          className={`knowledge__applied${
            flashStamp === undefined ? '' : ' knowledge__applied--bump'
          }`}
        >
          適用 {rule.applied}回
        </span>
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

// ---- #26 受信箱（ナレッジCIの提案カード） ------------------------------------------

interface ProposalCardProps {
  proposal: KnowledgeProposal;
  /** 対象ルールの現文表示用（K-xx -> Rule）。無ければ id のみ表示 */
  ruleById: ReadonlyMap<string, Rule>;
  onAdopt: (proposalId: string) => void;
  onDismiss: (proposalId: string) => void;
}

/** 提案カード: kind バッジ＋判断説明（note）＋対象ルールの現文と提案文の対比＋採用/却下 */
function ProposalCard({ proposal, ruleById, onAdopt, onDismiss }: ProposalCardProps) {
  return (
    <div className={`knowledge__proposal knowledge__proposal--${proposal.kind}`}>
      <div className="knowledge__proposal-head">
        <span className={`knowledge__kind knowledge__kind--${proposal.kind}`}>
          {PROPOSAL_KIND_META[proposal.kind].label}
        </span>
        <span className="knowledge__proposal-note">{proposal.note}</span>
      </div>
      {proposal.targetRuleIds.length > 0 && (
        <div className="knowledge__proposal-targets">
          {proposal.targetRuleIds.map((ruleId) => (
            <div key={ruleId} className="knowledge__proposal-target">
              <span className="knowledge__proposal-marker">現</span>
              <span className="knowledge__proposal-target-id">{ruleId}</span>
              <span className="knowledge__proposal-target-text">
                {ruleById.get(ruleId)?.text ?? ''}
              </span>
            </div>
          ))}
        </div>
      )}
      {proposal.text !== '' && (
        <div className="knowledge__proposal-draft">
          <span className="knowledge__proposal-marker knowledge__proposal-marker--draft">
            案
          </span>
          <span className="knowledge__proposal-text">{proposal.text}</span>
        </div>
      )}
      <div className="knowledge__proposal-meta">
        {proposal.source !== '' && (
          <span className="knowledge__source">出典: {proposal.source}</span>
        )}
        <div className="knowledge__proposal-actions">
          <button
            type="button"
            className="knowledge__adopt"
            onClick={() => onAdopt(proposal.id)}
          >
            採用
          </button>
          <button
            type="button"
            className="knowledge__dismiss"
            onClick={() => onDismiss(proposal.id)}
          >
            却下
          </button>
        </div>
      </div>
    </div>
  );
}

export function KnowledgeOverlay() {
  const showKnowledge = useBoardStore((s) => s.showKnowledge);
  const rules = useBoardStore((s) => s.rules);
  const closeKnowledge = useBoardStore((s) => s.closeKnowledge);
  const promoteRule = useBoardStore((s) => s.promoteRule);
  // #20: 適用直後の ruleId -> 時刻（カードのフラッシュ＋「適用 N回」バンプ演出）
  const justApplied = useBoardStore((s) => s.justApplied);
  // #25: 学習ダッシュボード集計（開くたびに最新へ更新。失敗時は前回値/非表示）
  const stats = useBoardStore((s) => s.stats);
  const loadStats = useBoardStore((s) => s.loadStats);
  // #26: 受信箱（pending の提案）と手動メンテナンス実行
  const proposals = useBoardStore((s) => s.proposals);
  const loadProposals = useBoardStore((s) => s.loadProposals);
  const runKnowledgeCi = useBoardStore((s) => s.runKnowledgeCi);
  const adoptProposal = useBoardStore((s) => s.adoptProposal);
  const dismissProposal = useBoardStore((s) => s.dismissProposal);
  const ciRunning = useBoardStore((s) => s.ciRunning);
  const ciNotice = useBoardStore((s) => s.ciNotice);
  // #26: 表示タブ（ルール一覧 / 受信箱）。開き直すたびルール一覧へ戻る
  const [tab, setTab] = useState<'rules' | 'inbox'>('rules');

  useEffect(() => {
    if (showKnowledge) {
      setTab('rules');
      void loadStats();
      void loadProposals(); // 受信箱バッジ件数のため一覧も先読み（#26）
    }
  }, [showKnowledge, loadStats, loadProposals]);

  if (!showKnowledge) return null;

  // アーカイブ済み（#26 棚卸し）は一覧から除外（retrieval と同じ扱い §6.6）
  const visibleRules = rules.filter((r) => r.archived !== true);
  const personal = visibleRules.filter((r) => r.scope === 'personal');
  const team = visibleRules.filter((r) => r.scope === 'team');
  const ruleById = new Map(rules.map((r) => [r.id, r] as const));

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
          <div className="knowledge__header-actions">
            {/* #26: 夜間バッチと同じナレッジCIをその場で実行（デモボタン） */}
            <button
              type="button"
              className="knowledge__run-ci"
              onClick={() => void runKnowledgeCi()}
              disabled={ciRunning}
            >
              {ciRunning ? (
                <>
                  <span className="knowledge__spinner" aria-hidden="true" />
                  実行中…
                </>
              ) : (
                '⟳ 今すぐメンテナンス実行'
              )}
            </button>
            <button
              type="button"
              className="knowledge__close"
              aria-label="閉じる"
              onClick={closeKnowledge}
            >
              ✕
            </button>
          </div>
        </div>
        {/* #26: 手動実行の完了トースト（件数）。閉じるまで/次の実行までは残す */}
        {ciNotice !== null && <div className="knowledge__notice">{ciNotice}</div>}
        {/* #25: 学習ダッシュボード（ヘッダ下）。stats 未取得なら出さない */}
        {stats !== null && <LearningDashboard stats={stats} rules={visibleRules} />}
        {/* #26: タブ（ルール一覧 / 受信箱 N）。バッジは SSE でライブ更新される */}
        <div className="knowledge__tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'rules'}
            className={`knowledge__tab${tab === 'rules' ? ' knowledge__tab--active' : ''}`}
            onClick={() => setTab('rules')}
          >
            ルール一覧
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'inbox'}
            className={`knowledge__tab${tab === 'inbox' ? ' knowledge__tab--active' : ''}`}
            onClick={() => setTab('inbox')}
          >
            受信箱
            <span className="knowledge__tab-badge">{proposals.length}</span>
          </button>
        </div>
        {tab === 'inbox' ? (
          <div className="knowledge__body">
            <div className="knowledge__proposals">
              {proposals.length === 0 ? (
                <div className="knowledge__empty">{INBOX_EMPTY_TEXT}</div>
              ) : (
                proposals.map((proposal) => (
                  <ProposalCard
                    key={proposal.id}
                    proposal={proposal}
                    ruleById={ruleById}
                    onAdopt={(id) => void adoptProposal(id)}
                    onDismiss={(id) => void dismissProposal(id)}
                  />
                ))
              )}
            </div>
          </div>
        ) : (
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
                      // #20: 適用時刻を key に含め、再適用のたびフラッシュを再生する
                      key={
                        justApplied[rule.id] === undefined
                          ? rule.id
                          : `${rule.id}-${justApplied[rule.id]}`
                      }
                      rule={rule}
                      onPromote={(id) => void promoteRule(id)}
                      flashStamp={justApplied[rule.id]}
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
                  team.map((rule) => (
                    <RuleCard
                      key={
                        justApplied[rule.id] === undefined
                          ? rule.id
                          : `${rule.id}-${justApplied[rule.id]}`
                      }
                      rule={rule}
                      flashStamp={justApplied[rule.id]}
                    />
                  ))
                )}
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
