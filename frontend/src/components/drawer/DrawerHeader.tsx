// ドロワーヘッダ（§3.3.1 共通）: 「{ID} · {レーン名}」・閉じる✕・タイトル・
// ステータスバッジ(lg)＋「担当: {Grow (AI) | あなた}」（owner は STATUS_META から導出）。
// #21: L0-L3 オートノミー・ダイヤル＋行動範囲ポリシー行（Web検索トグル・コスト上限）。
// 変更は PATCH で即保存（楽観的。store の setAutonomy / setPolicy が担う）。

import { useBoardStore } from '../../store/board.ts';
import type { Task } from '../../types/domain.ts';
import {
  ALL_AUTONOMY_LEVELS,
  AUTONOMY_META,
  STATUS_META,
  taskAllowWebSearch,
  taskAutonomy,
  taskCostCapUsd,
} from '../../types/domain.ts';
import { StatusBadge } from '../board/StatusBadge';
import './DrawerHeader.css';

interface DrawerHeaderProps {
  task: Task;
}

export function DrawerHeader({ task }: DrawerHeaderProps) {
  const laneName = useBoardStore(
    (s) => s.lanes.find((lane) => lane.key === task.laneKey)?.name ?? task.laneKey,
  );
  const closePanel = useBoardStore((s) => s.closePanel);
  const setAutonomy = useBoardStore((s) => s.setAutonomy);
  const setPolicy = useBoardStore((s) => s.setPolicy);
  const owner = STATUS_META[task.status].owner;

  const autonomy = taskAutonomy(task);
  const allowWebSearch = taskAllowWebSearch(task);
  const costCapUsd = taskCostCapUsd(task);

  /** コスト上限入力の確定（blur/Enter）。空欄 = 上限なし。不正・負値は無視 */
  const commitCostCap = (raw: string) => {
    const trimmed = raw.trim();
    const next = trimmed === '' ? null : Number(trimmed);
    if (next !== null && (!Number.isFinite(next) || next < 0)) return;
    if (next === costCapUsd) return;
    void setPolicy(task.id, { allowWebSearch, costCapUsd: next });
  };

  return (
    <div className="drawer-header">
      <div className="drawer-header__top">
        <span className="drawer-header__meta">
          {task.id} · {laneName}
        </span>
        <button
          type="button"
          className="drawer-header__close"
          aria-label="閉じる"
          onClick={closePanel}
        >
          ✕
        </button>
      </div>
      <div className="drawer-header__title">{task.title}</div>
      <div className="drawer-header__status">
        <StatusBadge status={task.status} size="lg" />
        <span className="drawer-header__owner">
          担当: {owner === 'ai' ? 'Grow (AI)' : 'あなた'}
        </span>
      </div>

      {/* #21 オートノミー・ダイヤル（L0-L3）＋ 行動範囲ポリシー */}
      <div className="drawer-header__autonomy">
        <div className="autonomy-dial" role="group" aria-label="オートノミー">
          {ALL_AUTONOMY_LEVELS.map((level) => (
            <button
              key={level}
              type="button"
              className={`autonomy-dial__btn autonomy-dial__btn--${level.toLowerCase()}${
                level === autonomy ? ' autonomy-dial__btn--active' : ''
              }`}
              aria-pressed={level === autonomy}
              title={`${AUTONOMY_META[level].label} — ${AUTONOMY_META[level].description}`}
              onClick={() => void setAutonomy(task.id, level)}
            >
              {level}
            </button>
          ))}
          <span className="autonomy-dial__label" title={AUTONOMY_META[autonomy].description}>
            {AUTONOMY_META[autonomy].label}
          </span>
        </div>
        <div className="policy-row">
          <button
            type="button"
            className={`policy-row__toggle${allowWebSearch ? ' policy-row__toggle--on' : ''}`}
            aria-pressed={allowWebSearch}
            title="AIがWeb検索（読み取りのみ）を使えるか。OFFでは既知情報のみで作成し要確認事項を明記する"
            onClick={() =>
              void setPolicy(task.id, { allowWebSearch: !allowWebSearch, costCapUsd })
            }
          >
            Web検索 {allowWebSearch ? 'ON' : 'OFF'}
          </button>
          <label className="policy-row__cap">
            コスト上限 $
            <input
              key={`${task.id}:${costCapUsd ?? ''}`}
              className="policy-row__cap-input"
              type="number"
              min="0"
              step="0.5"
              defaultValue={costCapUsd ?? ''}
              placeholder="なし"
              aria-label="コスト上限（USD）"
              title="タスクの累計AIコストがこの額に達すると自動停止して人へハンドオフする。空欄 = 上限なし"
              onBlur={(e) => commitCostCap(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur();
              }}
            />
          </label>
        </div>
      </div>
    </div>
  );
}
