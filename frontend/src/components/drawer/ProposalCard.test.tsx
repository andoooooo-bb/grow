import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { SubtaskProposal } from '../../types/api.ts';
import type { Task } from '../../types/domain.ts';
import { ProposalCard } from './ProposalCard';

/** mock provider（SUBTASKS_T130）と同じ 5 件の分解候補（ai3 / human2） */
function proposalFixture(): SubtaskProposal[] {
  return [
    { title: '情報設計・サイトマップ作成', owner: 'ai' },
    { title: 'ワイヤーフレーム作成', owner: 'ai' },
    { title: '掲載する実績コンテンツの選定', owner: 'human', rationale: '本人の意思決定が必要' },
    { title: 'デザイン方向性の決定', owner: 'human', rationale: '好みの判断は人が行う' },
    { title: 'コーディング・実装', owner: 'ai' },
  ];
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

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** proposal を積んで T-130 の ProposalCard を描画する */
function renderCard(proposal: SubtaskProposal[] = proposalFixture()) {
  useBoardStore.setState((s) => ({
    panelMode: 'chat',
    proposal: { ...s.proposal, 'T-130': proposal },
  }));
  return render(<ProposalCard taskId="T-130" />);
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ProposalCard: 候補表示（§3.3.3）', () => {
  it('proposal が無ければ何も描画しない', () => {
    const { container } = render(<ProposalCard taskId="T-130" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('ヘッダ「提案された分解 — 5 件」と候補5件（担当ミニバッジ AI×3 / あなた×2）を表示する', () => {
    const { container } = renderCard();

    expect(screen.getByText('提案された分解 — 5 件')).toBeInTheDocument();
    // 候補の名称（生成順）
    const rows = container.querySelectorAll('.proposal-card__row');
    expect(rows).toHaveLength(5);
    expect(screen.getByText('情報設計・サイトマップ作成')).toBeInTheDocument();
    expect(screen.getByText('ワイヤーフレーム作成')).toBeInTheDocument();
    expect(screen.getByText('掲載する実績コンテンツの選定')).toBeInTheDocument();
    expect(screen.getByText('デザイン方向性の決定')).toBeInTheDocument();
    expect(screen.getByText('コーディング・実装')).toBeInTheDocument();
    // 担当ミニバッジ: ai=「AI」ティール系 / human=「あなた」アンバー系（クラスで判別）
    expect(container.querySelectorAll('.proposal-card__owner--ai')).toHaveLength(3);
    expect(container.querySelectorAll('.proposal-card__owner--human')).toHaveLength(2);
    // プライマリボタンと補足文
    expect(
      screen.getByRole('button', { name: 'この内容でボードに反映する' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('反映後、AIが着手できるサブタスクから自動で進めます'),
    ).toBeInTheDocument();
  });
});

describe('ProposalCard: 反映（§1.6 step5 / §5.3 confirmBreakdown）', () => {
  const children = [
    makeChild('T-131', '情報設計・サイトマップ作成', 3, 'ai_work'),
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
  };

  it('「この内容でボードに反映する」で confirmBreakdown（POST /breakdown/confirm {subtasks}）を呼ぶ', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, { parent, children }),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderCard();

    fireEvent.click(
      screen.getByRole('button', { name: 'この内容でボードに反映する' }),
    );

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-130/breakdown/confirm',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    // body は候補の title/owner のみ（rationale は表示専用なので送らない）
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      subtasks: [
        { title: '情報設計・サイトマップ作成', owner: 'ai' },
        { title: 'ワイヤーフレーム作成', owner: 'ai' },
        { title: '掲載する実績コンテンツの選定', owner: 'human' },
        { title: 'デザイン方向性の決定', owner: 'human' },
        { title: 'コーディング・実装', owner: 'ai' },
      ],
    });
    // 反映成功: proposal クリア＋detail 復帰（§5.3）→ カード自体が消える
    await waitFor(() =>
      expect(useBoardStore.getState().proposal['T-130']).toBeUndefined(),
    );
    expect(useBoardStore.getState().panelMode).toBe('detail');
    expect(screen.queryByText(/提案された分解/)).toBeNull();
  });

  it('confirm 送信中はボタンが disabled になる', async () => {
    let resolvePost!: (value: unknown) => void;
    const fetchMock = vi.fn(() => new Promise((resolve) => (resolvePost = resolve)));
    vi.stubGlobal('fetch', fetchMock);
    renderCard();

    const button = screen.getByRole('button', {
      name: 'この内容でボードに反映する',
    });
    expect(button).toBeEnabled();
    fireEvent.click(button);

    // 応答待ちの間は無効化（confirming フラグ, 二重送信防止）
    await waitFor(() => expect(button).toBeDisabled());

    resolvePost(jsonResponse(200, { parent, children }));
    await waitFor(() =>
      expect(useBoardStore.getState().confirming['T-130']).toBe(false),
    );
  });

  it('confirm 失敗（409 等）では候補が残り、再試行できる（§5.4）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(409, {})));
    renderCard();

    fireEvent.click(
      screen.getByRole('button', { name: 'この内容でボードに反映する' }),
    );

    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe('ボードへの反映に失敗しました'),
    );
    // 候補は残り（proposal 維持）、ボタンは再度有効
    expect(screen.getByText('提案された分解 — 5 件')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'この内容でボードに反映する' }),
    ).toBeEnabled();
    expect(useBoardStore.getState().panelMode).toBe('chat');
  });
});
