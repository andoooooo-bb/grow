// SSE 購読（§5.4 / #7）: GET /api/events を EventSource で購読し、
// task.updated / comment.created をストアへ適用する。
// ワイヤ形式（backend/app/routers/events.py と鏡写し）:
//   event: <type>
//   data: {"type": <type>, "payload": <DTO の camelCase>}

import { getBoard } from './api.ts';
import { useBoardStore } from '../store/board.ts';
import type {
  ArtifactDeltaEvent,
  RuleProposalCreatedEvent,
  SubtaskProposalEvent,
} from '../types/api.ts';
import type { Artifact, ChatMessage, Comment, Rule, Task } from '../types/domain.ts';

export const EVENTS_URL = '/api/events';

// イベント種別（backend/app/events.py の定数と鏡写し。後続 Wave もここに追加する）
export const TASK_UPDATED = 'task.updated';
export const COMMENT_CREATED = 'comment.created';
export const ARTIFACT_CREATED = 'artifact.created'; // #10（backend/app/repo/artifacts.py）
export const ARTIFACT_DELTA = 'artifact.delta'; // #24（ライブ実況。サーバ非永続）
export const CHAT_MESSAGE_CREATED = 'chat.message.created'; // #11/#12（壁打ち）
export const SUBTASK_PROPOSAL = 'subtask.proposal'; // #11/#12（分解候補。サーバ非永続）
export const RULE_CREATED = 'rule.created'; // #13/#14（蒸留候補の採用）
export const RULE_UPDATED = 'rule.updated'; // #13/#14（昇格・applied++ の同期）
export const RULE_PROPOSAL_CREATED = 'rule_proposal.created'; // #26（受信箱のライブ更新）
export const KNOWLEDGE_CI_COMPLETED = 'knowledge.ci.completed'; // #26（CI実行サマリー）

interface SseEnvelope<T> {
  type: string;
  payload: T;
}

/**
 * /api/events へ接続し、受信イベントをストアへ適用する（App 起動時に一度呼ぶ）。
 * - task.updated → applyTaskUpdated（レーン移動・commentCount 同期を含むカード差し替え）
 * - comment.created → applyCommentCreated（開いているドロワーのスレッドへ追記。id で重複排除）
 * - artifact.created → applyArtifactCreated（成果物の新版を version 昇順で追記。id で重複排除）
 * - artifact.delta → applyArtifactDelta（#24 ライブ実況の増分を liveDraft へ連結）
 * - chat.message.created → applyChatMessageCreated（開始済みの壁打ちへ追記。id で重複排除）
 * - subtask.proposal → applySubtaskProposal（分解候補を proposal[taskId] へセット）
 * - rule.created / rule.updated → applyRuleCreated / applyRuleUpdated（id で upsert。
 *   isNew はクライアント表示状態なのでローカル既存値を保持する — #14）
 * 切断時の再接続は EventSource が自動で行う。戻り値は切断用のクリーンアップ。
 */
export function connectEvents(): () => void {
  // EventSource が無い環境（jsdom 等）では何もしない（テストは vi.stubGlobal でモックする）
  if (typeof EventSource === 'undefined') return () => {};

  const source = new EventSource(EVENTS_URL);
  // zustand のアクション参照は安定なので接続時に一度だけ取得すればよい
  const {
    applyTaskUpdated,
    applyCommentCreated,
    applyArtifactCreated,
    applyArtifactDelta,
    applyChatMessageCreated,
    applySubtaskProposal,
    applyRuleCreated,
    applyRuleUpdated,
    applyRuleProposalCreated,
  } = useBoardStore.getState();

  source.addEventListener(TASK_UPDATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Task>;
    applyTaskUpdated(payload);
  });
  source.addEventListener(COMMENT_CREATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Comment>;
    applyCommentCreated(payload);
  });
  source.addEventListener(ARTIFACT_CREATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Artifact>;
    applyArtifactCreated(payload);
  });
  source.addEventListener(ARTIFACT_DELTA, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<ArtifactDeltaEvent>;
    applyArtifactDelta(payload);
  });
  source.addEventListener(CHAT_MESSAGE_CREATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<ChatMessage>;
    applyChatMessageCreated(payload);
  });
  source.addEventListener(SUBTASK_PROPOSAL, (e: MessageEvent) => {
    const { payload } = JSON.parse(
      e.data as string,
    ) as SseEnvelope<SubtaskProposalEvent>;
    applySubtaskProposal(payload);
  });
  source.addEventListener(RULE_CREATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Rule>;
    applyRuleCreated(payload);
  });
  source.addEventListener(RULE_UPDATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(e.data as string) as SseEnvelope<Rule>;
    applyRuleUpdated(payload);
  });
  source.addEventListener(RULE_PROPOSAL_CREATED, (e: MessageEvent) => {
    const { payload } = JSON.parse(
      e.data as string,
    ) as SseEnvelope<RuleProposalCreatedEvent>;
    applyRuleProposalCreated(payload);
  });

  return () => source.close();
}

/**
 * ポーリング・フォールバック（#本番SSE対策）。
 *
 * Cloud Run はストリーミング応答（SSE）をバッファする挙動があり、本番では
 * EventSource がイベントを受け取れないことがある（AIジョブの進捗・成果物・
 * レビュー往復が画面に反映されない）。SSE が届かない環境でも UI が確実に
 * 更新されるよう、一定間隔でサーバの真実を取り直す保険を並走させる。
 *
 * - board（cards/lanes/rules）を再取得 → setBoard（UI状態は保持。楽観更新は
 *   サーバ値へ収束する）。AIジョブによる status/レーン/進捗の変化が反映される。
 * - ドロワーを開いていれば、そのカードの comments / artifacts も取り直す
 *   （着手・レビュー・完了コメントや成果物の新版がドロワーに出る）。
 * SSE と併走しても適用は冪等なので二重反映の害はない。タブ非表示中は休む。
 */
export function startPolling(intervalMs = 2500): () => void {
  let stopped = false;
  let inFlight = false;

  const tick = async () => {
    if (stopped || inFlight) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    inFlight = true;
    try {
      const board = await getBoard();
      if (stopped) return;
      const store = useBoardStore.getState();
      store.setBoard(board);
      const { selectedId } = store;
      if (selectedId && board.cards[selectedId]) {
        await Promise.all([
          store.loadComments(selectedId),
          store.loadArtifacts(selectedId),
        ]);
      }
    } catch {
      // 一時的な取得失敗は無視して次の tick に任せる（トーストは出さない）
    } finally {
      inFlight = false;
    }
  };

  const id = setInterval(() => void tick(), intervalMs);
  return () => {
    stopped = true;
    clearInterval(id);
  };
}
