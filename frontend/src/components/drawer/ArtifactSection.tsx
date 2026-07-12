// 成果物セクション（§3.3.2 c-2 / §00 #2, #10）: artifacts がある時のみ表示。
// 見出し「成果物（レポート）」＋版セレクタ（複数版時のみ・最新デフォルト）＋
// Markdown プレビュー＋「編集」（textarea → 新版保存）＋「再生成」（assignAi 再実行）。
// リッチエディタは持たない（§00 #12: Markdown を textarea で直接編集）。
// #20: 「差分」トグルで直前版との行 diff（追加=緑 / 削除=赤・取り消し線）を表示し、
// 由来ルール（appliedRuleIds）のチップで『使うほど賢くなる』を差分で証明する。

import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { diffLines } from '../../lib/diff.ts';
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
  const rules = useBoardStore((s) => s.rules);
  const assignAi = useBoardStore((s) => s.assignAi);
  const saveArtifact = useBoardStore((s) => s.saveArtifact);
  const openKnowledge = useBoardStore((s) => s.openKnowledge);
  // 選択中の版（null = 最新）。最新をデフォルト表示し、新版が届いたら自動で追従する。
  // #25: TraceSection のハイライト・行クリックと連動するためストアで共有する
  const selectedVersion = useBoardStore((s) => s.artifactVersion[task.id] ?? null);
  const selectArtifactVersion = useBoardStore((s) => s.selectArtifactVersion);
  // 編集中の Markdown 生文字列（null = プレビュー表示）
  const [editText, setEditText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // #20: 差分表示トグル（選択版と直前版の行 diff。直前版が無い v1 ではプレビューに戻る）
  const [showDiff, setShowDiff] = useState(false);

  // §3.3.2 c-2: 成果物がある時のみ表示（§5.5 準拠で空状態文言も出さない）
  if (artifacts === undefined || artifacts.length === 0) return null;

  // version 昇順・末尾が最新（GET /artifacts / applyArtifactCreated が昇順を維持する）
  const latest = artifacts[artifacts.length - 1];
  const selected =
    selectedVersion === null
      ? undefined
      : artifacts.find((a) => a.version === selectedVersion);
  const current = selected ?? latest;
  // #20: 直前版（配列順 = version 昇順）。v1 には無い → 差分トグル非表示
  const currentIndex = artifacts.indexOf(current);
  const previous = currentIndex > 0 ? artifacts[currentIndex - 1] : undefined;
  // #20: この版の由来ルール（生成ジョブが注入した human_id。人の編集版は空）
  const appliedRuleIds = current.appliedRuleIds ?? [];

  const save = async () => {
    if (editText === null) return;
    setSaving(true);
    const ok = await saveArtifact(task.id, editText);
    setSaving(false);
    if (ok) {
      // 保存で新版が積まれる（POST 応答/SSE で store 反映）→ 選択版を最新へ戻す
      setEditText(null);
      selectArtifactVersion(task.id, null);
    }
  };

  return (
    <section className="artifact">
      <div className="artifact__heading">
        <span className="artifact__title">
          成果物（レポート）
          {current.version === latest.version ? (
            <span className="artifact__version-badge">最新 v{latest.version}</span>
          ) : (
            <span className="artifact__version-badge artifact__version-badge--old">
              v{current.version} を表示（最新 v{latest.version}）
            </span>
          )}
        </span>
        <div className="artifact__controls">
          {/* #20: 差分トグル（直前版がある版のみ。編集中は隠す） */}
          {editText === null && previous !== undefined && (
            <button
              type="button"
              className={`artifact__diff-toggle${showDiff ? ' artifact__diff-toggle--on' : ''}`}
              aria-pressed={showDiff}
              onClick={() => setShowDiff((v) => !v)}
            >
              差分
            </button>
          )}
          {artifacts.length > 1 && (
            <select
              className="artifact__version"
              aria-label="版を選択"
              value={current.version}
              onChange={(e) => selectArtifactVersion(task.id, Number(e.target.value))}
            >
              {artifacts.map((a) => (
                <option key={a.id} value={a.version}>
                  v{a.version}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>
      {/* レビュー局面（you_review/reviewing）: 何をすればよいかを明示（レビュー導線） */}
      {(task.status === 'you_review' || task.status === 'reviewing') && (
        <p className="artifact__review-guide">
          これが実行AIの最終成果物です。内容を確認し、問題なければ上部の
          <strong>「完了にする」</strong>、直したい点があれば
          <strong>「差し戻す」</strong>（理由を書くとAIが直します）か、下のコメント欄で指示してください。
        </p>
      )}
      {/* 複数版 = AIが見直して改善した証跡。差分で変化を確認できることを明示（#20/レビューUX） */}
      {editText === null && artifacts.length > 1 && (
        <p className="artifact__revision-hint">
          AIが {artifacts.length - 1} 回見直して改善しました（全 {artifacts.length} 版）。
          <button
            type="button"
            className="artifact__hint-link"
            onClick={() => {
              if (previous !== undefined) setShowDiff(true);
            }}
          >
            {showDiff ? '差分を表示中' : '差分を見る'}
          </button>
        </p>
      )}
      {/* #20: 由来ルールチップ（例 K-01）。ツールチップでルール文、クリックでナレッジへ */}
      {editText === null && appliedRuleIds.length > 0 && (
        <div className="artifact__rules" aria-label="この版に適用されたルール">
          <span className="artifact__rules-label">◈ 適用ルール</span>
          {appliedRuleIds.map((ruleId) => (
            <button
              key={ruleId}
              type="button"
              className="artifact__rule-chip"
              title={rules.find((r) => r.id === ruleId)?.text}
              onClick={openKnowledge}
            >
              {ruleId}
            </button>
          ))}
        </div>
      )}
      {editText === null ? (
        <>
          {showDiff && previous !== undefined ? (
            // #20: Before/After 差分リプレイ（追加=緑背景 / 削除=赤背景・取り消し線）
            <div className="artifact__diff">
              <div className="artifact__diff-head">
                v{previous.version} → v{current.version} の差分
              </div>
              <div className="artifact__diff-body">
                {diffLines(previous.contentMd, current.contentMd).map((line, i) => (
                  <div
                    // 差分行は再並べ替えされない静的リストなので index キーで安全
                    key={i}
                    className={`artifact__diff-line artifact__diff-line--${line.op}`}
                  >
                    <span className="artifact__diff-sign" aria-hidden="true">
                      {line.op === 'add' ? '+' : line.op === 'del' ? '−' : ' '}
                    </span>
                    <span className="artifact__diff-text">{line.text}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="artifact__preview">
              {/* GFM（比較表・§00 #1 レポートの核）を有効化 */}
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{current.contentMd}</ReactMarkdown>
            </div>
          )}
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
