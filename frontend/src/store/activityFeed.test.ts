// #19 ライブフィード（activity / pushActivity）と loadJobs のストアテスト。
// 既存 apply* のシグネチャ・挙動は変えず、内部で pushActivity を積むことを検証する。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { boardFixture } from '../test/boardFixture.ts';
import type { AiJob, Artifact, Comment, Rule, Task } from '../types/domain.ts';
import {
  ACTIVITY_LIMIT,
  type ActivityEntry,
  createInitialBoardState,
  useBoardStore,
} from './board.ts';

const AT = '2026-07-07T00:00:00Z';

function makeEntry(partial: Partial<ActivityEntry> & Pick<ActivityEntry, 'id'>): ActivityEntry {
  return {
    taskId: 'T-098',
    taskTitle: '競合調査レポートの下書き',
    text: 'テスト',
    at: 0,
    ...partial,
  };
}

function makeAiComment(partial: Partial<Comment> & Pick<Comment, 'id' | 'text'>): Comment {
  return { taskId: 'T-098', author: 'ai', createdAt: AT, ...partial };
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('pushActivity（#19 リングバッファ）', () => {
  it('新しい順に積む', () => {
    const { pushActivity } = useBoardStore.getState();
    pushActivity(makeEntry({ id: 'a-1', text: '1件目' }));
    pushActivity(makeEntry({ id: 'a-2', text: '2件目' }));
    expect(useBoardStore.getState().activity.map((e) => e.id)).toEqual(['a-2', 'a-1']);
  });

  it('同一 id は重複排除する（POST 応答と SSE の二重適用に冪等）', () => {
    const { pushActivity } = useBoardStore.getState();
    pushActivity(makeEntry({ id: 'dup', text: '先着' }));
    pushActivity(makeEntry({ id: 'dup', text: '後着' }));
    const activity = useBoardStore.getState().activity;
    expect(activity).toHaveLength(1);
    expect(activity[0].text).toBe('先着');
  });

  it(`上限 ${ACTIVITY_LIMIT} 件で古い行から捨てる`, () => {
    const { pushActivity } = useBoardStore.getState();
    for (let i = 1; i <= ACTIVITY_LIMIT + 5; i += 1) {
      pushActivity(makeEntry({ id: `a-${i}` }));
    }
    const activity = useBoardStore.getState().activity;
    expect(activity).toHaveLength(ACTIVITY_LIMIT);
    expect(activity[0].id).toBe(`a-${ACTIVITY_LIMIT + 5}`); // 最新は残る
    expect(activity.at(-1)?.id).toBe('a-6'); // 最古の5件（a-1..a-5）が落ちる
  });
});

describe('apply* からのフィード蓄積（#19）', () => {
  it('applyCommentCreated: AIコメントは未読込タスクでも要約30字＋役割で積む', () => {
    const long = 'あ'.repeat(40);
    useBoardStore.getState().applyCommentCreated(
      makeAiComment({ id: 'c-1', text: long, agentRole: 'executor' }),
    );
    const [entry] = useBoardStore.getState().activity;
    expect(entry.id).toBe('comment-c-1');
    expect(entry.taskId).toBe('T-098');
    expect(entry.taskTitle).toBe('競合調査レポートの下書き');
    expect(entry.text).toBe(`${'あ'.repeat(30)}…`);
    expect(entry.role).toBe('executor');
    // スレッド未読込の挙動は従来通り（comments は積まれない）
    expect(useBoardStore.getState().comments['T-098']).toBeUndefined();
  });

  it('applyCommentCreated: 30字以内はそのまま・役割なしは role undefined', () => {
    useBoardStore.getState().applyCommentCreated(
      makeAiComment({ id: 'c-2', text: '承知しました。着手します。' }),
    );
    const [entry] = useBoardStore.getState().activity;
    expect(entry.text).toBe('承知しました。着手します。');
    expect(entry.role).toBeUndefined();
  });

  it('applyCommentCreated: human コメントは積まない', () => {
    useBoardStore.getState().applyCommentCreated(
      makeAiComment({ id: 'c-3', text: '了解', author: 'human' }),
    );
    expect(useBoardStore.getState().activity).toEqual([]);
  });

  it('applyTaskUpdated: ステータス変化時のみ「{ラベル}へ」を積む', () => {
    const task = useBoardStore.getState().cards['T-098']; // ai_work
    useBoardStore.getState().applyTaskUpdated({
      ...task,
      status: 'you_review',
      laneKey: 'review',
      progress: undefined,
    } satisfies Task);
    const [entry] = useBoardStore.getState().activity;
    expect(entry.taskId).toBe('T-098');
    expect(entry.text).toBe('あなたのレビュー待ちへ');

    // 同一ステータスの差し替え（進捗更新等）では積まない
    useBoardStore.getState().applyTaskUpdated({
      ...useBoardStore.getState().cards['T-098'],
    });
    expect(useBoardStore.getState().activity).toHaveLength(1);
  });

  it('applyTaskUpdated: 未知カード（新規作成）は積まない', () => {
    const task = { ...useBoardStore.getState().cards['T-098'], id: 'T-999' };
    useBoardStore.getState().applyTaskUpdated(task);
    expect(useBoardStore.getState().activity).toEqual([]);
  });

  it('applyArtifactCreated: 「成果物vNを作成」（AI生成=executor / 人の編集=役割なし）', () => {
    const base: Artifact = {
      id: 'art-1',
      taskId: 'T-098',
      jobId: 'job-1',
      version: 2,
      contentMd: '# v2',
      createdAt: AT,
    };
    useBoardStore.getState().applyArtifactCreated(base);
    useBoardStore.getState().applyArtifactCreated(base); // 重複配信は1行のまま
    useBoardStore.getState().applyArtifactCreated({
      ...base,
      id: 'art-2',
      jobId: null,
      version: 3,
    });
    const activity = useBoardStore.getState().activity;
    expect(activity).toHaveLength(2);
    expect(activity[1]).toMatchObject({
      id: 'artifact-art-1',
      text: '成果物v2を作成',
      role: 'executor',
    });
    expect(activity[0]).toMatchObject({ text: '成果物v3を作成', role: undefined });
  });

  it('applyRuleCreated: 「ルールK-xxを学習」を distiller 名義で積む（upsert 済みでも1行）', () => {
    const rule: Rule = {
      id: 'K-06',
      workspaceId: 'ws-1',
      scope: 'personal',
      text: '新ルール',
      tags: [],
      source: 'T-091 から学習',
      sourceTaskId: 'T-091',
      confidence: 'med',
      applied: 0,
      createdAt: AT,
      updatedAt: AT,
    };
    useBoardStore.getState().applyRuleCreated(rule);
    useBoardStore.getState().applyRuleCreated(rule); // adopt 応答先着 → SSE 再適用
    const activity = useBoardStore.getState().activity;
    expect(activity).toHaveLength(1);
    expect(activity[0]).toMatchObject({
      id: 'rule-K-06',
      taskId: 'T-091',
      taskTitle: '確定申告サマリーの最終確認',
      text: 'ルールK-06を学習',
      role: 'distiller',
    });
  });
});

describe('loadJobs（#19 リレー履歴の読込）', () => {
  const job: AiJob = {
    id: 'job-1',
    taskId: 'T-098',
    kind: 'execute',
    status: 'running',
    appliedRuleIds: [],
    createdAt: AT,
  };

  it('GET /api/tasks/:id/jobs の応答を jobs[taskId] へ格納する', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ taskId: 'T-098', jobs: [job] }),
      })),
    );
    await useBoardStore.getState().loadJobs('T-098');
    expect(useBoardStore.getState().jobs['T-098']).toEqual([job]);
    expect(fetch).toHaveBeenCalledWith('/api/tasks/T-098/jobs', undefined);
  });

  it('取得失敗・不正応答は無視する（タイムライン非表示のまま）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    await useBoardStore.getState().loadJobs('T-098');
    expect(useBoardStore.getState().jobs['T-098']).toBeUndefined();

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => boardFixture() })),
    );
    await useBoardStore.getState().loadJobs('T-098');
    expect(useBoardStore.getState().jobs['T-098']).toBeUndefined();
  });
});
