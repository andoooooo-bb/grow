"""GeminiProvider のテスト（Issue #15, §7.1/§7.3〜§7.5）。

実GCP・実ネットワークは一切叩かない。google.genai.Client をフェイクに差し替え、
応答は実SDKの types.GenerateContentResponse を組み立てて返す（パース処理が実際の
SDK 型と整合することを担保）。フェイク未設置で Client が生成された場合はテストを
失敗させるため、遅延初期化の検証も兼ねてネットワーク接続が発生しないことを保証する。
"""

from types import SimpleNamespace

import pytest
from google.genai import types

import app.ai.gemini_provider as gemini_module
from app.ai import get_provider
from app.ai.gemini_provider import GeminiProvider, GeminiResponseError
from app.ai.provider import RuleProposal, SubtaskProposal, TokenUsage
from app.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """get_settings は lru_cache 済みのため、環境変数切替の前後でキャッシュを破棄する。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _forbid_real_client(monkeypatch: pytest.MonkeyPatch):
    """既定では genai.Client の生成自体を禁止する（実ネットワーク接続の芽を断つ）。

    Client を使うテストは patch_client フィクスチャで明示的にフェイクを設置する。
    """

    def _fail(**kwargs):
        raise AssertionError("genai.Client が生成されました（テストでは禁止）")

    monkeypatch.setattr(gemini_module.genai, "Client", _fail)


class FakeAsyncModels:
    """client.aio.models.generate_content / generate_content_stream の記録付きフェイク。"""

    def __init__(self, responses: list[types.GenerateContentResponse]) -> None:
        self._responses = list(responses)
        self._stream_chunks: list[list[types.GenerateContentResponse]] = []
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def queue_stream(self, chunks: list[types.GenerateContentResponse]) -> None:
        """generate_content_stream の1回分の応答チャンク列を積む（#24）。"""
        self._stream_chunks.append(list(chunks))

    async def generate_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        assert self._responses, "予期しない generate_content 呼び出し"
        return self._responses.pop(0)

    async def generate_content_stream(self, *, model, contents, config=None):
        """実SDK同様、await するとチャンクの AsyncIterator が返る（#24）。"""
        self.stream_calls.append({"model": model, "contents": contents, "config": config})
        assert self._stream_chunks, "予期しない generate_content_stream 呼び出し"
        chunks = self._stream_chunks.pop(0)

        async def _aiter():
            for chunk in chunks:
                yield chunk

        return _aiter()


class FakeClient:
    def __init__(self, responses: list[types.GenerateContentResponse]) -> None:
        self.init_kwargs: dict | None = None
        self.models = FakeAsyncModels(responses)
        self.aio = SimpleNamespace(models=self.models)


@pytest.fixture
def patch_client(monkeypatch: pytest.MonkeyPatch):
    """genai.Client をフェイクに差し替え、キューした応答を返すファクトリを提供する。"""
    state = {"constructed": 0}

    def install(*responses: types.GenerateContentResponse) -> FakeClient:
        fake = FakeClient(list(responses))

        def factory(**kwargs):
            state["constructed"] += 1
            fake.init_kwargs = kwargs
            return fake

        monkeypatch.setattr(gemini_module.genai, "Client", factory)
        fake.constructed = lambda: state["constructed"]  # type: ignore[attr-defined]
        return fake

    return install


# ---- 応答の組み立てヘルパ（実SDK型を使用） -----------------------------------------


def _usage_meta(input_tokens: int, output_tokens: int):
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=input_tokens, candidates_token_count=output_tokens
    )


def _grounding(*pairs: tuple[str, str | None]) -> types.GroundingMetadata:
    return types.GroundingMetadata(
        grounding_chunks=[
            types.GroundingChunk(web=types.GroundingChunkWeb(uri=uri, title=title))
            for uri, title in pairs
        ]
    )


def _text_response(
    text: str,
    *,
    grounding: types.GroundingMetadata | None = None,
    usage: tuple[int, int] = (120, 34),
) -> types.GenerateContentResponse:
    candidate = types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        grounding_metadata=grounding,
    )
    return types.GenerateContentResponse(
        candidates=[candidate], usage_metadata=_usage_meta(*usage)
    )


def _function_call_response(
    name: str, args: dict, *, usage: tuple[int, int] = (80, 21)
) -> types.GenerateContentResponse:
    candidate = types.Candidate(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
        )
    )
    return types.GenerateContentResponse(
        candidates=[candidate], usage_metadata=_usage_meta(*usage)
    )


def _task(human_id: str = "T-104", title: str = "競合SaaS 5社の料金プランを調査"):
    return {
        "id": f"uuid-{human_id}",
        "humanId": human_id,
        "title": title,
        "labels": ["仕事", "調査"],
    }


_RULES = [
    {
        "id": "K-01",
        "text": "レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く",
        "scope": "personal",
        "confidence": "high",
        "tags": ["調査"],
        "source": "T-098 のレビューから抽出",
    },
    {
        "id": "K-03",
        "text": "競合調査は料金を表形式にし、各項目に出典URLを付ける",
        "scope": "team",
        "confidence": "med",
        "tags": ["調査"],
        "source": "チーム合意",
    },
]


# ---- (a) ファクトリ / (e) 遅延初期化 ------------------------------------------------


def test_factory_returns_gemini_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    get_settings.cache_clear()
    assert isinstance(get_provider(), GeminiProvider)


def test_instantiation_does_not_create_client():
    """インスタンス化だけでは Client を作らない（autouse の禁止パッチが発火しない）。"""
    provider = GeminiProvider()
    assert provider._client is None


async def test_client_is_created_lazily_and_reused(
    patch_client, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GCP_PROJECT", "proj-test")
    monkeypatch.setenv("GCP_LOCATION", "asia-northeast1")
    get_settings.cache_clear()
    fake = patch_client(
        _text_response("応答1"),
        _text_response("応答2"),
    )
    provider = GeminiProvider()
    assert fake.constructed() == 0  # まだ作られない
    await provider.chat_reply(_task(), [], [])
    await provider.chat_reply(_task(), [], [])
    assert fake.constructed() == 1  # 初回呼び出しで1度だけ生成・以後再利用
    assert fake.init_kwargs == {
        "vertexai": True,
        "project": "proj-test",
        "location": "asia-northeast1",
    }


# ---- (b) execute（§7.3） -----------------------------------------------------------


async def test_execute_injects_rules_and_history_into_system_prompt(patch_client):
    fake = patch_client(_text_response("# レポート"))
    comments = [{"who": "human", "text": "候補は5社に絞ってください"}]
    await GeminiProvider().execute(_task(), _RULES, comments)

    call = fake.models.calls[0]
    system = call["config"].system_instruction
    assert "# 適用ルール（優先度: 高→低）" in system
    assert (
        "- [personal/high] レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く"
        "（出典: T-098 のレビューから抽出）" in system
    )
    assert "- [team/med] 競合調査は料金を表形式にし、各項目に出典URLを付ける" in system
    assert "タイトル: 競合SaaS 5社の料金プランを調査" in system
    assert "ラベル: 仕事, 調査" in system
    assert "human: 候補は5社に絞ってください" in system  # 履歴の注入
    assert "# 指示" in system


async def test_execute_omits_rules_section_when_no_rules(patch_client):
    fake = patch_client(_text_response("# レポート"))
    await GeminiProvider().execute(_task(), [], [])
    system = fake.models.calls[0]["config"].system_instruction
    assert "# 適用ルール" not in system


async def test_execute_uses_google_search_grounding_and_pro_model(patch_client):
    fake = patch_client(_text_response("# レポート"))
    await GeminiProvider().execute(_task(), [], [])
    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-pro"  # 実作業は Pro 系（§00 #6）
    tools = call["config"].tools
    assert any(t.google_search is not None for t in tools)
    assert all(not t.function_declarations for t in tools)  # execute は FC を使わない


async def test_execute_model_is_overridable_by_env(
    patch_client, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GEMINI_MODEL_EXECUTE", "gemini-9.9-custom")
    get_settings.cache_clear()
    fake = patch_client(_text_response("# レポート"))
    await GeminiProvider().execute(_task(), [], [])
    assert fake.models.calls[0]["model"] == "gemini-9.9-custom"


async def test_execute_appends_grounding_sources_and_usage(patch_client):
    grounding = _grounding(
        ("https://example.com/pricing", "料金ページ"),
        ("https://example.com/docs", None),
        ("https://example.com/pricing", "重複は捨てる"),
    )
    patch_client(
        _text_response("# 調査レポート\n\n本文です。", grounding=grounding, usage=(321, 45))
    )
    result = await GeminiProvider().execute(_task(), [], [])

    assert result.content_md.startswith("# 調査レポート")
    assert "## 出典" in result.content_md
    assert "- [料金ページ](https://example.com/pricing)" in result.content_md
    assert "- https://example.com/docs" in result.content_md
    assert result.content_md.count("https://example.com/pricing") == 1  # 重複なし
    assert result.usage.input_tokens == 321
    assert result.usage.output_tokens == 45


async def test_execute_without_grounding_keeps_text_as_is(patch_client):
    patch_client(_text_response("# レポートのみ"))
    result = await GeminiProvider().execute(_task(), [], [])
    assert result.content_md == "# レポートのみ"
    assert "## 出典" not in result.content_md


async def test_execute_policy_disables_google_search(patch_client):
    """#21 allowWebSearch=False: 検索ツールを付けず、system に検索不可指示を注入する。"""
    fake = patch_client(_text_response("# レポート"))
    await GeminiProvider().execute(_task(), [], [], policy={"allowWebSearch": False})

    call = fake.models.calls[0]
    assert call["config"].tools is None  # Google Search ツールを付けない
    system = call["config"].system_instruction
    assert "- Web検索は使用不可。既知情報のみで作成し、要確認事項を明記する。" in system
    user_text = call["contents"][0].parts[0].text
    assert "Google 検索" not in user_text
    assert "要確認事項" in user_text


async def test_execute_policy_defaults_keep_google_search(patch_client):
    """policy 省略キー（allowWebSearch なし）は既定=検索可のまま。"""
    fake = patch_client(_text_response("# レポート"))
    await GeminiProvider().execute(_task(), [], [], policy={"costCapUsd": 3.0})
    tools = fake.models.calls[0]["config"].tools
    assert any(t.google_search is not None for t in tools)


async def test_execute_plan_only_switches_to_plan_prompt(patch_client):
    """#21 L0: plan_only=True はプラン生成のユーザーメッセージ＋system 指示に切り替わる。"""
    fake = patch_client(_text_response("## 実行プラン"))
    result = await GeminiProvider().execute(_task(), [], [], plan_only=True)

    call = fake.models.calls[0]
    user_text = call["contents"][0].parts[0].text
    assert "まだ実行しないでください" in user_text
    assert "実行プラン" in user_text
    system = call["config"].system_instruction
    assert "- 今回は「実行プラン」の提案のみを行う。成果物の本文は作成しない。" in system
    assert result.content_md == "## 実行プラン"


async def test_execute_raises_on_empty_response(patch_client):
    patch_client(types.GenerateContentResponse(candidates=[]))
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().execute(_task(), [], [])


async def test_usage_defaults_to_zero_when_metadata_missing(patch_client):
    candidate = types.Candidate(content=types.Content(role="model", parts=[types.Part(text="x")]))
    patch_client(types.GenerateContentResponse(candidates=[candidate]))
    result = await GeminiProvider().execute(_task(), [], [])
    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0


# ---- (b') execute のライブ実況（#24: generate_content_stream 経路） ------------------


def _stream_chunk(
    text: str | None,
    *,
    grounding: types.GroundingMetadata | None = None,
    usage: tuple[int, int] | None = None,
) -> types.GenerateContentResponse:
    """ストリーム1チャンク分の応答（text は増分。usage は通常最終チャンクのみ載る）。"""
    parts = [types.Part(text=text)] if text is not None else []
    candidate = types.Candidate(
        content=types.Content(role="model", parts=parts),
        grounding_metadata=grounding,
    )
    return types.GenerateContentResponse(
        candidates=[candidate],
        usage_metadata=_usage_meta(*usage) if usage is not None else None,
    )


async def test_execute_streams_deltas_and_appends_sources(patch_client):
    """on_delta 指定時: 増分が順に届き、全文＋出典（全チャンクから重複なし収集）を返す。

    usage は usage_metadata を載せた最終チャンクの累計値を使う。
    """
    fake = patch_client()
    fake.models.queue_stream(
        [
            _stream_chunk("# 調査レポート\n\n"),
            _stream_chunk(
                "候補Aが有力。",
                grounding=_grounding(("https://example.com/a", "候補A")),
            ),
            _stream_chunk(
                "候補Bも比較した。",
                grounding=_grounding(
                    ("https://example.com/a", "重複は捨てる"),
                    ("https://example.com/b", None),
                ),
                usage=(321, 45),
            ),
        ]
    )
    deltas: list[str] = []

    async def on_delta(delta: str) -> None:
        deltas.append(delta)

    result = await GeminiProvider().execute(_task(), _RULES, [], on_delta=on_delta)

    # 増分は「累積でなく差分」がそのままの順で届く
    assert deltas == ["# 調査レポート\n\n", "候補Aが有力。", "候補Bも比較した。"]
    # 全文 = 増分の連結。出典はストリーム完了後に付加される（増分には含めない）
    assert result.content_md.startswith("".join(deltas))
    assert "## 出典" in result.content_md
    assert "- [候補A](https://example.com/a)" in result.content_md
    assert "- https://example.com/b" in result.content_md
    assert result.content_md.count("https://example.com/a") == 1  # 重複なし
    assert result.usage.input_tokens == 321
    assert result.usage.output_tokens == 45

    # ストリーム経路が使われ、非ストリームの generate_content は呼ばれない
    assert fake.models.calls == []
    assert len(fake.models.stream_calls) == 1
    call = fake.models.stream_calls[0]
    assert call["model"] == "gemini-2.5-pro"  # 実作業は Pro 系（§00 #6）
    assert any(t.google_search is not None for t in call["config"].tools)
    assert "# 適用ルール（優先度: 高→低）" in call["config"].system_instruction


async def test_execute_stream_skips_textless_chunks(patch_client):
    """text の無いチャンク（grounding/usage のみ等）は on_delta を呼ばず収集だけ行う。"""
    fake = patch_client()
    fake.models.queue_stream(
        [
            _stream_chunk("本文", usage=(10, 2)),
            _stream_chunk(None, usage=(100, 20)),  # 終端の usage 専用チャンク
        ]
    )
    deltas: list[str] = []

    async def on_delta(delta: str) -> None:
        deltas.append(delta)

    result = await GeminiProvider().execute(_task(), [], [], on_delta=on_delta)
    assert deltas == ["本文"]
    assert result.content_md == "本文"
    assert result.usage.input_tokens == 100  # 最後に usage を載せたチャンクの累計
    assert result.usage.output_tokens == 20
    assert fake.models.calls == []


async def test_execute_stream_raises_when_no_text_arrives(patch_client):
    fake = patch_client()
    fake.models.queue_stream([_stream_chunk(None), _stream_chunk(None, usage=(5, 0))])

    async def on_delta(delta: str) -> None:  # pragma: no cover — 呼ばれないはず
        raise AssertionError("textless ストリームで on_delta が呼ばれた")

    with pytest.raises(GeminiResponseError):
        await GeminiProvider().execute(_task(), [], [], on_delta=on_delta)


async def test_execute_plan_only_stays_non_stream_even_with_on_delta(patch_client):
    """plan_only=True（L0）は on_delta を渡されても非ストリームのまま（#24 仕様）。"""
    fake = patch_client(_text_response("## 実行プラン"))
    deltas: list[str] = []

    async def on_delta(delta: str) -> None:
        deltas.append(delta)

    result = await GeminiProvider().execute(
        _task(), [], [], plan_only=True, on_delta=on_delta
    )
    assert result.content_md == "## 実行プラン"
    assert deltas == []
    assert len(fake.models.calls) == 1  # 非ストリーム経路
    assert fake.models.stream_calls == []


# ---- (c) propose_subtasks（§7.4b） -------------------------------------------------


async def test_propose_subtasks_declares_schema_and_forces_function_calling(patch_client):
    fake = patch_client(_function_call_response("propose_subtasks", {"subtasks": []}))
    await GeminiProvider().propose_subtasks(_task(), [], [])

    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"  # 分解は Flash 系（§00 #6）

    decl = call["config"].tools[0].function_declarations[0]
    assert decl.name == "propose_subtasks"
    assert decl.parameters.type == types.Type.OBJECT
    assert decl.parameters.required == ["subtasks"]
    item = decl.parameters.properties["subtasks"].items
    assert set(item.properties.keys()) == {"title", "owner", "rationale"}
    assert item.properties["owner"].enum == ["ai", "human"]
    assert item.required == ["title", "owner"]

    fc = call["config"].tool_config.function_calling_config
    assert fc.mode == types.FunctionCallingConfigMode.ANY  # function calling を強制
    assert fc.allowed_function_names == ["propose_subtasks"]


async def test_propose_subtasks_converts_function_call_args(patch_client):
    patch_client(
        _function_call_response(
            "propose_subtasks",
            {
                "subtasks": [
                    {"title": "情報設計・サイトマップ作成", "owner": "ai"},
                    {
                        "title": "デザイン方向性の決定",
                        "owner": "human",
                        "rationale": "好み・ブランドに関わる判断は人が行うため",
                    },
                ]
            },
            usage=(150, 60),
        )
    )
    result = await GeminiProvider().propose_subtasks(_task(), [], [])
    assert result.subtasks == [
        SubtaskProposal(title="情報設計・サイトマップ作成", owner="ai", rationale=None),
        SubtaskProposal(
            title="デザイン方向性の決定",
            owner="human",
            rationale="好み・ブランドに関わる判断は人が行うため",
        ),
    ]
    assert result.usage.input_tokens == 150
    assert result.usage.output_tokens == 60


async def test_propose_subtasks_maps_chat_history_into_contents(patch_client):
    fake = patch_client(_function_call_response("propose_subtasks", {"subtasks": []}))
    chat = [
        {"who": "ai", "text": "前提を3点教えてください"},
        {"who": "human", "text": "3月末公開・資産は流用します"},
    ]
    await GeminiProvider().propose_subtasks(_task(), chat, [])
    contents = fake.models.calls[0]["contents"]
    assert [c.role for c in contents[:2]] == ["model", "user"]
    assert contents[0].parts[0].text == "前提を3点教えてください"
    assert contents[-1].role == "user"  # 末尾にツール呼び出し指示


async def test_propose_subtasks_raises_without_function_call(patch_client):
    patch_client(_text_response("自由文で分解します: 1. …"))
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().propose_subtasks(_task(), [], [])


async def test_propose_subtasks_raises_on_invalid_owner(patch_client):
    patch_client(
        _function_call_response(
            "propose_subtasks", {"subtasks": [{"title": "x", "owner": "robot"}]}
        )
    )
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().propose_subtasks(_task(), [], [])


# ---- (c) propose_rules（§7.5） -----------------------------------------------------


async def test_propose_rules_declares_schema_and_distill_principles(patch_client):
    fake = patch_client(_function_call_response("propose_rules", {"rules": []}))
    comments = [{"who": "human", "text": "税抜/税込を明記してください"}]
    chat = [{"who": "human", "text": "比較表を末尾に置いて"}]
    await GeminiProvider().propose_rules(_task(), comments, chat)

    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"  # 蒸留は Flash 系（§00 #6）

    system = call["config"].system_instruction
    assert "3件以内。無ければ空で良い（無理に作らない）。" in system
    assert "固有名詞・機密・個人情報はルール文に含めない（一般化する）。" in system
    assert "差し戻し・修正・繰り返された指示は特に良い材料。" in system
    assert "human: 税抜/税込を明記してください" in system  # コメント履歴の注入
    assert "human: 比較表を末尾に置いて" in system  # チャット履歴の注入

    decl = call["config"].tools[0].function_declarations[0]
    assert decl.name == "propose_rules"
    assert decl.parameters.required == ["rules"]
    item = decl.parameters.properties["rules"].items
    assert set(item.properties.keys()) == {"text", "scope", "tags", "confidence", "source"}
    assert item.properties["scope"].enum == ["personal", "team"]
    assert item.properties["confidence"].enum == ["high", "med", "low"]
    assert item.properties["tags"].type == types.Type.ARRAY
    assert item.required == ["text", "scope", "tags", "confidence"]

    fc = call["config"].tool_config.function_calling_config
    assert fc.mode == types.FunctionCallingConfigMode.ANY
    assert fc.allowed_function_names == ["propose_rules"]


async def test_propose_rules_converts_function_call_args(patch_client):
    patch_client(
        _function_call_response(
            "propose_rules",
            {
                "rules": [
                    {
                        "text": "料金は必ず税抜/税込を明記する",
                        "scope": "personal",
                        "tags": ["調査", "経理"],
                        "confidence": "med",
                        "source": "同じ修正指示が2回あった",
                    },
                    {  # source は §7.5 スキーマ上任意 → 既定は空文字
                        "text": "比較表を末尾に置く",
                        "scope": "team",
                        "tags": [],
                        "confidence": "low",
                    },
                ]
            },
            usage=(200, 88),
        )
    )
    result = await GeminiProvider().propose_rules(_task(), [], [])
    assert result.rules == [
        RuleProposal(
            text="料金は必ず税抜/税込を明記する",
            scope="personal",
            tags=["調査", "経理"],
            confidence="med",
            source="同じ修正指示が2回あった",
        ),
        RuleProposal(
            text="比較表を末尾に置く", scope="team", tags=[], confidence="low", source=""
        ),
    ]
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 88


async def test_propose_rules_raises_without_function_call(patch_client):
    patch_client(_text_response("ルール: 比較表を置く"))
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().propose_rules(_task(), [], [])


async def test_propose_rules_raises_on_invalid_confidence(patch_client):
    patch_client(
        _function_call_response(
            "propose_rules",
            {"rules": [{"text": "x", "scope": "personal", "tags": [], "confidence": "最強"}]},
        )
    )
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().propose_rules(_task(), [], [])


# ---- (d) chat_reply（§7.4a） -------------------------------------------------------


async def test_chat_reply_maps_history_roles_and_returns_text(patch_client):
    fake = patch_client(
        _text_response("① 公開時期は？ ② 資産は流用？ ③ 見せたい実績は？", usage=(90, 30))
    )
    chat = [
        {"who": "human", "text": "ポートフォリオを刷新したい"},
        {"who": "ai", "text": "前提を確認させてください"},
        {"who": "human", "text": "3月末までに公開したい"},
    ]
    task = _task("T-130", "ポートフォリオサイトのリニューアル")
    result = await GeminiProvider().chat_reply(task, chat, [])

    contents = fake.models.calls[0]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]  # human→user / ai→model
    assert [c.parts[0].text for c in contents] == [
        "ポートフォリオを刷新したい",
        "前提を確認させてください",
        "3月末までに公開したい",
    ]
    assert result.text == "① 公開時期は？ ② 資産は流用？ ③ 見せたい実績は？"
    assert result.usage.input_tokens == 90
    assert result.usage.output_tokens == 30


async def test_chat_reply_uses_light_model_and_planning_system_with_rules(patch_client):
    fake = patch_client(_text_response("質問です"))
    await GeminiProvider().chat_reply(_task(), [], _RULES)
    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"  # 壁打ちは Flash 系（§00 #6）
    system = call["config"].system_instruction
    assert "3 点以内の的確な質問" in system
    assert "# 適用ルール（優先度: 高→低）" in system  # ルール注入（§7.4a）
    assert "タスク: 競合SaaS 5社の料金プランを調査 / ラベル: 仕事, 調査" in system
    assert call["config"].tools is None  # 壁打ちはツール不要


async def test_chat_reply_with_empty_history_sends_opening_user_message(patch_client):
    fake = patch_client(_text_response("初回質問"))
    await GeminiProvider().chat_reply(_task(), [], [])
    contents = fake.models.calls[0]["contents"]
    assert len(contents) == 1
    assert contents[0].role == "user"  # contents は空にできないため開始メッセージを置く
    assert contents[0].parts[0].text


async def test_chat_reply_raises_on_textless_response(patch_client):
    patch_client(_function_call_response("propose_subtasks", {"subtasks": []}))
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().chat_reply(_task(), [], [])


# ---- (e) decide_next_action（#22 指揮者） -------------------------------------------


def _conductor_task() -> dict:
    """orchestrate ジョブが集約する現況キー付きのタスク dict（#22）。"""
    return {
        **_task(),
        "status": "queued",
        "autonomy": "L1",
        "hasChat": False,
        "hasArtifact": False,
        "childStatuses": [],
    }


async def test_decide_next_action_declares_enum_and_forces_function_calling(patch_client):
    fake = patch_client(
        _function_call_response(
            "decide_next_action", {"action": "execute", "reason": "実行可能な状態のため"}
        )
    )
    history = [{"who": "human", "text": "料金は税抜で統一してください"}]
    result = await GeminiProvider().decide_next_action(_conductor_task(), history, _RULES)

    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"  # 判断は毎ループ走るので Flash 系（§00 #6）

    system = call["config"].system_instruction
    assert "指揮者エージェント" in system
    assert "ステータス: queued" in system  # 現況の注入
    assert "オートノミー: L1" in system
    assert "human: 料金は税抜で統一してください" in system  # コメント履歴の注入
    assert "# 適用ルール（優先度: 高→低）" in system

    decl = call["config"].tools[0].function_declarations[0]
    assert decl.name == "decide_next_action"
    assert decl.parameters.required == ["action", "reason"]
    assert decl.parameters.properties["action"].enum == [
        "hearing",
        "breakdown",
        "execute",
        "review",  # レビューAIへのリレー（#23）
        "handoff_human",
        "done",
    ]

    fc = call["config"].tool_config.function_calling_config
    assert fc.mode == types.FunctionCallingConfigMode.ANY  # function calling を強制
    assert fc.allowed_function_names == ["decide_next_action"]

    assert result.action == "execute"
    assert result.reason == "実行可能な状態のため"
    assert result.usage.input_tokens == 80
    assert result.usage.output_tokens == 21


async def test_decide_next_action_raises_on_invalid_action(patch_client):
    patch_client(
        _function_call_response(
            "decide_next_action", {"action": "retry", "reason": "もう一度やる"}
        )
    )
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().decide_next_action(_conductor_task(), [], [])


async def test_decide_next_action_raises_without_function_call(patch_client):
    patch_client(_text_response("次は execute が良いと思います"))
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().decide_next_action(_conductor_task(), [], [])


# ---- review_artifact（#23 セルフレビュー） ------------------------------------------


async def test_review_artifact_declares_verdict_enum_and_injects_rules(patch_client):
    fake = patch_client(
        _function_call_response(
            "review_artifact", {"verdict": "approve", "findings": []}
        )
    )
    result = await GeminiProvider().review_artifact(
        _task(), "# 調査レポート\n本文", _RULES
    )
    assert result.verdict == "approve"
    assert result.findings == []
    assert result.usage == TokenUsage(input_tokens=80, output_tokens=21)

    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"  # 検査は execute のたび走るので Flash 系

    system = call["config"].system_instruction
    assert "レビューエージェント" in system
    # ルールが「審査基準」として注入され、検査対象の成果物も system に載る
    assert "# 審査基準（適用ルール）" in system
    assert "レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く" in system
    assert "# 調査レポート" in system

    decl = call["config"].tools[0].function_declarations[0]
    assert decl.name == "review_artifact"
    assert decl.parameters.properties["verdict"].enum == ["approve", "revise"]
    assert decl.parameters.required == ["verdict", "findings"]
    fc = call["config"].tool_config.function_calling_config
    assert fc.mode == types.FunctionCallingConfigMode.ANY
    assert fc.allowed_function_names == ["review_artifact"]


async def test_review_artifact_parses_revise_findings(patch_client):
    patch_client(
        _function_call_response(
            "review_artifact",
            {"verdict": "revise", "findings": ["比較表に出典URL列を追加してください"]},
        )
    )
    result = await GeminiProvider().review_artifact(_task(), "# v1", _RULES)
    assert result.verdict == "revise"
    assert result.findings == ["比較表に出典URL列を追加してください"]


async def test_review_artifact_rejects_revise_without_findings(patch_client):
    """指摘なしの revise は実行AIが対処できないため応答エラー扱い。"""
    patch_client(
        _function_call_response("review_artifact", {"verdict": "revise", "findings": []})
    )
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().review_artifact(_task(), "# v1", _RULES)


async def test_review_artifact_rejects_unknown_verdict(patch_client):
    patch_client(
        _function_call_response("review_artifact", {"verdict": "maybe", "findings": []})
    )
    with pytest.raises(GeminiResponseError):
        await GeminiProvider().review_artifact(_task(), "# v1", _RULES)


# ---- check_rule_conflicts（#23 矛盾検出） -------------------------------------------


async def test_check_rule_conflicts_filters_unknown_rule_ids(patch_client):
    """渡したルール以外の id（幻覚）は捨て、実在ルールのみ返す。"""
    fake = patch_client(
        _function_call_response(
            "check_rule_conflicts", {"ruleIds": ["K-01", "K-99", "K-01"]}
        )
    )
    result = await GeminiProvider().check_rule_conflicts(
        "ルールとは逆にしてください", _RULES
    )
    assert result.rule_ids == ["K-01"]  # K-99（幻覚）と重複は除外
    assert result.usage == TokenUsage(input_tokens=80, output_tokens=21)

    call = fake.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    system = call["config"].system_instruction
    assert "差し戻し理由" in system
    assert "K-01: レポートは結論→根拠の順で書き、冒頭に3行サマリーを置く" in system
    fc = call["config"].tool_config.function_calling_config
    assert fc.allowed_function_names == ["check_rule_conflicts"]


async def test_check_rule_conflicts_skips_call_without_rules(patch_client):
    """前回適用ルールが無ければ LLM を呼ばずに空で返す。"""
    fake = patch_client()  # 応答なし = 呼ばれたら失敗する
    result = await GeminiProvider().check_rule_conflicts("理由", [])
    assert result.rule_ids == []
    assert fake.models.calls == []


# ---- execute への差し戻し理由の注入（#23） ------------------------------------------


async def test_execute_injects_reject_reason_section(patch_client):
    """直近の【差し戻し理由】コメントが「# 差し戻し理由（最優先で対処）」節として載る。"""
    fake = patch_client(_text_response("# 修正版レポート"))
    comments = [
        {"who": "ai", "text": "完了しました。レビューをお願いします。"},
        {"who": "human", "text": "【差し戻し理由】比較表は不要です。箇条書きにしてください"},
    ]
    await GeminiProvider().execute(_task(), _RULES, comments)

    system = fake.models.calls[0]["config"].system_instruction
    assert "# 差し戻し理由（最優先で対処）" in system
    assert "比較表は不要です。箇条書きにしてください" in system


async def test_execute_omits_reject_section_without_reject_comment(patch_client):
    fake = patch_client(_text_response("# レポート"))
    await GeminiProvider().execute(
        _task(), _RULES, [{"who": "human", "text": "お願いします"}]
    )
    system = fake.models.calls[0]["config"].system_instruction
    assert "# 差し戻し理由（最優先で対処）" not in system
