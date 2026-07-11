// 意思決定トレース（#25）のテスト:
// 行フォーマット（役割・K-xx・tokens・$）、人編集版の「あなたが編集」、
// 折りたたみ（既定閉）、GET /tasks/:id/trace 読込、ArtifactSection の版切替との連動。

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { TraceEntry } from '../../types/api.ts';
import {
  formatTraceCost,
  HUMAN_EDIT_LABEL,
  TraceSection,
  traceRowText,
} from './TraceSection';

const AT = '2026-07-07T00:00:00Z';

function makeEntry(
  partial: Partial<TraceEntry> & Pick<TraceEntry, 'version'>,
): TraceEntry {
  return { appliedRuleIds: [], createdAt: AT, ...partial };
}

const V1 = makeEntry({
  version: 1,
  jobId: 'job-1',
  kind: 'execute',
  status: 'succeeded',
  appliedRuleIds: ['K-01', 'K-03'],
  inputTokens: 1400,
  outputTokens: 4032,
  costUsd: 0.0123,
  finishedAt: AT,
});
const V2 = makeEntry({
  version: 2,
  jobId: 'job-2',
  kind: 'execute',
  status: 'succeeded',
  appliedRuleIds: ['K-01', 'K-03'],
  inputTokens: 1500,
  outputTokens: 4000,
  costUsd: 1.5,
  finishedAt: AT,
});
const V3_HUMAN = makeEntry({ version: 3 });

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** T-091（you_review）でトレースをストアに持たせて描画する */
function renderSection(entries: TraceEntry[]) {
  useBoardStore.setState((s) => ({ trace: { ...s.trace, 'T-091': entries } }));
  const task = useBoardStore.getState().cards['T-091'];
  return render(<TraceSection task={task} />);
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
  // 既定スタブ: loadTrace の再取得はストア内容を上書きしない（entries を返さない）
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('traceRowText / formatTraceCost（#25 行フォーマット）', () => {
  it('AI生成版: v番号 · 役割 · K-xx を前提 · tokens · $コスト（mono 1行）', () => {
    expect(traceRowText(V1)).toBe('v1 · 実行AI · K-01, K-03 を前提 · 5,432 tokens · $0.0123');
  });

  it('$1 以上は2桁・$1 未満は4桁で表示する', () => {
    expect(formatTraceCost(0.0123)).toBe('$0.0123');
    expect(formatTraceCost(1.5)).toBe('$1.50');
    expect(traceRowText(V2)).toContain('$1.50');
  });

  it('ルール0件なら「を前提」の節を出さない', () => {
    const noRules = makeEntry({
      version: 1,
      kind: 'review',
      appliedRuleIds: [],
      inputTokens: 100,
      outputTokens: 100,
      costUsd: 0.0005,
    });
    expect(traceRowText(noRules)).toBe('v1 · レビューAI · 200 tokens · $0.0005');
  });

  it('人の編集版（kind なし）は「あなたが編集」', () => {
    expect(traceRowText(V3_HUMAN)).toBe(`v3 · ${HUMAN_EDIT_LABEL}`);
  });
});

describe('TraceSection: 表示と折りたたみ', () => {
  it('トレースが無ければ何も描画しない', () => {
    const task = useBoardStore.getState().cards['T-091'];
    const { container } = render(<TraceSection task={task} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('見出し「意思決定トレース」＋件数を表示し、既定は閉（行は出さない）', () => {
    renderSection([V1, V2, V3_HUMAN]);
    expect(screen.getByText('意思決定トレース')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /意思決定トレース/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(screen.queryByText(/tokens/)).toBeNull();
  });

  it('見出しクリックで開き、版ごとの行（AI生成＋人編集）を表示する', () => {
    renderSection([V1, V2, V3_HUMAN]);
    fireEvent.click(screen.getByRole('button', { name: /意思決定トレース/ }));
    expect(
      screen.getByText('v1 · 実行AI · K-01, K-03 を前提 · 5,432 tokens · $0.0123'),
    ).toBeInTheDocument();
    expect(screen.getByText(`v3 · ${HUMAN_EDIT_LABEL}`)).toBeInTheDocument();
  });

  it('マウント時に GET /tasks/:id/trace で読み込んで描画する', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { taskId: 'T-098', entries: [V1] }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const task = useBoardStore.getState().cards['T-098'];
    render(<TraceSection task={task} />);

    expect(fetchMock).toHaveBeenCalledWith('/api/tasks/T-098/trace', undefined);
    await waitFor(() =>
      expect(useBoardStore.getState().trace['T-098']).toHaveLength(1),
    );
    expect(screen.getByText('意思決定トレース')).toBeInTheDocument();
  });
});

describe('TraceSection: ArtifactSection の版切替と連動（#25）', () => {
  it('既定（未選択）は最新版の行をハイライトする', () => {
    renderSection([V1, V2, V3_HUMAN]);
    fireEvent.click(screen.getByRole('button', { name: /意思決定トレース/ }));
    const rows = screen.getAllByRole('button', { pressed: true });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('v3');
  });

  it('版セレクタ相当（artifactVersion）の変更でハイライトが移る', () => {
    renderSection([V1, V2, V3_HUMAN]);
    fireEvent.click(screen.getByRole('button', { name: /意思決定トレース/ }));
    act(() => {
      useBoardStore.getState().selectArtifactVersion('T-091', 1);
    });
    const rows = screen.getAllByRole('button', { pressed: true });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('v1 · 実行AI');
  });

  it('行クリックでその版を選択し、最新版クリックは null（最新追従）へ戻す', () => {
    renderSection([V1, V2, V3_HUMAN]);
    fireEvent.click(screen.getByRole('button', { name: /意思決定トレース/ }));

    fireEvent.click(screen.getByText(/^v1 · 実行AI/));
    expect(useBoardStore.getState().artifactVersion['T-091']).toBe(1);

    fireEvent.click(screen.getByText(`v3 · ${HUMAN_EDIT_LABEL}`));
    expect(useBoardStore.getState().artifactVersion['T-091']).toBeNull();
  });
});
