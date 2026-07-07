"""共有ドメイン層（§2.2 / §5.6）。

frontend/src/types/{domain,api}.ts / frontend/src/lib/stateMachine.ts と鏡写し。
正準フィクスチャは shared/contracts/*.json（テストで一致を担保）。
"""

from app.domain.models import (
    STATUS_META,
    AiJob,
    AiJobKind,
    AiJobStatus,
    Artifact,
    Author,
    ChatMessage,
    Comment,
    Confidence,
    LaneKey,
    Owner,
    Rule,
    RuleProposal,
    RuleScope,
    StatusMeta,
    Task,
    TaskStatus,
    Tone,
)
from app.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    can_transition,
    validate_progress_invariant,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "STATUS_META",
    "AiJob",
    "AiJobKind",
    "AiJobStatus",
    "Artifact",
    "Author",
    "ChatMessage",
    "Comment",
    "Confidence",
    "LaneKey",
    "Owner",
    "Rule",
    "RuleProposal",
    "RuleScope",
    "StatusMeta",
    "Task",
    "TaskStatus",
    "Tone",
    "can_transition",
    "validate_progress_invariant",
]
