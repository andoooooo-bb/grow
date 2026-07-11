import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CI_NOTICE_TEMPLATE,
  createInitialBoardState,
  useBoardStore,
} from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { KnowledgeProposal, StatsResponse } from '../../types/api.ts';
import type { Rule } from '../../types/domain.ts';
import {
  formatStatsCost,
  INBOX_EMPTY_TEXT,
  KNOWLEDGE_EMPTY_TEXT,
  KnowledgeOverlay,
  topAppliedRules,
} from './KnowledgeOverlay';

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function rule(id: string): Rule {
  const r = useBoardStore.getState().rules.find((x) => x.id === id);
  if (r === undefined) throw new Error(`fixture にルールが無い: ${id}`);
  return r;
}

beforeEach(() => {
  useBoardStore.setState({
    ...createInitialBoardState(),
    ...boardFixture(),
    showKnowledge: true,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('KnowledgeOverlay: 表示（§3.4）', () => {
  it('showKnowledge=false では何も描画しない', () => {
    useBoardStore.setState({ showKnowledge: false });
    const { container } = render(<KnowledgeOverlay />);
    expect(container).toBeEmptyDOMElement();
  });

  it('ヘッダ（タイトル・説明文・閉じる✕）を表示する', () => {
    render(<KnowledgeOverlay />);
    expect(screen.getByText('ナレッジ — 学習した働き方')).toBeInTheDocument();
    expect(screen.getByText(/AIは作業を始める前に/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '閉じる' })).toBeInTheDocument();
  });

  it('personal は「あなたのルール」、team は「チームのルール（形式知）」へ振り分ける', () => {
    const { container } = render(<KnowledgeOverlay />);
    expect(screen.getByText('あなたのルール')).toBeInTheDocument();
    expect(screen.getByText('チームのルール（形式知）')).toBeInTheDocument();

    // fixture: personal = K-01..K-03（3件）/ team = K-04, K-05（2件）
    const counts = [...container.querySelectorAll('.knowledge__section-count')];
    expect(counts.map((el) => el.textContent)).toEqual(['3', '2']);
    const teamCards = [...container.querySelectorAll('.knowledge__rule--team')];
    expect(teamCards).toHaveLength(2);
    expect(screen.getByText(rule('K-01').text)).toBeInTheDocument();
    expect(screen.getByText(rule('K-04').text)).toBeInTheDocument();
  });

  it('確度ドット（high緑/med琥珀/low灰）をルールごとに出す', () => {
    useBoardStore.setState({
      rules: [
        { ...rule('K-01'), confidence: 'high' },
        { ...rule('K-03'), confidence: 'med' },
        { ...rule('K-02'), id: 'K-09', confidence: 'low' },
      ],
    });
    const { container } = render(<KnowledgeOverlay />);
    expect(container.querySelectorAll('.knowledge__conf--high')).toHaveLength(1);
    expect(container.querySelectorAll('.knowledge__conf--med')).toHaveLength(1);
    expect(container.querySelectorAll('.knowledge__conf--low')).toHaveLength(1);
  });

  it('下段に「出典: {source}」と「適用 {N}回」を表示する', () => {
    render(<KnowledgeOverlay />);
    expect(screen.getByText('出典: T-098 で2回同じ修正')).toBeInTheDocument();
    expect(screen.getByText('適用 6回')).toBeInTheDocument(); // K-01
    expect(screen.getByText('適用 9回')).toBeInTheDocument(); // K-04（team）
  });

  it('isNew=true のルールにだけ NEW バッジを出す', () => {
    useBoardStore.setState((s) => ({
      rules: s.rules.map((r) => (r.id === 'K-03' ? { ...r, isNew: true } : r)),
    }));
    const { container } = render(<KnowledgeOverlay />);
    expect(container.querySelectorAll('.knowledge__new')).toHaveLength(1);
    expect(screen.getByText('NEW')).toBeInTheDocument();
  });

  it('rules が空なら両セクションに空状態文言を出す（§5.5）', () => {
    useBoardStore.setState({ rules: [] });
    render(<KnowledgeOverlay />);
    expect(screen.getAllByText(KNOWLEDGE_EMPTY_TEXT)).toHaveLength(2);
  });
});

describe('KnowledgeOverlay: 閉じる（§5.3 openKnowledge/closeKnowledge/stop）', () => {
  it('遮蔽クリックで閉じる', () => {
    const { container } = render(<KnowledgeOverlay />);
    fireEvent.click(container.querySelector('.knowledge')!);
    expect(useBoardStore.getState().showKnowledge).toBe(false);
    expect(container).toBeEmptyDOMElement();
  });

  it('パネル内側のクリックでは閉じない（stopPropagation）', () => {
    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('dialog'));
    expect(useBoardStore.getState().showKnowledge).toBe(true);
  });

  it('✕ ボタンで閉じる', () => {
    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('button', { name: '閉じる' }));
    expect(useBoardStore.getState().showKnowledge).toBe(false);
  });
});

describe('KnowledgeOverlay: チームへ昇格（§1.8 promoteRule）', () => {
  it('昇格ボタンは personal のルールにだけ出る', () => {
    const { container } = render(<KnowledgeOverlay />);
    const buttons = screen.getAllByRole('button', { name: 'チームへ昇格 ↑' });
    expect(buttons).toHaveLength(3); // personal K-01..K-03 のみ
    for (const card of container.querySelectorAll('.knowledge__rule--team')) {
      expect(card.querySelector('.knowledge__promote')).toBeNull();
    }
  });

  it('昇格で POST /rules/:id/promote → scope=team になりチームのセクションへ移動（NEW 再表示）', async () => {
    const promoted: Rule = { ...rule('K-01'), scope: 'team' };
    const fetchMock = vi.fn(async () => jsonResponse(200, promoted));
    vi.stubGlobal('fetch', fetchMock);

    const { container } = render(<KnowledgeOverlay />);
    // 1つ目の昇格ボタン = personal 先頭 K-01
    fireEvent.click(screen.getAllByRole('button', { name: 'チームへ昇格 ↑' })[0]);

    expect(fetchMock).toHaveBeenCalledWith('/api/rules/K-01/promote', {
      method: 'POST',
    });
    await waitFor(() => {
      const counts = [...container.querySelectorAll('.knowledge__section-count')];
      expect(counts.map((el) => el.textContent)).toEqual(['2', '3']);
    });
    // K-01 のカードが team 側スタイルになり、NEW が再表示される
    const updated = useBoardStore.getState().rules.find((r) => r.id === 'K-01');
    expect(updated?.scope).toBe('team');
    expect(updated?.isNew).toBe(true);
    expect(screen.getByText('NEW')).toBeInTheDocument();
    expect(container.querySelectorAll('.knowledge__rule--team')).toHaveLength(3);
  });
});

// ---- #20: 適用フラッシュ＋適用回数カウントアップ ----

describe('KnowledgeOverlay: 適用フラッシュ（#20）', () => {
  it('justApplied のカードに flash、「適用 N回」に bump クラスを付与する', () => {
    useBoardStore.setState({ justApplied: { 'K-01': 1234 } });
    const { container } = render(<KnowledgeOverlay />);

    const flashed = [...container.querySelectorAll('.knowledge__rule--flash')];
    expect(flashed).toHaveLength(1);
    expect(flashed[0].textContent).toContain(rule('K-01').text);

    const bumped = container.querySelectorAll('.knowledge__applied--bump');
    expect(bumped).toHaveLength(1);
    expect(bumped[0].textContent).toBe('適用 6回'); // K-01 の適用回数
  });

  it('justApplied が空ならフラッシュ/バンプは付かない', () => {
    const { container } = render(<KnowledgeOverlay />);
    expect(container.querySelector('.knowledge__rule--flash')).toBeNull();
    expect(container.querySelector('.knowledge__applied--bump')).toBeNull();
  });
});

// ---- #25: 学習ダッシュボード（スタットタイル＋スパークライン＋TOP3） ----

function statsFixture(partial: Partial<StatsResponse> = {}): StatsResponse {
  // 直近14日（古い順）。後半に向かって適用が増える学習曲線
  const ruleApplications = Array.from({ length: 14 }, (_, i) => ({
    date: `2026-07-${String(i + 1).padStart(2, '0')}`,
    count: i < 7 ? 0 : i - 6,
  }));
  return {
    aiDoneCount: 4,
    totalCostUsd: 0.0231,
    totalTokens: 12345,
    ruleApplications,
    ruleApplicationsTotal: 36,
    rejectCount: 2,
    rulesCount: 5,
    ...partial,
  };
}

describe('KnowledgeOverlay: 学習ダッシュボード（#25）', () => {
  it('開くと GET /api/stats を読み込み、スタットタイル4枚を表示する', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, statsFixture()));
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<KnowledgeOverlay />);

    expect(fetchMock).toHaveBeenCalledWith('/api/stats', undefined);
    await waitFor(() =>
      expect(container.querySelectorAll('.knowledge__tile')).toHaveLength(4),
    );
    const tiles = [...container.querySelectorAll('.knowledge__tile')];
    expect(tiles.map((t) => t.textContent)).toEqual([
      '4AI完了',
      '36ルール適用',
      '2差し戻し',
      '$0.0231累計コスト',
    ]);
  });

  it('直近14日のルール適用スパークライン（素SVG）を描画する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, statsFixture())));
    const { container } = render(<KnowledgeOverlay />);

    await waitFor(() =>
      expect(container.querySelector('.knowledge__spark')).toBeInTheDocument(),
    );
    expect(screen.getByText('ルール適用の推移（直近14日）')).toBeInTheDocument();
    // 合計 = 1+2+…+7 = 28 回。単系列なので凡例は無し（見出しが系列名を兼ねる）
    const svg = screen.getByRole('img', { name: '直近14日のルール適用 計28回' });
    const polyline = svg.querySelector('polyline.knowledge__spark-line');
    expect(polyline).not.toBeNull();
    expect(polyline?.getAttribute('points')?.split(' ')).toHaveLength(14);
    // 面ウォッシュと端点ドット（サーフェスリング）も描かれる
    expect(svg.querySelector('.knowledge__spark-area')).not.toBeNull();
    expect(svg.querySelector('circle.knowledge__spark-dot')).not.toBeNull();
  });

  it('TOP3ルール（applied 降順: K-02→K-04→K-01）を表示する', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, statsFixture())));
    const { container } = render(<KnowledgeOverlay />);

    await waitFor(() =>
      expect(container.querySelectorAll('.knowledge__top-rule')).toHaveLength(3),
    );
    expect(screen.getByText('よく効いているルール TOP3')).toBeInTheDocument();
    const rows = [...container.querySelectorAll('.knowledge__top-rule')];
    expect(
      rows.map((row) => within(row as HTMLElement).getByText(/^K-\d+$/).textContent),
    ).toEqual(['K-02', 'K-04', 'K-01']);
    expect(within(rows[0] as HTMLElement).getByText('適用 14回')).toBeInTheDocument();
  });

  it('stats 未取得（失敗・形の違う応答）ではダッシュボードを出さない', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { unexpected: true }));
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<KnowledgeOverlay />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.querySelector('.knowledge__dashboard')).toBeNull();
    // 既存のルール一覧はそのまま表示される
    expect(screen.getByText('あなたのルール')).toBeInTheDocument();
  });
});

describe('topAppliedRules / formatStatsCost（#25 単体）', () => {
  it('applied 0 回は除外し、降順（同数は id 昇順）で最大3件', () => {
    const rules = useBoardStore.getState().rules.map((r) =>
      r.id === 'K-03' ? { ...r, applied: 0 } : r,
    );
    expect(topAppliedRules(rules).map((r) => r.id)).toEqual(['K-02', 'K-04', 'K-01']);
    expect(topAppliedRules(rules, 2).map((r) => r.id)).toEqual(['K-02', 'K-04']);
    expect(topAppliedRules([])).toEqual([]);
  });

  it('コストは $1 未満 4桁 / 以上 2桁（TraceSection と同規約）', () => {
    expect(formatStatsCost(0)).toBe('$0.0000');
    expect(formatStatsCost(0.0231)).toBe('$0.0231');
    expect(formatStatsCost(12.5)).toBe('$12.50');
  });
});

// ---- #26: 夜間ナレッジCI（受信箱タブ・採用/却下・手動実行） ----

function proposalFixture(partial: Partial<KnowledgeProposal> = {}): KnowledgeProposal {
  return {
    id: 'prop-1',
    workspaceId: 'ws-1',
    kind: 'conflict',
    text: '報告書は常体で書く',
    scope: 'personal',
    tags: [],
    confidence: 'med',
    source: 'ナレッジCI: K-04 と K-06 の矛盾検出',
    targetRuleIds: ['K-04'],
    note: '「敬体」と「常体」の方針が矛盾しています',
    sourceTaskId: null,
    status: 'pending',
    createdAt: '2026-07-11T03:00:00Z',
    decidedAt: null,
    ...partial,
  };
}

describe('KnowledgeOverlay: 受信箱タブ（#26）', () => {
  it('タブ「ルール一覧 / 受信箱 N」を表示し、バッジに pending 件数を出す', () => {
    useBoardStore.setState({
      proposals: [proposalFixture(), proposalFixture({ id: 'prop-2', kind: 'demote' })],
    });
    const { container } = render(<KnowledgeOverlay />);
    expect(screen.getByRole('tab', { name: 'ルール一覧' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /受信箱/ })).toBeInTheDocument();
    expect(container.querySelector('.knowledge__tab-badge')?.textContent).toBe('2');
    // 既定はルール一覧タブ（既存2セクションが見えている）
    expect(screen.getByText('あなたのルール')).toBeInTheDocument();
  });

  it('受信箱タブへ切り替えると提案カード（kindバッジ・対比表示・note）が出る', () => {
    useBoardStore.setState({ proposals: [proposalFixture()] });
    const { container } = render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('tab', { name: /受信箱/ }));

    // kind バッジ（矛盾=アンバーは CSS クラスで表現）
    const badge = container.querySelector('.knowledge__kind--conflict');
    expect(badge?.textContent).toBe('矛盾');
    // AI の判断説明（note）
    expect(screen.getByText(/「敬体」と「常体」の方針が矛盾/)).toBeInTheDocument();
    // 対象ルールの現文（K-04 の text を store の rules から解決）と提案文の対比
    expect(screen.getByText(rule('K-04').text)).toBeInTheDocument();
    expect(screen.getByText('報告書は常体で書く')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '採用' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '却下' })).toBeInTheDocument();
  });

  it('kind ごとのバッジ文言（新規/統合/矛盾/棚卸し）を出す', () => {
    useBoardStore.setState({
      proposals: [
        proposalFixture({ id: 'p1', kind: 'distill', targetRuleIds: [] }),
        proposalFixture({ id: 'p2', kind: 'merge', targetRuleIds: ['K-01', 'K-03'] }),
        proposalFixture({ id: 'p3', kind: 'conflict' }),
        proposalFixture({ id: 'p4', kind: 'demote', text: '', targetRuleIds: ['K-01'] }),
      ],
    });
    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('tab', { name: /受信箱/ }));
    expect(screen.getByText('新規')).toBeInTheDocument();
    expect(screen.getByText('統合')).toBeInTheDocument();
    expect(screen.getByText('矛盾')).toBeInTheDocument();
    expect(screen.getByText('棚卸し')).toBeInTheDocument();
  });

  it('受信箱が空なら空状態文言を出す', () => {
    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('tab', { name: /受信箱/ }));
    expect(screen.getByText(INBOX_EMPTY_TEXT)).toBeInTheDocument();
  });

  it('採用で POST adopt → 提案が消え、新ルールが NEW 付きで rules へ・対象は archived', async () => {
    useBoardStore.setState({ proposals: [proposalFixture()] });
    const created: Rule = {
      ...rule('K-04'),
      id: 'K-06',
      text: '報告書は常体で書く',
      applied: 0,
    };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, {
        proposal: proposalFixture({ status: 'adopted' }),
        rule: created,
        archivedRuleIds: ['K-04'],
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('tab', { name: /受信箱/ }));
    fireEvent.click(screen.getByRole('button', { name: '採用' }));

    expect(fetchMock).toHaveBeenCalledWith('/api/knowledge/proposals/prop-1/adopt', {
      method: 'POST',
    });
    await waitFor(() =>
      expect(useBoardStore.getState().proposals).toEqual([]),
    );
    const rules = useBoardStore.getState().rules;
    expect(rules.find((r) => r.id === 'K-06')?.isNew).toBe(true);
    expect(rules.find((r) => r.id === 'K-04')?.archived).toBe(true);
    // アーカイブ済みはルール一覧から消えている（チームのルールは K-05 のみ）
    expect(screen.getByText(INBOX_EMPTY_TEXT)).toBeInTheDocument();
  });

  it('却下で POST dismiss → 提案だけが消える（rules は不変）', async () => {
    useBoardStore.setState({ proposals: [proposalFixture({ kind: 'demote', text: '' })] });
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, proposalFixture({ status: 'dismissed' })),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<KnowledgeOverlay />);
    fireEvent.click(screen.getByRole('tab', { name: /受信箱/ }));
    const before = useBoardStore.getState().rules;
    fireEvent.click(screen.getByRole('button', { name: '却下' }));

    expect(fetchMock).toHaveBeenCalledWith('/api/knowledge/proposals/prop-1/dismiss', {
      method: 'POST',
    });
    await waitFor(() => expect(useBoardStore.getState().proposals).toEqual([]));
    expect(useBoardStore.getState().rules).toEqual(before);
  });

  it('アーカイブ済みルールはルール一覧・件数から除外される', () => {
    useBoardStore.setState((s) => ({
      rules: s.rules.map((r) => (r.id === 'K-01' ? { ...r, archived: true } : r)),
    }));
    const { container } = render(<KnowledgeOverlay />);
    const counts = [...container.querySelectorAll('.knowledge__section-count')];
    expect(counts.map((el) => el.textContent)).toEqual(['2', '2']); // personal 3→2
    expect(screen.queryByText(rule('K-01').text)).not.toBeInTheDocument();
  });
});

describe('KnowledgeOverlay: 今すぐメンテナンス実行（#26）', () => {
  it('ボタンで POST /api/knowledge/ci/run → 受信箱を再読込し件数トーストを出す', async () => {
    const proposal = proposalFixture();
    const fetchMock = vi.fn(async (path: string) => {
      if (path === '/api/knowledge/ci/run') {
        return jsonResponse(200, { runId: 'run-1', proposalsCreated: 1 });
      }
      if (path === '/api/knowledge/proposals') {
        return jsonResponse(200, { proposals: [proposal] });
      }
      return jsonResponse(200, statsFixture());
    });
    vi.stubGlobal('fetch', fetchMock);

    const { container } = render(<KnowledgeOverlay />);
    fireEvent.click(
      screen.getByRole('button', { name: '⟳ 今すぐメンテナンス実行' }),
    );

    expect(fetchMock).toHaveBeenCalledWith('/api/knowledge/ci/run', { method: 'POST' });
    await waitFor(() =>
      expect(screen.getByText(CI_NOTICE_TEMPLATE(1))).toBeInTheDocument(),
    );
    // 受信箱バッジもライブ更新される
    expect(container.querySelector('.knowledge__tab-badge')?.textContent).toBe('1');
    expect(useBoardStore.getState().ciRunning).toBe(false);
  });

  it('実行中はボタンが無効（スピナー表示）', () => {
    useBoardStore.setState({ ciRunning: true });
    const { container } = render(<KnowledgeOverlay />);
    const button = screen.getByRole('button', { name: /実行中/ });
    expect(button).toBeDisabled();
    expect(container.querySelector('.knowledge__spinner')).not.toBeNull();
  });

  it('失敗時は boardError を立ててボタンを再度有効にする', async () => {
    const fetchMock = vi.fn(async (path: string) =>
      path === '/api/knowledge/ci/run'
        ? jsonResponse(500, {})
        : jsonResponse(200, statsFixture()),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<KnowledgeOverlay />);
    fireEvent.click(
      screen.getByRole('button', { name: '⟳ 今すぐメンテナンス実行' }),
    );
    await waitFor(() =>
      expect(useBoardStore.getState().boardError).toBe(
        'メンテナンスの実行に失敗しました',
      ),
    );
    expect(useBoardStore.getState().ciRunning).toBe(false);
    expect(useBoardStore.getState().ciNotice).toBeNull();
  });
});

describe('applyRuleProposalCreated（#26 SSE ライブ更新）', () => {
  it('新提案を受信箱の先頭へ追加し、既知 id は重複させない（冪等）', () => {
    const existing = proposalFixture();
    useBoardStore.setState({ proposals: [existing] });
    const incoming = proposalFixture({ id: 'prop-2', kind: 'distill' });
    useBoardStore
      .getState()
      .applyRuleProposalCreated({ count: 2, proposals: [existing, incoming] });
    expect(useBoardStore.getState().proposals.map((p) => p.id)).toEqual([
      'prop-2',
      'prop-1',
    ]);
    // 同じイベントを再適用しても増えない
    useBoardStore
      .getState()
      .applyRuleProposalCreated({ count: 2, proposals: [existing, incoming] });
    expect(useBoardStore.getState().proposals).toHaveLength(2);
  });

  it('closeKnowledge で ciNotice をクリアする', () => {
    useBoardStore.setState({ ciNotice: CI_NOTICE_TEMPLATE(3) });
    useBoardStore.getState().closeKnowledge();
    expect(useBoardStore.getState().ciNotice).toBeNull();
    expect(useBoardStore.getState().showKnowledge).toBe(false);
  });
});
