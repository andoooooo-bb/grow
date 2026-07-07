import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Task } from '../../types/domain.ts';
import { Card } from './Card';

function getTask(id: string): Task {
  const task = useBoardStore.getState().cards[id];
  if (!task) throw new Error(`fixture task not found: ${id}`);
  return task;
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(cleanup);

describe('Card: 左バー色と tone クラス（§3.2）', () => {
  it('ai_work（owner=ai）: 左バー ai ＋ tone work バッジ「AI作業中」', () => {
    const { container } = render(<Card task={getTask('T-098')} />);
    expect(container.querySelector('.card__bar')).toHaveClass('card__bar--ai');
    expect(container.querySelector('.status-badge')).toHaveClass('status-badge--work');
    expect(screen.getByText('AI作業中')).toBeInTheDocument();
  });

  it('queued（owner=ai）: 左バー ai ＋ tone neutral バッジ「AI待機中」', () => {
    const { container } = render(<Card task={getTask('T-121')} />);
    expect(container.querySelector('.card__bar')).toHaveClass('card__bar--ai');
    expect(container.querySelector('.status-badge')).toHaveClass('status-badge--neutral');
    expect(screen.getByText('AI待機中')).toBeInTheDocument();
  });

  it('spec（owner=human）: 左バー human ＋ tone spec バッジ「壁打ち中」', () => {
    const { container } = render(<Card task={getTask('T-104')} />);
    expect(container.querySelector('.card__bar')).toHaveClass('card__bar--human');
    expect(container.querySelector('.status-badge')).toHaveClass('status-badge--spec');
    expect(screen.getByText('壁打ち中')).toBeInTheDocument();
  });

  it('you_todo（owner=human）: 左バー human ＋ tone attention バッジ', () => {
    const { container } = render(<Card task={getTask('T-109')} />);
    expect(container.querySelector('.card__bar')).toHaveClass('card__bar--human');
    expect(container.querySelector('.status-badge')).toHaveClass(
      'status-badge--attention',
    );
    expect(screen.getByText('あなたの作業待ち')).toBeInTheDocument();
  });

  it('done: 左バー done（緑）＋ tone done バッジ「完了」', () => {
    const { container } = render(<Card task={getTask('T-080')} />);
    expect(container.querySelector('.card__bar')).toHaveClass('card__bar--done');
    expect(container.querySelector('.status-badge')).toHaveClass('status-badge--done');
    expect(screen.getByText('完了')).toBeInTheDocument();
  });
});

describe('Card: 上段・進捗・ラベル（§3.2）', () => {
  it('アバターは STATUS_META の owner から導出（ai=AI / human=YK）', () => {
    render(<Card task={getTask('T-098')} />);
    expect(screen.getByText('AI')).toBeInTheDocument();
    render(<Card task={getTask('T-109')} />);
    expect(screen.getByText('YK')).toBeInTheDocument();
  });

  it('タスクIDとコメント数プレースホルダ（0）を表示する', () => {
    render(<Card task={getTask('T-098')} />);
    expect(screen.getByText('T-098')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('progress があるとき進捗バーを幅N%で表示する', () => {
    const { container } = render(<Card task={getTask('T-098')} />);
    expect(screen.getByText('progress')).toBeInTheDocument();
    expect(screen.getByText('60%')).toBeInTheDocument();
    const fill = container.querySelector('.card__meter-fill');
    expect(fill).toHaveStyle({ width: '60%' });
  });

  it('progress が無いカードは進捗バーを表示しない', () => {
    const { container } = render(<Card task={getTask('T-109')} />);
    expect(container.querySelector('.card__meter')).toBeNull();
  });

  it('childIds があるときサブタスク進捗（done数/総数）を表示する', () => {
    const fixture = boardFixture();
    const parent: Task = {
      ...fixture.cards['T-130'],
      id: 'T-200',
      title: '親タスク',
      childIds: ['T-201', 'T-202', 'T-203'],
    };
    const children: Task[] = [
      { ...fixture.cards['T-080'], id: 'T-201', parentId: 'T-200' }, // done
      { ...fixture.cards['T-109'], id: 'T-202', parentId: 'T-200' },
      { ...fixture.cards['T-112'], id: 'T-203', parentId: 'T-200' },
    ];
    useBoardStore.setState((s) => ({
      cards: {
        ...s.cards,
        [parent.id]: parent,
        ...Object.fromEntries(children.map((c) => [c.id, c])),
      },
    }));

    const { container } = render(<Card task={parent} />);
    expect(screen.getByText('サブタスク')).toBeInTheDocument();
    expect(screen.getByText('1/3')).toBeInTheDocument();
    const fill = container.querySelector('.card__meter-fill');
    expect(fill).toHaveStyle({ width: `${(1 / 3) * 100}%` });
  });

  it('サブタスクは「親: 親タイトル先頭12字…」タグを表示する', () => {
    const fixture = boardFixture();
    const parent: Task = {
      ...fixture.cards['T-130'], // ポートフォリオサイトのリニューアル（12字超）
      id: 'T-200',
      childIds: ['T-201'],
    };
    const child: Task = { ...fixture.cards['T-109'], id: 'T-201', parentId: 'T-200' };
    useBoardStore.setState((s) => ({
      cards: { ...s.cards, [parent.id]: parent, [child.id]: child },
    }));

    render(<Card task={child} />);
    // 「ポートフォリオサイトのリニューアル」の先頭12字＋…
    expect(screen.getByText('親: ポートフォリオサイトのリ…')).toBeInTheDocument();
  });

  it('labels をチップで表示する', () => {
    render(<Card task={getTask('T-098')} />);
    expect(screen.getByText('仕事')).toBeInTheDocument();
    expect(screen.getByText('調査')).toBeInTheDocument();
  });
});

describe('Card: クリックで select(id)（§5.3）', () => {
  it('クリックすると selectedId が更新され panelMode=detail になる', () => {
    render(<Card task={getTask('T-098')} />);
    fireEvent.click(screen.getByText('競合調査レポートの下書き'));
    expect(useBoardStore.getState().selectedId).toBe('T-098');
    expect(useBoardStore.getState().panelMode).toBe('detail');
  });
});
