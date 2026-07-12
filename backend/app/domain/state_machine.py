"""ステータス・ステートマシン（§5.6 が正。§7.2 の ai_work→you_todo を補完）。

frontend/src/lib/stateMachine.ts と鏡写し。
正準フィクスチャは shared/contracts/transitions.json（テストで一致を担保）。
"""

from app.domain.models import TaskStatus

# 許可遷移テーブル（from, to）
# - done→任意 は再オープン（管理操作）として全statusへ展開済み
# - 同一statusへの遷移（from==to）は no-op として can_transition が常に許可
ALLOWED_TRANSITIONS: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (TaskStatus.BREAKDOWN, TaskStatus.SPEC),  # 壁打ち開始
        (TaskStatus.SPEC, TaskStatus.AI_WORK),  # 分解反映 / AIにまかせる
        (TaskStatus.QUEUED, TaskStatus.AI_WORK),  # AIにまかせる/依頼
        (TaskStatus.YOU_TODO, TaskStatus.DONE),  # 人が着手・完了
        (TaskStatus.YOU_TODO, TaskStatus.AI_WORK),  # AIに委任
        (TaskStatus.AI_WORK, TaskStatus.YOU_REVIEW),  # AI完了
        (TaskStatus.AI_WORK, TaskStatus.YOU_TODO),  # ジョブ最終失敗時の人戻し（§7.2）
        (TaskStatus.YOU_REVIEW, TaskStatus.REVIEWING),  # レビュー開始
        (TaskStatus.YOU_REVIEW, TaskStatus.DONE),  # 承認
        (TaskStatus.YOU_REVIEW, TaskStatus.AI_WORK),  # 差し戻し
        (TaskStatus.REVIEWING, TaskStatus.DONE),  # 承認
        (TaskStatus.REVIEWING, TaskStatus.AI_WORK),  # 差し戻し
        (TaskStatus.REVIEWING, TaskStatus.YOU_TODO),  # 差し戻し
        # done → 任意（再オープン管理操作）
        (TaskStatus.DONE, TaskStatus.QUEUED),
        (TaskStatus.DONE, TaskStatus.BREAKDOWN),
        (TaskStatus.DONE, TaskStatus.SPEC),
        (TaskStatus.DONE, TaskStatus.AI_WORK),
        (TaskStatus.DONE, TaskStatus.YOU_TODO),
        (TaskStatus.DONE, TaskStatus.YOU_REVIEW),
        (TaskStatus.DONE, TaskStatus.REVIEWING),
    }
)


def can_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """from→to の遷移が許可されているか（§5.6）。from==to は常に許可（no-op）。"""
    if from_status == to_status:
        return True
    return (from_status, to_status) in ALLOWED_TRANSITIONS


def validate_progress_invariant(status: TaskStatus, progress: int | None) -> bool:
    """不変条件: progress は ai_work のときのみ非null（0..100）。それ以外は None。"""
    if progress is None:
        return True
    return status == TaskStatus.AI_WORK and 0 <= progress <= 100
