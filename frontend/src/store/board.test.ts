import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { boardFixture } from '../test/boardFixture.ts';
import type { SubtaskProposal } from '../types/api.ts';
import type {
  Artifact,
  ChatMessage,
  Comment,
  LaneKey,
  Rule,
  RuleProposal,
  Task,
} from '../types/domain.ts';
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

  it('完了レーンから引き出し: done→他レーンは you_todo へ再オープンして PATCH', async () => {
    // T-080（done, 完了レーン）→ 進行中レーンへ引き出し → you_todo に再オープン
    const server: Task = {
      ...boardFixture().cards['T-080'],
      laneKey: 'progress',
      status: 'you_todo',
      progress: undefined,
      orderInLane: 2,
    };
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, server),
    );
    vi.stubGlobal('fetch', fetchMock);

    await useBoardStore.getState().move('T-080', 'progress');

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      laneKey: 'progress',
      status: 'you_todo',
      progress: null,
    });
    const s = useBoardStore.getState();
    expect(s.cards['T-080'].status).toBe('you_todo');
    expect(laneIds('progress')).toContain('T-080');
    expect(laneIds('done')).not.toContain('T-080');
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

    // 呼び出し内容（status 省略時 breakdown / AIコメント文言。#19: 初期質問は計画AI名義）
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      laneKey: 'backlog',
      title: '新しいタスク',
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      author: 'ai',
      text: 'タイトルと、やりたいことを教えてください。大きければ壁打ちで分解しましょう。',
      agentRole: 'planner',
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

// ---- AI 実作業（#10: §5.3 assignAI / §5.4 サーバ起点） ----

describe('assignAi（#10: §5.3）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POST /api/tasks/:id/assign-ai を呼び、202 後はローカル状態を変えない（SSE に任せる）', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(202, { jobId: 'job-1' }));
    vi.stubGlobal('fetch', fetchMock);
    const before = useBoardStore.getState();

    await useBoardStore.getState().assignAi('T-104');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/T-104/assign-ai',
      expect.objectContaining({ method: 'POST' }),
    );
    const s = useBoardStore.getState();
    // 楽観的更新しない: ai_work 化・レーン移動・コメントは task.updated / comment.created が届いて反映される
    expect(s.cards['T-104'].status).toBe('spec');
    expect(s.lanes).toEqual(before.lanes);
    expect(s.assigning['T-104']).toBe(false); // 完了後フラグ解除
    expect(s.boardError).toBeNull();
  });

  it('送信中は assigning フラグが立ち、二重送信しない', async () => {
    let resolvePost!: (value: unknown) => void;
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise((resolve) => (resolvePost = resolve)),
    );
    vi.stubGlobal('fetch', fetchMock);

    const first = useBoardStore.getState().assignAi('T-104');
    expect(useBoardStore.getState().assigning['T-104']).toBe(true);

    const second = useBoardStore.getState().assignAi('T-104'); // 送信中の再呼び出しは no-op
    resolvePost(jsonResponse(202, { jobId: 'job-1' }));
    await Promise.all([first, second]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useBoardStore.getState().assigning['T-104']).toBe(false);
  });

  it('409（不正遷移）で boardError を設定する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(409, {})));

    await useBoardStore.getState().assignAi('T-098'); // ai_work → ai_work は 409

    const s = useBoardStore.getState();
    expect(s.boardError).toBe('AIにまかせられませんでした');
    expect(s.assigning['T-098']).toBe(false);
  });

  it('存在しないカードは API を呼ばない', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await useBoardStore.getState().assignAi('T-999');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ---- 成果物（#10: §00 #2 / §3.3.2 c-2） ----

const ARTIFACT_AT = '2026-07-07T01:00:00Z';

function makeArtifact(
  partial: Partial<Artifact> & Pick<Artifact, 'id' | 'version'>,
): Artifact {
  return {
    taskId: 'T-091',
    contentMd: `# レポート v${partial.version}`,
    createdAt: ARTIFACT_AT,
    ...partial,
  };
}

describe('artifacts（#10）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loadArtifacts が GET 結果（version 昇順・末尾が最新）を反映する', async () => {
    const list = [makeArtifact({ id: 'a-1', version: 1 }), makeArtifact({ id: 'a-2', version: 2 })];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(200, { taskId: 'T-091', artifacts: list })),
    );

    await useBoardStore.getState().loadArtifacts('T-091');

    expect(fetch).toHaveBeenCalledWith('/api/tasks/T-091/artifacts', undefined);
    expect(useBoardStore.getState().artifacts['T-091']).toEqual(list);
  });

  it('loadArtifacts 失敗は静かに無視する（§5.5: セクション非表示のまま）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    await useBoardStore.getState().loadArtifacts('T-091');
    const s = useBoardStore.getState();
    expect(s.artifacts['T-091']).toBeUndefined();
    expect(s.boardError).toBeNull();
  });

  it('applyArtifactCreated は version 昇順を保って追記し、id 重複を排除する', () => {
    const v2 = makeArtifact({ id: 'a-2', version: 2 });
    const v1 = makeArtifact({ id: 'a-1', version: 1 });
    const { applyArtifactCreated } = useBoardStore.getState();

    applyArtifactCreated(v2);
    applyArtifactCreated(v1); // 逆順で届いても昇順に整列
    expect(useBoardStore.getState().artifacts['T-091']).toEqual([v1, v2]);

    applyArtifactCreated(v2); // id 重複（POST 応答と SSE の二重適用）は無視
    expect(useBoardStore.getState().artifacts['T-091']).toEqual([v1, v2]);
  });

  it('saveArtifact が POST {contentMd} を送り、201 応答を反映して true を返す', async () => {
    const created = makeArtifact({ id: 'a-3', version: 3, contentMd: '# 編集版' });
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(201, created),
    );
    vi.stubGlobal('fetch', fetchMock);

    const ok = await useBoardStore.getState().saveArtifact('T-091', '# 編集版');

    expect(ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/T-091/artifacts',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      contentMd: '# 編集版',
    });
    expect(useBoardStore.getState().artifacts['T-091']).toEqual([created]);
  });

  it('saveArtifact 失敗で boardError を設定し false を返す', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));

    const ok = await useBoardStore.getState().saveArtifact('T-091', '# 編集版');

    expect(ok).toBe(false);
    expect(useBoardStore.getState().boardError).toBe('成果物の保存に失敗しました');
    expect(useBoardStore.getState().artifacts['T-091']).toBeUndefined();
  });
});

// ---- 壁打ち → 分解（#12: §1.6 / §5.3 startChat・sendChat・confirmBreakdown） ----

function makeChatMessage(
  partial: Partial<ChatMessage> & Pick<ChatMessage, 'id' | 'text'>,
): ChatMessage {
  return { taskId: 'T-130', author: 'ai', createdAt: AT, ...partial };
}

/** confirm 応答の子カード（parentId=T-130, todo レーン末尾へ生成順） */
function makeChild(id: string, title: string, orderInLane: number, status: Task['status']): Task {
  return {
    ...boardFixture().cards['T-130'],
    id,
    title,
    laneKey: 'todo',
    orderInLane,
    status,
    parentId: 'T-130',
    childIds: undefined,
    progress: status === 'ai_work' ? 10 : undefined,
  };
}

/** mock provider（SUBTASKS_T130）と同じ 5 件の分解候補 */
function proposalFixture(): SubtaskProposal[] {
  return [
    { title: '情報設計・サイトマップ作成', owner: 'ai' },
    { title: 'ワイヤーフレーム作成', owner: 'ai' },
    { title: '掲載する実績コンテンツの選定', owner: 'human', rationale: '本人の意思決定が必要' },
    { title: 'デザイン方向性の決定', owner: 'human', rationale: '好みの判断は人が行う' },
    { title: 'コーディング・実装', owner: 'ai' },
  ];
}

describe('startChat（#12: §5.3）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POST /tasks/:id/chat/start → 一覧を chat へセットし panelMode=chat', async () => {
    const greeting = makeChatMessage({ id: 'm-1', text: '① 公開したい時期は？' });
    const fetchMock = vi.fn(async () => jsonResponse(200, [greeting]));
    vi.stubGlobal('fetch', fetchMock);
    useBoardStore.getState().select('T-130'); // panelMode='detail'

    await useBoardStore.getState().startChat('T-130');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/T-130/chat/start',
      expect.objectContaining({ method: 'POST' }),
    );
    const s = useBoardStore.getState();
    expect(s.chat['T-130']).toEqual([greeting]);
    expect(s.panelMode).toBe('chat');
    expect(s.chatError['T-130']).toBeNull();
  });

  it('冪等: 既存 chat があっても再POSTでサーバの一覧に同期されるだけで壊れない', async () => {
    const history = [
      makeChatMessage({ id: 'm-1', text: '① 公開したい時期は？' }),
      makeChatMessage({ id: 'm-2', author: 'human', text: '来月中に公開したい' }),
    ];
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, history)));

    await useBoardStore.getState().startChat('T-130');
    await useBoardStore.getState().startChat('T-130'); // 2回目（detail→chat の再入）

    const s = useBoardStore.getState();
    expect(s.chat['T-130']).toEqual(history); // 重複しない
    expect(s.panelMode).toBe('chat');
  });

  it('失敗時は boardError を設定し detail のまま', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    useBoardStore.getState().select('T-130');

    await useBoardStore.getState().startChat('T-130');

    const s = useBoardStore.getState();
    expect(s.panelMode).toBe('detail');
    expect(s.boardError).toBe('壁打ちを開始できませんでした');
    expect(s.chat['T-130']).toBeUndefined();
  });

  it('store に無いカードでは API を呼ばない', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await useBoardStore.getState().startChat('T-999');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('sendChat（#12: §5.3 / §5.4 楽観的更新）', () => {
  beforeEach(() => {
    useBoardStore.setState({
      ...createInitialBoardState(),
      ...boardFixture(),
      panelMode: 'chat',
      chat: { 'T-130': [makeChatMessage({ id: 'm-1', text: '① 公開したい時期は？' })] },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('楽観的追加（即UI反映・chatDrafts クリア）→ POST {text} → 確定版へ id 差し替え', async () => {
    const server = makeChatMessage({ id: 'm-2', author: 'human', text: '来月中に公開したい' });
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(201, server),
    );
    vi.stubGlobal('fetch', fetchMock);
    useBoardStore.getState().setChatDraft('T-130', '来月中に公開したい');

    const pending = useBoardStore.getState().sendChat('T-130', '来月中に公開したい');

    // await 前 = API 応答前に反映されている（§5.4 楽観的更新）
    let s = useBoardStore.getState();
    expect(s.chat['T-130']).toHaveLength(2);
    expect(s.chat['T-130'][1].id).toMatch(/^tmp-/);
    expect(s.chat['T-130'][1].text).toBe('来月中に公開したい');
    expect(s.chatDrafts['T-130']).toBe('');

    await pending;
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/T-130/chat',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      text: '来月中に公開したい',
    });
    s = useBoardStore.getState();
    expect(s.chat['T-130']).toHaveLength(2);
    expect(s.chat['T-130'][1]).toEqual(server); // id 差し替え（増えない）
  });

  it('SSE が先に確定版を届けていたら POST 応答では temp の除去だけ行う（重複排除）', async () => {
    const server = makeChatMessage({ id: 'm-2', author: 'human', text: '来月中に公開したい' });
    let resolvePost!: (value: unknown) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((resolve) => (resolvePost = resolve))),
    );

    const pending = useBoardStore.getState().sendChat('T-130', '来月中に公開したい');
    // 楽観的追加が入った状態で SSE（chat.message.created）が先着
    useBoardStore.getState().applyChatMessageCreated(server);
    // applyChatMessageCreated が temp を確定版へ差し替える
    expect(useBoardStore.getState().chat['T-130']).toHaveLength(2);
    expect(useBoardStore.getState().chat['T-130'][1]).toEqual(server);

    resolvePost(jsonResponse(201, server));
    await pending;
    const list = useBoardStore.getState().chat['T-130'];
    expect(list).toHaveLength(2); // 二重にならない
    expect(list.filter((m) => m.id === 'm-2')).toHaveLength(1);
  });

  it('失敗時は楽観的追加をロールバックし chatError を設定する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));

    await useBoardStore.getState().sendChat('T-130', '失敗するメッセージ');

    const s = useBoardStore.getState();
    expect(s.chat['T-130']).toHaveLength(1); // 初期質問のみ
    expect(s.chatError['T-130']).toBe('メッセージの送信に失敗しました');
  });

  it('空白のみの入力は送信しない', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await useBoardStore.getState().sendChat('T-130', '  \n ');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(useBoardStore.getState().chat['T-130']).toHaveLength(1);
  });
});

describe('applyChatMessageCreated / applySubtaskProposal（#12: SSE 適用）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  it('開始済みの壁打ちへ追記し、id 重複を排除する', () => {
    useBoardStore.setState((s) => ({
      chat: { ...s.chat, 'T-130': [makeChatMessage({ id: 'm-1', text: '初期質問' })] },
    }));
    const reply = makeChatMessage({ id: 'm-2', text: 'いかがでしょう。' });

    useBoardStore.getState().applyChatMessageCreated(reply);
    expect(useBoardStore.getState().chat['T-130']).toHaveLength(2);

    useBoardStore.getState().applyChatMessageCreated(reply); // 再送は無視
    expect(useBoardStore.getState().chat['T-130']).toHaveLength(2);
  });

  it('未開始（chat 未セット）のタスクへのメッセージは無視する', () => {
    useBoardStore
      .getState()
      .applyChatMessageCreated(makeChatMessage({ id: 'm-1', text: '初期質問' }));
    expect(useBoardStore.getState().chat['T-130']).toBeUndefined();
  });

  it('applySubtaskProposal が proposal[taskId] をセットする', () => {
    useBoardStore
      .getState()
      .applySubtaskProposal({ taskId: 'T-130', subtasks: proposalFixture() });
    expect(useBoardStore.getState().proposal['T-130']).toHaveLength(5);
    expect(useBoardStore.getState().proposal['T-130'][0]).toEqual({
      title: '情報設計・サイトマップ作成',
      owner: 'ai',
    });
  });
});

describe('confirmBreakdown（#12: §1.6 step5 / §5.3）', () => {
  const children = [
    makeChild('T-131', '情報設計・サイトマップ作成', 3, 'ai_work'), // 先頭AI子は自動着手
    makeChild('T-132', 'ワイヤーフレーム作成', 4, 'queued'),
    makeChild('T-133', '掲載する実績コンテンツの選定', 5, 'you_todo'),
    makeChild('T-134', 'デザイン方向性の決定', 6, 'you_todo'),
    makeChild('T-135', 'コーディング・実装', 7, 'queued'),
  ];
  const parent: Task = {
    ...boardFixture().cards['T-130'],
    status: 'ai_work',
    laneKey: 'progress',
    orderInLane: 0,
    childIds: ['T-131', 'T-132', 'T-133', 'T-134', 'T-135'],
    commentCount: 1,
  };

  beforeEach(() => {
    useBoardStore.setState({
      ...createInitialBoardState(),
      ...boardFixture(),
      selectedId: 'T-130',
      panelMode: 'chat',
      proposal: { 'T-130': proposalFixture() },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('proposal を body に POST → 応答反映（子は todo 末尾・親は progress 先頭）→ proposal クリア → detail 復帰', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, { parent, children }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await useBoardStore.getState().confirmBreakdown('T-130');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/T-130/breakdown/confirm',
      expect.objectContaining({ method: 'POST' }),
    );
    // body は候補の title/owner のみ（rationale は送らない）
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      subtasks: [
        { title: '情報設計・サイトマップ作成', owner: 'ai' },
        { title: 'ワイヤーフレーム作成', owner: 'ai' },
        { title: '掲載する実績コンテンツの選定', owner: 'human' },
        { title: 'デザイン方向性の決定', owner: 'human' },
        { title: 'コーディング・実装', owner: 'ai' },
      ],
    });

    const s = useBoardStore.getState();
    // 子5枚が todo レーン末尾へ生成順に並ぶ（§1.6 step5）
    expect(laneIds('todo')).toEqual([
      'T-104', 'T-109', 'T-112', 'T-131', 'T-132', 'T-133', 'T-134', 'T-135',
    ]);
    expect(s.cards['T-131'].status).toBe('ai_work'); // 先頭AI子は自動着手
    expect(s.cards['T-131'].progress).toBe(10);
    expect(s.cards['T-133'].parentId).toBe('T-130');
    // 親は ai_work で progress レーン先頭・childIds 込み
    expect(laneIds('progress')).toEqual(['T-130', 'T-098', 'T-101']);
    expect(laneIds('backlog')).toEqual(['T-121']); // 元レーンから除去
    expect(s.cards['T-130'].childIds).toEqual(parent.childIds);
    // proposal クリア＋detail 復帰（§5.3）
    expect(s.proposal['T-130']).toBeUndefined();
    expect(s.panelMode).toBe('detail');
    expect(s.confirming['T-130']).toBe(false);
    expect(s.boardError).toBeNull();
  });

  it('409（breakdown/done 親）は boardError 表示＋proposal は残す＋chat のまま', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(409, {})));

    await useBoardStore.getState().confirmBreakdown('T-130');

    const s = useBoardStore.getState();
    expect(s.boardError).toBe('ボードへの反映に失敗しました');
    expect(s.proposal['T-130']).toHaveLength(5); // 残る（再試行できる）
    expect(s.panelMode).toBe('chat');
    expect(s.confirming['T-130']).toBe(false);
  });

  it('proposal が無ければ API を呼ばない（422 予防）', async () => {
    useBoardStore.setState({ proposal: {} });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await useBoardStore.getState().confirmBreakdown('T-130');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('confirm 送信中は二重送信しない', async () => {
    let resolvePost!: (value: unknown) => void;
    const fetchMock = vi.fn(() => new Promise((resolve) => (resolvePost = resolve)));
    vi.stubGlobal('fetch', fetchMock);

    const first = useBoardStore.getState().confirmBreakdown('T-130');
    expect(useBoardStore.getState().confirming['T-130']).toBe(true);
    const second = useBoardStore.getState().confirmBreakdown('T-130'); // 送信中の再クリック

    resolvePost(jsonResponse(200, { parent, children }));
    await Promise.all([first, second]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('applyTaskUpdated: 未知 id の挿入（#12: 分解直後の子カード出現）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  it('store に無い id の task.updated でもカードを追加しレーンへ挿入する', () => {
    const child = makeChild('T-131', '情報設計・サイトマップ作成', 3, 'ai_work');
    useBoardStore.getState().applyTaskUpdated(child);

    const s = useBoardStore.getState();
    expect(s.cards['T-131']).toEqual(child);
    expect(laneIds('todo')).toEqual(['T-104', 'T-109', 'T-112', 'T-131']);
  });
});

// ---- 学習（蒸留）・ナレッジ（#14: §5.3 learnFrom/adoptLearn/dismissLearn/promoteRule） ----

describe('learn / rule actions（#14）', () => {
  const proposal: RuleProposal = {
    tempId: 'tmp-rule-1',
    taskId: 'T-091',
    text: '確定申告サマリーは控除候補を別セクションで先に提示する',
    scope: 'personal',
    tags: ['経理'],
    confidence: 'med',
  };

  const adopted: Rule = {
    id: 'K-06',
    workspaceId: 'ws-1',
    scope: 'personal',
    ownerUserId: 'user-yk',
    text: proposal.text,
    tags: ['経理'],
    source: 'T-091 から学習',
    confidence: 'med',
    applied: 0,
    createdAt: AT,
    updatedAt: AT,
  };

  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('learnFrom: GET /learn の候補を learn[taskId] へセットし learning を解除する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, [proposal])));

    const pending = useBoardStore.getState().learnFrom('T-091');
    expect(useBoardStore.getState().learning['T-091']).toBe(true); // 実行中はボタン無効化

    await pending;
    const s = useBoardStore.getState();
    expect(fetch).toHaveBeenCalledWith('/api/tasks/T-091/learn', undefined);
    expect(s.learn['T-091']).toEqual([proposal]);
    expect(s.learning['T-091']).toBe(false);
  });

  it('learnFrom: 実行中の再クリックは二重送信しない', async () => {
    let resolveGet!: (value: unknown) => void;
    const fetchMock = vi.fn(() => new Promise((resolve) => (resolveGet = resolve)));
    vi.stubGlobal('fetch', fetchMock);

    const first = useBoardStore.getState().learnFrom('T-091');
    const second = useBoardStore.getState().learnFrom('T-091');
    resolveGet(jsonResponse(200, [proposal]));
    await Promise.all([first, second]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('learnFrom: 409/失敗は boardError を設定し learn は空のまま', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(409, {})));
    await useBoardStore.getState().learnFrom('T-091');
    const s = useBoardStore.getState();
    expect(s.boardError).toBe('ルール候補を生成できませんでした');
    expect(s.learn['T-091']).toBeUndefined();
    expect(s.learning['T-091']).toBe(false);
  });

  it('adoptLearn: POST 応答の Rule を isNew=true で rules へ upsert し候補から除去する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(201, adopted)));
    useBoardStore.setState((s) => ({ learn: { ...s.learn, 'T-091': [proposal] } }));

    await useBoardStore.getState().adoptLearn('T-091', 'tmp-rule-1');

    const s = useBoardStore.getState();
    expect(fetch).toHaveBeenCalledWith(
      '/api/tasks/T-091/learn/adopt',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(s.rules).toHaveLength(6);
    expect(s.rules.at(-1)).toEqual({ ...adopted, isNew: true });
    expect(s.learn['T-091']).toEqual([]);
  });

  it('adoptLearn: SSE の rule.created が先着していても id upsert で重複しない（isNew は立つ）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(201, adopted)));
    useBoardStore.setState((s) => ({ learn: { ...s.learn, 'T-091': [proposal] } }));
    useBoardStore.getState().applyRuleCreated(adopted); // SSE 先着

    await useBoardStore.getState().adoptLearn('T-091', 'tmp-rule-1');

    const s = useBoardStore.getState();
    expect(s.rules).toHaveLength(6);
    expect(s.rules.filter((r) => r.id === 'K-06')).toHaveLength(1);
    expect(s.rules.find((r) => r.id === 'K-06')?.isNew).toBe(true);
  });

  it('adoptLearn: 失敗時は候補を残し boardError を設定する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    useBoardStore.setState((s) => ({ learn: { ...s.learn, 'T-091': [proposal] } }));

    await useBoardStore.getState().adoptLearn('T-091', 'tmp-rule-1');

    const s = useBoardStore.getState();
    expect(s.boardError).toBe('ナレッジへの追加に失敗しました');
    expect(s.rules).toHaveLength(5);
    expect(s.learn['T-091']).toEqual([proposal]); // 残る（再試行できる）
  });

  it('dismissLearn: POST /learn/dismiss 後に候補から除去する（rules は不変）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 204, json: async () => undefined })),
    );
    useBoardStore.setState((s) => ({ learn: { ...s.learn, 'T-091': [proposal] } }));

    await useBoardStore.getState().dismissLearn('T-091', 'tmp-rule-1');

    const s = useBoardStore.getState();
    expect(fetch).toHaveBeenCalledWith(
      '/api/tasks/T-091/learn/dismiss',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(s.learn['T-091']).toEqual([]);
    expect(s.rules).toHaveLength(5);
  });

  it('promoteRule: POST /rules/:id/promote の応答を isNew=true で upsert する（NEW 再表示）', async () => {
    const target = boardFixture().rules.find((r) => r.id === 'K-01');
    if (!target) throw new Error('fixture にルールが無い: K-01');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(200, { ...target, scope: 'team' })),
    );

    await useBoardStore.getState().promoteRule('K-01');

    const s = useBoardStore.getState();
    expect(fetch).toHaveBeenCalledWith('/api/rules/K-01/promote', { method: 'POST' });
    const promoted = s.rules.find((r) => r.id === 'K-01');
    expect(promoted?.scope).toBe('team');
    expect(promoted?.isNew).toBe(true);
    expect(s.rules).toHaveLength(5); // 差し替えのみで増えない
  });

  it('promoteRule: 失敗は boardError 表示のみで scope は変わらない', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(404, {})));
    await useBoardStore.getState().promoteRule('K-01');
    const s = useBoardStore.getState();
    expect(s.boardError).toBe('チームへの昇格に失敗しました');
    expect(s.rules.find((r) => r.id === 'K-01')?.scope).toBe('personal');
  });
});

// ---- ルール適用フラッシュ（#20: justApplied） ----

describe('justApplied（#20 ルール適用フラッシュ）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function fixtureRule(id: string): Rule {
    const rule = useBoardStore.getState().rules.find((r) => r.id === id);
    if (!rule) throw new Error(`fixture にルールが無い: ${id}`);
    return rule;
  }

  it('初期状態は空', () => {
    expect(createInitialBoardState().justApplied).toEqual({});
  });

  it('applyRuleUpdated: applied が増えたルールに適用時刻を記録する', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1111);
    const k01 = fixtureRule('K-01'); // applied 6
    useBoardStore.getState().applyRuleUpdated({ ...k01, applied: k01.applied + 1 });

    const s = useBoardStore.getState();
    expect(s.justApplied['K-01']).toBe(1111);
    // rules 側も upsert で同期される（applied++）
    expect(s.rules.find((r) => r.id === 'K-01')?.applied).toBe(7);
  });

  it('applied が増えない rule.updated（昇格など）ではフラッシュしない', () => {
    const k01 = fixtureRule('K-01');
    useBoardStore.getState().applyRuleUpdated({ ...k01, scope: 'team' });
    expect(useBoardStore.getState().justApplied).toEqual({});
    expect(useBoardStore.getState().rules.find((r) => r.id === 'K-01')?.scope).toBe(
      'team',
    );
  });

  it('ストアに無いルールの rule.updated は upsert のみ（フラッシュなし）', () => {
    const unknown: Rule = { ...fixtureRule('K-01'), id: 'K-99', applied: 1 };
    useBoardStore.getState().applyRuleUpdated(unknown);
    const s = useBoardStore.getState();
    expect(s.justApplied).toEqual({});
    expect(s.rules.find((r) => r.id === 'K-99')).toBeDefined();
  });

  it('再適用のたびに時刻が更新される（アニメ再生のトリガ）', () => {
    const spy = vi.spyOn(Date, 'now').mockReturnValue(1111);
    const k01 = fixtureRule('K-01');
    useBoardStore.getState().applyRuleUpdated({ ...k01, applied: k01.applied + 1 });
    expect(useBoardStore.getState().justApplied['K-01']).toBe(1111);

    spy.mockReturnValue(2222);
    useBoardStore.getState().applyRuleUpdated({ ...k01, applied: k01.applied + 2 });
    expect(useBoardStore.getState().justApplied['K-01']).toBe(2222);
  });
});
