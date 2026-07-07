// 成果物セクション（§3.3.2 c-2 / §00 #2, #10）: artifacts がある時のみ表示。
// 見出し「成果物（レポート）」＋版セレクタ（複数版時のみ・最新デフォルト）＋
// Markdown プレビュー＋「編集」（textarea → 新版保存）＋「再生成」（assignAi 再実行）。
// リッチエディタは持たない（§00 #12: Markdown を textarea で直接編集）。

import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import './ArtifactSection.css';

interface ArtifactSectionProps {
  task: Task;
  /** 「再生成」の活性（「AIにまかせる」と同条件。Drawer が計算して渡す） */
  canAssignAi: boolean;
}

export function ArtifactSection({ task, canAssignAi }: ArtifactSectionProps) {
  const artifacts = useBoardStore((s) => s.artifacts[task.id]);
  const assignAi = useBoardStore((s) => s.assignAi);
  const saveArtifact = useBoardStore((s) => s.saveArtifact);
  // 選択中の版（null = 最新）。最新をデフォルト表示し、新版が届いたら自動で追従する
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  // 編集中の Markdown 生文字列（null = プレビュー表示）
  const [editText, setEditText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // §3.3.2 c-2: 成果物がある時のみ表示（§5.5 準拠で空状態文言も出さない）
  if (artifacts === undefined || artifacts.length === 0) return null;

  // version 昇順・末尾が最新（GET /artifacts / applyArtifactCreated が昇順を維持する）
  const latest = artifacts[artifacts.length - 1];
  const selected =
    selectedVersion === null
      ? undefined
      : artifacts.find((a) => a.version === selectedVersion);
  const current = selected ?? latest;

  const save = async () => {
    if (editText === null) return;
    setSaving(true);
    const ok = await saveArtifact(task.id, editText);
    setSaving(false);
    if (ok) {
      // 保存で新版が積まれる（POST 応答/SSE で store 反映）→ 選択版を最新へ戻す
      setEditText(null);
      setSelectedVersion(null);
    }
  };

  return (
    <section className="artifact">
      <div className="artifact__heading">
        <span className="artifact__title">成果物（レポート）</span>
        {artifacts.length > 1 && (
          <select
            className="artifact__version"
            aria-label="版を選択"
            value={current.version}
            onChange={(e) => setSelectedVersion(Number(e.target.value))}
          >
            {artifacts.map((a) => (
              <option key={a.id} value={a.version}>
                v{a.version}
              </option>
            ))}
          </select>
        )}
      </div>
      {editText === null ? (
        <>
          <div className="artifact__preview">
            {/* GFM（比較表・§00 #1 レポートの核）を有効化 */}
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{current.contentMd}</ReactMarkdown>
          </div>
          <div className="artifact__actions">
            <button
              type="button"
              className="artifact__button"
              onClick={() => setEditText(current.contentMd)}
            >
              編集
            </button>
            {/* 再生成 = assignAi 再実行（確認ダイアログ不要。活性は「AIにまかせる」と同条件） */}
            <button
              type="button"
              className="artifact__button"
              disabled={!canAssignAi}
              onClick={() => void assignAi(task.id)}
            >
              再生成
            </button>
          </div>
        </>
      ) : (
        <>
          <textarea
            className="artifact__editor"
            aria-label="成果物のMarkdown"
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
          />
          <div className="artifact__actions">
            <button
              type="button"
              className="artifact__button artifact__button--save"
              disabled={saving}
              onClick={() => void save()}
            >
              保存
            </button>
            <button
              type="button"
              className="artifact__button"
              disabled={saving}
              onClick={() => setEditText(null)}
            >
              キャンセル
            </button>
          </div>
        </>
      )}
    </section>
  );
}
