// アクティビティスレッド（§3.3.2e + レビューUX）: コメントと成果物の版を「1つの会話」
// として時系列に描画する。実行AI「作業しました」→ [成果物vN] → レビューAI「修正が必要」
// → …「完了。レビューして」→ [最新版] と、人が上から下に読むだけで各版と最新の
// レビュー対象に辿り着ける（成果物を別セクションに切り出さない）。
//
// - コメント: 従来どおりアバター＋名義＋役割バッジ＋バブル（#19）。
// - 成果物: 各版を ArtifactMessage として差し込む。中間版はコメントと時系列マージ、
//   最新版（レビュー対象）は会話の最後にピン留めして展開＋レビュー案内を出す。
// - 生成中（ai_work）は末尾にライブ実況（#24）。

import { useBoardStore } from '../../store/board.ts';
import type { Artifact, Comment, Task } from '../../types/domain.ts';
import { AgentBadge } from '../board/AgentBadge';
import { Avatar } from '../board/Avatar';
import { ArtifactMessage } from './ArtifactMessage';
import { LiveDraftSection } from './LiveDraftSection';
import './ActivityThread.css';

interface ActivityThreadProps {
  task: Task;
  /** 「再生成」の活性（「AIにまかせる」と同条件。Drawer が計算して渡す） */
  canAssignAi: boolean;
}

type TimelineItem =
  | { kind: 'comment'; at: string; comment: Comment }
  | { kind: 'artifact'; at: string; artifact: Artifact; previous: Artifact | undefined };

function CommentRow({ comment }: { comment: Comment }) {
  return (
    <div className="activity__item">
      <Avatar owner={comment.author} variant="thread" />
      <div className="activity__body">
        <div className="activity__meta">
          <span className="activity__name">
            {comment.author === 'ai' ? 'Grow' : 'あなた'}
          </span>
          {comment.author === 'ai' && comment.agentRole != null && (
            <AgentBadge role={comment.agentRole} />
          )}
        </div>
        <div className="activity__bubble">{comment.text}</div>
      </div>
    </div>
  );
}

export function ActivityThread({ task, canAssignAi }: ActivityThreadProps) {
  const comments = useBoardStore((s) => s.comments[task.id]);
  const artifacts = useBoardStore((s) => s.artifacts[task.id]);
  const error = useBoardStore((s) => s.commentError[task.id]);
  const liveDraft = useBoardStore((s) => s.liveDraft[task.id]);

  const list = artifacts ?? [];
  // 最新版（レビュー対象）は会話の最後にピン留めするので、中間版だけを時系列に混ぜる
  const latest = list.length > 0 ? list[list.length - 1] : undefined;
  const midArtifacts = list.slice(0, -1);

  const items: TimelineItem[] = [
    ...(comments ?? []).map(
      (comment): TimelineItem => ({ kind: 'comment', at: comment.createdAt, comment }),
    ),
    ...midArtifacts.map(
      (artifact, i): TimelineItem => ({
        kind: 'artifact',
        at: artifact.createdAt,
        artifact,
        previous: i > 0 ? midArtifacts[i - 1] : undefined,
      }),
    ),
  ].sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));

  // 生成中は末尾にライブ実況。完了後は最新成果物（レビュー対象）を末尾に。
  const showLive =
    task.status === 'ai_work' && liveDraft !== undefined && liveDraft !== '';

  return (
    <div className="activity">
      <div className="activity__heading">アクティビティ</div>
      {items.map((item) =>
        item.kind === 'comment' ? (
          <CommentRow key={item.comment.id} comment={item.comment} />
        ) : (
          <ArtifactMessage
            key={item.artifact.id}
            task={task}
            artifact={item.artifact}
            previous={item.previous}
            isLatest={false}
            canAssignAi={canAssignAi}
          />
        ),
      )}
      {error != null && (
        <div className="activity__error" role="alert">
          {error}
        </div>
      )}
      {showLive ? (
        <LiveDraftSection task={task} />
      ) : (
        latest !== undefined && (
          <ArtifactMessage
            task={task}
            artifact={latest}
            previous={list.length > 1 ? list[list.length - 2] : undefined}
            isLatest
            canAssignAi={canAssignAi}
          />
        )
      )}
    </div>
  );
}
