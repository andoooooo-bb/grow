import { describe, expect, it } from 'vitest';
import statusMetaJson from '../../../shared/contracts/status_meta.json';
import { ALL_TASK_STATUSES, STATUS_META, type TaskStatus } from './domain.ts';

describe('STATUS_META', () => {
  it('正準フィクスチャ shared/contracts/status_meta.json と完全一致する', () => {
    expect(STATUS_META).toEqual(statusMetaJson);
  });

  it('全 TaskStatus（8種）を網羅する', () => {
    const expected: TaskStatus[] = [
      'queued',
      'breakdown',
      'spec',
      'ai_work',
      'you_todo',
      'you_review',
      'reviewing',
      'done',
    ];
    expect(Object.keys(STATUS_META).sort()).toEqual([...expected].sort());
    expect(ALL_TASK_STATUSES.sort()).toEqual([...expected].sort());
  });

  it('status から owner / tone / label が導出できる（§5.1 派生値）', () => {
    expect(STATUS_META.ai_work).toEqual({ label: 'AI作業中', owner: 'ai', tone: 'work' });
    expect(STATUS_META.you_review).toEqual({
      label: 'あなたのレビュー待ち',
      owner: 'human',
      tone: 'attention',
    });
    expect(STATUS_META.queued.owner).toBe('ai');
    expect(STATUS_META.breakdown.label).toBe('分解しましょう');
    expect(STATUS_META.spec.tone).toBe('spec');
    expect(STATUS_META.you_todo.tone).toBe('attention');
    expect(STATUS_META.reviewing.tone).toBe('neutral');
    expect(STATUS_META.done).toEqual({ label: '完了', owner: 'ai', tone: 'done' });
  });

  it('owner=human は「あなたの番」対象の5statusと一致する（§5.6）', () => {
    const humanStatuses = ALL_TASK_STATUSES.filter((s) => STATUS_META[s].owner === 'human');
    expect(humanStatuses.sort()).toEqual(
      ['breakdown', 'spec', 'you_todo', 'you_review', 'reviewing'].sort(),
    );
  });
});
