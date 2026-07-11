import { describe, expect, it } from 'vitest';
import autonomyLevelsJson from '../../../shared/contracts/autonomy_levels.json';
import {
  ALL_AUTONOMY_LEVELS,
  AUTONOMY_META,
  DEFAULT_AUTONOMY,
  taskAllowWebSearch,
  taskAutonomy,
  taskCostCapUsd,
  type AutonomyLevel,
  type Task,
} from './domain.ts';

describe('AUTONOMY_META（#21）', () => {
  it('正準フィクスチャ shared/contracts/autonomy_levels.json と完全一致する', () => {
    expect(AUTONOMY_META).toEqual(autonomyLevelsJson);
  });

  it('全 AutonomyLevel（4段階）を L0→L3 の順で網羅する', () => {
    const expected: AutonomyLevel[] = ['L0', 'L1', 'L2', 'L3'];
    expect(Object.keys(AUTONOMY_META)).toEqual(expected);
    expect(ALL_AUTONOMY_LEVELS).toEqual(expected);
  });

  it('既定は L1（現行挙動 = 下書きまで）', () => {
    expect(DEFAULT_AUTONOMY).toBe('L1');
    expect(AUTONOMY_META.L1.label).toBe('下書きまで');
    expect(AUTONOMY_META.L1.description).toContain('既定');
  });

  it('各レベルにツールチップ用の label / description を持つ', () => {
    for (const level of ALL_AUTONOMY_LEVELS) {
      expect(AUTONOMY_META[level].label.length).toBeGreaterThan(0);
      expect(AUTONOMY_META[level].description.length).toBeGreaterThan(0);
    }
  });
});

describe('taskAutonomy / taskAllowWebSearch / taskCostCapUsd（省略時既定の補完）', () => {
  const base: Task = {
    id: 'T-001',
    workspaceId: 'ws-1',
    boardId: 'b-1',
    laneKey: 'todo',
    orderInLane: 0,
    title: 't',
    status: 'queued',
    ownerUserId: 'u-1',
    labels: [],
    commentCount: 0,
    createdAt: '2026-07-07T00:00:00Z',
    updatedAt: '2026-07-07T00:00:00Z',
  };

  it('未設定タスクは L1・Web検索可・上限なし（BE 既定と鏡写し）', () => {
    expect(taskAutonomy(base)).toBe('L1');
    expect(taskAllowWebSearch(base)).toBe(true);
    expect(taskCostCapUsd(base)).toBeNull();
  });

  it('設定済みタスクはその値を返す', () => {
    const task: Task = {
      ...base,
      autonomy: 'L3',
      policy: { allowWebSearch: false, costCapUsd: 2.5 },
    };
    expect(taskAutonomy(task)).toBe('L3');
    expect(taskAllowWebSearch(task)).toBe(false);
    expect(taskCostCapUsd(task)).toBe(2.5);
  });

  it('policy の省略キーは既定値で補完する（jsonb {} 相当）', () => {
    const task: Task = { ...base, policy: {} };
    expect(taskAllowWebSearch(task)).toBe(true);
    expect(taskCostCapUsd(task)).toBeNull();
  });
});
