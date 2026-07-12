import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Task } from '../../types/domain.ts';
import { SubtaskSection } from './SubtaskSection';

/** 親 T-130 の子カード（分解反映後の形。laneKey は集計に影響しない） */
function makeChild(id: string, title: string, status: Task['status']): Task {
  return {
    ...boardFixture().cards['T-130'],
    id,
    title,
    laneKey: 'todo',
    orderInLane: 0,
    status,
    parentId: 'T-130',
    childIds: undefined,
  };
}

const CHILDREN = [
  makeChild('T-131', '情報設計・サイトマップ作成', 'done'),
  makeChild('T-132', 'ワイヤーフレーム作成', 'done'),
  makeChild('T-133', '掲載する実績コンテンツの選定', 'ai_work'),
  makeChild('T-134', 'デザイン方向性の決定', 'you_todo'),
  makeChild('T-135', 'コーディング・実装', 'queued'),
];

/** 子カードを store へ積み、childIds 付きの親でセクションを描画する */
function renderSection(childIds: string[] = CHILDREN.map((c) => c.id)) {
  useBoardStore.setState((s) => {
    const cards = { ...s.cards };
    for (const child of CHILDREN) cards[child.id] = child;
    cards['T-130'] = { ...cards['T-130'], childIds };
    return { cards };
  });
  const task = useBoardStore.getState().cards['T-130'];
  return render(<SubtaskSection task={task} />);
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
});

describe('SubtaskSection（§3.3.2 d）', () => {
  it('childIds が無ければ何も描画しない', () => {
    const task = useBoardStore.getState().cards['T-130']; // childIds なし
    const { container } = render(<SubtaskSection task={task} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('見出しに done/total 集計「サブタスク 2/5」を表示する（§5.1 派生）', () => {
    renderSection();
    expect(screen.getByText('サブタスク 2/5')).toBeInTheDocument();
  });

  it('子行にタイトル・担当アバター・ステータスラベルを childIds 順で表示する', () => {
    const { container } = renderSection();

    const rows = container.querySelectorAll('.subtask-section__row');
    expect(rows).toHaveLength(5);
    expect(screen.getByText('情報設計・サイトマップ作成')).toBeInTheDocument();
    expect(screen.getByText('掲載する実績コンテンツの選定')).toBeInTheDocument();
    // ステータスラベル（STATUS_META 準拠）
    expect(screen.getAllByText('完了')).toHaveLength(2);
    expect(screen.getByText('AI作業中')).toBeInTheDocument();
    expect(screen.getByText('あなたの作業待ち')).toBeInTheDocument();
    expect(screen.getByText('AI待機中')).toBeInTheDocument();
    // 担当アバター: status の owner を反映（ai_work/queued/done=AI, you_todo=YK）
    expect(rows[2].querySelector('.avatar')).toHaveClass('avatar--ai');
    expect(rows[3].querySelector('.avatar')).toHaveClass('avatar--human');
    expect(rows[0].querySelector('.avatar')).toHaveClass('avatar--subtask'); // 17px variant
  });

  it('子行クリックで select(childId) — 子カードのドロワーへ切り替わる（§5.3）', () => {
    useBoardStore.setState({ selectedId: 'T-130', panelMode: 'chat' });
    renderSection();

    fireEvent.click(
      screen.getByRole('button', { name: /デザイン方向性の決定/ }),
    );

    expect(useBoardStore.getState().selectedId).toBe('T-134');
    expect(useBoardStore.getState().panelMode).toBe('detail'); // select は detail に戻す
  });

  it('cards に無い childId は行に出さない（total は childIds 基準のまま）', () => {
    const { container } = renderSection([...CHILDREN.map((c) => c.id), 'T-999']);

    expect(container.querySelectorAll('.subtask-section__row')).toHaveLength(5);
    expect(screen.getByText('サブタスク 2/6')).toBeInTheDocument();
  });
});
