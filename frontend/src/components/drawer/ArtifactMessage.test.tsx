// ArtifactMessage（成果物を会話メッセージとして描画）のテスト:
// 最新版は展開＋レビュー案内、過去版は折りたたみ、差分トグル、編集/再生成。

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { Artifact, Task } from '../../types/domain.ts';
import { ArtifactMessage } from './ArtifactMessage';

const AT = '2026-07-07T00:00:00Z';

function art(version: number, contentMd: string): Artifact {
  return { id: `a-${version}`, taskId: 'T-091', version, contentMd, createdAt: AT };
}

function reviewTask(): Task {
  return { ...useBoardStore.getState().cards['T-091'], status: 'you_review' };
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});
afterEach(cleanup);

describe('ArtifactMessage', () => {
  it('最新版は展開して本文とレビュー案内を出す（レビュー局面）', () => {
    render(
      <ArtifactMessage
        task={reviewTask()}
        artifact={art(1, '# 最終レポート')}
        previous={undefined}
        isLatest
        canAssignAi={true}
      />,
    );
    expect(screen.getByText('成果物 v1')).toBeInTheDocument();
    expect(screen.getByText('最新')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '最終レポート' })).toBeInTheDocument();
    // レビュー導線（何をすればよいか）
    expect(screen.getByText(/これが最新の成果物です/)).toBeInTheDocument();
    // 最新版には編集・再生成
    expect(screen.getByText('編集')).toBeInTheDocument();
    expect(screen.getByText('再生成')).toBeInTheDocument();
  });

  it('過去版は折りたたみ（クリックで展開）', () => {
    render(
      <ArtifactMessage
        task={reviewTask()}
        artifact={art(1, '# v1本文')}
        previous={undefined}
        isLatest={false}
        canAssignAi={false}
      />,
    );
    // 折りたたみ時は本文が見えない
    expect(screen.queryByRole('heading', { name: 'v1本文' })).toBeNull();
    expect(screen.getByText('クリックで展開')).toBeInTheDocument();
    fireEvent.click(screen.getByText('成果物 v1'));
    expect(screen.getByRole('heading', { name: 'v1本文' })).toBeInTheDocument();
  });

  it('前版があれば差分トグルで前版との差分を表示する（#20）', () => {
    render(
      <ArtifactMessage
        task={reviewTask()}
        artifact={art(2, 'A\nB\nC')}
        previous={art(1, 'A\nX\nC')}
        isLatest
        canAssignAi={false}
      />,
    );
    fireEvent.click(screen.getByText('前版との差分'));
    expect(screen.getByText('v1 → v2 の差分')).toBeInTheDocument();
  });
});
