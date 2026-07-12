import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import { LiveDraftSection } from './LiveDraftSection';

/** T-098（ai_work）で liveDraft をセットして描画する */
function renderSection(taskId = 'T-098', draft?: string) {
  if (draft !== undefined) {
    useBoardStore.setState((s) => ({
      liveDraft: { ...s.liveDraft, [taskId]: draft },
    }));
  }
  const task = useBoardStore.getState().cards[taskId];
  return render(<LiveDraftSection task={task} />);
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
});

describe('LiveDraftSection（#24 ライブ実況）', () => {
  it('liveDraft が無ければ何も描画しない', () => {
    const { container } = renderSection('T-098');
    expect(container).toBeEmptyDOMElement();
  });

  it('ai_work 中に liveDraft の Markdown と点滅カーソル「▍」をライブ描画する', () => {
    renderSection('T-098', '# 競合調査レポート\n\n生成中の本文');
    expect(screen.getByText('成果物（レポート）')).toBeInTheDocument();
    expect(screen.getByText('生成中…')).toBeInTheDocument();
    // Markdown がレンダリングされる（# → h1）
    expect(
      screen.getByRole('heading', { name: '競合調査レポート' }),
    ).toBeInTheDocument();
    expect(screen.getByText('生成中の本文')).toBeInTheDocument();
    expect(screen.getByText('▍')).toBeInTheDocument();
  });

  it('増分の追記（store 更新）に描画が追従する', () => {
    renderSection('T-098', '# 見出し');
    act(() => {
      useBoardStore.setState((s) => ({
        liveDraft: { ...s.liveDraft, 'T-098': '# 見出し\n\n追記された段落' },
      }));
    });
    expect(screen.getByText('追記された段落')).toBeInTheDocument();
  });

  it('ai_work 以外のタスクでは liveDraft があっても描画しない', () => {
    // T-091 は you_review（実況対象外。残骸があっても出さない）
    const { container } = renderSection('T-091', '# 残骸');
    expect(container).toBeEmptyDOMElement();
  });

  it('GFM の比較表もレンダリングする（確定版プレビューと同条件）', () => {
    renderSection(
      'T-098',
      '| 評価軸 | 候補 A |\n| --- | --- |\n| 機能 | ◎ |',
    );
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('評価軸')).toBeInTheDocument();
  });
});
