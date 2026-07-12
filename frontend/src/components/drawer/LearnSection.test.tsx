import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { RuleProposalDto } from '../../types/api.ts';
import type { Rule } from '../../types/domain.ts';
import { LearnSection } from './LearnSection';

const AT = '2026-07-07T00:00:00Z';

// T-091（you_review, labels=[経理]）の蒸留候補（Grow.dc.html の learnProposals 準拠）
const PROPOSAL: RuleProposalDto = {
  tempId: 'tmp-rule-1',
  taskId: 'T-091',
  text: '確定申告サマリーは控除候補を別セクションで先に提示する',
  scope: 'personal',
  tags: ['経理'],
  confidence: 'med',
  source: 'T-091 のやり取り',
};

// POST /learn/adopt の応答（K-06〜連番, applied 0）
const ADOPTED: Rule = {
  id: 'K-06',
  workspaceId: 'ws-1',
  scope: 'personal',
  ownerUserId: 'user-yk',
  text: PROPOSAL.text,
  tags: ['経理'],
  source: 'T-091 から学習',
  confidence: 'med',
  applied: 0,
  createdAt: AT,
  updatedAt: AT,
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** 学習セクションが叩く API（learn / adopt / dismiss）を受ける fetch スタブ */
function installFetch({ proposals = [PROPOSAL] }: { proposals?: RuleProposalDto[] } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method === 'GET' && url.endsWith('/learn')) {
      return jsonResponse(200, proposals);
    }
    if (method === 'POST' && url.endsWith('/learn/adopt')) {
      return jsonResponse(201, ADOPTED);
    }
    if (method === 'POST' && url.endsWith('/learn/dismiss')) {
      return jsonResponse(204, undefined);
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function task(id: string) {
  const t = useBoardStore.getState().cards[id];
  if (t === undefined) throw new Error(`fixture にタスクが無い: ${id}`);
  return t;
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('LearnSection: 表示条件（§1.7 / §3.3.2c）', () => {
  it('ai_work のカードでは何も描画しない', () => {
    const { container } = render(<LearnSection task={task('T-098')} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('you_review のカードで説明文と「✧ 学ぶ」を表示する（候補カードはまだ出さない）', () => {
    render(<LearnSection task={task('T-091')} />);
    expect(
      screen.getByText(/このやり取りから/),
    ).toHaveTextContent('このやり取りから働き方のルールを学べます');
    expect(screen.getByRole('button', { name: '✧ 学ぶ' })).toBeEnabled();
    expect(screen.queryByText(/AIが見つけたルール候補/)).toBeNull();
  });

  it('reviewing / done のカードでも表示する', () => {
    for (const id of ['T-089', 'T-080']) {
      const { unmount } = render(<LearnSection task={task(id)} />);
      expect(screen.getByRole('button', { name: '✧ 学ぶ' })).toBeInTheDocument();
      unmount();
    }
  });
});

describe('LearnSection: 学ぶ → 候補提示（§5.3 learnFrom）', () => {
  it('「✧ 学ぶ」で GET /learn を呼び、候補カード（ヘッダ帯＋scopeバッジ＋候補文）を描画する', async () => {
    const fetchMock = installFetch();
    render(<LearnSection task={task('T-091')} />);

    fireEvent.click(screen.getByRole('button', { name: '✧ 学ぶ' }));
    expect(fetchMock).toHaveBeenCalledWith('/api/tasks/T-091/learn', undefined);

    await screen.findByText('AIが見つけたルール候補 — 採用でナレッジに追加');
    expect(screen.getByText(PROPOSAL.text)).toBeInTheDocument();
    const badge = screen.getByText('個人');
    expect(badge).toHaveClass('learn-section__scope--personal');
    expect(screen.getByRole('button', { name: '採用' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '却下' })).toBeInTheDocument();
  });

  it('team scope の候補は「チーム」ミニバッジになる', async () => {
    installFetch({
      proposals: [{ ...PROPOSAL, scope: 'team' }],
    });
    render(<LearnSection task={task('T-091')} />);
    fireEvent.click(screen.getByRole('button', { name: '✧ 学ぶ' }));

    const badge = await screen.findByText('チーム');
    expect(badge).toHaveClass('learn-section__scope--team');
  });

  it('学ぶ実行中はボタンが disabled になり、完了で戻る', async () => {
    let resolve!: (value: unknown) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((r) => (resolve = r))),
    );
    render(<LearnSection task={task('T-091')} />);

    const button = screen.getByRole('button', { name: '✧ 学ぶ' });
    fireEvent.click(button);
    expect(button).toBeDisabled();

    resolve(jsonResponse(200, [PROPOSAL]));
    await waitFor(() => expect(button).toBeEnabled());
  });

  it('409（完了系以外）は boardError を設定し候補は出さない', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(409, {})));
    render(<LearnSection task={task('T-091')} />);
    fireEvent.click(screen.getByRole('button', { name: '✧ 学ぶ' }));

    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe(
        'ルール候補を生成できませんでした',
      ),
    );
    expect(screen.queryByText(/AIが見つけたルール候補/)).toBeNull();
  });
});

describe('LearnSection: 採用 / 却下（§5.3 adoptLearn / dismissLearn）', () => {
  it('採用で POST /learn/adopt に候補内容を送り、rules に isNew 付きで増え、候補行が消える', async () => {
    const fetchMock = installFetch();
    render(<LearnSection task={task('T-091')} />);
    fireEvent.click(screen.getByRole('button', { name: '✧ 学ぶ' }));
    await screen.findByText(PROPOSAL.text);

    fireEvent.click(screen.getByRole('button', { name: '採用' }));

    // POST 内容 = {text, scope, tags, confidence}（§5.3 adoptLearn）
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-091/learn/adopt',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/learn/adopt'),
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      text: PROPOSAL.text,
      scope: 'personal',
      tags: ['経理'],
      confidence: 'med',
    });

    // rules に K-06 が isNew=true で入る（NEW バッジはクライアント状態 — §5.3）
    await waitFor(() => {
      const rule = useBoardStore.getState().rules.find((r) => r.id === 'K-06');
      expect(rule).toBeDefined();
      expect(rule?.isNew).toBe(true);
    });
    expect(useBoardStore.getState().rules).toHaveLength(6);

    // 候補から消え、候補0件になったのでカードごと見出し行のみに戻る（プロト準拠）
    expect(screen.queryByText(PROPOSAL.text)).toBeNull();
    expect(screen.queryByText(/AIが見つけたルール候補/)).toBeNull();
    expect(screen.getByRole('button', { name: '✧ 学ぶ' })).toBeInTheDocument();
  });

  it('却下で POST /learn/dismiss に候補内容を送り、候補行が消える（rules は増えない）', async () => {
    const fetchMock = installFetch();
    render(<LearnSection task={task('T-091')} />);
    fireEvent.click(screen.getByRole('button', { name: '✧ 学ぶ' }));
    await screen.findByText(PROPOSAL.text);

    fireEvent.click(screen.getByRole('button', { name: '却下' }));

    await waitFor(() => expect(screen.queryByText(PROPOSAL.text)).toBeNull());
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/learn/dismiss'),
    );
    expect(call?.[1]?.method).toBe('POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      text: PROPOSAL.text,
      scope: 'personal',
      tags: ['経理'],
      confidence: 'med',
    });
    expect(useBoardStore.getState().rules).toHaveLength(5);
    expect(useBoardStore.getState().learn['T-091']).toEqual([]);
  });
});
