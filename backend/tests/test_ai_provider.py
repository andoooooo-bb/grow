"""AiProvider 抽象 / MockProvider / ファクトリ切替のテスト（Issue #4, §7.0/§7.7）。"""

import pytest

from app.ai import get_provider
from app.ai.gemini_provider import GeminiProvider
from app.ai.mock_provider import (
    CHAT_FOLLOWUP,
    GREETING_GENERIC,
    GREETING_T130,
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
