// チーム昇格DLPガードレール モーダル（#29 §6.7）。
// ナレッジ・オーバーレイのルール一覧で「チームへ昇格 ↑」を押したとき、ルール文へ
// 機微情報（人名・メール・電話・マイナンバー・カード番号）が含まれていると BE が
// 409 を返す。その findings をこのモーダルで警告表示し、「AIに一般化させる」→
// generalize API → 編集可能な textarea で確認 →「この内容で昇格」で text 付き
// 再昇格（PATCH 相当）する。KnowledgeOverlay 内の軽量実装（受信箱UIは壊さない）。

import { useEffect, useState } from 'react';
import { useBoardStore } from '../../store/board.ts';
import type { DlpFinding } from '../../types/api.ts';
import './PromoteGuardModal.css';

// DLP infoType → 日本語ラベル（未知の種別はそのまま表示する）
export const DLP_INFO_TYPE_LABEL: Record<string, string> = {
  PERSON_NAME: '人名',
  EMAIL_ADDRESS: 'メールアドレス',
  PHONE_NUMBER: '電話番号',
  JAPAN_INDIVIDUAL_NUMBER: 'マイナンバー',
  CREDIT_CARD_NUMBER: 'クレジットカード番号',
};

/** 警告見出し（BE の 409 detail と同文言 — routers/rules.py PROMOTE_BLOCKED_DETAIL） */
export const PROMOTE_BLOCKED_TITLE = '機微情報が含まれるためチーム昇格できません';

export function dlpInfoTypeLabel(infoType: string): string {
  return DLP_INFO_TYPE_LABEL[infoType] ?? infoType;
}

/** 検出された機微情報の一覧（種別バッジ＋該当文字列） */
function FindingList({ findings }: { findings: DlpFinding[] }) {
  return (
    <ul className="promote-guard__findings">
      {findings.map((f, i) => (
        <li key={`${f.infoType}-${f.quote}-${i}`} className="promote-guard__finding">
          <span className="promote-guard__finding-type">{dlpInfoTypeLabel(f.infoType)}</span>
          <span className="promote-guard__finding-quote">{f.quote}</span>
        </li>
      ))}
    </ul>
  );
}

export function PromoteGuardModal() {
  const guard = useBoardStore((s) => s.promoteGuard);
  const closePromoteGuard = useBoardStore((s) => s.closePromoteGuard);
  const generalizePromote = useBoardStore((s) => s.generalizePromote);
  const confirmPromoteWithText = useBoardStore((s) => s.confirmPromoteWithText);

  // 編集中の一般化文案（textarea のローカル状態。generalized 取得時に初期化する）
  const [draft, setDraft] = useState('');
  useEffect(() => {
    if (guard?.generalized != null) setDraft(guard.generalized);
  }, [guard?.generalized]);

  if (guard === null) return null;

  const busy = guard.phase === 'generalizing' || guard.phase === 'promoting';
  const editing = guard.phase === 'edit' || guard.phase === 'promoting';

  return (
    // オーバーレイの上にさらに重ねる遮蔽。遮蔽クリックで閉じる（内側は stopPropagation）
    <div className="promote-guard" onClick={closePromoteGuard}>
      <div
        className="promote-guard__panel"
        role="dialog"
        aria-label={PROMOTE_BLOCKED_TITLE}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="promote-guard__header">
          <span className="promote-guard__icon" aria-hidden="true">
            ⚠
          </span>
          <span className="promote-guard__title">{PROMOTE_BLOCKED_TITLE}</span>
        </div>

        <div className="promote-guard__body">
          <p className="promote-guard__lead">
            このルール文に次の機微情報が含まれています。チームの形式知には固有名詞・
            秘密情報を残せません（§6.7）。AIに一般化させて安全な文案にできます。
          </p>
          <FindingList findings={guard.findings} />

          {editing ? (
            <label className="promote-guard__edit">
              <span className="promote-guard__edit-label">一般化された文案（編集できます）</span>
              <textarea
                className="promote-guard__textarea"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={4}
                disabled={guard.phase === 'promoting'}
              />
            </label>
          ) : (
            <div className="promote-guard__original">
              <span className="promote-guard__original-label">現在のルール文</span>
              <span className="promote-guard__original-text">{guard.ruleText}</span>
            </div>
          )}
        </div>

        <div className="promote-guard__actions">
          <button
            type="button"
            className="promote-guard__cancel"
            onClick={closePromoteGuard}
            disabled={busy}
          >
            キャンセル
          </button>
          {editing ? (
            <button
              type="button"
              className="promote-guard__promote"
              onClick={() => void confirmPromoteWithText(draft)}
              disabled={busy || draft.trim() === ''}
            >
              {guard.phase === 'promoting' ? (
                <>
                  <span className="promote-guard__spinner" aria-hidden="true" />
                  昇格中…
                </>
              ) : (
                'この内容で昇格'
              )}
            </button>
          ) : (
            <button
              type="button"
              className="promote-guard__generalize"
              onClick={() => void generalizePromote()}
              disabled={busy}
            >
              {guard.phase === 'generalizing' ? (
                <>
                  <span className="promote-guard__spinner" aria-hidden="true" />
                  一般化中…
                </>
              ) : (
                'AIに一般化させる'
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
