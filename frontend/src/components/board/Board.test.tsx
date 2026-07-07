import { cleanup, render, screen } from '@testing-library/react';
import type { DragEndEvent } from '@dnd-kit/core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import { Board, handleDragEnd } from './Board';

/** dnd-kit の DragEndEvent 相当（ハンドラ単体テスト用の最小形） */
function dragEndEvent(activeId: string, overId: string | null): DragEndEvent {
  return {
    active: { id: activeId },
    over: overId === null ? null : { id: overId },
  } as unknown as DragEndEvent;
}

describe('handleDragEnd（#8: DnD → move）', () => {
  it('レーンへドロップで move(taskId, toLaneKey) を呼ぶ', () => {
    const move = vi.fn(async () => {});
    handleDragEnd(dragEndEvent('T-104', 'progress'), move);
    expect(move).toHaveBeenCalledExactlyOnceWith('T-104', 'progress');
  });

  it('レーン外へのドロップ（over=null）は何もしない', () => {
    const move = vi.fn(async () => {});
    handleDragEnd(dragEndEvent('T-104', null), move);
    expect(move).not.toHaveBeenCalled();
  });
});

describe('Board（#8: 簡易エラー表示）', () => {
  beforeEach(() => {
    useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  });

  afterEach(cleanup);

  it('boardError を role=alert で表示する', () => {
    useBoardStore.setState({ boardError: 'カードの移動に失敗しました' });
    render(<Board />);
    expect(screen.getByRole('alert')).toHaveTextContent('カードの移動に失敗しました');
  });

  it('boardError が無ければ alert を出さず、5レーンを描画する', () => {
    render(<Board />);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('バックログ')).toBeInTheDocument();
    expect(document.querySelectorAll('.lane')).toHaveLength(5);
  });
});
