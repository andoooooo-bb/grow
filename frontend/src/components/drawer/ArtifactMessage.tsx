// 成果物メッセージ（#10/#20 + レビューUX）: 1つの成果物の版を「会話スレッド内の
// メッセージ」として描画する。独立セクションではなく会話の流れに差し込むことで、
// 実行AI「作業しました」→ [成果物vN] → レビューAI「修正が必要」→ …「完了」→ [最新版]
// と、人が上から下に読むだけで各版と最新のレビュー対象に辿り着ける。
//
// - isLatest: 最新版（レビュー対象）。既定で展開し、編集/再生成/レビュー案内を出す。
//   過去版は折りたたみ（ヘッダのクリックで展開）で会話を読みやすく保つ。
// - previous があれば「前版との差分」トグル（#20。追加=緑/削除=赤）。

import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { diffLines } from '../../lib/diff.ts';
import { useBoardStore } from '../../store/board.ts';
import type { Artifact, Task } from '../../types/domain.ts';
import { Avatar } from '../board/Avatar';
import './ArtifactMessage.css';

interface ArtifactMessageProps {
  task: Task;
  artifact: Artifact;
  previous: Artifact | undefined;
  isLatest: boolean;
  /** 「再生成」の活性（「AIにまかせる」と同条件。Drawer が計算） */
  canAssignAi: boolean;
}

export function ArtifactMessage({
  task,
  artifact,
  previous,
  isLatest,
  canAssignAi,
}: ArtifactMessageProps) {
  const rules = useBoardStore((s) => s.rules);
  const assignAi = useBoardStore((s) => s.assignAi);
  const saveArtifact = useBoardStore((s) => s.saveArtifact);
  const openKnowledge = useBoardStore((s) => s.openKnowledge);
  // 過去版は折りたたみ、最新版は展開して見せる
  const [expanded, setExpanded] = useState(isLatest);
  const [editText, setEditText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  const appliedRuleIds = artifact.appliedRuleIds ?? [];
  const inReview = task.status === 'you_review' || task.status === 'reviewing';

  const save = async () => {
    if (editText === null) return;
    setSaving(true);
    const ok = await saveArtifact(task.id, editText);
    setSaving(false);
    if (ok) setEditText(null);
  };

  return (
    <div className={`artifact-msg${isLatest ? ' artifact-msg--latest' : ''}`}>
      <Avatar owner="ai" variant="thread" />
      <div className="artifact-msg__body">
        <button
          type="button"
          className="artifact-msg__header"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          <span className="artifact-msg__caret" aria-hidden="true">
            {expanded ? '▾' : '▸'}
          </span>
          <span className="artifact-msg__label">成果物 v{artifact.version}</span>
          {isLatest && <span className="artifact-msg__badge">最新</span>}
          {!expanded && <span className="artifact-msg__hint">クリックで展開</span>}
        </button>

        {expanded && (
          <div className="artifact-msg__card">
            {/* 由来ルールチップ（例 K-01）。クリックでナレッジへ */}
            {editText === null && appliedRuleIds.length > 0 && (
              <div className="artifact-msg__rules" aria-label="この版に適用されたルール">
                <span className="artifact-msg__rules-label">◈ 適用ルール</span>
                {appliedRuleIds.map((ruleId) => (
                  <button
                    key={ruleId}
                    type="button"
                    className="artifact-msg__rule-chip"
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
                  <div className="artifact-msg__diff">
                    <div className="artifact-msg__diff-head">
                      v{previous.version} → v{artifact.version} の差分
                    </div>
                    {diffLines(previous.contentMd, artifact.contentMd).map((line, i) => (
                      <div
                        key={i}
                        className={`artifact-msg__diff-line artifact-msg__diff-line--${line.op}`}
                      >
                        <span className="artifact-msg__diff-sign" aria-hidden="true">
                          {line.op === 'add' ? '+' : line.op === 'del' ? '−' : ' '}
                        </span>
                        <span>{line.text}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="artifact-msg__preview">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {artifact.contentMd}
                    </ReactMarkdown>
                  </div>
                )}
                <div className="artifact-msg__actions">
                  {previous !== undefined && (
                    <button
                      type="button"
                      className={`artifact-msg__button${showDiff ? ' artifact-msg__button--on' : ''}`}
                      aria-pressed={showDiff}
                      onClick={() => setShowDiff((v) => !v)}
                    >
                      {showDiff ? '全文を表示' : '前版との差分'}
                    </button>
                  )}
                  {isLatest && (
                    <>
                      <button
                        type="button"
                        className="artifact-msg__button"
                        onClick={() => setEditText(artifact.contentMd)}
                      >
                        編集
                      </button>
                      <button
                        type="button"
                        className="artifact-msg__button"
                        disabled={!canAssignAi}
                        onClick={() => void assignAi(task.id)}
                      >
                        再生成
                      </button>
                    </>
                  )}
                </div>
              </>
            ) : (
              <>
                <textarea
                  className="artifact-msg__editor"
                  aria-label="成果物のMarkdown"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                />
                <div className="artifact-msg__actions">
                  <button
                    type="button"
                    className="artifact-msg__button artifact-msg__button--save"
                    disabled={saving}
                    onClick={() => void save()}
                  >
                    保存
                  </button>
                  <button
                    type="button"
                    className="artifact-msg__button"
                    disabled={saving}
                    onClick={() => setEditText(null)}
                  >
                    キャンセル
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* 最新版がレビュー対象のとき、何をすればよいかを明示（レビュー導線） */}
        {isLatest && inReview && editText === null && (
          <p className="artifact-msg__review-guide">
            これが最新の成果物です。問題なければ上部の<strong>「完了にする」</strong>、
            直したい点があれば<strong>「差し戻す」</strong>（理由を書くとAIが直して新しい版を出します）
            か、下のコメント欄で指示してください。
          </p>
        )}
      </div>
    </div>
  );
}
