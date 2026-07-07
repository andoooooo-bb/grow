import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { createInitialBoardState, useBoardStore } from './store/board.ts';
import { boardFixture } from './test/boardFixture.ts';

describe('App', () => {
  beforeEach(() => {
    useBoardStore.setState(createInitialBoardState());
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => boardFixture(),
      })),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('GET /api/board を取得してストアへ反映し、トップバーと5レーンを描画する', async () => {
    render(<App />);

    // データ到着後にカードが描画される
    expect(await screen.findByText('競合調査レポートの下書き')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith('/api/board');

    // 5レーン（「完了」は done バッジとも重複するためレーンヘッダ要素で判定）
    const laneNames = [...document.querySelectorAll('.lane__name')].map(
      (el) => el.textContent,
    );
    expect(laneNames).toEqual(['バックログ', 'ToDo', '進行中', 'レビュー', '完了']);

    // 派生カウンタ（§5.1）
    expect(screen.getByText('あなたの番 6')).toBeInTheDocument();
    expect(screen.getByText('AI稼働 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '◈ ナレッジ 5' })).toBeInTheDocument();
  });

  it('取得失敗時はエラーメッセージを表示する', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 500,
        json: async () => ({}),
      })),
    );
    render(<App />);
    // retry:1 のリトライ待ち（約1s）があるためタイムアウトを延ばす
    expect(
      await screen.findByText('ボードの読み込みに失敗しました', undefined, {
        timeout: 4000,
      }),
    ).toBeInTheDocument();
  });
});
