import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Rule } from '../../types/domain.ts';
import { KNOWLEDGE_EMPTY_TEXT, KnowledgeOverlay } from './KnowledgeOverlay';

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function rule(id: string): Rule {
  const r = useBoardStore.getState().rules.find((x) => x.id === id);
  if (r === undefined) throw new Error(`fixture にルールが無い: ${id}`);
  return r;
}

beforeEach(() => {
  useBoardStore.setState({
    ...createInitialBoardState(),
    ...boardFixture(),
    showKnowledge: true,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('KnowledgeOverlay: 表示（§3.4）', () => {
  it('showKnowledge=false では何も描画しない', () => {
    useBoardStore.setState({ showKnowledge: false });
    const { container } = render(<KnowledgeOverlay />);
    expect(container).toBeEmptyDOMElement();
  });

  it('ヘッダ（タイトル・説明文・閉じる✕）を表示する', () => {
    render(<KnowledgeOverlay />);
    expect(screen.getByText('ナレッジ — 学習した働き方')).toBeInTheDocument();
    expect(screen.getByText(/AIは作業を始める前に/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '閉じる' })).toBeInTheDocument();
  });

  it('personal は「あなたのルール」、team は「チームのルール（形式知）」へ振り分ける', () => {
    const { container } = render(<KnowledgeOverlay />);
    expect(screen.getByText('あなたのルール')).toBeInTheDocument();
    expect(screen.getByText('チームのルール（形式知）')).toBeInTheDocument();

    // fixture: personal = K-01..K-03（3件）/ team = K-04, K-05（2件）
    const counts = [...container.querySelectorAll('.knowledge__section-count')];
    expect(counts.map((el) => el.textContent)).toEqual(['3', '2']);
    const teamCards = [...container.querySelectorAll('.knowledge__rule--team')];
    expect(teamCards).toHaveLength(2);
    expect(screen.getByText(rule('K-01').text)).toBeInTheDocument();
    expect(screen.getByText(rule('K-04').text)).toBeInTheDocument();
  });

  it('確度ドット（high緑/med琥珀/low灰）をルールごとに出す', () => {
    useBoardStore.setState({
      rules: [
        { ...rule('K-01'), confidence: 'high' },
        { ...rule('K-03'), confidence: 'med' },
        { ...rule('K-02'), id: 'K-09', confidence: 'low' },
      ],
    });
    const { container } = render(<KnowledgeOverlay />);
    expect(container.querySelectorAll('.knowledge__conf--high')).toHaveLength(1);
    expect(container.querySelectorAll('.knowledge__conf--med')).toHaveLength(1);
    expect(container.querySelectorAll('.knowledge__conf--low')).toHaveLength(1);
  });

  it('下段に「出典: {source}」と「適用 {N}回」を表示する', () => {
    render(<KnowledgeOverlay />);
    expect(screen.getByText('出典: T-098 で2回同じ修正')).toBeInTheDocument();
    expect(screen.getByText('適用 6回')).toBeInTheDocument(); // K-01
    expect(screen.getByText('適用 9回')).toBeInTheDocument(); // K-04（team）
  });

  it('isNew=true のルールにだけ NEW バッジを出す', () => {
    useBoardStore.setState((s) => ({
      rules: s.rules.map((r) => (r.id === 'K-03' ? { ...r, isNew: true } : r)),
    }));
    const { container } = render(<KnowledgeOverlay />);
    expect(container.querySelectorAll('.knowledge__new')).toHaveLength(1);
    expect(screen.getByText('NEW')).toBeInTheDocument();
  });

  it('rules が空なら両セクションに空状態文言を出す（§5.5）', () => {
    useBoardStore.setState({ rules: [] });
    render(<KnowledgeOverlay />);
    expect(screen.getAllByText(KNOWLEDGE_EMPTY_TEXT)).toHaveLength(2);
  });
});

describe('KnowledgeOverlay: 閉じる（§5.3 openKnowledge/closeKnowledge/stop）', () => {
  it('遮蔽クリックで閉じる', () => {
    const { container } = render(<KnowledgeOverlay />);
    fireEvent.click(container.querySelector('.knowledge')!);
    expect(useBoardStore.getState().showKnowledge).toBe(false);
    expect(container).toBeEmptyDOMElement();
  });

  it('パネル内側のクリックでは閉じない（stopPropagation）', () => {
    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('dialog'));
    expect(useBoardStore.getState().showKnowledge).toBe(true);
  });

  it('✕ ボタンで閉じる', () => {
    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('button', { name: '閉じる' }));
    expect(useBoardStore.getState().showKnowledge).toBe(false);
  });
});

describe('KnowledgeOverlay: チームへ昇格（§1.8 promoteRule）', () => {
  it('昇格ボタンは personal のルールにだけ出る', () => {
    const { container } = render(<KnowledgeOverlay />);
    const buttons = screen.getAllByRole('button', { name: 'チームへ昇格 ↑' });
    expect(buttons).toHaveLength(3); // personal K-01..K-03 のみ
    for (const card of container.querySelectorAll('.knowledge__rule--team')) {
      expect(card.querySelector('.knowledge__promote')).toBeNull();
    }
  });

  it('昇格で POST /rules/:id/promote → scope=team になりチームのセクションへ移動（NEW 再表示）', async () => {
    const promoted: Rule = { ...rule('K-01'), scope: 'team' };
    const fetchMock = vi.fn(async () => jsonResponse(200, promoted));
    vi.stubGlobal('fetch', fetchMock);

    const { container } = render(<KnowledgeOverlay />);
    // 1つ目の昇格ボタン = personal 先頭 K-01
    fireEvent.click(screen.getAllByRole('button', { name: 'チームへ昇格 ↑' })[0]);

    expect(fetchMock).toHaveBeenCalledWith('/api/rules/K-01/promote', {
      method: 'POST',
    });
    await waitFor(() => {
      const counts = [...container.querySelectorAll('.knowledge__section-count')];
      expect(counts.map((el) => el.textContent)).toEqual(['2', '3']);
    });
    // K-01 のカードが team 側スタイルになり、NEW が再表示される
    const updated = useBoardStore.getState().rules.find((r) => r.id === 'K-01');
    expect(updated?.scope).toBe('team');
    expect(updated?.isNew).toBe(true);
    expect(screen.getByText('NEW')).toBeInTheDocument();
    expect(container.querySelectorAll('.knowledge__rule--team')).toHaveLength(3);
  });
});
