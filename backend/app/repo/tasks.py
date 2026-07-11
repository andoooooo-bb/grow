"""タスクのリポジトリ層。

- API 境界の taskId は human_id（例 "T-098"）。UUID は内部実装に閉じる。
- child_ids は tasks.parent_id の逆引き SELECT で導出する（列は追加しない）。
- ボードは MVP のシングルボード前提（boards の先頭 1 件を既定ボードとする）。
"""

from typing import Any

import asyncpg

from app.domain.dto import BoardResponse, LaneDto, TaskCreate
from app.domain.models import Rule, Task, TaskPolicy


class InvalidParentError(Exception):
    """parent_id が不正（存在しない human_id、または自分自身）。"""


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def policy_from_row(row: asyncpg.Record) -> TaskPolicy:
    """tasks.policy（jsonb）を TaskPolicy へ変換する（#21）。

    省略キーは既定値（Web検索可・コスト上限なし）。#22 指揮者もここから権限を読む。
    """
    return TaskPolicy.model_validate_json(row["policy"])


def _row_to_task(
    row: asyncpg.Record,
    *,
    parent_human_id: str | None = None,
    child_human_ids: list[str] | None = None,
    comment_count: int = 0,
) -> Task:
    return Task(
        id=row["human_id"],
        workspace_id=str(row["workspace_id"]),
        board_id=str(row["board_id"]),
        lane_key=row["lane_key"],
        order_in_lane=row["order_in_lane"],
        title=row["title"],
        status=row["status"],
        owner_user_id=str(row["owner_user_id"]) if row["owner_user_id"] is not None else "",
        labels=list(row["labels"]),
        progress=row["progress"],
        parent_id=parent_human_id,
        child_ids=child_human_ids if child_human_ids else None,
        autonomy=row["autonomy"],
        policy=policy_from_row(row),
        comment_count=comment_count,
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
    )


async def get_default_board(conn: asyncpg.Connection) -> asyncpg.Record:
    """MVP のシングルボード（先頭 1 件）を返す。"""
    return await conn.fetchrow("select id, workspace_id from boards order by id limit 1")


# ---- ボード取得（§2.3 正規化形） -------------------------------------------------


async def fetch_board(conn: asyncpg.Connection) -> BoardResponse:
    """ボード全体を正規化形（cards 辞書 / lanes cardIds / rules）で返す。"""
    board = await get_default_board(conn)
    lane_rows = await conn.fetch(
        "select key, name from lanes where board_id = $1 order by position", board["id"]
    )
    task_rows = await conn.fetch(
        "select * from tasks where board_id = $1 order by lane_key, order_in_lane, created_at",
        board["id"],
    )

    human_by_uuid = {row["id"]: row["human_id"] for row in task_rows}
    children_by_parent: dict[Any, list[str]] = {}
    for row in sorted(task_rows, key=lambda r: (r["created_at"], r["human_id"])):
        if row["parent_id"] is not None:
            children_by_parent.setdefault(row["parent_id"], []).append(row["human_id"])

    # コメント件数の集計（§3.2 カード右上 / #7）。タスクUUID -> 件数。
    count_rows = await conn.fetch(
        """
        select c.task_id, count(*)::int as comment_count
        from comments c
        join tasks t on t.id = c.task_id
        where t.board_id = $1
        group by c.task_id
        """,
        board["id"],
    )
    comment_counts = {row["task_id"]: row["comment_count"] for row in count_rows}

    cards: dict[str, Task] = {}
    lane_card_ids: dict[str, list[str]] = {row["key"]: [] for row in lane_rows}
    for row in task_rows:
        task = _row_to_task(
            row,
            parent_human_id=human_by_uuid.get(row["parent_id"]),
            child_human_ids=children_by_parent.get(row["id"]),
            comment_count=comment_counts.get(row["id"], 0),
        )
        cards[task.id] = task
        lane_card_ids.setdefault(row["lane_key"], []).append(task.id)

    lanes = [
        LaneDto(key=row["key"], name=row["name"], card_ids=lane_card_ids[row["key"]])
        for row in lane_rows
    ]
    rules = await _fetch_rules(conn, board["workspace_id"])
    return BoardResponse(lanes=lanes, cards=cards, rules=rules)


async def _fetch_rules(conn: asyncpg.Connection, workspace_id: Any) -> list[Rule]:
    rows = await conn.fetch(
        """
        select r.*, t.human_id as source_task_human_id
        from rules r
        left join tasks t on t.id = r.source_task_id
        where r.workspace_id = $1
        order by r.human_id
        """,
        workspace_id,
    )
    return [
        Rule(
            id=row["human_id"],
            workspace_id=str(row["workspace_id"]),
            scope=row["scope"],
            owner_user_id=(
                str(row["owner_user_id"]) if row["owner_user_id"] is not None else None
            ),
            text=row["text"],
            tags=list(row["tags"]),
            source=row["source"],
            source_task_id=row["source_task_human_id"],
            confidence=row["confidence"],
            applied=row["applied"],
            last_applied_at=_iso(row["last_applied_at"]),
            created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
        )
        for row in rows
    ]


# ---- 単一タスク -----------------------------------------------------------------


async def get_task_row(
    conn: asyncpg.Connection, human_id: str, *, for_update: bool = False
) -> asyncpg.Record | None:
    """human_id でタスク行を引く（更新系はトランザクション内で for_update=True 推奨）。"""
    query = "select * from tasks where human_id = $1"
    if for_update:
        query += " for update"
    return await conn.fetchrow(query, human_id)


async def task_from_row(conn: asyncpg.Connection, row: asyncpg.Record) -> Task:
    """DB 行を Task DTO へ変換する（parent/child の human_id を逆引きで補完）。"""
    parent_human_id = None
    if row["parent_id"] is not None:
        parent_human_id = await conn.fetchval(
            "select human_id from tasks where id = $1", row["parent_id"]
        )
    child_rows = await conn.fetch(
        "select human_id from tasks where parent_id = $1 order by created_at, human_id",
        row["id"],
    )
    comment_count = await conn.fetchval(
        "select count(*)::int from comments where task_id = $1", row["id"]
    )
    return _row_to_task(
        row,
        parent_human_id=parent_human_id,
        child_human_ids=[r["human_id"] for r in child_rows],
        comment_count=comment_count,
    )


# ---- 作成 -----------------------------------------------------------------------


async def next_human_id(conn: asyncpg.Connection, workspace_id: Any) -> str:
    """workspace 内の T-{seq} 連番（既存最大値 +1）。トランザクション内で呼ぶこと。"""
    max_seq = await conn.fetchval(
        "select coalesce(max(substring(human_id from 3)::int), 0) from tasks "
        "where workspace_id = $1",
        workspace_id,
    )
    return f"T-{max_seq + 1}"


async def create_task(conn: asyncpg.Connection, data: TaskCreate) -> Task:
    """タスクを作成し、指定レーンの末尾に置く（§5.3 addCard）。"""
    board = await get_default_board(conn)
    owner_user_id = await conn.fetchval(
        "select id from users where workspace_id = $1 order by created_at limit 1",
        board["workspace_id"],
    )
    parent_uuid = None
    if data.parent_id is not None:
        parent_uuid = await conn.fetchval(
            "select id from tasks where human_id = $1", data.parent_id
        )
        if parent_uuid is None:
            raise InvalidParentError(f"parent task not found: {data.parent_id}")

    human_id = await next_human_id(conn, board["workspace_id"])
    order_in_lane = await conn.fetchval(
        "select coalesce(max(order_in_lane) + 1, 0) from tasks "
        "where board_id = $1 and lane_key = $2",
        board["id"],
        data.lane_key.value,
    )
    row = await conn.fetchrow(
        """
        insert into tasks
          (human_id, workspace_id, board_id, lane_key, order_in_lane,
           title, status, owner_user_id, labels, parent_id)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        returning *
        """,
        human_id,
        board["workspace_id"],
        board["id"],
        data.lane_key.value,
        order_in_lane,
        data.title,
        data.status.value,
        owner_user_id,
        data.labels,
        parent_uuid,
    )
    return await task_from_row(conn, row)


# ---- 部分更新（PATCH） ------------------------------------------------------------


async def apply_patch(
    conn: asyncpg.Connection, row: asyncpg.Record, fields: dict[str, Any]
) -> Task:
    """検証済みフィールドを適用する（遷移/不変条件の検証は呼び出し側 = ルーター）。

    lane_key / order_in_lane の変更は §5.3 move 準拠で両レーンの order を振り直す。
    トランザクション内で呼ぶこと。
    """
    updates: dict[str, Any] = {}

    if fields.get("title") is not None:
        updates["title"] = fields["title"]
    if fields.get("labels") is not None:
        updates["labels"] = fields["labels"]
    if fields.get("status") is not None:
        updates["status"] = str(fields["status"])
    if "progress" in fields:
        updates["progress"] = fields["progress"]  # None は明示クリア（§5.6 不変条件）
    if "parent_id" in fields:
        updates["parent_id"] = await _resolve_parent(conn, row, fields["parent_id"])
    if fields.get("autonomy") is not None:
        updates["autonomy"] = str(fields["autonomy"])  # #21 L0-L3 ダイヤル
    if fields.get("policy") is not None:
        # #21 行動範囲ポリシーは全体置換（jsonb には camelCase で保存 = API 表現と同形）
        updates["policy"] = fields["policy"].model_dump_json(by_alias=True)

    lane_specified = fields.get("lane_key") is not None
    order_specified = fields.get("order_in_lane") is not None
    if lane_specified or order_specified:
        target_lane = str(fields["lane_key"]) if lane_specified else row["lane_key"]
        updates["lane_key"] = target_lane
        await _move_task(
            conn,
            board_id=row["board_id"],
            task_id=row["id"],
            from_lane=row["lane_key"],
            to_lane=target_lane,
            new_order=fields["order_in_lane"] if order_specified else None,
        )

    set_parts = ["updated_at = now()"]
    values: list[Any] = []
    for column, value in updates.items():
        values.append(value)
        set_parts.append(f"{column} = ${len(values)}")
    values.append(row["id"])
    new_row = await conn.fetchrow(
        f"update tasks set {', '.join(set_parts)} where id = ${len(values)} returning *",
        *values,
    )
    if new_row["status"] != row["status"]:
        await _on_status_transition(conn, row, new_row)
    return await task_from_row(conn, new_row)


async def _on_status_transition(
    conn: asyncpg.Connection, old_row: asyncpg.Record, new_row: asyncpg.Record
) -> None:
    """ステータス遷移フック（apply_patch = 全遷移が通る単一点。遷移時のみ呼ばれる）。

    - #26: レビュー承認/差し戻し/再オープンを適用済みルールへの暗黙評価として
      rule_signals へ自動記録する（§6.6 確度ライフサイクルの材料）
    - #28: 信頼グラデュエーションの task_transitions 記録もここに同居させる予定
    トランザクション内で呼ばれる（apply_patch と同一コミット）。
    """
    from app.repo import rules as rules_repo  # 循環 import 回避（rules は tasks を参照しない）

    await rules_repo.record_transition_signals(
        conn, new_row, old_status=old_row["status"], new_status=new_row["status"]
    )


async def _resolve_parent(
    conn: asyncpg.Connection, row: asyncpg.Record, parent_human_id: str | None
) -> Any:
    """parent の human_id を UUID に解決する（None はクリア）。"""
    if parent_human_id is None:
        return None
    if parent_human_id == row["human_id"]:
        raise InvalidParentError("task cannot be its own parent")
    parent_uuid = await conn.fetchval(
        "select id from tasks where human_id = $1", parent_human_id
    )
    if parent_uuid is None:
        raise InvalidParentError(f"parent task not found: {parent_human_id}")
    return parent_uuid


async def _move_task(
    conn: asyncpg.Connection,
    *,
    board_id: Any,
    task_id: Any,
    from_lane: str,
    to_lane: str,
    new_order: int | None,
) -> None:
    """§5.3 move: 元レーンから抜き、対象レーンの指定位置（未指定は末尾）へ挿入。

    影響を受けた両レーンの order_in_lane を 0..n-1 に振り直す。
    """

    async def _lane_ids(lane_key: str) -> list[Any]:
        rows = await conn.fetch(
            "select id from tasks where board_id = $1 and lane_key = $2 "
            "order by order_in_lane, created_at",
            board_id,
            lane_key,
        )
        return [r["id"] for r in rows]

    from_ids = await _lane_ids(from_lane)
    if task_id in from_ids:
        from_ids.remove(task_id)

    same_lane = to_lane == from_lane
    to_ids = from_ids if same_lane else await _lane_ids(to_lane)
    position = len(to_ids) if new_order is None else max(0, min(new_order, len(to_ids)))
    to_ids.insert(position, task_id)

    assignments: dict[Any, int] = {}
    if not same_lane:
        assignments.update({tid: idx for idx, tid in enumerate(from_ids)})
    assignments.update({tid: idx for idx, tid in enumerate(to_ids)})
    await conn.executemany(
        "update tasks set order_in_lane = $2 where id = $1",
        list(assignments.items()),
    )
