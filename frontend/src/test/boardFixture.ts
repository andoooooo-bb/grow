// §2.5 シードデータ相当のテストフィクスチャ（BoardResponse）。
// status の並び（§2.5 忠実）: breakdown, queued, spec, you_todo, queued,
// ai_work, you_todo, you_review, reviewing, done, done
// → youCount=6 / aiCount=3 / ruleCount=5

import type { BoardResponse } from '../types/api.ts';
import type {
  Confidence,
  LaneKey,
  Rule,
  RuleScope,
  Task,
  TaskStatus,
} from '../types/domain.ts';

const WORKSPACE_ID = 'ws-1';
const BOARD_ID = 'board-1';
const OWNER_USER_ID = 'user-yk';
const AT = '2026-07-01T00:00:00Z';

interface SeedTask {
  id: string;
  laneKey: LaneKey;
  orderInLane: number;
  title: string;
  status: TaskStatus;
  labels: string[];
  progress?: number;
}

function toTask(seed: SeedTask): Task {
  return {
    workspaceId: WORKSPACE_ID,
    boardId: BOARD_ID,
    ownerUserId: OWNER_USER_ID,
    createdAt: AT,
    updatedAt: AT,
    ...seed,
  };
}

interface SeedRule {
  id: string;
  scope: RuleScope;
  text: string;
  tags: string[];
  source: string;
  confidence: Confidence;
  applied: number;
}

function toRule(seed: SeedRule): Rule {
  return {
    workspaceId: WORKSPACE_ID,
    ownerUserId: seed.scope === 'personal' ? OWNER_USER_ID : undefined,
    createdAt: AT,
    updatedAt: AT,
    ...seed,
  };
}

const SEED_TASKS: SeedTask[] = [
  // backlog
  { id: 'T-130', laneKey: 'backlog', orderInLane: 0, title: 'ポートフォリオサイトのリニューアル', status: 'breakdown', labels: ['個人', 'デザイン'] },
  { id: 'T-121', laneKey: 'backlog', orderInLane: 1, title: '確定申告に必要な書類リストを作成', status: 'queued', labels: ['経理'] },
  // todo
  { id: 'T-104', laneKey: 'todo', orderInLane: 0, title: '競合SaaS 5社の料金プランを調査', status: 'spec', labels: ['仕事', '調査'] },
  { id: 'T-109', laneKey: 'todo', orderInLane: 1, title: '週次レビューのテンプレートを記入', status: 'you_todo', labels: ['個人'] },
  { id: 'T-112', laneKey: 'todo', orderInLane: 2, title: 'ブログ記事の構成案づくり', status: 'queued', labels: ['ブログ'] },
  // progress
  { id: 'T-098', laneKey: 'progress', orderInLane: 0, title: '競合調査レポートの下書き', status: 'ai_work', progress: 60, labels: ['仕事', '調査'] },
  { id: 'T-101', laneKey: 'progress', orderInLane: 1, title: '新機能のリリースノート', status: 'you_todo', labels: ['ブログ'] },
  // review
  { id: 'T-091', laneKey: 'review', orderInLane: 0, title: '確定申告サマリーの最終確認', status: 'you_review', labels: ['経理'] },
  { id: 'T-089', laneKey: 'review', orderInLane: 1, title: '記事『AI協働術』の校正', status: 'reviewing', labels: ['ブログ'] },
  // done
  { id: 'T-080', laneKey: 'done', orderInLane: 0, title: '先週の経費を費目ごとに分類', status: 'done', labels: ['経理'] },
  { id: 'T-077', laneKey: 'done', orderInLane: 1, title: '名刺データをCSVに整理', status: 'done', labels: ['仕事'] },
];

const SEED_RULES: SeedRule[] = [
  { id: 'K-01', scope: 'personal', text: 'レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く', tags: ['調査', 'ブログ'], source: 'T-098 で2回同じ修正', confidence: 'high', applied: 6 },
  { id: 'K-02', scope: 'personal', text: '絵文字は使わない。文体は簡潔・断定調に統一する', tags: [], source: '複数タスクで一貫', confidence: 'high', applied: 14 },
  { id: 'K-03', scope: 'personal', text: '競合調査は料金を表形式にし、各項目に出典URLを付ける', tags: ['調査'], source: 'T-098', confidence: 'med', applied: 2 },
  { id: 'K-04', scope: 'team', text: '社外向け文書は敬体。数値は必ず出典を明記する', tags: [], source: 'チーム標準', confidence: 'high', applied: 9 },
  { id: 'K-05', scope: 'team', text: '経費の費目分類は自社の勘定科目マスタに合わせる', tags: ['経理'], source: '経理チーム', confidence: 'high', applied: 5 },
];

const LANE_NAMES: Record<LaneKey, string> = {
  backlog: 'バックログ',
  todo: 'ToDo',
  progress: '進行中',
  review: 'レビュー',
  done: '完了',
};

/** §2.5 シード相当の BoardResponse を毎回新しいオブジェクトで返す */
export function boardFixture(): BoardResponse {
  const cards: Record<string, Task> = {};
  for (const seed of SEED_TASKS) {
    cards[seed.id] = toTask(seed);
  }
  const laneKeys: LaneKey[] = ['backlog', 'todo', 'progress', 'review', 'done'];
  return {
    lanes: laneKeys.map((key) => ({
      key,
      name: LANE_NAMES[key],
      cardIds: SEED_TASKS.filter((t) => t.laneKey === key).map((t) => t.id),
    })),
    cards,
    rules: SEED_RULES.map(toRule),
  };
}
