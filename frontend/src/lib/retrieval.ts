// retrieval の FE ミラー（§6.3 / §00 #8, #10）。
// 真実は BE（backend/app/repo/rules.py relevant_rules — assign-ai 時に注入・記録する）。
// FE は適用ルールセクション（§3.3.2b）の表示のために同一ロジックで決定的に再現する:
//   tags 空（全体ルール） or カードの labels と交差
//   → confidence 降順（high > med > low）→ applied 降順 → human_id 昇順 → 上限8件。

import type { Confidence, Rule, Task } from '../types/domain.ts';

/** 注入件数の上限（§00 #8 で確定: 上限8件・confidence 降順で足切り） */
export const RETRIEVAL_LIMIT = 8;

/** confidence の並び順（BE の case 式と鏡写し: high=0 < med=1 < low=2） */
const CONFIDENCE_RANK: Record<Confidence, number> = { high: 0, med: 1, low: 2 };

/**
 * タスクに適用されるルールを返す（§6.3 retrieval, MVP はタグ一致・決定的）。
 * personal / team の両 scope を対象にする（§00 #8）。0件なら空配列（セクション非表示, §5.5）。
 * アーカイブ済み（#26 棚卸し）は BE relevant_rules と同じく除外する。
 */
export function relevantRules(
  rules: readonly Rule[],
  task: Pick<Task, 'labels'>,
  limit: number = RETRIEVAL_LIMIT,
): Rule[] {
  return rules
    .filter(
      (rule) =>
        rule.archived !== true &&
        (rule.tags.length === 0 || rule.tags.some((tag) => task.labels.includes(tag))),
    )
    .sort(
      (a, b) =>
        CONFIDENCE_RANK[a.confidence] - CONFIDENCE_RANK[b.confidence] ||
        b.applied - a.applied ||
        // 決定性のため human_id（Rule.id の K-xx）昇順。BE の bytea 順と一致する辞書順比較
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    )
    .slice(0, limit);
}
