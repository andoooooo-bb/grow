// ライブ実況セクション（#24）: ai_work 中に実行AIの生成テキスト（artifact.delta の連結 =
// store.liveDraft）をタイプライターのように描画する。Markdown ＋ 点滅カーソル「▍」で
// 「いま書いている」を見せ、生成に自動スクロールで追従する。
// 完了時は artifact.created が liveDraft をクリアし、確定版（ArtifactSection）に差し替わる。

import { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import './LiveDraftSection.css';

interface LiveDraftSectionProps {
  task: Task;
}

export function LiveDraftSection({ task }: LiveDraftSectionProps) {
  const draft = useBoardStore((s) => s.liveDraft[task.id]);
  const previewRef = useRef<HTMLDivElement | null>(null);

  // 生成に追従して常に最新行が見えるよう自動スクロール（テキスト更新のたび）
  useEffect(() => {
    const el = previewRef.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [draft]);

  // ai_work 中かつ実況テキストがあるときのみ表示（§5.5 準拠で空状態文言も出さない）
  if (task.status !== 'ai_work' || draft === undefined || draft === '') return null;

  return (
    <section className="live-draft" aria-label="成果物を生成中">
      <div className="live-draft__heading">
        <span className="live-draft__title">成果物（レポート）</span>
        <span className="live-draft__badge">生成中…</span>
      </div>
      <div className="live-draft__preview" ref={previewRef}>
        {/* GFM（比較表・§00 #1 レポートの核）を有効化。確定版プレビューと同条件 */}
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown>
        <span className="live-draft__cursor" aria-hidden="true">
          ▍
        </span>
      </div>
    </section>
  );
}
