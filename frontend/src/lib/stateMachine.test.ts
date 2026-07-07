import { describe, expect, it } from 'vitest';
import transitionsJson from '../../../shared/contracts/transitions.json';
import { ALL_TASK_STATUSES, type TaskStatus } from '../types/domain.ts';
import { ALLOWED_TRANSITIONS, assertInvariants, canTransition, isProgressInvariantSatisfied } from './stateMachine.ts';

const toKey = (from: string, to: string) => `${from}->${to}`;
const jsonSet = new Set(transitionsJson.map((t) => toKey(t.from, t.to)));

describe('ALLOWED_TRANSITIONS', () => {
  it('正準フィクスチャ shared/contracts/transitions.json と完全一致する', () => {
    const tsSet = new Set(ALLOWED_TRANSITIONS.map((t) => toKey(t.from, t.to)));
    expect(tsSet).toEqual(jsonSet);
    expect(ALLOWED_TRANSITIONS).toHaveLength(transitionsJson.length);
  });
});

describe('canTransition（§5.6 準拠＋§7.2 補完）', () => {
  it('§5.6 の許可遷移をすべて許可する', () => {
    const allowed: Array<[TaskStatus, TaskStatus]> = [
      ['breakdown', 'spec'],
      ['spec', 'ai_work'],
      ['queued', 'ai_work'],
      ['you_todo', 'done'],
      ['you_todo', 'ai_work'],
      ['ai_work', 'you_review'],
      ['you_review', 'reviewing'],
      ['you_review', 'done'],
      ['you_review', 'ai_work'],
      ['reviewing', 'done'],
      ['reviewing', 'ai_work'],
      ['reviewing', 'you_todo'],
    ];
    for (const [from, to] of allowed) {
      expect(canTransition(from, to), `${from} -> ${to}`).toBe(true);
    }
  });

  it('ai_work → you_todo（ジョブ最終失敗時の人戻し §7.2）を許可する', () => {
    expect(canTransition('ai_work', 'you_todo')).toBe(true);
  });

  it('done → 任意（再オープン管理操作）をすべて許可する', () => {
    for (const to of ALL_TASK_STATUSES) {
      expect(canTransition('done', to), `done -> ${to}`).toBe(true);
    }
  });

  it('同一statusへの遷移（no-op）は常に許可する', () => {
    for (const s of ALL_TASK_STATUSES) {
      expect(canTransition(s, s), `${s} -> ${s}`).toBe(true);
    }
  });

  it('§5.6 に無い遷移を拒否する', () => {
    const denied: Array<[TaskStatus, TaskStatus]> = [
      ['queued', 'done'],
      ['queued', 'you_todo'],
      ['queued', 'breakdown'],
      ['queued', 'spec'],
      ['breakdown', 'ai_work'],
      ['breakdown', 'done'],
      ['breakdown', 'queued'],
      ['spec', 'done'],
      ['spec', 'you_review'],
      ['spec', 'breakdown'],
      ['ai_work', 'done'],
      ['ai_work', 'reviewing'],
      ['ai_work', 'queued'],
      ['ai_work', 'spec'],
      ['you_todo', 'you_review'],
      ['you_todo', 'reviewing'],
      ['you_review', 'you_todo'],
      ['you_review', 'queued'],
      ['reviewing', 'queued'],
      ['reviewing', 'spec'],
      ['reviewing', 'you_review'],
    ];
    for (const [from, to] of denied) {
      expect(canTransition(from, to), `${from} -> ${to}`).toBe(false);
    }
  });

  it('全 8×8 ペアで判定が正準フィクスチャ（＋no-op）と一致する', () => {
    for (const from of ALL_TASK_STATUSES) {
      for (const to of ALL_TASK_STATUSES) {
        const expected = from === to || jsonSet.has(toKey(from, to));
        expect(canTransition(from, to), `${from} -> ${to}`).toBe(expected);
      }
    }
  });
});

describe('progress 不変条件（§5.6: ai_work のみ非null）', () => {
  it('ai_work は progress 0..100 を許容する', () => {
    expect(isProgressInvariantSatisfied({ status: 'ai_work', progress: 0 })).toBe(true);
    expect(isProgressInvariantSatisfied({ status: 'ai_work', progress: 60 })).toBe(true);
    expect(isProgressInvariantSatisfied({ status: 'ai_work', progress: 100 })).toBe(true);
    expect(() => assertInvariants({ status: 'ai_work', progress: 45 })).not.toThrow();
  });

  it('progress 未設定はどの status でも許容する', () => {
    for (const s of ALL_TASK_STATUSES) {
      expect(isProgressInvariantSatisfied({ status: s, progress: undefined })).toBe(true);
      expect(() => assertInvariants({ status: s })).not.toThrow();
    }
  });

  it('ai_work 以外で progress が非null なら違反', () => {
    const nonAiWork = ALL_TASK_STATUSES.filter((s) => s !== 'ai_work');
    for (const s of nonAiWork) {
      expect(isProgressInvariantSatisfied({ status: s, progress: 50 }), s).toBe(false);
      expect(() => assertInvariants({ status: s, progress: 50 })).toThrow(/Invariant violation/);
    }
  });

  it('ai_work でも範囲外（<0, >100）は違反', () => {
    expect(isProgressInvariantSatisfied({ status: 'ai_work', progress: -1 })).toBe(false);
    expect(isProgressInvariantSatisfied({ status: 'ai_work', progress: 101 })).toBe(false);
    expect(() => assertInvariants({ status: 'ai_work', progress: 101 })).toThrow();
  });
});
