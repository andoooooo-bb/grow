import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { boardFixture } from '../test/boardFixture.ts';
import type { Comment } from '../types/domain.ts';
import {
  createInitialBoardState,
  deriveAiCount,
  deriveRuleCount,
  deriveYouCount,
  useBoardStore,
} from './board.ts';

describe('派生カウンタ（§5.1）', () => {
  const fixture = boardFixture();

  it('youCount = owner が human かつ status≠done のカード数 = 6', () => {
    // breakdown, spec, you_todo ×2, you_review, reviewing の6件
    expect(deriveYouCount(fixture.cards)).toBe(6);
  });

  it('aiCount = status が ai_work または queued のカード数 = 3', () => {
    // queued ×2, ai_work ×1 の3件
    expect(deriveAiCount(fixture.cards)).toBe(3);
  });

  it('ruleCount = rules 総数 = 5', () => {
    expect(deriveRuleCount(fixture.rules)).toBe(5);
  });
});

describe('board store（§2.3 / §5.3）', () => {
  beforeEach(() => {
    useBoardStore.setState(createInitialBoardState());
  });

  it('初期状態が §2.3 の BoardState 形をもつ', () => {
    const s = useBoardStore.getState();
    expect(s.cards).toEqual({});
    expect(s.lanes).toEqual([]);
    expect(s.rules).toEqual([]);
    expect(s.selectedId).toBeNull();
    expect(s.panelMode).toBe('detail');
    expect(s.showKnowledge).toBe(false);
    expect(s.comments).toEqual({});
    expect(s.commentError).toEqual({});
    expect(s.chat).toEqual({});
    expect(s.proposal).toEqual({});
    expect(s.learn).toEqual({});
    expect(s.artifacts).toEqual({});
    expect(s.drafts).toEqual({});
  });

  it('setBoard が cards/lanes/rules を正規化ストアへ反映する', () => {
    useBoardStore.getState().setBoard(boardFixture());
    const s = useBoardStore.getState();
    expect(Object.keys(s.cards)).toHaveLength(11);
    expect(s.lanes.map((l) => l.key)).toEqual([
      'backlog',
      'todo',
      'progress',
      'review',
      'done',
    ]);
    expect(s.lanes[1].cardIds).toEqual(['T-104', 'T-109', 'T-112']);
    expect(s.rules).toHaveLength(5);
  });

  it('select(id) は selectedId を設定し panelMode を detail に戻す', () => {
    useBoardStore.setState({ panelMode: 'chat' });
    useBoardStore.getState().select('T-098');
    expect(useBoardStore.getState().selectedId).toBe('T-098');
    expect(useBoardStore.getState().panelMode).toBe('detail');
  });

  it('closePanel() は selectedId を null に戻す', () => {
    useBoardStore.getState().select('T-098');
    useBoardStore.getState().closePanel();
    expect(useBoardStore.getState().selectedId).toBeNull();
  });

  it('openKnowledge/closeKnowledge が showKnowledge をトグルする', () => {
    useBoardStore.getState().openKnowledge();
    expect(useBoardStore.getState().showKnowledge).toBe(true);
    useBoardStore.getState().closeKnowledge();
    expect(useBoardStore.getState().showKnowledge).toBe(false);
  });

  it('setDraft がタスクごとのコンポーザ入力を保持する', () => {
    useBoardStore.getState().setDraft('T-098', '修正お願いします');
    expect(useBoardStore.getState().drafts['T-098']).toBe('修正お願いします');
  });
});

// ---- コメント（#7: §5.3 postComment / §5.4 楽観的更新） ----

const AT = '2026-07-07T00:00:00Z';

function makeComment(partial: Partial<Comment> & Pick<Comment, 'id' | 'text'>): Comment {
  return { taskId: 'T-098', author: 'human', createdAt: AT, ...partial };
}

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

describe('comment actions（#7）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loadComments が GET 結果をスレッドへ反映する', async () => {
    const loaded = [makeComment({ id: 'c-1', author: 'ai', text: '着手します' })];
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, loaded)));

    await useBoardStore.getState().loadComments('T-098');
    expect(fetch).toHaveBeenCalledWith('/api/tasks/T-098/comments', undefined);
    expect(useBoardStore.getState().comments['T-098']).toEqual(loaded);
    expect(useBoardStore.getState().commentError['T-098']).toBeNull();
  });

  it('loadComments 失敗で簡易エラーを設定する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    await useBoardStore.getState().loadComments('T-098');
    expect(useBoardStore.getState().commentError['T-098']).toBe(
      'コメントの読み込みに失敗しました',
    );
  });

  it('postComment: 楽観的追加（即UI反映・入力クリア・commentCount+1）→ API 成功で確定版に差し替え', async () => {
    const server = makeComment({ id: 'c-server-1', text: '修正お願いします' });
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(201, server)));
    useBoardStore.getState().setDraft('T-098', '修正お願いします');

    const pending = useBoardStore.getState().postComment('T-098', '修正お願いします');

    // await 前 = API 応答前に反映されている（§5.4 楽観的更新）
    let s = useBoardStore.getState();
    expect(s.comments['T-098']).toHaveLength(1);
    expect(s.comments['T-098'][0].id).toMatch(/^tmp-/);
    expect(s.comments['T-098'][0].text).toBe('修正お願いします');
    expect(s.cards['T-098'].commentCount).toBe(1);
    expect(s.drafts['T-098']).toBe('');

    await pending;
    s = useBoardStore.getState();
    expect(s.comments['T-098']).toHaveLength(1);
    expect(s.comments['T-098'][0]).toEqual(server);
    expect(s.cards['T-098'].commentCount).toBe(1);
  });

  it('postComment: API 失敗でロールバック（除去・commentCount復元・エラー設定）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));

    await useBoardStore.getState().postComment('T-098', '失敗するコメント');
    const s = useBoardStore.getState();
    expect(s.comments['T-098']).toHaveLength(0);
    expect(s.cards['T-098'].commentCount).toBe(0);
    expect(s.commentError['T-098']).toBe('コメントの送信に失敗しました');
  });

  it('postComment: 空白のみの入力は送信しない', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await useBoardStore.getState().postComment('T-098', '  \n ');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(useBoardStore.getState().comments['T-098']).toBeUndefined();
  });

  it('confirmComment: SSE が先に確定版を届けていたら temp の除去だけ行う（重複排除）', () => {
    const server = makeComment({ id: 'c-1', text: 'こんにちは' });
    useBoardStore.setState((s) => ({
      comments: {
        ...s.comments,
        'T-098': [server, makeComment({ id: 'tmp-50', text: 'こんにちは' })],
      },
    }));

    useBoardStore.getState().confirmComment('T-098', 'tmp-50', server);
    const list = useBoardStore.getState().comments['T-098'];
    expect(list).toHaveLength(1);
    expect(list[0].id).toBe('c-1');
  });
});
