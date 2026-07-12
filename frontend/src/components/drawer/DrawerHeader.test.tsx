// DrawerHeader（§3.3.1 / #21 オートノミー・ダイヤル＋行動範囲ポリシー）のテスト。
// 変更は PATCH /api/tasks/:id で即保存（楽観的）し、失敗時はロールバックする。

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import { AUTONOMY_META, type Task } from '../../types/domain.ts';
import { DrawerHeader } from './DrawerHeader';

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function getTask(id: string): Task {
  const task = useBoardStore.getState().cards[id];
  if (!task) throw new Error(`fixture task not found: ${id}`);
  return task;
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('DrawerHeader: オートノミー・ダイヤル（#21）', () => {
  it('L0-L3 の4ボタンを表示し、既定は L1 がアクティブ（ラベル「下書きまで」）', () => {
    render(<DrawerHeader task={getTask('T-104')} />);
    const dial = screen.getByRole('group', { name: 'オートノミー' });
    expect(dial).toBeInTheDocument();
    for (const level of ['L0', 'L1', 'L2', 'L3']) {
      expect(screen.getByRole('button', { name: level })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'L1' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'L3' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByText('下書きまで')).toBeInTheDocument();
  });

  it('各ボタンに autonomy_levels.json 由来の説明ツールチップ（title）を持つ', () => {
    render(<DrawerHeader task={getTask('T-104')} />);
    const l0 = screen.getByRole('button', { name: 'L0' });
    expect(l0.getAttribute('title')).toContain(AUTONOMY_META.L0.label);
    expect(l0.getAttribute('title')).toContain(AUTONOMY_META.L0.description);
  });

  it('レベルをクリックすると楽観的更新し PATCH {autonomy} で即保存する', async () => {
    const updated = { ...getTask('T-104'), autonomy: 'L3' as const };
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, updated),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<DrawerHeader task={getTask('T-104')} />);
    fireEvent.click(screen.getByRole('button', { name: 'L3' }));

    // 楽観的更新（即時）
    expect(useBoardStore.getState().cards['T-104'].autonomy).toBe('L3');
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-104',
        expect.objectContaining({ method: 'PATCH' }),
      ),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      autonomy: 'L3',
    });
  });

  it('現在と同じレベルのクリックは no-op（PATCH しない）', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<DrawerHeader task={getTask('T-104')} />);
    fireEvent.click(screen.getByRole('button', { name: 'L1' }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('PATCH 失敗時はロールバックして boardError を表示する（§5.4）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    render(<DrawerHeader task={getTask('T-104')} />);
    fireEvent.click(screen.getByRole('button', { name: 'L2' }));

    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe(
        'オートノミーの変更に失敗しました',
      ),
    );
    expect(useBoardStore.getState().cards['T-104'].autonomy).toBeUndefined(); // 既定のまま
  });
});

describe('DrawerHeader: 行動範囲ポリシー（#21）', () => {
  it('Web検索トグル: 既定 ON → クリックで PATCH {policy: {allowWebSearch: false}}', async () => {
    const updated: Task = {
      ...getTask('T-104'),
      policy: { allowWebSearch: false, costCapUsd: null },
    };
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, updated),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<DrawerHeader task={getTask('T-104')} />);
    const toggle = screen.getByRole('button', { name: 'Web検索 ON' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(toggle);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      policy: { allowWebSearch: false, costCapUsd: null },
    });
    // 楽観的反映 → OFF 表示（store の task を再レンダリング）
    cleanup();
    render(<DrawerHeader task={getTask('T-104')} />);
    expect(screen.getByRole('button', { name: 'Web検索 OFF' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('コスト上限入力: blur で PATCH {policy: {costCapUsd}} 保存（Web検索設定は維持）', async () => {
    const updated: Task = {
      ...getTask('T-104'),
      policy: { allowWebSearch: true, costCapUsd: 2.5 },
    };
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, updated),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<DrawerHeader task={getTask('T-104')} />);
    const input = screen.getByLabelText('コスト上限（USD）');
    fireEvent.change(input, { target: { value: '2.5' } });
    fireEvent.blur(input);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      policy: { allowWebSearch: true, costCapUsd: 2.5 },
    });
    expect(useBoardStore.getState().cards['T-104'].policy).toEqual({
      allowWebSearch: true,
      costCapUsd: 2.5,
    });
  });

  it('コスト上限を空欄にすると null（上限なし）で保存する', async () => {
    useBoardStore.setState((s) => ({
      cards: {
        ...s.cards,
        'T-104': {
          ...s.cards['T-104'],
          policy: { allowWebSearch: true, costCapUsd: 1 },
        },
      },
    }));
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(200, {
          ...getTask('T-104'),
          policy: { allowWebSearch: true, costCapUsd: null },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<DrawerHeader task={getTask('T-104')} />);
    const input = screen.getByLabelText('コスト上限（USD）');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      policy: { allowWebSearch: true, costCapUsd: null },
    });
  });

  it('不正なコスト上限（負値）や未変更の blur では PATCH しない', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<DrawerHeader task={getTask('T-104')} />);
    const input = screen.getByLabelText('コスト上限（USD）');

    fireEvent.change(input, { target: { value: '-1' } });
    fireEvent.blur(input);
    fireEvent.change(input, { target: { value: '' } }); // 既定（null）のまま
    fireEvent.blur(input);

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
