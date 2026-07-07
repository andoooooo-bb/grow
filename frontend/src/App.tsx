// アプリのルート: TanStack Query で GET /api/board を取得し、正規化ストアへ setBoard、
// TopBar + Board を描画する（§03 レイアウト: 固定トップバー54px ＋ flex:1 ボディ）。

import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Board } from './components/board/Board';
import { TopBar } from './components/board/TopBar';
import { getBoard } from './lib/api.ts';
import { useBoardStore } from './store/board.ts';
import './styles/tokens.css';
import './App.css';

function BoardScreen() {
  const setBoard = useBoardStore((s) => s.setBoard);
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
          <Board />
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

  return (
    <QueryClientProvider client={queryClient}>
      <BoardScreen />
    </QueryClientProvider>
  );
}

export default App;
