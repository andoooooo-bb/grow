"""Issue #5: ボード取得 / タスク CRUD / 遷移検証 / コメント API のテスト。

専用DB grow_test を使う（tests/conftest.py の api_client フィクスチャ参照）。
"""

# ---- (a) GET /board: シードの正規化形 --------------------------------------------


async def test_get_board_matches_seed(api_client):
    res = await api_client.get("/api/board")
    assert res.status_code == 200
    body = res.json()

    assert [lane["key"] for lane in body["lanes"]] == [
        "backlog",
        "todo",
        "progress",
        "review",
        "done",
    ]
    assert [lane["name"] for lane in body["lanes"]] == [
        "バックログ",
        "ToDo",
        "進行中",
        "レビュー",
        "完了",
    ]
    card_ids = {lane["key"]: lane["cardIds"] for lane in body["lanes"]}
    assert card_ids["backlog"] == ["T-130", "T-121"]
    assert card_ids["todo"] == ["T-104", "T-109", "T-112"]
    assert card_ids["progress"] == ["T-098", "T-101"]
    assert card_ids["review"] == ["T-091", "T-089"]
    assert card_ids["done"] == ["T-080", "T-077"]

    assert len(body["cards"]) == 11
    t098 = body["cards"]["T-098"]
    assert t098["laneKey"] == "progress"
    assert t098["status"] == "ai_work"
    assert t098["progress"] == 60
    assert t098["labels"] == ["仕事", "調査"]
    assert t098["orderInLane"] == 0
    assert t098["commentCount"] == 0  # シードにコメントは無い（#7）

    assert [rule["id"] for rule in body["rules"]] == ["K-01", "K-02", "K-03", "K-04", "K-05"]
    k01 = body["rules"][0]
    assert k01["scope"] == "personal"
    assert k01["confidence"] == "high"
    assert k01["applied"] == 6
    assert k01["sourceTaskId"] == "T-098"  # UUID ではなく human_id で返る


# ---- (b) POST /tasks: T-131 が採番されレーン末尾に付く ---------------------------


async def test_post_task_creates_t131_at_lane_end(api_client):
    res = await api_client.post("/api/tasks", json={"laneKey": "todo", "title": "新しいタスク"})
    assert res.status_code == 201
    task = res.json()
    assert task["id"] == "T-131"  # シード最大 T-130 の次
    assert task["status"] == "breakdown"  # 省略時デフォルト（§5.3 addCard）
    assert task["laneKey"] == "todo"
    assert task["orderInLane"] == 3  # todo 3件の末尾

    board = (await api_client.get("/api/board")).json()
    todo = next(lane for lane in board["lanes"] if lane["key"] == "todo")
    assert todo["cardIds"] == ["T-104", "T-109", "T-112", "T-131"]


async def test_post_task_with_unknown_parent_is_422(api_client):
    res = await api_client.post(
        "/api/tasks", json={"laneKey": "todo", "title": "子タスク", "parentId": "T-999"}
    )
    assert res.status_code == 422


async def test_post_task_with_parent_derives_child_ids(api_client):
    res = await api_client.post(
        "/api/tasks", json={"laneKey": "todo", "title": "子タスク", "parentId": "T-104"}
    )
    assert res.status_code == 201
    child = res.json()
    assert child["parentId"] == "T-104"

    board = (await api_client.get("/api/board")).json()
    assert board["cards"]["T-104"]["childIds"] == [child["id"]]  # parent_id 逆引きで導出


# ---- (c) PATCH /tasks: ステータス遷移の検証 -------------------------------------


async def test_invalid_transition_is_rejected_with_409(api_client):
    # breakdown → done は §5.6 で許可されていない
    res = await api_client.patch("/api/tasks/T-130", json={"status": "done"})
    assert res.status_code == 409

    board = (await api_client.get("/api/board")).json()
    assert board["cards"]["T-130"]["status"] == "breakdown"  # 変更されていない


async def test_valid_transition_succeeds(api_client):
    # you_review → done は承認として許可
    res = await api_client.patch("/api/tasks/T-091", json={"status": "done"})
    assert res.status_code == 200
    assert res.json()["status"] == "done"


async def test_transition_out_of_ai_work_nullifies_progress(api_client):
    # T-098 は ai_work / progress=60。ai_work → you_review で progress は自動 null 化（§5.6）
    res = await api_client.patch("/api/tasks/T-098", json={"status": "you_review"})
    assert res.status_code == 200
    task = res.json()
    assert task["status"] == "you_review"
    assert task["progress"] is None


async def test_progress_on_non_ai_work_is_422(api_client):
    # T-104 は spec。progress の手動指定は不変条件違反
    res = await api_client.patch("/api/tasks/T-104", json={"progress": 50})
    assert res.status_code == 422


async def test_progress_update_on_ai_work_succeeds(api_client):
    res = await api_client.patch("/api/tasks/T-098", json={"progress": 80})
    assert res.status_code == 200
    assert res.json()["progress"] == 80


async def test_patch_unknown_task_is_404(api_client):
    res = await api_client.patch("/api/tasks/T-999", json={"title": "x"})
    assert res.status_code == 404


# ---- (d) レーン移動と order_in_lane 再計算（§5.3 move） --------------------------


async def test_move_to_other_lane_appends_and_recomputes_orders(api_client):
    # laneKey のみ指定 → 対象レーン末尾へ。元レーンは詰めて再計算
    res = await api_client.patch("/api/tasks/T-098", json={"laneKey": "review"})
    assert res.status_code == 200
    moved = res.json()
    assert moved["laneKey"] == "review"
    assert moved["orderInLane"] == 2  # review 2件の末尾

    board = (await api_client.get("/api/board")).json()
    lanes = {lane["key"]: lane["cardIds"] for lane in board["lanes"]}
    assert lanes["review"] == ["T-091", "T-089", "T-098"]
    assert lanes["progress"] == ["T-101"]
    assert board["cards"]["T-101"]["orderInLane"] == 0  # 詰められている


async def test_move_with_explicit_order_inserts_at_position(api_client):
    res = await api_client.patch("/api/tasks/T-077", json={"laneKey": "todo", "orderInLane": 1})
    assert res.status_code == 200
    assert res.json()["orderInLane"] == 1

    board = (await api_client.get("/api/board")).json()
    lanes = {lane["key"]: lane["cardIds"] for lane in board["lanes"]}
    assert lanes["todo"] == ["T-104", "T-077", "T-109", "T-112"]
    assert lanes["done"] == ["T-080"]
    orders = [board["cards"][card_id]["orderInLane"] for card_id in lanes["todo"]]
    assert orders == [0, 1, 2, 3]  # 0..n-1 で振り直し


async def test_reorder_within_same_lane(api_client):
    # todo 内で T-112 (order 2) を先頭へ
    res = await api_client.patch("/api/tasks/T-112", json={"orderInLane": 0})
    assert res.status_code == 200
    assert res.json()["orderInLane"] == 0

    board = (await api_client.get("/api/board")).json()
    todo = next(lane for lane in board["lanes"] if lane["key"] == "todo")
    assert todo["cardIds"] == ["T-112", "T-104", "T-109"]


# ---- (e) コメント作成 → 取得 ------------------------------------------------------


async def test_comment_create_then_list(api_client):
    res = await api_client.post(
        "/api/tasks/T-098/comments", json={"author": "human", "text": "進捗はどうですか？"}
    )
    assert res.status_code == 201
    created = res.json()
    assert created["taskId"] == "T-098"  # API 境界は human_id
    assert created["author"] == "human"
    assert created["text"] == "進捗はどうですか？"
    assert created["id"]  # コメント id は UUID 文字列

    res = await api_client.get("/api/tasks/T-098/comments")
    assert res.status_code == 200
    comments = res.json()
    assert [c["text"] for c in comments] == ["進捗はどうですか？"]
    assert comments[0]["id"] == created["id"]


async def test_board_aggregates_comment_count(api_client):
    """#7: fetch_board が各タスクの comments 件数を commentCount へ集計する。"""
    for text in ["1件目", "2件目"]:
        res = await api_client.post(
            "/api/tasks/T-098/comments", json={"author": "human", "text": text}
        )
        assert res.status_code == 201
    res = await api_client.post(
        "/api/tasks/T-104/comments", json={"author": "ai", "text": "着手します"}
    )
    assert res.status_code == 201

    board = (await api_client.get("/api/board")).json()
    assert board["cards"]["T-098"]["commentCount"] == 2
    assert board["cards"]["T-104"]["commentCount"] == 1
    assert board["cards"]["T-130"]["commentCount"] == 0  # コメントの無いタスクは 0


async def test_comment_on_unknown_task_is_404(api_client):
    res = await api_client.post(
        "/api/tasks/T-999/comments", json={"author": "human", "text": "x"}
    )
    assert res.status_code == 404
    res = await api_client.get("/api/tasks/T-999/comments")
    assert res.status_code == 404
