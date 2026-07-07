import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInitialBoardState, useBoardStore } from '../../store/board.ts';
import { boardFixture } from '../../test/boardFixture.ts';
import type { ChatMessage } from '../../types/domain.ts';
import { ChatMode } from './ChatMode';

const AT = '2026-07-07T00:00:00Z';

function makeChatMessage(
  partial: Partial<ChatMessage> & Pick<ChatMessage, 'id' | 'text'>,
): ChatMessage {
  return { taskId: 'T-130', author: 'ai', createdAt: AT, ...partial };
}

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** T-130（breakdown）で chat 状態を積んで ChatMode を描画する */
function renderChatMode(messages: ChatMessage[]) {
  useBoardStore.setState((s) => ({
    panelMode: 'chat',
    chat: { ...s.chat, 'T-130': messages },
  }));
  const task = useBoardStore.getState().cards['T-130'];
  return render(<ChatMode task={task} />);
}

beforeEach(() => {
  useBoardStore.setState({ ...createInitialBoardState(), ...boardFixture() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ChatMode: ヘッダ帯とメッセージ描画（§3.3.3）', () => {
  it('ヘッダ帯「壁打ちチャット — 分解の意識合わせ」と「← 戻る」を表示する', () => {
    renderChatMode([]);
    expect(
      screen.getByText('壁打ちチャット — 分解の意識合わせ'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '← 戻る' })).toBeInTheDocument();
  });

  it('「← 戻る」で panelMode=detail に戻る（§5.3 backToDetail）', () => {
    renderChatMode([]);
    fireEvent.click(screen.getByRole('button', { name: '← 戻る' }));
    expect(useBoardStore.getState().panelMode).toBe('detail');
  });

  it('ai=パープルアバター"AI"＋名義「Grow」/ human=アンバー"YK"＋名義「あなた」で描画する', () => {
    const { container } = renderChatMode([
      makeChatMessage({ id: 'm-1', text: '① 公開したい時期は？' }),
      makeChatMessage({ id: 'm-2', author: 'human', text: '来月中に公開したい' }),
    ]);

    expect(screen.getByText('① 公開したい時期は？')).toBeInTheDocument();
    expect(screen.getByText('来月中に公開したい')).toBeInTheDocument();
    expect(screen.getByText('Grow')).toBeInTheDocument();
    expect(screen.getByText('あなた')).toBeInTheDocument();

    // アバター: ai はパープル（#5d4eb0 を当てる --ai クラス）で "AI"、human は --human で "YK"
    const avatars = container.querySelectorAll('.chat-mode__avatar');
    expect(avatars).toHaveLength(2);
    expect(avatars[0]).toHaveClass('chat-mode__avatar--ai');
    expect(avatars[0]).toHaveTextContent('AI');
    expect(avatars[1]).toHaveClass('chat-mode__avatar--human');
    expect(avatars[1]).toHaveTextContent('YK');
  });

  it('proposal がある時のみ分解候補カードをメッセージ領域に描画する', () => {
    renderChatMode([makeChatMessage({ id: 'm-1', text: '初期質問' })]);
    expect(screen.queryByText(/提案された分解/)).toBeNull();

    useBoardStore.setState((s) => ({
      proposal: {
        ...s.proposal,
        'T-130': [{ title: '情報設計・サイトマップ作成', owner: 'ai' as const }],
      },
    }));
    cleanup();
    renderChatMode([makeChatMessage({ id: 'm-1', text: '初期質問' })]);
    expect(screen.getByText('提案された分解 — 1 件')).toBeInTheDocument();
  });

  it('chatError があれば簡易エラーを表示する（§5.4）', () => {
    useBoardStore.setState((s) => ({
      chatError: { ...s.chatError, 'T-130': 'メッセージの送信に失敗しました' },
    }));
    renderChatMode([]);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'メッセージの送信に失敗しました',
    );
  });
});

describe('ChatMode: コンポーザ（variant=chat, §3.3.3 / §00 #11）', () => {
  it('placeholder「前提や要望を伝える…」の入力に Enter で送信し、POST /chat {text} を呼ぶ', async () => {
    const created = makeChatMessage({
      id: 'm-9',
      author: 'human',
      text: '来月中に公開したい。実績は5件掲載する',
    });
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(201, created),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderChatMode([makeChatMessage({ id: 'm-1', text: '初期質問' })]);

    const input = screen.getByPlaceholderText('前提や要望を伝える…');
    fireEvent.change(input, {
      target: { value: '来月中に公開したい。実績は5件掲載する' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    // 楽観的更新（§5.4）: 即UI反映＋入力クリア
    expect(
      screen.getByText('来月中に公開したい。実績は5件掲載する'),
    ).toBeInTheDocument();
    expect(input).toHaveValue('');

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/tasks/T-130/chat',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      text: '来月中に公開したい。実績は5件掲載する',
    });
    // 確定版へ id 差し替え（件数は増えない）
    await waitFor(() =>
      expect(useBoardStore.getState().chat['T-130'].at(-1)?.id).toBe('m-9'),
    );
    expect(useBoardStore.getState().chat['T-130']).toHaveLength(2);
  });

  it('入力は chatDrafts に保持され、detail のコンポーザ（drafts）とは別領域', () => {
    renderChatMode([]);
    const input = screen.getByPlaceholderText('前提や要望を伝える…');
    fireEvent.change(input, { target: { value: '前提メモ' } });

    expect(useBoardStore.getState().chatDrafts['T-130']).toBe('前提メモ');
    expect(useBoardStore.getState().drafts['T-130']).toBeUndefined();
  });

  it('Shift+Enter は送信しない（改行。§00 #11）', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    renderChatMode([]);

    const input = screen.getByPlaceholderText('前提や要望を伝える…');
    fireEvent.change(input, { target: { value: '1行目' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(input).toHaveValue('1行目');
  });
});
