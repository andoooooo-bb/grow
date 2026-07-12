import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { createInitialBoardState, useBoardStore } from './store/board.ts';
import { boardFixture } from './test/boardFixture.ts';

describe('App', () => {
  beforeEach(() => {
    useBoardStore.setState(createInitialBoardState());
    // GET /api/board と GET /api/tasks/:id/comments（#7 ドロワー）を受けるスタブ
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/comments')) {
          return { ok: true, status: 200, json: async () => [] };
        }
        return { ok: true, status: 200, json: async () => boardFixture() };
      }),
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
    expect(fetch).toHaveBeenCalledWith('/api/board', undefined);

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

  it('カードクリックでドロワーが開き、閉じる✕で閉じる（#7 DoD）', async () => {
    render(<App />);

    fireEvent.click(await screen.findByText('競合調査レポートの下書き'));

    // §03 冒頭レイアウト: ボディ横flex にボード＋ドロワー(412px)
    expect(await screen.findByText('T-098 · 進行中')).toBeInTheDocument();
    expect(document.querySelector('.drawer')).not.toBeNull();
    expect(
      screen.getByPlaceholderText('コメントで依頼・指示を残す…'),
    ).toBeInTheDocument();
    // スレッドの読込が呼ばれる
    expect(fetch).toHaveBeenCalledWith('/api/tasks/T-098/comments', undefined);

    fireEvent.click(screen.getByRole('button', { name: '閉じる' }));
    expect(screen.queryByText('T-098 · 進行中')).toBeNull();
    expect(document.querySelector('.drawer')).toBeNull();
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
