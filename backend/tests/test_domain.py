"""共有ドメイン型・STATUS_META・ステートマシンのテスト（#3）。

shared/contracts/*.json（正準フィクスチャ）と Python 定義の一致を担保する。
"""

import json
from pathlib import Path

import pytest

from app.domain.dto import (
    BoardResponse,
    ChatMessageCreate,
    CommentCreate,
    LaneDto,
    RuleCreate,
    RuleProposalDto,
    SubtaskProposal,
    TaskPatch,
)
from app.domain.models import (
    STATUS_META,
    Owner,
    Task,
    TaskStatus,
)
from app.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    can_transition,
    validate_progress_invariant,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "shared" / "contracts"
STATUS_META_JSON = json.loads((CONTRACTS_DIR / "status_meta.json").read_text(encoding="utf-8"))
TRANSITIONS_JSON = json.loads((CONTRACTS_DIR / "transitions.json").read_text(encoding="utf-8"))

ALL_STATUSES = list(TaskStatus)


# ---- STATUS_META ----
class TestStatusMeta:
    def test_matches_canonical_fixture(self):
        """STATUS_META が shared/contracts/status_meta.json と完全一致する。"""
        as_dict = {
            status.value: meta.model_dump(by_alias=True) for status, meta in STATUS_META.items()
        }
        assert as_dict == STATUS_META_JSON

    def test_covers_all_statuses(self):
        assert set(STATUS_META.keys()) == set(ALL_STATUSES)
        assert len(STATUS_META) == 8

    def test_owner_tone_label_derivable(self):
        """status から owner / tone / label が導出できる（§5.1 派生値）。"""
        meta = STATUS_META[TaskStatus.AI_WORK]
        assert (meta.label, meta.owner, meta.tone) == ("AI作業中", "ai", "work")
        meta = STATUS_META[TaskStatus.YOU_REVIEW]
        assert (meta.label, meta.owner, meta.tone) == ("あなたのレビュー待ち", "human", "attention")
        assert STATUS_META[TaskStatus.QUEUED].owner == Owner.AI
        assert STATUS_META[TaskStatus.BREAKDOWN].label == "分解しましょう"
        assert STATUS_META[TaskStatus.DONE].tone == "done"

    def test_human_statuses_match_spec(self):
        """owner=human は「あなたの番」対象の5statusと一致する（§5.6）。"""
        human = {s for s in ALL_STATUSES if STATUS_META[s].owner == Owner.HUMAN}
        assert human == {
            TaskStatus.BREAKDOWN,
            TaskStatus.SPEC,
            TaskStatus.YOU_TODO,
            TaskStatus.YOU_REVIEW,
            TaskStatus.REVIEWING,
        }


# ---- ステートマシン ----
class TestTransitions:
    def test_matches_canonical_fixture(self):
        """ALLOWED_TRANSITIONS が shared/contracts/transitions.json と完全一致する。"""
        json_pairs = {(t["from"], t["to"]) for t in TRANSITIONS_JSON}
        py_pairs = {(f.value, t.value) for f, t in ALLOWED_TRANSITIONS}
        assert py_pairs == json_pairs
        assert len(ALLOWED_TRANSITIONS) == len(TRANSITIONS_JSON)

    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (TaskStatus.BREAKDOWN, TaskStatus.SPEC),
            (TaskStatus.SPEC, TaskStatus.AI_WORK),
            (TaskStatus.QUEUED, TaskStatus.AI_WORK),
            (TaskStatus.YOU_TODO, TaskStatus.DONE),
            (TaskStatus.YOU_TODO, TaskStatus.AI_WORK),
            (TaskStatus.AI_WORK, TaskStatus.YOU_REVIEW),
            (TaskStatus.AI_WORK, TaskStatus.YOU_TODO),  # ジョブ最終失敗時の人戻し（§7.2）
            (TaskStatus.YOU_REVIEW, TaskStatus.REVIEWING),
            (TaskStatus.YOU_REVIEW, TaskStatus.DONE),
            (TaskStatus.YOU_REVIEW, TaskStatus.AI_WORK),
            (TaskStatus.REVIEWING, TaskStatus.DONE),
            (TaskStatus.REVIEWING, TaskStatus.AI_WORK),
            (TaskStatus.REVIEWING, TaskStatus.YOU_TODO),
        ],
    )
    def test_allowed(self, from_status: TaskStatus, to_status: TaskStatus):
        assert can_transition(from_status, to_status) is True

    def test_done_reopens_to_any(self):
        """done → 任意（再オープン管理操作）。"""
        for to_status in ALL_STATUSES:
            assert can_transition(TaskStatus.DONE, to_status) is True

    def test_self_transition_is_noop(self):
        """同一statusへの遷移（from==to）は常に許可。"""
        for status in ALL_STATUSES:
            assert can_transition(status, status) is True

    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (TaskStatus.QUEUED, TaskStatus.DONE),
            (TaskStatus.QUEUED, TaskStatus.YOU_TODO),
            (TaskStatus.QUEUED, TaskStatus.BREAKDOWN),
            (TaskStatus.BREAKDOWN, TaskStatus.AI_WORK),
            (TaskStatus.BREAKDOWN, TaskStatus.DONE),
            (TaskStatus.SPEC, TaskStatus.DONE),
            (TaskStatus.SPEC, TaskStatus.YOU_REVIEW),
            (TaskStatus.AI_WORK, TaskStatus.DONE),
            (TaskStatus.AI_WORK, TaskStatus.REVIEWING),
            (TaskStatus.AI_WORK, TaskStatus.QUEUED),
            (TaskStatus.YOU_TODO, TaskStatus.YOU_REVIEW),
            (TaskStatus.YOU_TODO, TaskStatus.REVIEWING),
            (TaskStatus.YOU_REVIEW, TaskStatus.YOU_TODO),
            (TaskStatus.YOU_REVIEW, TaskStatus.QUEUED),
            (TaskStatus.REVIEWING, TaskStatus.QUEUED),
            (TaskStatus.REVIEWING, TaskStatus.YOU_REVIEW),
        ],
    )
    def test_denied(self, from_status: TaskStatus, to_status: TaskStatus):
        assert can_transition(from_status, to_status) is False

    def test_exhaustive_against_fixture(self):
        """全 8×8 ペアで判定が正準フィクスチャ（＋no-op）と一致する。"""
        json_pairs = {(t["from"], t["to"]) for t in TRANSITIONS_JSON}
        for from_status in ALL_STATUSES:
            for to_status in ALL_STATUSES:
                expected = from_status == to_status or (
                    from_status.value,
                    to_status.value,
                ) in json_pairs
                assert can_transition(from_status, to_status) is expected, (
                    f"{from_status} -> {to_status}"
                )


# ---- progress 不変条件（§5.6: ai_work のみ非null） ----
class TestProgressInvariant:
    def test_ai_work_allows_0_to_100(self):
        for progress in (0, 45, 100):
            assert validate_progress_invariant(TaskStatus.AI_WORK, progress) is True

    def test_none_progress_is_valid_for_any_status(self):
        for status in ALL_STATUSES:
            assert validate_progress_invariant(status, None) is True

    def test_non_ai_work_rejects_progress(self):
        for status in ALL_STATUSES:
            if status is TaskStatus.AI_WORK:
                continue
            assert validate_progress_invariant(status, 50) is False, status

    def test_ai_work_rejects_out_of_range(self):
        assert validate_progress_invariant(TaskStatus.AI_WORK, -1) is False
        assert validate_progress_invariant(TaskStatus.AI_WORK, 101) is False


# ---- DTO / camelCase alias ----
class TestDtoContracts:
    def _task_camel(self) -> dict:
        return {
            "id": "T-098",
            "workspaceId": "ws-1",
            "boardId": "b-1",
            "laneKey": "progress",
            "orderInLane": 0,
            "title": "競合調査レポートの下書き",
            "status": "ai_work",
            "ownerUserId": "u-1",
            "labels": ["仕事", "調査"],
            "progress": 60,
            "createdAt": "2026-07-07T00:00:00Z",
            "updatedAt": "2026-07-07T00:00:00Z",
        }

    def test_task_accepts_camel_case_and_dumps_by_alias(self):
        """API 表現（camelCase）で構築でき、by_alias=True で同じ形に戻る。"""
        payload = self._task_camel()
        task = Task.model_validate(payload)
        assert task.lane_key == "progress"
        assert task.order_in_lane == 0
        assert task.owner_user_id == "u-1"
        dumped = task.model_dump(by_alias=True, exclude_none=True)
        assert dumped == payload

    def test_task_accepts_snake_case_too(self):
        """populate_by_name=True で snake_case でも構築できる。"""
        task = Task(
            id="T-001",
            workspace_id="ws-1",
            board_id="b-1",
            lane_key="todo",
            order_in_lane=1,
            title="t",
            status=TaskStatus.QUEUED,
            owner_user_id="u-1",
            labels=[],
            created_at="2026-07-07T00:00:00Z",
            updated_at="2026-07-07T00:00:00Z",
        )
        assert task.model_dump(by_alias=True)["laneKey"] == "todo"

    def test_board_response_normalized_shape(self):
        """BoardResponse が §2.3 の正規化形（lanes/cards/rules）を持つ。"""
        board = BoardResponse.model_validate(
            {
                "lanes": [
                    {"key": "backlog", "name": "バックログ", "cardIds": []},
                    {"key": "progress", "name": "進行中", "cardIds": ["T-098"]},
                ],
                "cards": {"T-098": self._task_camel()},
                "rules": [],
            }
        )
        assert isinstance(board.lanes[0], LaneDto)
        assert board.lanes[1].card_ids == ["T-098"]
        assert board.cards["T-098"].status == TaskStatus.AI_WORK

    def test_request_dtos_accept_camel_case(self):
        patch = TaskPatch.model_validate({"laneKey": "done", "status": "done"})
        assert patch.lane_key == "done"
        comment = CommentCreate.model_validate(
            {"author": "human", "authorUserId": "u-1", "text": "x"}
        )
        assert comment.author_user_id == "u-1"
        chat = ChatMessageCreate.model_validate({"author": "ai", "text": "y"})
        assert chat.author == "ai"
        rule = RuleCreate.model_validate(
            {
                "scope": "personal",
                "text": "レポートは結論→根拠の順で書く",
                "tags": ["調査"],
                "source": "T-098 から学習",
                "sourceTaskId": "T-098",
                "confidence": "med",
            }
        )
        assert rule.source_task_id == "T-098"
        sub = SubtaskProposal.model_validate({"title": "たたき台の作成", "owner": "ai"})
        assert sub.owner == Owner.AI
        proposal = RuleProposalDto.model_validate(
            {
                "tempId": "tmp-1",
                "taskId": "T-098",
                "text": "料金は必ず税抜/税込を明記する",
                "scope": "personal",
                "tags": ["調査", "経理"],
                "confidence": "med",
            }
        )
        assert proposal.temp_id == "tmp-1"
