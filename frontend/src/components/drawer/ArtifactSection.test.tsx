import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Artifact } from '../../types/domain.ts';
import { ArtifactSection } from './ArtifactSection';

const AT = '2026-07-07T00:00:00Z';

function makeArtifact(
  partial: Partial<Artifact> & Pick<Artifact, 'id' | 'version' | 'contentMd'>,
): Artifact {
  return { taskId: 'T-091', createdAt: AT, ...partial };
}

const V1 = makeArtifact({ id: 'a-1', version: 1, contentMd: '# 初版レポート\n\n本文 v1' });
const V2 = makeArtifact({ id: 'a-2', version: 2, contentMd: '# 改訂レポート\n\n本文 v2' });

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** T-091（you_review）で成果物を持たせて描画する */
function renderSection(artifacts: Artifact[], canAssignAi = true) {
  useBoardStore.setState((s) => ({
    artifacts: { ...s.artifacts, 'T-091': artifacts },
  }));
  const task = useBoardStore.getState().cards['T-091'];
  return render(<ArtifactSection task={task} canAssignAi={canAssignAi} />);
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ArtifactSection（§3.3.2 c-2）', () => {
  it('artifacts が無ければ何も描画しない', () => {
    const task = useBoardStore.getState().cards['T-091'];
    const { container } = render(<ArtifactSection task={task} canAssignAi />);
    expect(container).toBeEmptyDOMElement();
  });

  it('見出し「成果物（レポート）」と最新版（末尾）の Markdown プレビューをデフォルト表示する', () => {
    renderSection([V1, V2]);
    expect(screen.getByText('成果物（レポート）')).toBeInTheDocument();
    // Markdown がレンダリングされる（# → h1）
    expect(
      screen.getByRole('heading', { name: '改訂レポート' }),
    ).toBeInTheDocument();
    expect(screen.getByText('本文 v2')).toBeInTheDocument();
    expect(screen.queryByText('本文 v1')).toBeNull();
  });

  it('単版なら版セレクタを出さない', () => {
    renderSection([V1]);
    expect(screen.queryByLabelText('版を選択')).toBeNull();
    expect(screen.getByRole('heading', { name: '初版レポート' })).toBeInTheDocument();
  });

  it('版セレクタ（複数版時のみ）で過去版へ切替できる', () => {
    renderSection([V1, V2]);
    const select = screen.getByLabelText('版を選択');
    expect(select).toHaveValue('2'); // 最新デフォルト

    fireEvent.change(select, { target: { value: '1' } });
    expect(screen.getByRole('heading', { name: '初版レポート' })).toBeInTheDocument();
    expect(screen.queryByText('本文 v2')).toBeNull();
  });

  it('「編集」で表示中の版の生 Markdown を textarea に出し、保存で POST → 新版を最新表示する', async () => {
    const created = makeArtifact({ id: 'a-3', version: 3, contentMd: '# 手直し版' });
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(201, created),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderSection([V1, V2]);

    fireEvent.click(screen.getByRole('button', { name: '編集' }));
    const editor = screen.getByLabelText('成果物のMarkdown');
    expect(editor).toHaveValue(V2.contentMd); // リッチエディタではなく生文字列（§00 #12）

    fireEvent.change(editor, { target: { value: '# 手直し版' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-091/artifacts',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      contentMd: '# 手直し版',
    });
    // 保存後: 編集モード終了 → 新版（v3）が最新として表示され、セレクタも v3
    expect(
      await screen.findByRole('heading', { name: '手直し版' }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('成果物のMarkdown')).toBeNull();
    expect(screen.getByLabelText('版を選択')).toHaveValue('3');
  });

  it('保存失敗では編集モードを維持する（boardError に簡易エラー）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, {})));
    renderSection([V1]);

    fireEvent.click(screen.getByRole('button', { name: '編集' }));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe('成果物の保存に失敗しました'),
    );
    expect(screen.getByLabelText('成果物のMarkdown')).toBeInTheDocument();
  });

  it('「キャンセル」で編集を破棄してプレビューに戻る', () => {
    renderSection([V1]);
    fireEvent.click(screen.getByRole('button', { name: '編集' }));
    fireEvent.change(screen.getByLabelText('成果物のMarkdown'), {
      target: { value: '破棄される編集' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'キャンセル' }));

    expect(screen.queryByLabelText('成果物のMarkdown')).toBeNull();
    expect(screen.getByRole('heading', { name: '初版レポート' })).toBeInTheDocument();
  });

  it('「再生成」で assignAi（POST /assign-ai）を呼ぶ', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(202, { jobId: 'job-1' }));
    vi.stubGlobal('fetch', fetchMock);
    renderSection([V1]);

    fireEvent.click(screen.getByRole('button', { name: '再生成' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-091/assign-ai',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
  });

  it('「再生成」は canAssignAi=false（ai_work/done/送信中）で無効', () => {
    renderSection([V1], false);
    expect(screen.getByRole('button', { name: '再生成' })).toBeDisabled();
  });

  it('SSE の新版反映（applyArtifactCreated）に最新表示が自動追従する', () => {
    renderSection([V1, V2]);
    expect(screen.getByRole('heading', { name: '改訂レポート' })).toBeInTheDocument();

    const v3 = makeArtifact({ id: 'a-3', version: 3, contentMd: '# 再生成版' });
    act(() => useBoardStore.getState().applyArtifactCreated(v3));

    expect(screen.getByRole('heading', { name: '再生成版' })).toBeInTheDocument();
    expect(screen.getByLabelText('版を選択')).toHaveValue('3');
  });
});
