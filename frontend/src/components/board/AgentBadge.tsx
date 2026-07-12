// エージェント役割のミニバッジ（#19）: 「計画AI」「実行AI」「レビューAI」「学習AI」「指揮者AI」。
// 色は §04 の既存トークンに馴染ませる（計画/学習=パープル・実行=ティール・
// レビュー=アンバー・指揮者=ダーク）。未知の役割は描画しない（前方互換）。

import type { AgentRole } from '../../types/domain.ts';
import { AGENT_ROLE_META } from '../../types/domain.ts';
import './AgentBadge.css';

interface AgentBadgeProps {
  role: AgentRole;
}

export function AgentBadge({ role }: AgentBadgeProps) {
  const meta = AGENT_ROLE_META[role] as { label: string } | undefined;
  if (meta === undefined) return null; // 後続エージェントの新役割にも壊れない
  return <span className={`agent-badge agent-badge--${role}`}>{meta.label}</span>;
}
