// AI活動ライブフィード（#19）のテスト: ピルの button 化・開閉・空状態・
// 行の表示（新しい順・役割バッジ）・行クリックで select して閉じる。

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  type ActivityEntry,
  createInitialBoardState,
  useBoardStore,
} from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import { AgentFeed } from './AgentFeed';

function makeEntry(partial: Partial<ActivityEntry> & Pick<ActivityEntry, 'id'>): ActivityEntry {
  return {
    taskId: 'T-098',
    taskTitle: '競合調査レポートの下書き',
    text: 'テスト活動',
    at: Date.parse('2026-07-07T12:34:00'),
    ...partial,
  };
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(cleanup);

describe('AgentFeed（#19 ライブフィード）', () => {
  it('「AI稼働 N」が button になり、クリックでパネルが開閉する', () => {
    render(<AgentFeed />);
    const toggle = screen.getByRole('button', { name: 'AI稼働 3' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(document.querySelector('.agent-feed__panel')).toBeNull();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('AIの活動')).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(document.querySelector('.agent-feed__panel')).toBeNull();
  });

  it('活動が無ければ空状態「AIの活動はまだありません」を表示する', () => {
    render(<AgentFeed />);
    fireEvent.click(screen.getByRole('button', { name: 'AI稼働 3' }));
    expect(screen.getByText('AIの活動はまだありません')).toBeInTheDocument();
  });

  it('活動行を新しい順に表示する（taskId・本文・役割バッジ）', () => {
    useBoardStore.setState({
      activity: [
        makeEntry({ id: 'a-2', text: '成果物v2を作成', role: 'executor' }),
        makeEntry({
          id: 'a-1',
          taskId: 'T-091',
          taskTitle: '確定申告サマリーの最終確認',
          text: 'ルールK-06を学習',
          role: 'distiller',
        }),
      ],
    });
    render(<AgentFeed />);
    fireEvent.click(screen.getByRole('button', { name: 'AI稼働 3' }));

    const rows = [...document.querySelectorAll('.agent-feed__row')];
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('T-098');
    expect(rows[0]).toHaveTextContent('成果物v2を作成');
    expect(rows[0].querySelector('.agent-badge--executor')).toHaveTextContent('実行AI');
    expect(rows[1]).toHaveTextContent('ルールK-06を学習');
    expect(rows[1].querySelector('.agent-badge--distiller')).toHaveTextContent('学習AI');
  });

  it('行クリックで select(taskId) して閉じる（該当カードへジャンプ）', () => {
    useBoardStore.setState({
      activity: [makeEntry({ id: 'a-1', taskId: 'T-091', text: 'レビュー待ちへ' })],
      selectedId: null,
      panelMode: 'chat',
    });
    render(<AgentFeed />);
    fireEvent.click(screen.getByRole('button', { name: 'AI稼働 3' }));
    fireEvent.click(screen.getByText('レビュー待ちへ'));

    const s = useBoardStore.getState();
    expect(s.selectedId).toBe('T-091');
    expect(s.panelMode).toBe('detail'); // select は detail に戻す（§5.3）
    expect(document.querySelector('.agent-feed__panel')).toBeNull();
  });

  it('存在しないタスクの行は選択せずに閉じるだけ', () => {
    useBoardStore.setState({
      activity: [makeEntry({ id: 'a-1', taskId: '', text: '出所不明の行' })],
    });
    render(<AgentFeed />);
    fireEvent.click(screen.getByRole('button', { name: 'AI稼働 3' }));
    fireEvent.click(screen.getByText('出所不明の行'));
    expect(useBoardStore.getState().selectedId).toBeNull();
    expect(document.querySelector('.agent-feed__panel')).toBeNull();
  });
});
