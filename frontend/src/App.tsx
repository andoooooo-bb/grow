// アプリのルート: TanStack Query で GET /api/board を取得し、正規化ストアへ setBoard、
// TopBar + Board（＋選択時のみ Drawer, #7）を描画する。
// §03 レイアウト: 縦flex（固定トップバー54px ＋ flex:1 ボディ）。
// ボディは横flex で「ボード(flex:1)」＋「ドロワー(412px, 選択時のみ)」。

import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Board } from './components/board/Board';
import { TopBar } from './components/board/TopBar';
import { Drawer } from './components/drawer/Drawer';
import { getBoard } from './lib/api.ts';
import { connectEvents } from './lib/sse.ts';
import { useBoardStore } from './store/board.ts';
import './styles/tokens.css';
import './App.css';

function BoardScreen() {
  const setBoard = useBoardStore((s) => s.setBoard);
  const selectedId = useBoardStore((s) => s.selectedId);
  const { data, isPending, isError } = useQuery({
    queryKey: ['board'],
    queryFn: getBoard,
  });

  useEffect(() => {
    if (data) setBoard(data);
  }, [data, setBoard]);

  return (
    <div className="app">
      <TopBar />
      <div className="app__body">
        {isPending ? (
          <div className="app__status">ボードを読み込んでいます…</div>
        ) : isError ? (
          <div className="app__status">ボードの読み込みに失敗しました</div>
        ) : (
          <>
            <Board />
            {selectedId !== null && <Drawer />}
          </>
        )}
      </div>
    </div>
  );
}

function App() {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
      }),
  );

  // SSE 購読（§5.4 / #7）: 起動時に /api/events へ接続、アンマウントで切断
  useEffect(() => connectEvents(), []);

  return (
    <QueryClientProvider client={queryClient}>
      <BoardScreen />
    </QueryClientProvider>
  );
}

export default App;
