import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import { AppliedRules } from './AppliedRules';

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
});

function task(id: string) {
  const t = useBoardStore.getState().cards[id];
  if (t === undefined) throw new Error(`fixture にタスクが無い: ${id}`);
  return t;
}

describe('AppliedRules（§3.3.2b）', () => {
  it('見出し「◈ AIが着手時に前提にするルール」と件数を表示する', () => {
    render(<AppliedRules task={task('T-104')} />);
    expect(
      screen.getByText('◈ AIが着手時に前提にするルール'),
    ).toBeInTheDocument();
    // T-104（仕事, 調査）→ K-02, K-04, K-01, K-03 の4件
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('ルール文を retrieval と同一順序（K-02,K-04,K-01,K-03）で表示する', () => {
    const { container } = render(<AppliedRules task={task('T-104')} />);
    const texts = [...container.querySelectorAll('.applied-rules__text')].map(
      (el) => el.textContent,
    );
    expect(texts).toEqual([
      '絵文字は使わない。文体は簡潔・断定調に統一する', // K-02
      '社外向け文書は敬体。数値は必ず出典を明記する', // K-04
      'レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く', // K-01
      '競合調査は料金を表形式にし、各項目に出典URLを付ける', // K-03
    ]);
  });

  it('scopeミニバッジ: personal=「個人」/ team=「チーム」を各行に表示する', () => {
    const { container } = render(<AppliedRules task={task('T-104')} />);
    const badges = [...container.querySelectorAll('.applied-rules__scope')];
    // 順序は K-02(personal), K-04(team), K-01(personal), K-03(personal)
    expect(badges.map((el) => el.textContent)).toEqual([
      '個人',
      'チーム',
      '個人',
      '個人',
    ]);
    expect(badges[0]).toHaveClass('applied-rules__scope--personal');
    expect(badges[1]).toHaveClass('applied-rules__scope--team');
  });

  it('retrieval 0件なら何も描画しない（§5.5: 空状態文言も出さない）', () => {
    useBoardStore.setState({ rules: [] });
    const { container } = render(<AppliedRules task={task('T-104')} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/まだルールがありません/)).toBeNull();
  });
});
