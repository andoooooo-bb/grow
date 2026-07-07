import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import { TopBar } from './TopBar';

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(cleanup);

describe('TopBar（§3.1）', () => {
  it('派生カウンタ（あなたの番 6 / AI稼働 3 / ナレッジ 5）を表示する', () => {
    render(<TopBar />);
    expect(screen.getByText('あなたの番 6')).toBeInTheDocument();
    expect(screen.getByText('AI稼働 3')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '◈ ナレッジ 5' }),
    ).toBeInTheDocument();
  });

  it('ロゴ・ワークスペース表示・アバターYK を表示する', () => {
    render(<TopBar />);
    expect(screen.getByText('Grow')).toBeInTheDocument();
    expect(screen.getByText('workspace / 個人')).toBeInTheDocument();
    expect(screen.getByText('YK')).toBeInTheDocument();
  });

  it('「◈ ナレッジ」クリックで showKnowledge が true になる（§5.3）', () => {
    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: '◈ ナレッジ 5' }));
    expect(useBoardStore.getState().showKnowledge).toBe(true);
  });

  it('カードが空なら カウンタは 0 を表示する', () => {
    useBoardStore.setState(createInitialBoardState());
    render(<TopBar />);
    expect(screen.getByText('あなたの番 0')).toBeInTheDocument();
    expect(screen.getByText('AI稼働 0')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '◈ ナレッジ 0' }),
    ).toBeInTheDocument();
  });
});
