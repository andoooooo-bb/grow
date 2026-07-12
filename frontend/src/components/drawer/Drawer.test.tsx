import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Comment } from '../../types/domain.ts';
import { Drawer } from './Drawer';

const AT = '2026-07-07T00:00:00Z';

function makeComment(partial: Partial<Comment> & Pick<Comment, 'id' | 'text'>): Comment {
  return { taskId: 'T-098', author: 'human', createdAt: AT, ...partial };
}

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

interface FetchStubOptions {
  /** GET /api/tasks/:id/comments の応答（省略時は []） */
  comments?: Comment[] | (() => unknown);
  /** POST /api/tasks/:id/comments の応答（省略時は 201 でサーバ確定版を返す） */
  post?: (body: { author: string; text: string }) => unknown;
  /** POST /api/tasks/:id/assign-ai の応答（省略時は 202 {jobId}） */
  assignAi?: () => unknown;
  /** POST /api/tasks/:id/autopilot の応答（省略時は 202 {jobId}。#22 指揮者AI） */
  autopilot?: () => unknown;
  /** POST /api/tasks/:id/reject の応答（省略時は 202 {jobId}。#23 構造化差し戻し） */
  reject?: (body: { reason: string }) => unknown;
}

/** ドロワーが叩く API（コメント・成果物・assign-ai）を受ける fetch スタブを差し込む */
function installFetch({
  comments = [],
  post,
  assignAi,
  autopilot,
  reject,
}: FetchStubOptions = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method === 'GET' && url.endsWith('/comments')) {
      return typeof comments === 'function'
        ? comments()
        : jsonResponse(200, comments);
    }
    if (method === 'POST' && url.endsWith('/comments')) {
      const body = JSON.parse(String(init?.body)) as { author: string; text: string };
      if (post) return post(body);
      return jsonResponse(
        201,
        makeComment({ id: 'c-server-1', author: 'human', text: body.text }),
      );
    }
    if (method === 'GET' && url.endsWith('/artifacts')) {
      // #10: ドロワーを開くと loadArtifacts が呼ぶ（既定は成果物なし）
      const taskId = url.split('/').at(-2) ?? '';
      return jsonResponse(200, { taskId, artifacts: [] });
    }
    if (method === 'POST' && url.endsWith('/assign-ai')) {
      if (assignAi) return assignAi();
      return jsonResponse(202, { jobId: 'job-1' });
    }
    if (method === 'POST' && url.endsWith('/autopilot')) {
      if (autopilot) return autopilot();
      return jsonResponse(202, { jobId: 'job-2' });
    }
    if (method === 'POST' && url.endsWith('/reject')) {
      const body = JSON.parse(String(init?.body)) as { reason: string };
      if (reject) return reject(body);
      return jsonResponse(202, { jobId: 'job-3' });
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** 初期読込（GET /comments）の反映まで待つ */
async function waitForLoaded(taskId: string) {
  await waitFor(() =>
    expect(useBoardStore.getState().comments[taskId]).toBeDefined(),
  );
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Drawer: detail モード表示（§3.3）', () => {
  it('ヘッダに「{ID} · {レーン名}」・タイトル・ステータスバッジ(lg)・担当を表示する', async () => {
    installFetch();
    useBoardStore.getState().select('T-098'); // ai_work / progress
    const { container } = render(<Drawer />);

    expect(screen.getByText('T-098 · 進行中')).toBeInTheDocument();
    expect(screen.getByText('競合調査レポートの下書き')).toBeInTheDocument();
    const badge = container.querySelector('.status-badge');
    expect(badge).toHaveClass('status-badge--lg');
    expect(badge).toHaveClass('status-badge--work');
    expect(screen.getByText('AI作業中')).toBeInTheDocument();
    expect(screen.getByText('担当: Grow (AI)')).toBeInTheDocument();
    await waitForLoaded('T-098');
  });

  it('owner が human のタスクは「担当: あなた」を表示する', async () => {
    installFetch();
    useBoardStore.getState().select('T-109'); // you_todo (owner=human)
    render(<Drawer />);

    expect(screen.getByText('T-109 · ToDo')).toBeInTheDocument();
    expect(screen.getByText('担当: あなた')).toBeInTheDocument();
    await waitForLoaded('T-109');
  });

  it('アクションバーに3ボタン、スレッド見出し、コンポーザを表示する（§3.3.2 a/e/f）', async () => {
    installFetch();
    useBoardStore.getState().select('T-098');
    render(<Drawer />);

    expect(screen.getByRole('button', { name: 'AIにまかせる' })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'AIと壁打ち / 分解' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '完了にする' })).toBeInTheDocument();
    expect(screen.getByText('アクティビティ')).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText('コメントで依頼・指示を残す…'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '送信' })).toBeInTheDocument();
    await waitForLoaded('T-098');
  });

  it('閉じる✕で closePanel（selectedId=null）になる（§5.3）', async () => {
    installFetch();
    useBoardStore.getState().select('T-098');
    render(<Drawer />);

    fireEvent.click(screen.getByRole('button', { name: '閉じる' }));
    expect(useBoardStore.getState().selectedId).toBeNull();
    await waitForLoaded('T-098');
  });

  it('開いたら GET /tasks/:id/comments でスレッドを読み込み、名義とバブルを表示する', async () => {
    const fetchMock = installFetch({
      comments: [
        makeComment({ id: 'c-1', author: 'ai', text: '承知しました。着手します。' }),
        makeComment({ id: 'c-2', author: 'human', text: '出典URLもお願いします' }),
      ],
    });
    useBoardStore.getState().select('T-098');
    render(<Drawer />);

    expect(fetchMock).toHaveBeenCalledWith('/api/tasks/T-098/comments', undefined);
    // ai=名義「Grow」/ human=名義「あなた」（§3.3.2e）
    expect(await screen.findByText('承知しました。着手します。')).toBeInTheDocument();
    expect(screen.getByText('Grow')).toBeInTheDocument();
    expect(screen.getByText('出典URLもお願いします')).toBeInTheDocument();
    expect(screen.getByText('あなた')).toBeInTheDocument();
  });

  it('スレッド読込に失敗したら簡易エラーを表示する', async () => {
    installFetch({ comments: () => jsonResponse(500, {}) });
    useBoardStore.getState().select('T-098');
    render(<Drawer />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'コメントの読み込みに失敗しました',
    );
  });
});

describe('Composer: Enter=送信 / Shift+Enter=改行（§00 #11）', () => {
  it('Enter で送信し、入力をクリアして POST する', async () => {
    const fetchMock = installFetch();
    useBoardStore.getState().select('T-098');
    render(<Drawer />);
    await waitForLoaded('T-098');

    const input = screen.getByPlaceholderText('コメントで依頼・指示を残す…');
    fireEvent.change(input, { target: { value: '比較表を追加してください' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    // 楽観的更新: 即UI反映＋入力クリア
    expect(screen.getByText('比較表を追加してください')).toBeInTheDocument();
    expect(input).toHaveValue('');

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-098/comments',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      author: 'human',
      text: '比較表を追加してください',
    });
  });

  it('Shift+Enter は送信せず改行（入力は保持される）', async () => {
    const fetchMock = installFetch();
    useBoardStore.getState().select('T-098');
    render(<Drawer />);
    await waitForLoaded('T-098');

    const input = screen.getByPlaceholderText('コメントで依頼・指示を残す…');
    fireEvent.change(input, { target: { value: '1行目' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });

    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/tasks/T-098/comments',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(input).toHaveValue('1行目');
    // ブラウザのデフォルト動作（改行挿入）を模して2行になっても送信されない
    fireEvent.change(input, { target: { value: '1行目\n2行目' } });
    expect(useBoardStore.getState().drafts['T-098']).toBe('1行目\n2行目');
  });

  it('空文字・空白のみでは送信しない', async () => {
    const fetchMock = installFetch();
    useBoardStore.getState().select('T-098');
    render(<Drawer />);
    await waitForLoaded('T-098');

    const input = screen.getByPlaceholderText('コメントで依頼・指示を残す…');
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: '送信' }));

    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/tasks/T-098/comments',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('markDone 結線（#8: §5.3）', () => {
  it('「完了にする」で PATCH {status,laneKey,progress} を送り、done・完了レーンへ反映する', async () => {
    // T-109（todo, you_todo）: you_todo→done は許可遷移
    const updated = {
      ...boardFixture().cards['T-109'],
      status: 'done',
      laneKey: 'done',
      orderInLane: 2,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'GET' && url.endsWith('/comments')) return jsonResponse(200, []);
      if (method === 'PATCH' && url === '/api/tasks/T-109') {
        return jsonResponse(200, updated);
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    useBoardStore.getState().select('T-109');
    render(<Drawer />);
    await waitForLoaded('T-109');

    fireEvent.click(screen.getByRole('button', { name: '完了にする' }));

    await waitFor(() =>
      expect(useBoardStore.getState().cards['T-109'].status).toBe('done'),
    );
    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      status: 'done',
      laneKey: 'done',
      progress: null,
    });
    const doneLane = useBoardStore
      .getState()
      .lanes.find((lane) => lane.key === 'done');
    expect(doneLane?.cardIds).toEqual(['T-080', 'T-077', 'T-109']);
  });

  it('done カードでは「完了にする」ボタンを表示しない（§03）', async () => {
    installFetch();
    useBoardStore.getState().select('T-080'); // done
    render(<Drawer />);

    expect(screen.queryByRole('button', { name: '完了にする' })).toBeNull();
    // 他の2ボタンは表示される
    expect(screen.getByRole('button', { name: 'AIにまかせる' })).toBeInTheDocument();
    await waitForLoaded('T-080');
  });
});

describe('楽観的更新とロールバック（§5.4）', () => {
  it('送信直後に即UI反映され、API 成功でサーバ確定版に差し替わる', async () => {
    let resolvePost!: (value: unknown) => void;
    installFetch({ post: () => new Promise((resolve) => (resolvePost = resolve)) });
    useBoardStore.getState().select('T-098');
    render(<Drawer />);
    await waitForLoaded('T-098');

    const input = screen.getByPlaceholderText('コメントで依頼・指示を残す…');
    fireEvent.change(input, { target: { value: 'ありがとうございます' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    // API 応答前から表示され、commentCount も +1（§5.4 楽観的更新）
    expect(screen.getByText('ありがとうございます')).toBeInTheDocument();
    expect(useBoardStore.getState().comments['T-098'][0].id).toMatch(/^tmp-/);
    expect(useBoardStore.getState().cards['T-098'].commentCount).toBe(1);

    resolvePost(
      jsonResponse(
        201,
        makeComment({ id: 'c-server-9', text: 'ありがとうございます' }),
      ),
    );
    await waitFor(() =>
      expect(useBoardStore.getState().comments['T-098'][0].id).toBe('c-server-9'),
    );
    // 差し替えなので件数は増えない
    expect(useBoardStore.getState().comments['T-098']).toHaveLength(1);
    expect(screen.getByText('ありがとうございます')).toBeInTheDocument();
  });

  it('API 失敗でロールバックし、簡易エラーを表示する', async () => {
    installFetch({ post: () => jsonResponse(500, {}) });
    useBoardStore.getState().select('T-098');
    render(<Drawer />);
    await waitForLoaded('T-098');

    const input = screen.getByPlaceholderText('コメントで依頼・指示を残す…');
    fireEvent.change(input, { target: { value: '失敗するコメント' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByText('失敗するコメント')).toBeInTheDocument();

    // ロールバック: コメント除去＋commentCount 復元＋エラー表示（§5.4）
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'コメントの送信に失敗しました',
    );
    expect(screen.queryByText('失敗するコメント')).toBeNull();
    expect(useBoardStore.getState().comments['T-098']).toHaveLength(0);
    expect(useBoardStore.getState().cards['T-098'].commentCount).toBe(0);
  });
});

describe('assignAi 結線と活性制御（#10: §5.3 / Grow.dc.html）', () => {
  it('「AIにまかせる」で POST /api/tasks/:id/assign-ai を呼ぶ', async () => {
    const fetchMock = installFetch();
    useBoardStore.getState().select('T-104'); // spec → ai_work は許可遷移
    render(<Drawer />);
    await waitForLoaded('T-104');

    fireEvent.click(screen.getByRole('button', { name: 'AIにまかせる' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-104/assign-ai',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    // 202 後はローカルで状態を変えない（SSE 反映待ち）
    expect(useBoardStore.getState().cards['T-104'].status).toBe('spec');
  });

  it('ai_work のカードでは「AIにまかせる」が無効', async () => {
    installFetch();
    useBoardStore.getState().select('T-098'); // ai_work
    render(<Drawer />);

    expect(screen.getByRole('button', { name: 'AIにまかせる' })).toBeDisabled();
    await waitForLoaded('T-098');
  });

  it('done のカードでは「AIにまかせる」が無効', async () => {
    installFetch();
    useBoardStore.getState().select('T-080'); // done
    render(<Drawer />);

    expect(screen.getByRole('button', { name: 'AIにまかせる' })).toBeDisabled();
    await waitForLoaded('T-080');
  });

  it('assign-ai の 409 で boardError を設定する', async () => {
    installFetch({ assignAi: () => jsonResponse(409, {}) });
    useBoardStore.getState().select('T-104');
    render(<Drawer />);
    await waitForLoaded('T-104');

    fireEvent.click(screen.getByRole('button', { name: 'AIにまかせる' }));

    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe('AIにまかせられませんでした'),
    );
  });
});

describe('autopilot 結線と活性制御（#22: 指揮者AI）', () => {
  it('「オートパイロット」で POST /api/tasks/:id/autopilot を呼ぶ', async () => {
    const fetchMock = installFetch();
    useBoardStore.getState().select('T-104'); // spec（assign-ai と同条件で活性）
    render(<Drawer />);
    await waitForLoaded('T-104');

    fireEvent.click(screen.getByRole('button', { name: 'オートパイロット' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-104/autopilot',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    // 202 後はローカルで状態を変えない（判断・遷移はすべて SSE 反映待ち）
    expect(useBoardStore.getState().cards['T-104'].status).toBe('spec');
  });

  it('ai_work のカードでは「オートパイロット」が無効', async () => {
    installFetch();
    useBoardStore.getState().select('T-098'); // ai_work
    render(<Drawer />);

    expect(screen.getByRole('button', { name: 'オートパイロット' })).toBeDisabled();
    await waitForLoaded('T-098');
  });

  it('L0（計画のみ #21）では無効＋ツールチップ「L0では提案のみです」', async () => {
    installFetch();
    const s = useBoardStore.getState();
    s.select('T-104');
    useBoardStore.setState({
      cards: { ...s.cards, 'T-104': { ...s.cards['T-104'], autonomy: 'L0' } },
    });
    render(<Drawer />);

    const button = screen.getByRole('button', { name: 'オートパイロット' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'L0では提案のみです');
    // 「AIにまかせる」（L0 の提案フロー）は引き続き使える
    expect(screen.getByRole('button', { name: 'AIにまかせる' })).toBeEnabled();
    await waitForLoaded('T-104');
  });

  it('autopilot の 409 で boardError を設定する', async () => {
    installFetch({ autopilot: () => jsonResponse(409, {}) });
    useBoardStore.getState().select('T-104');
    render(<Drawer />);
    await waitForLoaded('T-104');

    fireEvent.click(screen.getByRole('button', { name: 'オートパイロット' }));

    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe(
        'オートパイロットを開始できませんでした',
      ),
    );
  });
});

describe('reject 結線と理由入力（#23: 構造化差し戻し）', () => {
  it('you_review のカードに「差し戻す」を表示し、理由を付けて POST /reject を呼ぶ', async () => {
    const fetchMock = installFetch();
    useBoardStore.getState().select('T-091'); // you_review
    render(<Drawer />);
    await waitForLoaded('T-091');

    // ボタンで理由フォームが展開する
    fireEvent.click(screen.getByRole('button', { name: '差し戻す' }));
    const input = screen.getByLabelText('差し戻し理由');

    // 理由が空のあいだは送信できない（理由必須）
    expect(
      screen.getByRole('button', { name: '理由を付けて差し戻す' }),
    ).toBeDisabled();

    fireEvent.change(input, { target: { value: '出典URLを追記してください' } });
    fireEvent.click(screen.getByRole('button', { name: '理由を付けて差し戻す' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-091/reject',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ reason: '出典URLを追記してください' }),
        }),
      ),
    );
    // 202 後はローカルで状態を変えない（理由コメント・ai_work 化は SSE 反映待ち）
    expect(useBoardStore.getState().cards['T-091'].status).toBe('you_review');
    // 送信後はフォームが畳まれる
    expect(screen.queryByLabelText('差し戻し理由')).not.toBeInTheDocument();
  });

  it('reviewing のカードにも「差し戻す」を表示する', async () => {
    installFetch();
    useBoardStore.getState().select('T-089'); // reviewing
    render(<Drawer />);

    expect(screen.getByRole('button', { name: '差し戻す' })).toBeInTheDocument();
    await waitForLoaded('T-089');
  });

  it('レビュー局面以外（you_todo / done）では「差し戻す」を出さない', async () => {
    installFetch();
    useBoardStore.getState().select('T-109'); // you_todo
    render(<Drawer />);
    expect(screen.queryByRole('button', { name: '差し戻す' })).not.toBeInTheDocument();
    await waitForLoaded('T-109');
    cleanup();

    installFetch();
    useBoardStore.getState().select('T-080'); // done
    render(<Drawer />);
    expect(screen.queryByRole('button', { name: '差し戻す' })).not.toBeInTheDocument();
    await waitForLoaded('T-080');
  });

  it('キャンセルでフォームを畳む（API は呼ばない）', async () => {
    const fetchMock = installFetch();
    useBoardStore.getState().select('T-091');
    render(<Drawer />);
    await waitForLoaded('T-091');

    fireEvent.click(screen.getByRole('button', { name: '差し戻す' }));
    fireEvent.click(screen.getByRole('button', { name: 'キャンセル' }));

    expect(screen.queryByLabelText('差し戻し理由')).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/tasks/T-091/reject',
      expect.anything(),
    );
  });

  it('reject の 409 で boardError を設定する', async () => {
    installFetch({ reject: () => jsonResponse(409, {}) });
    useBoardStore.getState().select('T-091');
    render(<Drawer />);
    await waitForLoaded('T-091');

    fireEvent.click(screen.getByRole('button', { name: '差し戻す' }));
    fireEvent.change(screen.getByLabelText('差し戻し理由'), {
      target: { value: '直してください' },
    });
    fireEvent.click(screen.getByRole('button', { name: '理由を付けて差し戻す' }));

    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe('差し戻しできませんでした'),
    );
  });
});

describe('適用ルール・成果物セクションの組み込み（#10: §3.3.2 b / c-2）', () => {
  it('該当ルールがあるカードで適用ルールセクションを表示する（アクションバー直下）', async () => {
    installFetch();
    useBoardStore.getState().select('T-104'); // 仕事,調査 → 4件
    const { container } = render(<Drawer />);

    expect(screen.getByText('◈ AIが着手時に前提にするルール')).toBeInTheDocument();
    // 配置: アクションバーの直後（§3.3.2 (b) の位置）
    const detail = container.querySelector('.drawer__detail');
    expect(detail?.children[0]).toHaveClass('action-bar');
    expect(detail?.children[1]).toHaveClass('applied-rules');
    await waitForLoaded('T-104');
  });

  it('retrieval 0件なら適用ルールセクションを出さない（§5.5）', async () => {
    useBoardStore.setState({ rules: [] });
    installFetch();
    useBoardStore.getState().select('T-104');
    render(<Drawer />);

    expect(screen.queryByText('◈ AIが着手時に前提にするルール')).toBeNull();
    await waitForLoaded('T-104');
  });

  it('ドロワーを開くと GET /tasks/:id/artifacts で成果物を読み込み、あれば表示する', async () => {
    const artifact = {
      id: 'a-1',
      taskId: 'T-091',
      version: 1,
      contentMd: '# 確定申告サマリー',
      createdAt: AT,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'GET' && url.endsWith('/comments')) return jsonResponse(200, []);
      if (method === 'GET' && url.endsWith('/artifacts')) {
        return jsonResponse(200, { taskId: 'T-091', artifacts: [artifact] });
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    useBoardStore.getState().select('T-091');
    render(<Drawer />);

    expect(fetchMock).toHaveBeenCalledWith('/api/tasks/T-091/artifacts', undefined);
    expect(await screen.findByText('成果物（レポート）')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '確定申告サマリー' }),
    ).toBeInTheDocument();
  });

  it('成果物が無ければ成果物セクションを出さない（§3.3.2 c-2）', async () => {
    installFetch();
    useBoardStore.getState().select('T-104');
    render(<Drawer />);
    await waitForLoaded('T-104');

    expect(screen.queryByText('成果物（レポート）')).toBeNull();
  });
});
