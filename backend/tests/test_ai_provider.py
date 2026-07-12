"""AiProvider 抽象 / MockProvider / ファクトリ切替のテスト（Issue #4, §7.0/§7.7）。"""

import pytest

from app.ai import get_provider
from app.ai.gemini_provider import GeminiProvider
from app.ai.mock_provider import (
    CHAT_FOLLOWUP,
    GREETING_GENERIC,
    GREETING_T130,
    STREAM_CHUNK_CHARS,
    MockProvider,
)
from app.ai.provider import TokenUsage
from app.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """get_settings は lru_cache 済みのため、環境変数切替の前後でキャッシュを破棄する。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


def _task(human_id: str = "T-999", title: str = "汎用タスク", labels: list[str] | None = None):
    return {
        "id": f"uuid-{human_id}",
        "humanId": human_id,
        "title": title,
        "labels": labels if labels is not None else ["仕事"],
    }


def _assert_positive_usage(usage: TokenUsage) -> None:
    assert isinstance(usage.input_tokens, int)
    assert isinstance(usage.output_tokens, int)
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0


# --- ファクトリ切替（AI_PROVIDER=mock|gemini） ---


def test_factory_returns_mock_by_default():
    assert isinstance(get_provider(), MockProvider)


def test_factory_returns_mock_when_env_is_mock(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_PROVIDER", "mock")
    get_settings.cache_clear()
    assert isinstance(get_provider(), MockProvider)


def test_factory_returns_gemini_when_env_is_gemini(monkeypatch: pytest.MonkeyPatch):
    """Gemini 実装（#15）の振る舞い自体は tests/test_gemini_provider.py で検証する。"""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    get_settings.cache_clear()
    assert isinstance(get_provider(), GeminiProvider)


# --- chat_reply（§7.4a / 05 §5.3） ---


async def test_chat_reply_initial_t130_asks_three_questions(provider: MockProvider):
    task = _task("T-130", "ポートフォリオサイトのリニューアル", ["個人", "デザイン"])
    result = await provider.chat_reply(task, chat=[], rules=[])
    assert result.text == GREETING_T130
    assert "① 公開したい時期は？" in result.text
    _assert_positive_usage(result.usage)


async def test_chat_reply_initial_generic_greeting(provider: MockProvider):
    result = await provider.chat_reply(_task(), chat=[], rules=[])
    assert result.text == GREETING_GENERIC
    _assert_positive_usage(result.usage)


async def test_chat_reply_followup_after_user_message(provider: MockProvider):
    chat = [{"who": "user", "text": "3月末公開、資産は流用、実績はアプリAを見せたい"}]
    result = await provider.chat_reply(_task("T-130"), chat=chat, rules=[])
    assert result.text == CHAT_FOLLOWUP
    _assert_positive_usage(result.usage)


# --- propose_subtasks（§7.4b / 02 §2.5） ---


async def test_propose_subtasks_t130_returns_five_specific_items(provider: MockProvider):
    task = _task("T-130", "ポートフォリオサイトのリニューアル", ["個人", "デザイン"])
    result = await provider.propose_subtasks(task, chat=[], rules=[])
    expected = [
        ("情報設計・サイトマップ作成", "ai"),
        ("ワイヤーフレーム作成", "ai"),
        ("掲載する実績コンテンツの選定", "human"),
        ("デザイン方向性の決定", "human"),
        ("コーディング・実装", "ai"),
    ]
    assert [(s.title, s.owner) for s in result.subtasks] == expected
    _assert_positive_usage(result.usage)


async def test_propose_subtasks_generic_returns_four_items(provider: MockProvider):
    result = await provider.propose_subtasks(_task(), chat=[], rules=[])
    expected = [
        ("要件・前提の整理", "ai"),
        ("たたき台の作成", "ai"),
        ("内容の確認・決定", "human"),
        ("仕上げ", "ai"),
    ]
    assert [(s.title, s.owner) for s in result.subtasks] == expected
    _assert_positive_usage(result.usage)


# --- propose_rules（§7.5 / 02 §2.5） ---


async def test_propose_rules_t098_returns_two_rules(provider: MockProvider):
    task = _task("T-098", "競合調査レポートの下書き", ["仕事", "調査"])
    result = await provider.propose_rules(task, comments=[], chat=[])
    assert len(result.rules) == 2
    assert result.rules[0].text == (
        "競合ごとにセクションを分け、末尾に横断比較表を置くと差し戻しが減る"
    )
    assert result.rules[0].scope == "personal"
    assert result.rules[0].tags == ["調査"]
    assert result.rules[0].confidence == "med"
    assert result.rules[1].text == "料金は必ず税抜/税込を明記する"
    assert result.rules[1].tags == ["調査", "経理"]
    _assert_positive_usage(result.usage)


async def test_propose_rules_t091_returns_one_rule(provider: MockProvider):
    task = _task("T-091", "確定申告サマリーの最終確認", ["経理"])
    result = await provider.propose_rules(task, comments=[], chat=[])
    assert len(result.rules) == 1
    assert result.rules[0].text == "確定申告サマリーは控除候補を別セクションで先に提示する"
    assert result.rules[0].scope == "personal"
    assert result.rules[0].tags == ["経理"]
    assert result.rules[0].confidence == "med"
    _assert_positive_usage(result.usage)


async def test_propose_rules_generic_uses_task_labels(provider: MockProvider):
    task = _task("T-077", "名刺データをCSVに整理", ["仕事"])
    result = await provider.propose_rules(task, comments=[], chat=[])
    assert len(result.rules) == 1
    rule = result.rules[0]
    assert rule.text == "このタスクで繰り返した指示を、今後の既定の進め方にする"
    assert rule.scope == "personal"
    assert rule.tags == ["仕事"]  # カードの labels を引き継ぐ
    assert rule.confidence == "low"
    _assert_positive_usage(result.usage)


# --- execute（§7.3: 調査 → Markdown レポート） ---


async def test_execute_returns_markdown_report_structure(provider: MockProvider):
    task = _task("T-104", "競合SaaS 5社の料金プランを調査", ["仕事", "調査"])
    result = await provider.execute(task, rules=[], comments=[])
    md = result.content_md
    assert "競合SaaS 5社の料金プランを調査" in md  # タスク title を織り込む
    assert "## サマリー" in md
    assert "## 比較表" in md
    assert "| 評価軸 |" in md
    assert "## 出典URL" in md
    assert "## 適用ルール" not in md  # ルール未指定なら適用ルール節は出さない
    _assert_positive_usage(result.usage)


async def test_execute_weaves_rule_texts_into_report(provider: MockProvider):
    task = _task("T-104", "競合SaaS 5社の料金プランを調査", ["仕事", "調査"])
    rules = [
        {"id": "K-01", "text": "レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く"},
        {"id": "K-03", "text": "競合調査は料金を表形式にし、各項目に出典URLを付ける"},
    ]
    result = await provider.execute(task, rules=rules, comments=[])
    assert "## 適用ルール" in result.content_md
    assert "レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く" in result.content_md
    assert "競合調査は料金を表形式にし、各項目に出典URLを付ける" in result.content_md
    _assert_positive_usage(result.usage)


# --- execute のポリシー分岐（#21: allowWebSearch / plan_only） ---


async def test_execute_without_web_search_notes_verification_items(provider: MockProvider):
    """allowWebSearch=False: 出典URLの代わりに要確認事項を明記する（決定的に文言変化）。"""
    task = _task("T-104", "競合SaaS 5社の料金プランを調査", ["仕事", "調査"])
    result = await provider.execute(
        task, rules=[], comments=[], policy={"allowWebSearch": False}
    )
    md = result.content_md
    assert "## 要確認事項" in md
    assert "Web検索は使用不可のため、既知情報のみで作成した。" in md
    assert "ポリシーによりWeb検索は使用せず、既知情報のみで作成した。" in md
    assert "## 出典URL" not in md


async def test_execute_default_policy_keeps_sources(provider: MockProvider):
    """policy 省略・allowWebSearch 省略時は既定（検索可）で出典URLを出す。"""
    task = _task("T-104", "競合SaaS 5社の料金プランを調査", ["仕事", "調査"])
    default = await provider.execute(task, rules=[], comments=[])
    omitted = await provider.execute(task, rules=[], comments=[], policy={"costCapUsd": 3.0})
    for result in (default, omitted):
        assert "## 出典URL" in result.content_md
        assert "## 要確認事項" not in result.content_md


async def test_execute_plan_only_returns_plan_not_report(provider: MockProvider):
    """plan_only=True（L0）: 実行プランだけを返し、成果物本文（比較表）は作らない。"""
    task = _task("T-104", "競合SaaS 5社の料金プランを調査", ["仕事", "調査"])
    result = await provider.execute(task, rules=[], comments=[], plan_only=True)
    md = result.content_md
    assert "実行プラン" in md
    assert "## 進め方（案）" in md
    assert "## 比較表" not in md
    assert "調査レポート" not in md
    _assert_positive_usage(result.usage)


async def test_execute_plan_only_weaves_rule_texts(provider: MockProvider):
    rules = [{"id": "K-01", "text": "レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く"}]
    result = await provider.execute(_task(), rules=rules, comments=[], plan_only=True)
    assert "## 適用ルール" in result.content_md
    assert "レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く" in result.content_md


# --- execute のライブ実況（#24: on_delta ストリーム模擬） ---


async def test_execute_streams_deterministic_chunks_via_on_delta(provider: MockProvider):
    """on_delta 指定時: 固定幅の増分が順に届き、連結が content_md と一致する（決定的）。"""
    task = _task("T-104", "競合SaaS 5社の料金プランを調査", ["仕事", "調査"])
    deltas: list[str] = []

    async def on_delta(delta: str) -> None:
        deltas.append(delta)

    result = await provider.execute(task, rules=[], comments=[], on_delta=on_delta)
    assert len(deltas) >= 2  # 数チャンクに分割されている
    assert "".join(deltas) == result.content_md
    # 分割点は固定幅（最終チャンク以外は STREAM_CHUNK_CHARS ちょうど）
    assert all(len(d) == STREAM_CHUNK_CHARS for d in deltas[:-1])
    assert 0 < len(deltas[-1]) <= STREAM_CHUNK_CHARS

    # 同じ入力なら増分列も同一（決定的）
    deltas2: list[str] = []

    async def on_delta2(delta: str) -> None:
        deltas2.append(delta)

    again = await provider.execute(task, rules=[], comments=[], on_delta=on_delta2)
    assert deltas2 == deltas
    assert again.content_md == result.content_md


async def test_execute_without_on_delta_does_not_stream(provider: MockProvider):
    """on_delta 省略時は従来どおり（結果のみ返す）。"""
    result = await provider.execute(_task(), rules=[], comments=[])
    assert "## 比較表" in result.content_md


async def test_execute_plan_only_does_not_stream(provider: MockProvider):
    """plan_only=True（L0）は実況しない（on_delta を渡しても呼ばれない）。"""
    deltas: list[str] = []

    async def on_delta(delta: str) -> None:
        deltas.append(delta)

    result = await provider.execute(
        _task(), rules=[], comments=[], plan_only=True, on_delta=on_delta
    )
    assert deltas == []
    assert "実行プラン" in result.content_md


# --- 決定性（同じ入力 → 同じ出力）と usage ---


async def test_all_methods_are_deterministic(provider: MockProvider):
    task = _task("T-130", "ポートフォリオサイトのリニューアル", ["個人", "デザイン"])
    chat = [{"who": "user", "text": "3月末公開を目指したい"}]
    rules = [{"id": "K-02", "text": "絵文字は使わない。文体は簡潔・断定調に統一する"}]
    comments = [{"who": "ai", "text": "調査を開始します。"}]

    assert await provider.execute(task, rules, comments) == await provider.execute(
        task, rules, comments
    )
    assert await provider.propose_subtasks(task, chat, rules) == await provider.propose_subtasks(
        task, chat, rules
    )
    assert await provider.propose_rules(task, comments, chat) == await provider.propose_rules(
        task, comments, chat
    )
    assert await provider.chat_reply(task, chat, rules) == await provider.chat_reply(
        task, chat, rules
    )


async def test_usage_grows_with_input_size(provider: MockProvider):
    """usage は入力サイズから決定的に算出される（入力が増えれば input_tokens も増える）。"""
    task = _task()
    small = await provider.chat_reply(task, chat=[], rules=[])
    large = await provider.chat_reply(
        task, chat=[], rules=[{"id": "K-02", "text": "絵文字は使わない" * 20}]
    )
    assert large.usage.input_tokens > small.usage.input_tokens


# --- decide_next_action（#22 指揮者。決定的な状態機械分岐） ---


def _conductor_task(
    status: str,
    *,
    has_chat: bool = False,
    has_artifact: bool = False,
    has_review: bool = False,
) -> dict:
    """orchestrate ジョブが集約する現況キー付きのタスク dict（#22/#23）。"""
    return {
        **_task(),
        "status": status,
        "autonomy": "L1",
        "hasChat": has_chat,
        "hasArtifact": has_artifact,
        "hasReview": has_review,
        "childStatuses": [],
    }


@pytest.mark.parametrize(
    ("status", "has_chat", "has_artifact", "has_review", "expected"),
    [
        ("breakdown", False, False, False, "hearing"),  # 前提が未確認 → まず質問
        ("spec", False, False, False, "hearing"),
        ("breakdown", True, False, False, "breakdown"),  # 壁打ち済み → 分解を提案
        ("spec", True, False, False, "breakdown"),
        ("queued", False, False, False, "execute"),  # 実行可能 → 実行AIへ
        ("you_todo", False, True, False, "execute"),  # 差し戻し後の再実行も execute
        # 成果物ありでもセルフレビュー未実施なら、人の前にレビューAIへ（#23）
        ("you_review", False, True, False, "review"),
        ("reviewing", False, True, False, "review"),
        # レビュー済みの成果物レビューは人の判断
        ("you_review", False, True, True, "handoff_human"),
        ("reviewing", False, True, True, "handoff_human"),
        ("ai_work", False, False, False, "handoff_human"),  # 判断できない状態は人へ（安全側）
        ("done", False, True, True, "done"),  # 完了済み → 終了
    ],
)
async def test_decide_next_action_state_machine(
    provider: MockProvider,
    status: str,
    has_chat: bool,
    has_artifact: bool,
    has_review: bool,
    expected: str,
):
    result = await provider.decide_next_action(
        _conductor_task(
            status,
            has_chat=has_chat,
            has_artifact=has_artifact,
            has_review=has_review,
        ),
        [],
        [],
    )
    assert result.action == expected
    assert result.reason  # 判断理由は必ず付く（（理由: …）コメントの材料）
    _assert_positive_usage(result.usage)


async def test_decide_next_action_is_deterministic(provider: MockProvider):
    task = _conductor_task("queued")
    history = [{"who": "human", "text": "出典URLもお願いします"}]
    assert await provider.decide_next_action(
        task, history, []
    ) == await provider.decide_next_action(task, history, [])


# --- review_artifact（#23 セルフレビュー。決定的な approve / revise 分岐） ---


async def test_review_artifact_revises_first_draft_with_rule_citation(
    provider: MockProvider,
):
    """対応セクションのない初回成果物は revise。指摘は「出典」ルールを優先引用する。"""
    rules = [
        {"id": "K-02", "text": "絵文字は使わない。文体は簡潔・断定調に統一する"},
        {"id": "K-03", "text": "競合調査は料金を表形式にし、各項目に出典URLを付ける"},
    ]
    result = await provider.review_artifact(_task(), "# 調査レポート\n本文", rules)
    assert result.verdict == "revise"
    assert len(result.findings) == 1
    assert "競合調査は料金を表形式にし、各項目に出典URLを付ける" in result.findings[0]
    _assert_positive_usage(result.usage)


async def test_review_artifact_revises_without_rules_using_generic_finding(
    provider: MockProvider,
):
    result = await provider.review_artifact(_task(), "# 調査レポート\n本文", [])
    assert result.verdict == "revise"
    assert result.findings == ["比較表に出典URL列がありません。出典を明記して追記してください"]


@pytest.mark.parametrize("section", ["## レビュー対応", "## 差し戻し対応"])
async def test_review_artifact_approves_fixed_versions(
    provider: MockProvider, section: str
):
    """指摘・差し戻しへの対応セクションがある修正版は approve（findings なし）。"""
    result = await provider.review_artifact(
        _task(), f"# 調査レポート\n{section}\n対応済み", []
    )
    assert result.verdict == "approve"
    assert result.findings == []
    _assert_positive_usage(result.usage)


async def test_review_artifact_is_deterministic(provider: MockProvider):
    task = _task()
    rules = [{"id": "K-03", "text": "競合調査は料金を表形式にし、各項目に出典URLを付ける"}]
    assert await provider.review_artifact(
        task, "# v1", rules
    ) == await provider.review_artifact(task, "# v1", rules)


# --- execute への差し戻し理由・レビュー指摘の反映（#23） ---


async def test_execute_reflects_reject_reason_in_report(provider: MockProvider):
    """履歴に【差し戻し理由】があれば、成果物に理由の全文を含む対応セクションが載る。"""
    reason = "比較表は不要です。ルールとは逆に、箇条書きでまとめてください"
    comments = [
        {"who": "ai", "text": "完了しました。レビューをお願いします。"},
        {"who": "human", "text": f"【差し戻し理由】{reason}"},
    ]
    result = await provider.execute(_task(), rules=[], comments=comments)
    assert "## 差し戻し対応" in result.content_md
    assert reason in result.content_md  # 理由の反映が検証可能（#23 DoD）


async def test_execute_reflects_review_findings_in_report(provider: MockProvider):
    """履歴に【レビュー指摘】があれば、修正版に「## レビュー対応」セクションが載る。"""
    comments = [
        {"who": "ai", "text": "【レビュー指摘】\n- 比較表に出典URL列がありません"},
    ]
    result = await provider.execute(_task(), rules=[], comments=comments)
    assert "## レビュー対応" in result.content_md


async def test_execute_without_revision_history_has_no_fix_sections(
    provider: MockProvider,
):
    result = await provider.execute(_task(), rules=[], comments=[])
    assert "## レビュー対応" not in result.content_md
    assert "## 差し戻し対応" not in result.content_md


# --- check_rule_conflicts（#23 矛盾検出。決定的分岐） ---


@pytest.mark.parametrize(
    "reason",
    [
        "比較表は不要です。ルールとは合わないのでやめてください",
        "逆に、結論は最後にまとめてください",
    ],
)
async def test_check_rule_conflicts_returns_first_rule_on_conflict_keywords(
    provider: MockProvider, reason: str
):
    rules = [
        {"id": "K-01", "text": "レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く"},
        {"id": "K-03", "text": "競合調査は料金を表形式にし、各項目に出典URLを付ける"},
    ]
    result = await provider.check_rule_conflicts(reason, rules)
    assert result.rule_ids == ["K-01"]
    _assert_positive_usage(result.usage)


async def test_check_rule_conflicts_returns_empty_for_unrelated_reason(
    provider: MockProvider,
):
    rules = [{"id": "K-01", "text": "レポートは結論→根拠の順で書く"}]
    result = await provider.check_rule_conflicts("誤字が多いので直してください", rules)
    assert result.rule_ids == []


async def test_check_rule_conflicts_returns_empty_without_rules(provider: MockProvider):
    result = await provider.check_rule_conflicts("ルールとは逆にしてください", [])
    assert result.rule_ids == []


# --- generalize_rule_text（#29 チーム昇格DLPガード。決定的な伏字リライト） ---


async def test_generalize_rule_text_masks_findings(provider: MockProvider):
    """findings の quote を「◯◯」へ置換し、末尾に「（一般化済み）」を付ける。"""
    from app.ai.mock_provider import GENERALIZE_MASK, GENERALIZE_SUFFIX

    text = "田中様のメール a@b.co に送る"
    findings = [
        {"infoType": "PERSON_NAME", "quote": "田中"},
        {"infoType": "EMAIL_ADDRESS", "quote": "a@b.co"},
    ]
    result = await provider.generalize_rule_text(text, findings)
    assert result.text == f"{GENERALIZE_MASK}様のメール {GENERALIZE_MASK} に送る{GENERALIZE_SUFFIX}"
    assert "田中" not in result.text
    assert "a@b.co" not in result.text
    _assert_positive_usage(result.usage)


async def test_generalize_rule_text_is_deterministic(provider: MockProvider):
    """同じ入力には常に同じ文案を返す（テスト・デモの再現性）。"""
    text = "山田様へ 090-1111-2222 で連絡"
    findings = [
        {"infoType": "PERSON_NAME", "quote": "山田"},
        {"infoType": "PHONE_NUMBER", "quote": "090-1111-2222"},
    ]
    first = await provider.generalize_rule_text(text, findings)
    second = await provider.generalize_rule_text(text, findings)
    assert first.text == second.text


async def test_generalize_rule_text_no_findings_just_suffix(provider: MockProvider):
    """findings が空なら本文はそのまま＋サフィックスのみ付く。"""
    from app.ai.mock_provider import GENERALIZE_SUFFIX

    result = await provider.generalize_rule_text("クリーンな文", [])
    assert result.text == f"クリーンな文{GENERALIZE_SUFFIX}"
