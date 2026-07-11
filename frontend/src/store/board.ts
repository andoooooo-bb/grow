// Zustand 正規化ストア（§2.3 の BoardState に忠実）
// cards（id辞書）/ lanes（cardIds 配列で順序保持）/ rules が真実。
// 派生値（§5.1）は保存せず、render のたびに derive* で計算する。

import { create } from 'zustand';
import {
  adoptLearn as adoptLearnRequest,
  assignAi as assignAiRequest,
  autopilot as autopilotRequest,
  confirmBreakdown as confirmBreakdownRequest,
  createArtifact,
  createComment,
  createTask,
  dismissLearn as dismissLearnRequest,
  getArtifacts,
  getComments,
  getJobs,
  getLearnProposals,
  patchTask,
  promoteRule as promoteRuleRequest,
  rejectTask as rejectTaskRequest,
  sendChatMessage,
  startChat as startChatRequest,
} from '../lib/api.ts';
import { canTransition } from '../lib/stateMachine.ts';
import type {
  ArtifactDeltaEvent,
  BoardResponse,
  LaneDto,
  SubtaskProposal,
  SubtaskProposalEvent,
  TaskPatch,
} from '../types/api.ts';
import type {
  AgentRole,
  AiJob,
  Artifact,
  AutonomyLevel,
  ChatMessage,
  Comment,
  LaneKey,
  Rule,
  RuleProposal,
  Task,
  TaskPolicy,
  TaskStatus,
} from '../types/domain.ts';
import { STATUS_META, taskAutonomy } from '../types/domain.ts';

export type PanelMode = 'detail' | 'chat';

/** #19 ライブフィードの1行（AI活動の横断ログ。上限 ACTIVITY_LIMIT のリングバッファ） */
export interface ActivityEntry {
  id: string; // 由来イベントごとに安定なID（comment-{id} 等。重複配信の排除に使う）
  taskId: string; // 行クリックで select(taskId) するジャンプ先
  taskTitle: string; // 表示用（積んだ時点のタイトル）
  text: string; // 例「成果物v2を作成」「あなたのレビュー待ちへ」
  role?: AgentRole; // どのエージェントの活動か（不明なら undefined）
  at: number; // 積んだ時刻（ms）
}

/** #19 ライブフィードの保持上限（最新100件のリングバッファ） */
export const ACTIVITY_LIMIT = 100;

/** §2.3 フロント状態ストア形（正規化） */
export interface BoardState {
  cards: Record<string, Task>; // id -> Task（サブタスク含む全カード）
  lanes: LaneDto[]; // { key, name, cardIds } — 並び順を cardIds が保持
  rules: Rule[];
  // UI状態
  selectedId: string | null; // 開いているカード
  panelMode: PanelMode; // ドロワーのモード
  showKnowledge: boolean; // ナレッジ・オーバーレイ表示
  comments: Record<string, Comment[]>; // taskId -> アクティビティ（ドロワーを開いたタスクのみ読込済み）
  commentError: Record<string, string | null>; // taskId -> コメント送信/取得失敗の簡易エラー（§5.4）
  boardError: string | null; // ボード操作（move/addCard/markDone, #8）の簡易エラー（§5.4）
  chat: Record<string, ChatMessage[]>; // taskId -> 壁打ちメッセージ
  chatError: Record<string, string | null>; // taskId -> 壁打ち送信/開始失敗の簡易エラー（§5.4, #12）
  // 分解候補（taskId -> ）。§2.3 の記載は RuleProposal[] だが、
  // §3.3.3/§5.3 の通り 担当(owner)＋名称(title) を持つ SubtaskProposal（§7.4b）が実体。
  proposal: Record<string, SubtaskProposal[]>;
  learn: Record<string, RuleProposal[]>; // 蒸留候補（taskId -> ）
  artifacts: Record<string, Artifact[]>; // taskId -> 成果物の版（§00 #2。version 昇順・末尾が最新）
  // #24 ライブ実況: taskId -> 生成途中の成果物テキスト（artifact.delta の連結）。
  // artifact.created（確定版）受信でクリアし、以降は artifacts が真実になる
  liveDraft: Record<string, string>;
  drafts: Record<string, string>; // コンポーザ入力
  chatDrafts: Record<string, string>; // 壁打ちコンポーザ入力（detail のコンポーザとは別持ち, #12）
  assigning: Record<string, boolean>; // taskId -> assign-ai 送信中（ボタン無効化, #10）
  rejecting: Record<string, boolean>; // taskId -> reject 送信中（ボタン無効化, #23）
  confirming: Record<string, boolean>; // taskId -> breakdown/confirm 送信中（ボタン無効化, #12）
  learning: Record<string, boolean>; // taskId -> 「✧ 学ぶ」実行中（ボタン無効化, #14）
  // #20: ルール適用フラッシュ演出 — rule.updated（applied++）受信直後の ruleId -> 時刻(ms)。
  // 消費側（AppliedRules/KnowledgeOverlay/TopBar）は値を key に使い、変わるたび
  // one-shot CSS アニメを再マウントで再生する（数秒で自然に減衰。クリア不要）。
  justApplied: Record<string, number>;
  // #19: AI活動ライブフィード（新しい順・上限 ACTIVITY_LIMIT）。SSE 起点の apply* が積む
  activity: ActivityEntry[];
  // #19: taskId -> AIジョブ履歴（createdAt 昇順）。ドロワーのリレー・タイムラインが読む
  jobs: Record<string, AiJob[]>;
}

export interface BoardActions {
  /** GET /api/board の結果を正規化ストアへ反映する */
  setBoard: (board: BoardResponse) => void;
  /** §5.3 select(id): selectedId を設定し panelMode='detail' */
  select: (id: string) => void;
  /** §5.3 closePanel(): selectedId=null */
  closePanel: () => void;
  setPanelMode: (mode: PanelMode) => void;
  /** §5.3 openKnowledge() / closeKnowledge() */
  openKnowledge: () => void;
  closeKnowledge: () => void;
  /** §5.3 onDraftInput: コンポーザ入力の保持 */
  setDraft: (taskId: string, text: string) => void;
  /** 壁打ちコンポーザ入力の保持（#12。detail のコンポーザとは別領域） */
  setChatDraft: (taskId: string, text: string) => void;

  // ---- コメント（#7） ----
  /** GET /tasks/:id/comments でスレッドを読み込む（ドロワーを開いたとき）。失敗は簡易エラー表示 */
  loadComments: (taskId: string) => Promise<void>;
  /** 楽観的追加（§5.4: 即UI反映）。カードの commentCount も +1 する */
  addCommentOptimistic: (taskId: string, comment: Comment) => void;
  /** API 成功: 楽観的追加（tempId）をサーバ確定版に差し替える（SSE 先着なら temp を除去） */
  confirmComment: (taskId: string, tempId: string, comment: Comment) => void;
  /** API 失敗: 楽観的追加を取り消し、簡易エラーを表示する（§5.4 ロールバック） */
  rollbackComment: (taskId: string, tempId: string, message: string) => void;
  /** §5.3 postComment: 入力を human コメントとして楽観的に投稿 → API → 確定/ロールバック */
  postComment: (taskId: string, text: string) => Promise<void>;

  // ---- ボード操作（#8） ----
  /**
   * §5.3 move: 楽観的に対象レーン末尾へ移動 → PATCH {laneKey} → 失敗ならロールバック。
   * 完了レーンへのドロップのみ done 化を自動整合（§00 #7）。canTransition 違反は
   * API を呼ばず即フィードバック（§5.2）。同一レーンへのドロップは no-op。
   */
  move: (taskId: string, toLaneKey: LaneKey) => Promise<void>;
  /**
   * §5.3 addCard: status=breakdown の新規カードを当該レーン末尾へ作成し、
   * AI初期コメントを付けてドロワーを開く。
   */
  addCard: (laneKey: LaneKey) => Promise<void>;
  /** §5.3 markDone: PATCH {status:'done', laneKey:'done', progress:null} → 反映 */
  markDone: (taskId: string) => Promise<void>;

  // ---- AI 実作業（#10） ----
  /**
   * §5.3 assignAI: POST /tasks/:id/assign-ai。202 後の反映（ai_work 化・着手コメント・
   * 進捗・完了ハンドオフ）はすべて SSE に任せる（§5.4 サーバ起点）。
   * 送信中は assigning フラグでボタンを無効化し、409/失敗は boardError。
   * コスト上限到達（#21）も 409 — 停止理由コメント・you_todo 戻しは SSE が届ける。
   */
  assignAi: (taskId: string) => Promise<void>;

  // ---- オートパイロット（#22 指揮者AI） ----
  /**
   * POST /tasks/:id/autopilot（assignAi と同型）。202 後の進行（判断理由コメント・
   * ジョブ連鎖・遷移）はすべて SSE に任せる。送信中は assigning フラグを共用して
   * 「AIにまかせる」とまとめて無効化。409（L0/コスト上限等）/失敗は boardError。
   */
  autopilot: (taskId: string) => Promise<void>;

  // ---- 構造化差し戻し（#23） ----
  /**
   * POST /tasks/:id/reject {reason}。202 後の反映（理由コメント・ai_work 化・
   * 再実行・矛盾ルールの確度降格）はすべて SSE に任せる。
   * 送信中は rejecting フラグでボタンを無効化し、409/失敗は boardError。
   */
  reject: (taskId: string, reason: string) => Promise<void>;

  // ---- タスク別オートノミー（#21） ----
  /** L0-L3 ダイヤル: 楽観的更新 → PATCH {autonomy}。失敗はロールバック＋boardError */
  setAutonomy: (taskId: string, autonomy: AutonomyLevel) => Promise<void>;
  /** 行動範囲ポリシー: 楽観的更新 → PATCH {policy}（全体置換）。失敗はロールバック */
  setPolicy: (taskId: string, policy: TaskPolicy) => Promise<void>;

  // ---- 成果物（#10 / §00 #2） ----
  /** GET /tasks/:id/artifacts で全版を読み込む（ドロワーを開いたとき）。失敗は非表示のまま */
  loadArtifacts: (taskId: string) => Promise<void>;
  /** POST /tasks/:id/artifacts で編集内容を新版として保存する。成功で true */
  saveArtifact: (taskId: string, contentMd: string) => Promise<boolean>;

  // ---- エージェント編成の見える化（#19） ----
  /** GET /tasks/:id/jobs でリレー履歴を読み込む（AgentTimeline が呼ぶ）。失敗は非表示のまま */
  loadJobs: (taskId: string) => Promise<void>;
  /** ライブフィードへ1行積む（新しい順・上限 ACTIVITY_LIMIT。同一 id は重複排除） */
  pushActivity: (entry: ActivityEntry) => void;

  // ---- 壁打ち → 分解（#12 / §1.6 / §5.3） ----
  /**
   * §5.3 startChat: POST /tasks/:id/chat/start（冪等）→ 一覧を chat へセット →
   * panelMode='chat'。初回の spec 遷移はサーバが行い task.updated（SSE）で同期する。
   */
  startChat: (taskId: string) => Promise<void>;
  /**
   * §5.3 sendChat: 楽観的追加（即UI反映・入力クリア）→ POST → id 差し替え。
   * 失敗ならロールバック（§5.4）。AI応答＋分解候補は +0.85s 後に SSE で届く。
   */
  sendChat: (taskId: string, text: string) => Promise<void>;
  /**
   * §5.3 confirmBreakdown: proposal を body に POST /breakdown/confirm →
   * 応答 {parent, children} を反映 → proposal クリア → panelMode='detail'。
   * 409/422/通信失敗は boardError 表示のみで proposal は残す。
   */
  confirmBreakdown: (taskId: string) => Promise<void>;

  // ---- 学習（蒸留）・ナレッジ（#14 / §1.7 / §1.8 / §5.3） ----
  /**
   * §5.3 learnFrom: 「✧ 学ぶ」で GET /tasks/:id/learn → 候補を learn[taskId] へセット。
   * 実行中は learning フラグでボタンを無効化。409/失敗は boardError（§5.4）。
   */
  learnFrom: (taskId: string) => Promise<void>;
  /**
   * §5.3 adoptLearn: 候補を POST /learn/adopt → 応答 Rule（K-xx）を rules へ
   * isNew=true（NEW バッジ, クライアント表示状態）で upsert → 候補から除去。
   * カードへのAIコメントは SSE（comment.created）が反映する。失敗時は候補を残す。
   */
  adoptLearn: (taskId: string, tempId: string) => Promise<void>;
  /** §5.3 dismissLearn: 候補を POST /learn/dismiss（feedback 記録のみ）→ 候補から除去 */
  dismissLearn: (taskId: string, tempId: string) => Promise<void>;
  /**
   * §1.8 promoteRule: POST /rules/:id/promote → 応答（scope=team）を isNew=true で
   * upsert（NEW 再表示）。ナレッジ・オーバーレイの「チームのルール」へ移る。
   */
  promoteRule: (ruleId: string) => Promise<void>;

  // ---- SSE 適用（#7 / #10 / #12 / src/lib/sse.ts から呼ばれる） ----
  /** task.updated: カードを差し替え、レーン移動も反映（全レーンから除去→laneKey へ挿入） */
  applyTaskUpdated: (task: Task) => void;
  /** comment.created: 読込済みスレッドへ追記（自分の楽観的追加との重複は id で排除） */
  applyCommentCreated: (comment: Comment) => void;
  /** artifact.created: version 昇順を保って追記（POST 応答との重複は id で排除） */
  applyArtifactCreated: (artifact: Artifact) => void;
  /**
   * artifact.delta: ライブ実況の増分を liveDraft[taskId] へ連結する（#24）。
   * seq=1 は新しいストリームの開始なのでリセットして始める（リトライ・再実行対応）。
   * 確定版は artifact.created が届き、applyArtifactCreated が liveDraft をクリアする。
   */
  applyArtifactDelta: (event: ArtifactDeltaEvent) => void;
  /**
   * chat.message.created: 開始済みの壁打ちへ追記（#12）。人メッセージ送信時も配信される
   * ため、id 重複排除＋自分の楽観的追加（tmp-）との差し替えを行う。
   */
  applyChatMessageCreated: (message: ChatMessage) => void;
  /** subtask.proposal: 分解候補を proposal[taskId] へセットする（#12。サーバ非永続） */
  applySubtaskProposal: (event: SubtaskProposalEvent) => void;
  /**
   * rule.created: rules へ id で upsert（#14。自分の adopt 応答との重複は upsert で解決）。
   * isNew はクライアント表示状態なので、既存のローカル値を保持する（SSE で NEW が消えない）。
   */
  applyRuleCreated: (rule: Rule) => void;
  /**
   * rule.updated: 昇格・applied++ の同期（#14）。upsert 方針は applyRuleCreated と同じ。
   * #20: applied が増えた（=いま注入された）ときのみ justApplied[ruleId] に時刻を記録し、
   * 適用ルール行・ナレッジカード・「◈ ナレッジ」のフラッシュ演出を発火する。
   */
  applyRuleUpdated: (rule: Rule) => void;
}

export type BoardStore = BoardState & BoardActions;

/** 初期状態（テストのリセットにも使う） */
export function createInitialBoardState(): BoardState {
  return {
    cards: {},
    lanes: [],
    rules: [],
    selectedId: null,
    panelMode: 'detail',
    showKnowledge: false,
    comments: {},
    commentError: {},
    boardError: null,
    chat: {},
    chatError: {},
    proposal: {},
    learn: {},
    artifacts: {},
    liveDraft: {},
    drafts: {},
    chatDrafts: {},
    assigning: {},
    rejecting: {},
    confirming: {},
    learning: {},
    justApplied: {},
    activity: [],
    jobs: {},
  };
}

// ---- 楽観的追加の一時ID（#7 コメント / #12 壁打ちで共用） ----

let tempSeq = 0;

/** 楽観的追加用の一時ID（サーバ確定時に UUID へ差し替わる） */
export function nextTempCommentId(): string {
  tempSeq += 1;
  return `tmp-${tempSeq}`;
}

function isTempId(id: string): boolean {
  return id.startsWith('tmp-');
}

// ---- ボード操作の文言（#8。プロトタイプ Grow.dc.html の addCard を踏襲） ----

/** addCard の新規カードのデフォルトタイトル */
export const NEW_CARD_TITLE = '新しいタスク';

/** addCard の AI 初期コメント（§5.3） */
export const ADD_CARD_AI_PROMPT =
  'タイトルと、やりたいことを教えてください。大きければ壁打ちで分解しましょう。';

/**
 * rules へ id で upsert する（#14）。isNew はクライアント表示状態（サーバは返さない）
 * なので、明示指定（adopt/promote 直後の true）が無ければローカル既存値を保持する —
 * SSE の rule.created/rule.updated で NEW バッジが消えないようにする（§5.3）。
 */
function upsertRule(rules: Rule[], rule: Rule): Rule[] {
  const existing = rules.find((r) => r.id === rule.id);
  if (existing === undefined) return [...rules, rule];
  const next: Rule = { ...rule, isNew: rule.isNew ?? existing.isNew };
  return rules.map((r) => (r.id === rule.id ? next : r));
}

// ---- #19 ライブフィードのヘルパ ----------------------------------------------------

/** フィード行のAIコメント要約（先頭30字。超過は…付き） */
export const ACTIVITY_TEXT_LIMIT = 30;

function summarizeActivityText(text: string): string {
  return text.length > ACTIVITY_TEXT_LIMIT
    ? `${text.slice(0, ACTIVITY_TEXT_LIMIT)}…`
    : text;
}

// ステータス変化エントリの一意ID用の連番（同一タスクの再遷移も別行として積む）
let activitySeq = 0;

/** cards[taskId].commentCount を delta 分ずらす（カードが無ければ何もしない） */
function shiftCommentCount(
  cards: Record<string, Task>,
  taskId: string,
  delta: number,
): Record<string, Task> {
  const task = cards[taskId];
  if (!task) return cards;
  return {
    ...cards,
    [taskId]: { ...task, commentCount: Math.max(0, task.commentCount + delta) },
  };
}

export const useBoardStore = create<BoardStore>()((set, get) => ({
  ...createInitialBoardState(),
  setBoard: (board) =>
    set({ cards: board.cards, lanes: board.lanes, rules: board.rules }),
  select: (id) => set({ selectedId: id, panelMode: 'detail' }),
  closePanel: () => set({ selectedId: null }),
  setPanelMode: (mode) => set({ panelMode: mode }),
  openKnowledge: () => set({ showKnowledge: true }),
  closeKnowledge: () => set({ showKnowledge: false }),
  setDraft: (taskId, text) =>
    set((s) => ({ drafts: { ...s.drafts, [taskId]: text } })),
  setChatDraft: (taskId, text) =>
    set((s) => ({ chatDrafts: { ...s.chatDrafts, [taskId]: text } })),

  // ---- コメント（#7） ----
  loadComments: async (taskId) => {
    try {
      const loaded = await getComments(taskId);
      set((s) => {
        // 送信中の楽観的追加（tmp-）は消さずに末尾へ残す（読込と送信の競合対策）
        const pending = (s.comments[taskId] ?? []).filter((c) => isTempId(c.id));
        return {
          comments: { ...s.comments, [taskId]: [...loaded, ...pending] },
          commentError: { ...s.commentError, [taskId]: null },
        };
      });
    } catch {
      set((s) => ({
        commentError: {
          ...s.commentError,
          [taskId]: 'コメントの読み込みに失敗しました',
        },
      }));
    }
  },
  addCommentOptimistic: (taskId, comment) =>
    set((s) => ({
      comments: {
        ...s.comments,
        [taskId]: [...(s.comments[taskId] ?? []), comment],
      },
      cards: shiftCommentCount(s.cards, taskId, +1),
      commentError: { ...s.commentError, [taskId]: null },
    })),
  confirmComment: (taskId, tempId, comment) =>
    set((s) => {
      const list = s.comments[taskId] ?? [];
      // SSE の comment.created が先に確定版を届けている場合は temp の除去だけ（id 重複排除）
      if (list.some((c) => c.id === comment.id)) {
        return {
          comments: { ...s.comments, [taskId]: list.filter((c) => c.id !== tempId) },
        };
      }
      return {
        comments: {
          ...s.comments,
          [taskId]: list.map((c) => (c.id === tempId ? comment : c)),
        },
      };
    }),
  rollbackComment: (taskId, tempId, message) =>
    set((s) => {
      const list = s.comments[taskId] ?? [];
      const rest = list.filter((c) => c.id !== tempId);
      const removed = rest.length !== list.length;
      return {
        comments: { ...s.comments, [taskId]: rest },
        cards: removed ? shiftCommentCount(s.cards, taskId, -1) : s.cards,
        commentError: { ...s.commentError, [taskId]: message },
      };
    }),
  postComment: async (taskId, text) => {
    const trimmed = text.trim();
    if (trimmed === '') return;
    const tempId = nextTempCommentId();
    const optimistic: Comment = {
      id: tempId,
      taskId,
      author: 'human',
      text: trimmed,
      createdAt: new Date().toISOString(),
    };
    // §5.3 postComment: 入力クリア → 即UI反映（§5.4 楽観的更新）
    get().setDraft(taskId, '');
    get().addCommentOptimistic(taskId, optimistic);
    try {
      const created = await createComment(taskId, { author: 'human', text: trimmed });
      get().confirmComment(taskId, tempId, created);
    } catch {
      get().rollbackComment(taskId, tempId, 'コメントの送信に失敗しました');
    }
  },

  // ---- ボード操作（#8） ----
  move: async (taskId, toLaneKey) => {
    const s = get();
    const task = s.cards[taskId];
    if (task === undefined) return;
    if (task.laneKey === toLaneKey) return; // 同一レーンへのドロップは no-op

    // §00 #7: 完了レーンへドロップ = done 化のみ自動整合。
    // 加えて、完了レーンから引き出したら「あなたの作業待ち」(you_todo) へ再オープン
    // （done→他レーンで status=done のまま残る矛盾を解消。ボールは人へ戻す）。
    const toDone = toLaneKey === 'done';
    const reopen = !toDone && task.status === 'done'; // done レーンから引き出し
    // 遷移可否のクライアント事前チェック（§5.2）: API を呼ばず即フィードバック
    const nextStatus: TaskStatus | null = toDone
      ? 'done'
      : reopen
        ? 'you_todo'
        : null;
    if (nextStatus !== null && !canTransition(task.status, nextStatus)) {
      set({
        boardError: `「${STATUS_META[task.status].label}」のカードは${
          toDone ? '完了レーンへ移動' : '再オープン'
        }できません`,
      });
      return;
    }

    // ロールバック用スナップショット（§5.4）
    const prevCards = s.cards;
    const prevLanes = s.lanes;

    // 楽観的更新: 対象レーン末尾へ追加（§5.3 move。レーン内並べ替えは非スコープ）
    const targetLane = s.lanes.find((lane) => lane.key === toLaneKey);
    const orderInLane =
      targetLane?.cardIds.filter((id) => id !== taskId).length ?? 0;
    const optimistic: Task =
      nextStatus !== null
        ? { ...task, laneKey: toLaneKey, orderInLane, status: nextStatus, progress: undefined }
        : { ...task, laneKey: toLaneKey, orderInLane };
    get().applyTaskUpdated(optimistic);
    set({ boardError: null });

    try {
      const patch: TaskPatch =
        nextStatus !== null
          ? { laneKey: toLaneKey, status: nextStatus, progress: null }
          : { laneKey: toLaneKey };
      const updated = await patchTask(taskId, patch);
      get().applyTaskUpdated(updated); // 成功レスポンスで確定
    } catch {
      // サーバ拒否（409 等）・通信失敗: 元状態へロールバック（§5.4）
      set({ cards: prevCards, lanes: prevLanes, boardError: 'カードの移動に失敗しました' });
    }
  },

  addCard: async (laneKey) => {
    set({ boardError: null });
    let created: Task;
    try {
      // status 省略時はサーバが 'breakdown' を採用（§5.3 addCard）
      created = await createTask({ laneKey, title: NEW_CARD_TITLE });
    } catch {
      set({ boardError: 'カードの追加に失敗しました' });
      return;
    }
    get().applyTaskUpdated(created); // 当該レーン末尾へ追加
    try {
      const comment = await createComment(created.id, {
        author: 'ai',
        text: ADD_CARD_AI_PROMPT,
        agentRole: 'planner', // 初期質問は計画AIの名義（#19）
      });
      get().addCommentOptimistic(created.id, comment); // スレッド反映＋commentCount+1
    } catch {
      set((st) => ({
        commentError: {
          ...st.commentError,
          [created.id]: 'コメントの送信に失敗しました',
        },
      }));
    }
    get().select(created.id); // ドロワーを開く（panelMode='detail'）
  },

  markDone: async (taskId) => {
    set({ boardError: null });
    try {
      // §5.3 markDone: done 化・progress クリア・完了レーンへ
      const updated = await patchTask(taskId, {
        status: 'done',
        laneKey: 'done',
        progress: null,
      });
      get().applyTaskUpdated(updated);
    } catch {
      set({ boardError: '完了にできませんでした' });
    }
  },

  // ---- AI 実作業（#10） ----
  assignAi: async (taskId) => {
    const s = get();
    if (s.assigning[taskId] === true) return; // 二重送信防止（送信中はボタンも無効）
    if (s.cards[taskId] === undefined) return;
    set((st) => ({
      assigning: { ...st.assigning, [taskId]: true },
      boardError: null,
    }));
    try {
      // 202 {jobId}。以降の進行（ai_work/progress 0→45→you_review・コメント・成果物）は
      // すべてサーバ起点の SSE（task.updated / comment.created / artifact.created）が反映する
      await assignAiRequest(taskId);
    } catch {
      // 不正遷移（409）や通信失敗（§5.4 / §00 #10）
      set({ boardError: 'AIにまかせられませんでした' });
    } finally {
      set((st) => ({ assigning: { ...st.assigning, [taskId]: false } }));
    }
  },

  // ---- オートパイロット（#22 指揮者AI） ----
  autopilot: async (taskId) => {
    const s = get();
    if (s.assigning[taskId] === true) return; // 二重送信防止（assignAi とフラグ共用）
    if (s.cards[taskId] === undefined) return;
    set((st) => ({
      assigning: { ...st.assigning, [taskId]: true },
      boardError: null,
    }));
    try {
      // 202 {jobId}。以降の判断理由コメント・ジョブ連鎖・遷移はすべて SSE が反映する
      await autopilotRequest(taskId);
    } catch {
      // 409（ai_work/done・L0・コスト上限）や通信失敗（§5.4）
      set({ boardError: 'オートパイロットを開始できませんでした' });
    } finally {
      set((st) => ({ assigning: { ...st.assigning, [taskId]: false } }));
    }
  },

  // ---- 構造化差し戻し（#23） ----
  reject: async (taskId, reason) => {
    const trimmed = reason.trim();
    if (trimmed === '') return; // 理由必須（フォーム側でも無効化）
    const s = get();
    if (s.rejecting[taskId] === true) return; // 二重送信防止（送信中はボタンも無効）
    if (s.cards[taskId] === undefined) return;
    set((st) => ({
      rejecting: { ...st.rejecting, [taskId]: true },
      boardError: null,
    }));
    try {
      // 202 {jobId}。以降の進行（理由コメント・ai_work 化・レビューAIとの往復・
      // 矛盾ルールの確度降格）はすべてサーバ起点の SSE が反映する
      await rejectTaskRequest(taskId, { reason: trimmed });
    } catch {
      // 不正遷移（409）や通信失敗（§5.4）
      set({ boardError: '差し戻しできませんでした' });
    } finally {
      set((st) => ({ rejecting: { ...st.rejecting, [taskId]: false } }));
    }
  },

  // ---- タスク別オートノミー（#21） ----
  setAutonomy: async (taskId, autonomy) => {
    const prev = get().cards[taskId];
    if (prev === undefined || taskAutonomy(prev) === autonomy) return;
    // 楽観的更新（§5.4）: レーンは変わらないので cards の差し替えのみ
    set((s) => ({
      cards: { ...s.cards, [taskId]: { ...prev, autonomy } },
      boardError: null,
    }));
    try {
      const updated = await patchTask(taskId, { autonomy });
      get().applyTaskUpdated(updated); // 成功レスポンスで確定
    } catch {
      set((s) => ({
        cards: { ...s.cards, [taskId]: prev },
        boardError: 'オートノミーの変更に失敗しました',
      }));
    }
  },
  setPolicy: async (taskId, policy) => {
    const prev = get().cards[taskId];
    if (prev === undefined) return;
    set((s) => ({
      cards: { ...s.cards, [taskId]: { ...prev, policy } },
      boardError: null,
    }));
    try {
      const updated = await patchTask(taskId, { policy });
      get().applyTaskUpdated(updated);
    } catch {
      set((s) => ({
        cards: { ...s.cards, [taskId]: prev },
        boardError: 'ポリシーの変更に失敗しました',
      }));
    }
  },

  // ---- 成果物（#10 / §00 #2） ----
  loadArtifacts: async (taskId) => {
    try {
      const res = await getArtifacts(taskId);
      // サーバは version 昇順で返す（末尾が最新）。全版で置き換える
      set((s) => ({ artifacts: { ...s.artifacts, [taskId]: res.artifacts } }));
    } catch {
      // 取得失敗はセクション非表示のまま（§5.5: 成果物なしと同じ扱い。文言は出さない）
    }
  },
  saveArtifact: async (taskId, contentMd) => {
    set({ boardError: null });
    try {
      const created = await createArtifact(taskId, { contentMd });
      get().applyArtifactCreated(created); // SSE 先着なら id で重複排除される
      return true;
    } catch {
      set({ boardError: '成果物の保存に失敗しました' });
      return false;
    }
  },

  // ---- エージェント編成の見える化（#19） ----
  loadJobs: async (taskId) => {
    try {
      const res = await getJobs(taskId);
      if (!Array.isArray(res.jobs)) return; // 予期しない応答は無視（タイムライン非表示のまま）
      set((s) => ({ jobs: { ...s.jobs, [taskId]: res.jobs } }));
    } catch {
      // 取得失敗はタイムライン非表示のまま（§5.5: ジョブなしと同じ扱い。文言は出さない）
    }
  },
  pushActivity: (entry) =>
    set((s) => {
      // 同一 id は重複排除（POST 応答と SSE の二重適用・SSE 再配信への冪等ガード）
      if (s.activity.some((e) => e.id === entry.id)) return {};
      return { activity: [entry, ...s.activity].slice(0, ACTIVITY_LIMIT) };
    }),

  // ---- 壁打ち → 分解（#12 / §1.6 / §5.3） ----
  startChat: async (taskId) => {
    if (get().cards[taskId] === undefined) return;
    try {
      // 冪等: chat が空のときだけサーバが AI 初期質問を生成・spec 遷移（task.updated は SSE）。
      // 既存履歴があれば一覧が返るだけなので、再実行しても壊れない。
      const messages = await startChatRequest(taskId);
      set((s) => ({
        chat: { ...s.chat, [taskId]: messages },
        chatError: { ...s.chatError, [taskId]: null },
        panelMode: 'chat',
      }));
    } catch {
      // 開始できなければ detail のまま（§5.4 簡易エラー）
      set({ boardError: '壁打ちを開始できませんでした' });
    }
  },
  sendChat: async (taskId, text) => {
    const trimmed = text.trim();
    if (trimmed === '') return;
    const tempId = nextTempCommentId();
    const optimistic: ChatMessage = {
      id: tempId,
      taskId,
      author: 'human',
      text: trimmed,
      createdAt: new Date().toISOString(),
    };
    // §5.3 sendChat step1: 入力クリア → 即UI反映（§5.4 楽観的更新）
    set((s) => ({
      chat: { ...s.chat, [taskId]: [...(s.chat[taskId] ?? []), optimistic] },
      chatDrafts: { ...s.chatDrafts, [taskId]: '' },
      chatError: { ...s.chatError, [taskId]: null },
    }));
    try {
      const created = await sendChatMessage(taskId, { text: trimmed });
      set((s) => {
        const list = s.chat[taskId] ?? [];
        // SSE の chat.message.created が先に確定版を届けている場合は temp の除去だけ
        const next = list.some((m) => m.id === created.id)
          ? list.filter((m) => m.id !== tempId)
          : list.map((m) => (m.id === tempId ? created : m));
        return { chat: { ...s.chat, [taskId]: next } };
      });
    } catch {
      // ロールバック（§5.4）: 楽観的追加を取り消して簡易エラー
      set((s) => ({
        chat: {
          ...s.chat,
          [taskId]: (s.chat[taskId] ?? []).filter((m) => m.id !== tempId),
        },
        chatError: { ...s.chatError, [taskId]: 'メッセージの送信に失敗しました' },
      }));
    }
  },
  confirmBreakdown: async (taskId) => {
    const s = get();
    const subtasks = s.proposal[taskId];
    if (subtasks === undefined || subtasks.length === 0) return; // 候補なしは no-op（422 予防）
    if (s.confirming[taskId] === true) return; // 二重送信防止（ボタンも無効化）
    set((st) => ({
      confirming: { ...st.confirming, [taskId]: true },
      boardError: null,
    }));
    try {
      // 候補（title/owner）をそのまま送り返す（rationale は表示専用なので送らない）
      const res = await confirmBreakdownRequest(taskId, {
        subtasks: subtasks.map(({ title, owner }) => ({ title, owner })),
      });
      // 応答反映（SSE 先着でも applyTaskUpdated は差し替えなので冪等）:
      // 子 → todo レーン末尾へ順に、親 → childIds 込みで progress 先頭へ
      for (const child of res.children) get().applyTaskUpdated(child);
      get().applyTaskUpdated(res.parent);
      set((st) => {
        const proposal = { ...st.proposal };
        delete proposal[taskId]; // 反映済み候補をクリア（§5.3）
        return { proposal, panelMode: 'detail' };
      });
    } catch {
      // 409（breakdown/done 親）/ 422 / 通信失敗: proposal は残す（§5.4）
      set({ boardError: 'ボードへの反映に失敗しました' });
    } finally {
      set((st) => ({ confirming: { ...st.confirming, [taskId]: false } }));
    }
  },

  // ---- 学習（蒸留）・ナレッジ（#14 / §1.7 / §1.8 / §5.3） ----
  learnFrom: async (taskId) => {
    const s = get();
    if (s.learning[taskId] === true) return; // 二重実行防止（実行中はボタンも無効）
    if (s.cards[taskId] === undefined) return;
    set((st) => ({
      learning: { ...st.learning, [taskId]: true },
      boardError: null,
    }));
    try {
      // 候補はサーバ非永続（§6.4a）: 受け取った内容を adopt/dismiss で送り返す
      const proposals = await getLearnProposals(taskId);
      set((st) => ({ learn: { ...st.learn, [taskId]: proposals } }));
    } catch {
      // 完了系以外（409）・通信失敗（§5.4 簡易エラー）
      set({ boardError: 'ルール候補を生成できませんでした' });
    } finally {
      set((st) => ({ learning: { ...st.learning, [taskId]: false } }));
    }
  },
  adoptLearn: async (taskId, tempId) => {
    const proposal = (get().learn[taskId] ?? []).find((p) => p.tempId === tempId);
    if (proposal === undefined) return;
    set({ boardError: null });
    try {
      const created = await adoptLearnRequest(taskId, {
        text: proposal.text,
        scope: proposal.scope,
        tags: proposal.tags,
        confidence: proposal.confidence,
      });
      set((st) => ({
        // NEW バッジはクライアント状態: 採用直後に isNew を立てる（§5.3 / プロト準拠）。
        // SSE の rule.created が先着していても id upsert で自然に一本化される。
        rules: upsertRule(st.rules, { ...created, isNew: true }),
        learn: {
          ...st.learn,
          [taskId]: (st.learn[taskId] ?? []).filter((p) => p.tempId !== tempId),
        },
      }));
      // 採用コメントとカードの commentCount は SSE（comment.created / task.updated）が反映する
    } catch {
      // 失敗時は候補行を残す（§5.4）
      set({ boardError: 'ナレッジへの追加に失敗しました' });
    }
  },
  dismissLearn: async (taskId, tempId) => {
    const proposal = (get().learn[taskId] ?? []).find((p) => p.tempId === tempId);
    if (proposal === undefined) return;
    set({ boardError: null });
    try {
      // 却下も内容を送り返す（rule_feedback に記録され将来の蒸留のお手本になる §6.4）
      await dismissLearnRequest(taskId, {
        text: proposal.text,
        scope: proposal.scope,
        tags: proposal.tags,
        confidence: proposal.confidence,
      });
      set((st) => ({
        learn: {
          ...st.learn,
          [taskId]: (st.learn[taskId] ?? []).filter((p) => p.tempId !== tempId),
        },
      }));
    } catch {
      set({ boardError: '候補の却下に失敗しました' });
    }
  },
  promoteRule: async (ruleId) => {
    set({ boardError: null });
    try {
      const promoted = await promoteRuleRequest(ruleId);
      // NEW 再表示（§5.3 promoteRule: isNew=true）。冪等時（既に team）も応答で確定する
      set((st) => ({ rules: upsertRule(st.rules, { ...promoted, isNew: true }) }));
    } catch {
      set({ boardError: 'チームへの昇格に失敗しました' });
    }
  },

  // ---- SSE 適用（#7 / #10 / #12） ----
  applyTaskUpdated: (task) => {
    const prev = get().cards[task.id];
    set((s) => {
      const lanes = s.lanes.map((lane) => {
        const without = lane.cardIds.filter((id) => id !== task.id);
        if (lane.key !== task.laneKey) {
          return without.length === lane.cardIds.length ? lane : { ...lane, cardIds: without };
        }
        // 対象レーンへ orderInLane 位置に挿入（範囲外は末尾へクランプ）
        const position = Math.max(0, Math.min(task.orderInLane, without.length));
        return {
          ...lane,
          cardIds: [...without.slice(0, position), task.id, ...without.slice(position)],
        };
      });
      // #24: ai_work を離れたらライブ実況の下書きを破棄する
      // （成功時は artifact.created が先にクリア済み。失敗ハンドオフ等の残骸対策）
      let liveDraft = s.liveDraft;
      if (task.status !== 'ai_work' && liveDraft[task.id] !== undefined) {
        liveDraft = { ...liveDraft };
        delete liveDraft[task.id];
      }
      return { cards: { ...s.cards, [task.id]: task }, lanes, liveDraft };
    });
    // #19 ライブフィード: ステータスが変わった瞬間だけ積む（差し替えのみでは積まない）
    if (prev !== undefined && prev.status !== task.status) {
      activitySeq += 1;
      get().pushActivity({
        id: `status-${task.id}-${activitySeq}`,
        taskId: task.id,
        taskTitle: task.title,
        text: `${STATUS_META[task.status].label}へ`,
        at: Date.now(),
      });
    }
  },
  applyCommentCreated: (comment) => {
    set((s) => {
      const list = s.comments[comment.taskId];
      // 未読込のタスクは何もしない（ドロワーを開いたとき GET で取得する）。
      // カードの commentCount は直後の task.updated が正の値で同期する。
      if (!list) return {};
      if (list.some((c) => c.id === comment.id)) return {}; // id 重複排除
      // 自分の楽観的追加（同一 author+text の tmp-）が送信中なら、確定版として差し替える
      // （SSE は POST 応答より先に届きうるため。confirmComment 側の id 重複排除と対になる）
      const pendingIndex = list.findIndex(
        (c) => isTempId(c.id) && c.author === comment.author && c.text === comment.text,
      );
      const next =
        pendingIndex >= 0
          ? list.map((c, i) => (i === pendingIndex ? comment : c))
          : [...list, comment];
      return { comments: { ...s.comments, [comment.taskId]: next } };
    });
    // #19 ライブフィード: AIコメントは未読込タスクでも横断ログに積む（要約30字。id で冪等）
    if (comment.author === 'ai') {
      get().pushActivity({
        id: `comment-${comment.id}`,
        taskId: comment.taskId,
        taskTitle: get().cards[comment.taskId]?.title ?? '',
        text: summarizeActivityText(comment.text),
        role: comment.agentRole ?? undefined,
        at: Date.now(),
      });
    }
  },
  applyArtifactCreated: (artifact) => {
    set((s) => {
      // #24: 確定版が届いたのでライブ実況の下書きはクリアする（差し替え）
      let liveDraft = s.liveDraft;
      if (liveDraft[artifact.taskId] !== undefined) {
        liveDraft = { ...liveDraft };
        delete liveDraft[artifact.taskId];
      }
      const list = s.artifacts[artifact.taskId] ?? [];
      // id 重複排除（自分の POST 応答と SSE の二重適用を防ぐ）
      if (list.some((a) => a.id === artifact.id)) return { liveDraft };
      // version 昇順（末尾が最新）を維持して追記
      const next = [...list, artifact].sort((a, b) => a.version - b.version);
      return { artifacts: { ...s.artifacts, [artifact.taskId]: next }, liveDraft };
    });
    // #19 ライブフィード: 版の作成を積む（AI生成=実行AI名義 / 人の編集版=名義なし）
    get().pushActivity({
      id: `artifact-${artifact.id}`,
      taskId: artifact.taskId,
      taskTitle: get().cards[artifact.taskId]?.title ?? '',
      text: `成果物v${artifact.version}を作成`,
      role: artifact.jobId != null ? 'executor' : undefined,
      at: Date.now(),
    });
  },
  applyArtifactDelta: ({ taskId, delta, seq }) =>
    set((s) => ({
      liveDraft: {
        ...s.liveDraft,
        // seq=1 は新しいストリームの開始（execute のリトライ・revise 再実行）:
        // 前のストリームの残骸を捨てて連結し直す
        [taskId]: seq <= 1 ? delta : (s.liveDraft[taskId] ?? '') + delta,
      },
    })),
  applyChatMessageCreated: (message) =>
    set((s) => {
      const list = s.chat[message.taskId];
      // 未開始の壁打ちは何もしない（startChat の POST 応答が一覧を丸ごと返す）
      if (!list) return {};
      if (list.some((m) => m.id === message.id)) return {}; // id 重複排除
      // 人メッセージ送信時も配信されるため、自分の楽観的追加（同一 author+text の tmp-）が
      // 送信中なら確定版として差し替える（sendChat 側の id 重複排除と対になる）
      const pendingIndex = list.findIndex(
        (m) => isTempId(m.id) && m.author === message.author && m.text === message.text,
      );
      const next =
        pendingIndex >= 0
          ? list.map((m, i) => (i === pendingIndex ? message : m))
          : [...list, message];
      return { chat: { ...s.chat, [message.taskId]: next } };
    }),
  applySubtaskProposal: (event) =>
    set((s) => ({
      proposal: { ...s.proposal, [event.taskId]: event.subtasks },
    })),
  applyRuleCreated: (rule) => {
    set((s) => ({ rules: upsertRule(s.rules, rule) }));
    // #19 ライブフィード: 学習AIのルール獲得を積む（adopt 応答先着でも id で一本化）
    get().pushActivity({
      id: `rule-${rule.id}`,
      taskId: rule.sourceTaskId ?? '',
      taskTitle:
        rule.sourceTaskId != null
          ? (get().cards[rule.sourceTaskId]?.title ?? '')
          : '',
      text: `ルール${rule.id}を学習`,
      role: 'distiller',
      at: Date.now(),
    });
  },
  applyRuleUpdated: (rule) =>
    set((s) => {
      // #20: applied が増えた（=「AIにまかせる」で注入された）瞬間だけフラッシュを発火
      const prev = s.rules.find((r) => r.id === rule.id);
      const flashed = prev !== undefined && rule.applied > prev.applied;
      return {
        rules: upsertRule(s.rules, rule),
        ...(flashed
          ? { justApplied: { ...s.justApplied, [rule.id]: Date.now() } }
          : {}),
      };
    }),
}));

// ---- 派生値（§5.1: render のたび計算, 保存しない） ----

/** youCount = owner が human かつ status≠done のカード数（トップバー「あなたの番」） */
export function deriveYouCount(cards: Record<string, Task>): number {
  return Object.values(cards).filter(
    (t) => STATUS_META[t.status].owner === 'human' && t.status !== 'done',
  ).length;
}

/** aiCount = status が ai_work または queued のカード数（「AI稼働」） */
export function deriveAiCount(cards: Record<string, Task>): number {
  return Object.values(cards).filter(
    (t) => t.status === 'ai_work' || t.status === 'queued',
  ).length;
}

/** ruleCount = rules 総数（「◈ ナレッジ」） */
export function deriveRuleCount(rules: readonly Rule[]): number {
  return rules.length;
}
