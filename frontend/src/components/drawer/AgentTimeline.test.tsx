// リレー・タイムライン（#19）のテスト: kind→役割名、状態（完了/実行中/未来）、
// 終端「あなた」、ジョブ0件時の非表示、GET /tasks/:id/jobs からの読込。

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { AiJob, AiJobKind, AiJobStatus } from '../../types/domain.ts';
import { AgentTimeline } from './AgentTimeline';

const AT = '2026-07-07T00:00:00Z';

let jobSeq = 0;

function makeJob(kind: AiJobKind, status: AiJobStatus): AiJob {
  jobSeq += 1;
  return {
    id: `job-${jobSeq}`,
    taskId: 'T-098',
    kind,
    status,
    appliedRuleIds: [],
    createdAt: AT,
    finishedAt: status === 'succeeded' || status === 'failed' ? AT : null,
  };
}

function stepLabels(container: HTMLElement): string[] {
  return [...container.querySelectorAll('.agent-timeline__step')].map(
    (el) => el.textContent ?? '',
  );
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('AgentTimeline（#19 リレー・タイムライン）', () => {
  it('ジョブ0件（未読込）なら何も描画しない', () => {
    const { container } = render(
      <AgentTimeline task={useBoardStore.getState().cards['T-098']} />,
    );
    expect(container.querySelector('.agent-timeline')).toBeNull();
  });

  it('kind→役割名（breakdown=計画AI/execute=実行AI/distill=学習AI）＋終端「あなた」', () => {
    useBoardStore.setState({
      jobs: {
        'T-098': [
          makeJob('breakdown', 'succeeded'),
          makeJob('execute', 'succeeded'),
          makeJob('distill', 'succeeded'),
        ],
      },
    });
    const { container } = render(
      <AgentTimeline task={useBoardStore.getState().cards['T-098']} />,
    );
    expect(screen.getByText('エージェント・リレー')).toBeInTheDocument();
    expect(stepLabels(container)).toEqual(['計画AI', '実行AI', '学習AI', 'あなた']);
  });

  it('完了=塗り / 実行中=点滅 / 未来=グレー、実行中はあなたが future', () => {
    useBoardStore.setState({
      jobs: {
        'T-098': [makeJob('breakdown', 'succeeded'), makeJob('execute', 'running')],
      },
    });
    const { container } = render(
      <AgentTimeline task={useBoardStore.getState().cards['T-098']} />,
    );
    const steps = [...container.querySelectorAll('.agent-timeline__step')];
    expect(steps[0]).toHaveClass('agent-timeline__step--done');
    expect(steps[0]).toHaveClass('agent-timeline__step--planner');
    expect(steps[1]).toHaveClass('agent-timeline__step--active');
    expect(steps[1]).toHaveClass('agent-timeline__step--executor');
    expect(steps[2]).toHaveClass('agent-timeline__step--future'); // あなた
  });

  it('全ジョブ完了で「あなた」が active、タスク done なら塗りになる', () => {
    useBoardStore.setState({
      jobs: { 'T-091': [makeJob('execute', 'succeeded')] },
    });
    const you = () =>
      [...document.querySelectorAll('.agent-timeline__step')].at(-1);

    const first = render(
      <AgentTimeline task={{ ...useBoardStore.getState().cards['T-091'] }} />,
    ); // you_review
    expect(you()).toHaveClass('agent-timeline__step--active');
    first.unmount();

    useBoardStore.setState({ jobs: { 'T-080': [makeJob('execute', 'succeeded')] } });
    render(<AgentTimeline task={useBoardStore.getState().cards['T-080']} />); // done
    expect(you()).toHaveClass('agent-timeline__step--done');
  });

  it('queued は未来（グレー）、failed はアンバーで人へのハンドオフを示す', () => {
    useBoardStore.setState({
      jobs: {
        'T-098': [makeJob('execute', 'failed'), makeJob('execute', 'queued')],
      },
    });
    const { container } = render(
      <AgentTimeline task={useBoardStore.getState().cards['T-098']} />,
    );
    const steps = [...container.querySelectorAll('.agent-timeline__step')];
    expect(steps[0]).toHaveClass('agent-timeline__step--failed');
    expect(steps[1]).toHaveClass('agent-timeline__step--future');
  });

  it('マウント時に GET /tasks/:id/jobs で履歴を読み込んで描画する', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          taskId: 'T-098',
          jobs: [makeJob('execute', 'running')],
        }),
      })),
    );
    const { container } = render(
      <AgentTimeline task={useBoardStore.getState().cards['T-098']} />,
    );
    expect(fetch).toHaveBeenCalledWith('/api/tasks/T-098/jobs', undefined);
    await waitFor(() =>
      expect(container.querySelector('.agent-timeline')).not.toBeNull(),
    );
    expect(stepLabels(container as HTMLElement)).toEqual(['実行AI', 'あなた']);
  });
});
