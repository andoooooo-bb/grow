// 担当アバター（§3.2: ai=ティール白文字"AI" / human=アンバー"YK"）
// variant: card=18px（ボードカード）/ subtask=17px（#7 サブタスク行）/ thread=24px（#7 アクティビティ）

import type { Owner } from '../../types/domain.ts';
import './Avatar.css';

interface AvatarProps {
  owner: Owner;
  variant?: 'card' | 'subtask' | 'thread';
}

export function Avatar({ owner, variant = 'card' }: AvatarProps) {
  return (
    <span className={`avatar avatar--${owner} avatar--${variant}`} aria-hidden="true">
      {owner === 'ai' ? 'AI' : 'YK'}
    </span>
  );
}
