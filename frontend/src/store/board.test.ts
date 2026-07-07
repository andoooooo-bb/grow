import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { boardFixture } from '../test/boardFixture.ts';
import type { Comment, LaneKey, Task } from '../types/domain.ts';
import {
  ADD_CARD_AI_PROMPT,
  createInitialBoardState,
  deriveAiCount,
  deriveRuleCount,
  deriveYouCount,
  NEW_CARD_TITLE,
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

// ---- ボード操作（#8: §5.3 move/addCard/markDone / §5.4 楽観的更新） ----

/** 指定レーンの cardIds を返す */
function laneIds(key: LaneKey): string[] {
  const lane = useBoardStore.getState().lanes.find((l) => l.key === key);
  return lane?.cardIds ?? [];
}

describe('move（#8: §5.3 / §5.2 / §5.4）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('楽観的に対象レーン末尾へ移動 → PATCH {laneKey} 成功でサーバ確定版に置き換わる', async () => {
    // T-104（todo, spec）→ progress へ
    const server: Task = {
      ...boardFixture().cards['T-104'],
      laneKey: 'progress',
      orderInLane: 2,
    };
    let resolvePatch!: (value: unknown) => void;
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise((resolve) => (resolvePatch = resolve)),
    );
    vi.stubGlobal('fetch', fetchMock);

    const pending = useBoardStore.getState().move('T-104', 'progress');

    // await 前 = API 応答前に反映されている（§5.4 楽観的更新）
    let s = useBoardStore.getState();
    expect(laneIds('todo')).toEqual(['T-109', 'T-112']);
    expect(laneIds('progress')).toEqual(['T-098', 'T-101', 'T-104']); // 末尾へ追加
    expect(s.cards['T-104'].laneKey).toBe('progress');
    expect(s.cards['T-104'].status).toBe('spec'); // 手動DnDは status を変えない（§5.2）

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/T-104',
      expect.objectContaining({ method: 'PATCH' }),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      laneKey: 'progress',
    });

    resolvePatch(jsonResponse(200, server));
    await pending;
    s = useBoardStore.getState();
    expect(s.cards['T-104']).toEqual(server);
    expect(laneIds('progress')).toEqual(['T-098', 'T-101', 'T-104']);
    expect(s.boardError).toBeNull();
  });

  it('PATCH 失敗で元状態へロールバック＋boardError を設定する（§5.4）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    const before = useBoardStore.getState();

    await useBoardStore.getState().move('T-104', 'progress');

    const s = useBoardStore.getState();
    expect(s.lanes).toEqual(before.lanes);
    expect(s.cards).toEqual(before.cards);
    expect(s.boardError).toBe('カードの移動に失敗しました');
  });

  it('同一レーンへのドロップは no-op（API を呼ばない）', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const before = useBoardStore.getState();

    await useBoardStore.getState().move('T-104', 'todo');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useBoardStore.getState().lanes).toEqual(before.lanes);
  });

  it('完了レーンへドロップ: you_review→done は done 化のみ自動整合して PATCH（§00 #7）', async () => {
    // T-091（review, you_review）→ done へ
    const server: Task = {
      ...boardFixture().cards['T-091'],
      laneKey: 'done',
      status: 'done',
      orderInLane: 2,
    };
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, server),
    );
    vi.stubGlobal('fetch', fetchMock);

    await useBoardStore.getState().move('T-091', 'done');

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      laneKey: 'done',
      status: 'done',
      progress: null,
    });
    const s = useBoardStore.getState();
    expect(s.cards['T-091'].status).toBe('done');
    expect(laneIds('review')).toEqual(['T-089']);
    expect(laneIds('done')).toEqual(['T-080', 'T-077', 'T-091']);
  });

  it('完了レーンへドロップ: queued→done は事前チェックで拒否され API 未呼び出し＋フィードバック', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const before = useBoardStore.getState();

    await useBoardStore.getState().move('T-112', 'done'); // T-112 は queued

    expect(fetchMock).not.toHaveBeenCalled();
    const s = useBoardStore.getState();
    expect(s.lanes).toEqual(before.lanes); // 動いていない（即ロールバック相当）
    expect(s.cards['T-112'].laneKey).toBe('todo');
    expect(s.boardError).toBe('「AI待機中」のカードは完了レーンへ移動できません');
  });

  it('完了レーンへドロップ: サーバ 409 でもロールバックする', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(409, {})));
    const before = useBoardStore.getState();

    await useBoardStore.getState().move('T-091', 'done'); // 事前チェックは通る

    const s = useBoardStore.getState();
    expect(s.lanes).toEqual(before.lanes);
    expect(s.cards).toEqual(before.cards);
    expect(s.boardError).toBe('カードの移動に失敗しました');
  });
});

describe('addCard（#8: §5.3）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const created: Task = {
    id: 'T-200',
    workspaceId: 'ws-1',
    boardId: 'board-1',
    laneKey: 'backlog',
    orderInLane: 2,
    title: NEW_CARD_TITLE,
    status: 'breakdown',
    ownerUserId: 'user-yk',
    labels: [],
    commentCount: 0,
    createdAt: AT,
    updatedAt: AT,
  };

  const aiComment: Comment = {
    id: 'c-ai-1',
    taskId: 'T-200',
    author: 'ai',
    text: ADD_CARD_AI_PROMPT,
    createdAt: AT,
  };

  it('POST /api/tasks → AI初期コメント → ストア反映＋ドロワーを開く', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST' && url === '/api/tasks') {
        return jsonResponse(201, created);
      }
      if (init?.method === 'POST' && url === '/api/tasks/T-200/comments') {
        return jsonResponse(201, aiComment);
      }
      throw new Error(`unexpected fetch: ${init?.method ?? 'GET'} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    await useBoardStore.getState().addCard('backlog');

    // 呼び出し内容（status 省略時 breakdown / AIコメント文言）
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      laneKey: 'backlog',
      title: '新しいタスク',
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      author: 'ai',
      text: 'タイトルと、やりたいことを教えてください。大きければ壁打ちで分解しましょう。',
    });

    // ストア反映: カード追加（当該レーン末尾）＋コメント＋ドロワーを開く
    const s = useBoardStore.getState();
    expect(s.cards['T-200'].status).toBe('breakdown');
    expect(s.cards['T-200'].commentCount).toBe(1);
    expect(laneIds('backlog')).toEqual(['T-130', 'T-121', 'T-200']);
    expect(s.comments['T-200']).toEqual([aiComment]);
    expect(s.selectedId).toBe('T-200');
    expect(s.panelMode).toBe('detail');
  });

  it('POST /api/tasks 失敗で boardError を設定し、何も追加しない', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    const before = useBoardStore.getState();

    await useBoardStore.getState().addCard('backlog');

    const s = useBoardStore.getState();
    expect(s.cards).toEqual(before.cards);
    expect(s.lanes).toEqual(before.lanes);
    expect(s.selectedId).toBeNull();
    expect(s.boardError).toBe('カードの追加に失敗しました');
  });
});

describe('markDone（#8: §5.3）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('PATCH {status:done, laneKey:done, progress:null} → 応答を反映し完了レーンへ移動する', async () => {
    // T-109（todo, you_todo）: you_todo→done は許可遷移
    const server: Task = {
      ...boardFixture().cards['T-109'],
      laneKey: 'done',
      status: 'done',
      orderInLane: 2,
    };
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, server),
    );
    vi.stubGlobal('fetch', fetchMock);

    await useBoardStore.getState().markDone('T-109');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/T-109',
      expect.objectContaining({ method: 'PATCH' }),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      status: 'done',
      laneKey: 'done',
      progress: null,
    });
    const s = useBoardStore.getState();
    expect(s.cards['T-109']).toEqual(server);
    expect(laneIds('todo')).toEqual(['T-104', 'T-112']);
    expect(laneIds('done')).toEqual(['T-080', 'T-077', 'T-109']);
  });

  it('PATCH 失敗（不正遷移 409 等）で boardError を設定し、状態を変えない', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(409, {})));
    const before = useBoardStore.getState();

    await useBoardStore.getState().markDone('T-112'); // queued→done は不正遷移

    const s = useBoardStore.getState();
    expect(s.cards).toEqual(before.cards);
    expect(s.lanes).toEqual(before.lanes);
    expect(s.boardError).toBe('完了にできませんでした');
  });
});
