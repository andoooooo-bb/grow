// ステータスバッジ（§3.2 / §04）。label / tone は STATUS_META から導出（§5.1）。
// tone: work=ティール(点滅) / spec=パープル / attention=アンバー(点滅) / neutral=グレー / done=グリーン
// size: sm=カード上（10px/600, padding 3px 8px, radius5, ドット5px）
//       lg=ドロワーヘッダ（11px/600, padding 4px 10px, radius6, ドット6px — #7 で使用）

import type { TaskStatus } from '../../types/domain.ts';
import { STATUS_META } from '../../types/domain.ts';
import './StatusBadge.css';

interface StatusBadgeProps {
  status: TaskStatus;
  size?: 'sm' | 'lg';
}

export function StatusBadge({ status, size = 'sm' }: StatusBadgeProps) {
  const { label, tone } = STATUS_META[status];
  return (
    <span className={`status-badge status-badge--${tone} status-badge--${size}`}>
      <span className="status-badge__dot" aria-hidden="true" />
      {label}
    </span>
  );
}
