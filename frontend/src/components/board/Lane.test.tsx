import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import { Lane } from './Lane';

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(cleanup);

describe('Lane（§3.2）', () => {
  it('ヘッダにレーン名と件数ピルを表示し、cardIds 順にカードを描画する', () => {
    const todoLane = useBoardStore.getState().lanes[1]; // todo: 3件
    render(<Lane lane={todoLane} />);

    expect(screen.getByText('ToDo')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('競合SaaS 5社の料金プランを調査')).toBeInTheDocument();
    expect(screen.getByText('週次レビューのテンプレートを記入')).toBeInTheDocument();
    expect(screen.getByText('ブログ記事の構成案づくり')).toBeInTheDocument();

    // 並び順は cardIds が保持（§2.3）
    const titles = [...document.querySelectorAll('.card__title')].map(
      (el) => el.textContent,
    );
    expect(titles).toEqual([
      '競合SaaS 5社の料金プランを調査',
      '週次レビューのテンプレートを記入',
      'ブログ記事の構成案づくり',
    ]);
  });

  it('末尾に「＋ カードを追加」破線ボタンを表示する（動作は後続Issue）', () => {
    const backlogLane = useBoardStore.getState().lanes[0];
    render(<Lane lane={backlogLane} />);
    expect(
      screen.getByRole('button', { name: '＋ カードを追加' }),
    ).toBeInTheDocument();
  });

  it('空レーンでは「＋ カードを追加」だけ表示する（§5.5）', () => {
    render(<Lane lane={{ key: 'todo', name: 'ToDo', cardIds: [] }} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(document.querySelectorAll('.card')).toHaveLength(0);
    expect(
      screen.getByRole('button', { name: '＋ カードを追加' }),
    ).toBeInTheDocument();
  });
});
