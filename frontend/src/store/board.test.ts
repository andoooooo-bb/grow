import { beforeEach, describe, expect, it } from 'vitest';
import { boardFixture } from '../test/boardFixture.ts';
import {
  createInitialBoardState,
  deriveAiCount,
  deriveRuleCount,
  deriveYouCount,
  useBoardStore,
} from './board.ts';

describe('派生カウンタ（§5.1）', () => {
  const fixture = boardFixture();

  it('youCount = owner が human かつ status≠done のカード数 = 6', () => {
    // breakdown, spec, you_todo ×2, you_review, reviewing の6件
    expect(deriveYouCount(fixture.cards)).toBe(6);
  });

  it('aiCount = status が ai_work または queued のカード数 = 3', () => {
    // queued ×2, ai_work ×1 の3件
    expect(deriveAiCount(fixture.cards)).toBe(3);
  });

  it('ruleCount = rules 総数 = 5', () => {
    expect(deriveRuleCount(fixture.rules)).toBe(5);
  });
});

describe('board store（§2.3 / §5.3）', () => {
  beforeEach(() => {
    useBoardStore.setState(createInitialBoardState());
  });

  it('初期状態が §2.3 の BoardState 形をもつ', () => {
    const s = useBoardStore.getState();
    expect(s.cards).toEqual({});
    expect(s.lanes).toEqual([]);
    expect(s.rules).toEqual([]);
    expect(s.selectedId).toBeNull();
    expect(s.panelMode).toBe('detail');
    expect(s.showKnowledge).toBe(false);
    expect(s.chat).toEqual({});
    expect(s.proposal).toEqual({});
    expect(s.learn).toEqual({});
    expect(s.artifacts).toEqual({});
    expect(s.drafts).toEqual({});
  });

  it('setBoard が cards/lanes/rules を正規化ストアへ反映する', () => {
    useBoardStore.getState().setBoard(boardFixture());
    const s = useBoardStore.getState();
    expect(Object.keys(s.cards)).toHaveLength(11);
    expect(s.lanes.map((l) => l.key)).toEqual([
      'backlog',
      'todo',
      'progress',
      'review',
      'done',
    ]);
    expect(s.lanes[1].cardIds).toEqual(['T-104', 'T-109', 'T-112']);
    expect(s.rules).toHaveLength(5);
  });

  it('select(id) は selectedId を設定し panelMode を detail に戻す', () => {
    useBoardStore.setState({ panelMode: 'chat' });
    useBoardStore.getState().select('T-098');
    expect(useBoardStore.getState().selectedId).toBe('T-098');
    expect(useBoardStore.getState().panelMode).toBe('detail');
  });

  it('closePanel() は selectedId を null に戻す', () => {
    useBoardStore.getState().select('T-098');
    useBoardStore.getState().closePanel();
    expect(useBoardStore.getState().selectedId).toBeNull();
  });

  it('openKnowledge/closeKnowledge が showKnowledge をトグルする', () => {
    useBoardStore.getState().openKnowledge();
    expect(useBoardStore.getState().showKnowledge).toBe(true);
    useBoardStore.getState().closeKnowledge();
    expect(useBoardStore.getState().showKnowledge).toBe(false);
  });

  it('setDraft がタスクごとのコンポーザ入力を保持する', () => {
    useBoardStore.getState().setDraft('T-098', '修正お願いします');
    expect(useBoardStore.getState().drafts['T-098']).toBe('修正お願いします');
  });
});
