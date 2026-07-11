// retrieval FE ミラー（#10）の単体テスト。
// backend/tests/test_retrieval.py と同じ期待値で BE（app/repo/rules.py）との一致を担保する。

import { describe, expect, it } from 'vitest';
import { boardFixture } from '../test/boardFixture.ts';
import type { Rule } from '../types/domain.ts';
import { RETRIEVAL_LIMIT, relevantRules } from './retrieval.ts';

const AT = '2026-07-01T00:00:00Z';

function makeRule(
  partial: Partial<Rule> & Pick<Rule, 'id' | 'confidence' | 'applied' | 'tags'>,
): Rule {
  return {
    workspaceId: 'ws-1',
    scope: 'personal',
    ownerUserId: 'user-yk',
    text: `ルール ${partial.id}`,
    source: 'テスト',
    createdAt: AT,
    updatedAt: AT,
    ...partial,
  };
}

describe('relevantRules（§6.3 / §00 #8 — BE ミラー）', () => {
  const fixture = boardFixture();

  it('タグ交差＋全体ルールが対象になり、BE と同一順序で並ぶ（T-104 → K-02,K-04,K-01,K-03）', () => {
    // T-104: labels [仕事, 調査] → K-01/K-02/K-03/K-04（K-05 経理は対象外）。
    // confidence降順 → applied降順 → human_id昇順（backend/tests/test_retrieval.py と同じ期待値）
    const rows = relevantRules(fixture.rules, fixture.cards['T-104']);
    expect(rows.map((r) => r.id)).toEqual(['K-02', 'K-04', 'K-01', 'K-03']);
  });

  it('personal / team 両方が対象になる（T-121 経理 → K-02,K-04,K-05）', () => {
    const rows = relevantRules(fixture.rules, fixture.cards['T-121']);
    expect(rows.map((r) => r.id)).toEqual(['K-02', 'K-04', 'K-05']);
    expect(new Set(rows.map((r) => r.scope))).toEqual(new Set(['personal', 'team']));
  });

  it('タグが交差しないタスクには全体ルール（tags空）だけが適用される', () => {
    // T-130: labels [個人, デザイン] → どのルールの tags とも交差しない
    const rows = relevantRules(fixture.rules, fixture.cards['T-130']);
    expect(rows.map((r) => r.id)).toEqual(['K-02', 'K-04']);
    expect(rows.every((r) => r.tags.length === 0)).toBe(true);
  });

  it('applied がどれだけ多くても confidence が低ければ後ろに並ぶ', () => {
    // backend/tests/test_retrieval.py test_confidence_order_beats_applied と同じ期待値
    const rules = [
      ...fixture.rules,
      makeRule({ id: 'K-90', confidence: 'med', applied: 100, tags: [] }),
      makeRule({ id: 'K-91', confidence: 'low', applied: 200, tags: [] }),
    ];
    const rows = relevantRules(rules, fixture.cards['T-104']);
    // high 群（K-02, K-04, K-01）→ med 群（K-90 applied100 > K-03 applied2）→ low 群
    expect(rows.map((r) => r.id)).toEqual([
      'K-02',
      'K-04',
      'K-01',
      'K-90',
      'K-03',
      'K-91',
    ]);
  });

  it('同 confidence・同 applied は human_id（K-xx）の辞書順で決定的に並ぶ', () => {
    const rules = [
      makeRule({ id: 'K-12', confidence: 'high', applied: 3, tags: [] }),
      makeRule({ id: 'K-07', confidence: 'high', applied: 3, tags: [] }),
    ];
    const rows = relevantRules(rules, fixture.cards['T-104']);
    expect(rows.map((r) => r.id)).toEqual(['K-07', 'K-12']);
  });

  it('上限8件・confidence 降順で足切りされる（§00 #8）', () => {
    // backend/tests/test_retrieval.py test_limit_8 と同じ期待値
    const extra = Array.from({ length: 10 }, (_, i) =>
      makeRule({
        id: `K-8${i}`,
        scope: 'team',
        confidence: 'high',
        applied: 1000 + i,
        tags: [],
      }),
    );
    const rows = relevantRules([...fixture.rules, ...extra], fixture.cards['T-104']);
    expect(rows).toHaveLength(RETRIEVAL_LIMIT);
    expect(RETRIEVAL_LIMIT).toBe(8);
    // 全て high・applied 降順（1009..1000 > 既存の K-02:14 等なので追加分が先頭）
    expect(rows.slice(0, 3).map((r) => r.id)).toEqual(['K-89', 'K-88', 'K-87']);
    expect(rows.every((r) => r.confidence === 'high')).toBe(true);
  });

  it('該当ルールが無ければ空配列（§5.5: セクション非表示の判定に使う）', () => {
    expect(relevantRules([], fixture.cards['T-104'])).toEqual([]);
    const nonMatching = [
      makeRule({ id: 'K-50', confidence: 'high', applied: 1, tags: ['経理'] }),
    ];
    expect(relevantRules(nonMatching, fixture.cards['T-104'])).toEqual([]);
  });

  it('入力の rules 配列を破壊しない（filter 後に sort する）', () => {
    const rules = [...fixture.rules];
    const before = rules.map((r) => r.id);
    relevantRules(rules, fixture.cards['T-104']);
    expect(rules.map((r) => r.id)).toEqual(before);
  });

  it('アーカイブ済み（#26 棚卸し）は BE relevant_rules と同じく除外される', () => {
    const rules = fixture.rules.map((r) =>
      r.id === 'K-02' ? { ...r, archived: true } : r,
    );
    const rows = relevantRules(rules, fixture.cards['T-104']);
    expect(rows.map((r) => r.id)).toEqual(['K-04', 'K-01', 'K-03']);
    // archived 未定義（旧フィクスチャ互換）は従来通り対象
    expect(relevantRules(fixture.rules, fixture.cards['T-104']).map((r) => r.id)).toEqual(
      ['K-02', 'K-04', 'K-01', 'K-03'],
    );
  });
});
