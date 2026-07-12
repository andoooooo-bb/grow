// 意思決定トレース（#25, detail モード）: 成果物の版ごとに
// 「v2 · 実行AI · K-01, K-03 を前提 · 5,432 tokens · $0.0123」を1行で示し、
// 『なぜAIはこう動いたか』を1枚で説明する。人の編集版は「あなたが編集」。
// 折りたたみ可（既定は閉）。ArtifactSection の版セレクタと連動し、
// 表示中の版をハイライト・行クリックでその版へ切り替える。
// トレース0件（未読込・取得失敗を含む）は非表示。ステータス変化・版追加のたび再取得する。

import { useEffect, useState } from 'react';
import { useBoardStore } from '../../store/board.ts';
import type { TraceEntry } from '../../types/api.ts';
import type { Task } from '../../types/domain.ts';
import { jobRoleLabel } from './AgentTimeline';
import './TraceSection.css';

interface TraceSectionProps {
  task: Task;
}

/** 人の編集版（jobId なし）の表示名 */
export const HUMAN_EDIT_LABEL = 'あなたが編集';

/** コスト表示（mono）: $1 未満は 4桁（例 $0.0123）、以上は 2桁（例 $1.25） */
export function formatTraceCost(costUsd: number): string {
  return costUsd >= 1 ? `$${costUsd.toFixed(2)}` : `$${costUsd.toFixed(4)}`;
}

/** トレース1行の要約テキスト（「·」区切り。null 項目は出さない） */
export function traceRowText(entry: TraceEntry): string {
  if (entry.kind == null) {
    // 人の編集版: ジョブ由来の情報は無い
    return `v${entry.version} · ${HUMAN_EDIT_LABEL}`;
  }
  const parts = [`v${entry.version}`, jobRoleLabel(entry.kind)];
  if (entry.appliedRuleIds.length > 0) {
    parts.push(`${entry.appliedRuleIds.join(', ')} を前提`);
  }
  const tokens = (entry.inputTokens ?? 0) + (entry.outputTokens ?? 0);
  parts.push(`${tokens.toLocaleString('en-US')} tokens`);
  if (entry.costUsd != null) {
    parts.push(formatTraceCost(entry.costUsd));
  }
  return parts.join(' · ');
}

export function TraceSection({ task }: TraceSectionProps) {
  const trace = useBoardStore((s) => s.trace[task.id]);
  const loadTrace = useBoardStore((s) => s.loadTrace);
  const artifacts = useBoardStore((s) => s.artifacts[task.id]);
  const selectedVersion = useBoardStore((s) => s.artifactVersion[task.id] ?? null);
  const selectArtifactVersion = useBoardStore((s) => s.selectArtifactVersion);
  // 折りたたみ（既定は閉。§5.5 準拠でトレース0件なら見出しごと出さない）
  const [open, setOpen] = useState(false);

  // ドロワーを開いたとき＋SSE でステータスが動いたとき＋版が増えたときに追いかける
  const artifactCount = artifacts?.length ?? 0;
  useEffect(() => {
    void loadTrace(task.id);
  }, [task.id, task.status, artifactCount, loadTrace]);

  if (trace === undefined || trace.length === 0) return null;

  // ArtifactSection と同じ規約: null = 最新（末尾の版）に追従
  const latestVersion = trace[trace.length - 1].version;
  const currentVersion = selectedVersion ?? latestVersion;

  return (
    <section className="trace">
      <button
        type="button"
        className="trace__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`trace__chevron${open ? ' trace__chevron--open' : ''}`} aria-hidden="true">
          ▸
        </span>
        <span className="trace__title">意思決定トレース</span>
        <span className="trace__count">{trace.length}</span>
      </button>
      {open && (
        <div className="trace__rows" aria-label="版ごとの意思決定トレース">
          {trace.map((entry) => (
            <button
              key={entry.version}
              type="button"
              className={`trace__row${
                entry.version === currentVersion ? ' trace__row--active' : ''
              }`}
              aria-pressed={entry.version === currentVersion}
              onClick={() =>
                // 最新版を選んだら null（= 以降の新版に自動追従）へ戻す
                selectArtifactVersion(
                  task.id,
                  entry.version === latestVersion ? null : entry.version,
                )
              }
            >
              {traceRowText(entry)}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
