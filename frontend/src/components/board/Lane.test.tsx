import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Task } from '../../types/domain.ts';
import { Lane } from './Lane';

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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

  it('末尾に「＋ カードを追加」破線ボタンを表示する', () => {
    const backlogLane = useBoardStore.getState().lanes[0];
    render(<Lane lane={backlogLane} />);
    expect(
      screen.getByRole('button', { name: '＋ カードを追加' }),
    ).toBeInTheDocument();
  });

  it('「＋ カードを追加」で addCard（POST /api/tasks → AIコメント → ドロワー）を実行する（#8）', async () => {
    const created: Task = {
      ...boardFixture().cards['T-130'],
      id: 'T-200',
      laneKey: 'backlog',
      orderInLane: 2,
      title: '新しいタスク',
      status: 'breakdown',
      labels: [],
      commentCount: 0,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST' && url === '/api/tasks') {
        return { ok: true, status: 201, json: async () => created };
      }
      if (init?.method === 'POST' && url === '/api/tasks/T-200/comments') {
        return {
          ok: true,
          status: 201,
          json: async () => ({
            id: 'c-ai-1',
            taskId: 'T-200',
            author: 'ai',
            text: 'タイトルと、やりたいことを教えてください。大きければ壁打ちで分解しましょう。',
            createdAt: '2026-07-07T00:00:00Z',
          }),
        };
      }
      throw new Error(`unexpected fetch: ${init?.method ?? 'GET'} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const backlogLane = useBoardStore.getState().lanes[0];
    render(<Lane lane={backlogLane} />);
    fireEvent.click(screen.getByRole('button', { name: '＋ カードを追加' }));

    await waitFor(() =>
      expect(useBoardStore.getState().selectedId).toBe('T-200'),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      laneKey: 'backlog',
      title: '新しいタスク',
    });
    expect(useBoardStore.getState().cards['T-200'].status).toBe('breakdown');
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
