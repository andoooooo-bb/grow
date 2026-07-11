// アクティビティスレッドの役割バッジ（#19）のテスト:
// agentRole 付きAIコメントに色分けミニバッジ、無指定は従来通り「Grow」のみ。

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Comment } from '../../types/domain.ts';
import { ActivityThread } from './ActivityThread';

const AT = '2026-07-07T00:00:00Z';

function makeComment(partial: Partial<Comment> & Pick<Comment, 'id' | 'text'>): Comment {
  return { taskId: 'T-098', author: 'ai', createdAt: AT, ...partial };
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(cleanup);

describe('ActivityThread: 役割バッジ（#19）', () => {
  it('agentRole 付きAIコメントは名義「Grow」の横に色分けミニバッジを出す', () => {
    useBoardStore.setState({
      comments: {
        'T-098': [
          makeComment({ id: 'c-1', text: '着手します。', agentRole: 'executor' }),
          makeComment({ id: 'c-2', text: '反映しました。', agentRole: 'planner' }),
          makeComment({ id: 'c-3', text: '学習しました。', agentRole: 'distiller' }),
          makeComment({ id: 'c-4', text: '編成します。', agentRole: 'conductor' }),
        ],
      },
    });
    render(<ActivityThread taskId="T-098" />);

    expect(screen.getAllByText('Grow')).toHaveLength(4);
    expect(screen.getByText('実行AI')).toHaveClass('agent-badge--executor');
    expect(screen.getByText('計画AI')).toHaveClass('agent-badge--planner');
    expect(screen.getByText('学習AI')).toHaveClass('agent-badge--distiller');
    expect(screen.getByText('指揮者AI')).toHaveClass('agent-badge--conductor');
  });

  it('無指定のAIコメントは従来通り「Grow」のみ（バッジなし）', () => {
    useBoardStore.setState({
      comments: { 'T-098': [makeComment({ id: 'c-1', text: '旧コメント' })] },
    });
    render(<ActivityThread taskId="T-098" />);
    expect(screen.getByText('Grow')).toBeInTheDocument();
    expect(document.querySelector('.agent-badge')).toBeNull();
  });

  it('human コメントにはバッジを出さない（agentRole が万一あっても）', () => {
    useBoardStore.setState({
      comments: {
        'T-098': [
          makeComment({ id: 'c-1', text: '了解', author: 'human', agentRole: 'executor' }),
        ],
      },
    });
    render(<ActivityThread taskId="T-098" />);
    expect(screen.getByText('あなた')).toBeInTheDocument();
    expect(document.querySelector('.agent-badge')).toBeNull();
  });
});
