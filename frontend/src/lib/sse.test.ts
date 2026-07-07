import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../store/board.ts';
import { boardFixture } from '../test/boardFixture.ts';
import type { Artifact, Comment, Task } from '../types/domain.ts';
import {
  ARTIFACT_CREATED,
  COMMENT_CREATED,
  EVENTS_URL,
  TASK_UPDATED,
  connectEvents,
} from './sse.ts';

type Listener = (e: MessageEvent) => void;

/** EventSource のモック。backend のワイヤ形式で emit できる */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  private listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  close(): void {
    this.closed = true;
  }

  /** event:<type> ＋ data:{"type","payload"}（backend/app/routers/events.py と同形式） */
  emit(type: string, payload: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify({ type, payload }) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function lastSource(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (!source) throw new Error('EventSource が生成されていない');
  return source;
}

const AT = '2026-07-07T00:00:00Z';

function makeComment(partial: Partial<Comment> & Pick<Comment, 'id' | 'text'>): Comment {
  return { taskId: 'T-104', author: 'ai', createdAt: AT, ...partial };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('connectEvents（§5.4 / #7）', () => {
  it('/api/events へ接続し、クリーンアップで close する', () => {
    const disconnect = connectEvents();
    expect(lastSource().url).toBe(EVENTS_URL);
    expect(lastSource().closed).toBe(false);
    disconnect();
    expect(lastSource().closed).toBe(true);
  });

  it('task.updated でカードを差し替え、レーン移動（全レーンから除去→laneKey へ挿入）する', () => {
    connectEvents();
    // T-104 が todo → progress の先頭へ移動し、commentCount も同期される
    const moved: Task = {
      ...useBoardStore.getState().cards['T-104'],
      laneKey: 'progress',
      orderInLane: 0,
      status: 'ai_work',
      commentCount: 2,
    };
    lastSource().emit(TASK_UPDATED, moved);

    const s = useBoardStore.getState();
    expect(s.cards['T-104'].laneKey).toBe('progress');
    expect(s.cards['T-104'].status).toBe('ai_work');
    expect(s.cards['T-104'].commentCount).toBe(2);
    const todo = s.lanes.find((l) => l.key === 'todo');
    const progress = s.lanes.find((l) => l.key === 'progress');
    expect(todo?.cardIds).toEqual(['T-109', 'T-112']);
    expect(progress?.cardIds).toEqual(['T-104', 'T-098', 'T-101']);
  });

  it('comment.created で読込済みスレッドへ追記する', () => {
    connectEvents();
    useBoardStore.setState((s) => ({ comments: { ...s.comments, 'T-104': [] } }));

    lastSource().emit(
      COMMENT_CREATED,
      makeComment({ id: 'c-1', text: '着手します' }),
    );
    expect(useBoardStore.getState().comments['T-104']).toHaveLength(1);
    expect(useBoardStore.getState().comments['T-104'][0].id).toBe('c-1');
  });

  it('comment.created は id で重複排除する（自分の POST 応答との二重適用を防ぐ）', () => {
    connectEvents();
    useBoardStore.setState((s) => ({
      comments: { ...s.comments, 'T-104': [makeComment({ id: 'c-1', text: '着手します' })] },
    }));

    lastSource().emit(COMMENT_CREATED, makeComment({ id: 'c-1', text: '着手します' }));
    expect(useBoardStore.getState().comments['T-104']).toHaveLength(1);
  });

  it('comment.created は自分の楽観的追加（tmp- で同一 author+text）を確定版に差し替える', () => {
    connectEvents();
    useBoardStore.setState((s) => ({
      comments: {
        ...s.comments,
        'T-104': [makeComment({ id: 'tmp-99', author: 'human', text: '修正お願いします' })],
      },
    }));

    lastSource().emit(
      COMMENT_CREATED,
      makeComment({ id: 'c-2', author: 'human', text: '修正お願いします' }),
    );
    const list = useBoardStore.getState().comments['T-104'];
    expect(list).toHaveLength(1);
    expect(list[0].id).toBe('c-2');
  });

  it('スレッド未読込のタスクへの comment.created は無視する（開いたとき GET で取得）', () => {
    connectEvents();
    lastSource().emit(COMMENT_CREATED, makeComment({ id: 'c-1', text: '着手します' }));
    expect(useBoardStore.getState().comments['T-104']).toBeUndefined();
  });

  it('artifact.created で成果物を store へ反映する（#10。version 昇順・id 重複排除）', () => {
    connectEvents();
    const v1: Artifact = {
      id: 'a-1',
      taskId: 'T-104',
      version: 1,
      contentMd: '# 競合調査レポート',
      createdAt: AT,
    };

    lastSource().emit(ARTIFACT_CREATED, v1);
    expect(useBoardStore.getState().artifacts['T-104']).toEqual([v1]);

    // 同一 id の再送（POST 応答との二重適用）は無視される
    lastSource().emit(ARTIFACT_CREATED, v1);
    expect(useBoardStore.getState().artifacts['T-104']).toHaveLength(1);

    // 新版は末尾（最新）に積まれる
    const v2: Artifact = { ...v1, id: 'a-2', version: 2, contentMd: '# 改訂版' };
    lastSource().emit(ARTIFACT_CREATED, v2);
    expect(useBoardStore.getState().artifacts['T-104']).toEqual([v1, v2]);
  });
});
