"""GeminiProvider — Vertex AI Gemini 実装（Issue #15, §7.1/§7.3〜§7.5）。

- SDK は google-genai（`google.genai.Client(vertexai=True, ...)`）。呼び出しは
  `client.aio.models.generate_content`（async）のみ。
- execute（§7.3）: 適用ルールを system に注入し、Grounding with Google Search
  （読み取りのみ, §00 #3）で情報収集して Markdown レポートを生成する。
  grounding metadata の出典 URL をレポート末尾に「## 出典」として付加する。
- propose_subtasks（§7.4b）/ propose_rules（§7.5）: §07 の JSON Schema を
  FunctionDeclaration に写像し、tool_config（mode=ANY）で function calling を
  強制する。自由文パースはしない。
- chat_reply（§7.4a）: 壁打ち応答。会話履歴を contents に写像（human→user / ai→model）。
- モデル割当（§00 #6）: execute=GEMINI_MODEL_EXECUTE（Pro 系）、
  分解/蒸留/壁打ち=GEMINI_MODEL_LIGHT（Flash 系）。
- usage は response.usage_metadata から取得（欠損時は 0）。コスト算定はしない
  （ai_jobs.cost_usd は既存ジョブ側ロジックのまま）。
- クライアントは遅延初期化（初回呼び出し時に生成）。認証情報・シークレットは
  ログに出さない。

応答が期待形でない場合（function_call 欠落・空テキスト等）は GeminiResponseError を
送出し、ジョブ層のリトライ → 最終失敗ハンドオフ（§7.2）に乗せる。
"""

from google import genai
from google.genai import types

from app.ai.provider import (
    AiProvider,
    ChatReplyResult,
    ExecuteResult,
    ProposeRulesResult,
    ProposeSubtasksResult,
    RuleProposal,
    SubtaskProposal,
    TokenUsage,
)
from app.config import get_settings


class GeminiResponseError(RuntimeError):
    """Gemini の応答が期待した形でない（function_call 欠落・空テキスト等）。"""


# ---- Function Declarations（§7.4b / §7.5 の JSON Schema を写像） -------------------

PROPOSE_SUBTASKS_TOOL_NAME = "propose_subtasks"
PROPOSE_SUBTASKS_DECLARATION = types.FunctionDeclaration(
    name=PROPOSE_SUBTASKS_TOOL_NAME,
    description="壁打ちで合意した内容に基づき、タスクを実行可能なサブタスクへ分解する",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "subtasks": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "title": types.Schema(
                            type=types.Type.STRING, description="サブタスクの短い題名"
                        ),
                        "owner": types.Schema(
                            type=types.Type.STRING,
                            enum=["ai", "human"],
                            description="AIが実行可能なら ai、人の判断/作業が必須なら human",
                        ),
                        "rationale": types.Schema(
                            type=types.Type.STRING, description="なぜこの担当か（任意）"
                        ),
                    },
                    required=["title", "owner"],
                ),
            )
        },
        required=["subtasks"],
    ),
)

PROPOSE_RULES_TOOL_NAME = "propose_rules"
PROPOSE_RULES_DECLARATION = types.FunctionDeclaration(
    name=PROPOSE_RULES_TOOL_NAME,
    description="タスク履歴から再利用可能な働き方のルールを抽出する",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "rules": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "text": types.Schema(
                            type=types.Type.STRING,
                            description="命令形・検証可能な粒度のルール文",
                        ),
                        "scope": types.Schema(type=types.Type.STRING, enum=["personal", "team"]),
                        "tags": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                            description="適用を絞るラベル。全タスク共通なら空配列",
                        ),
                        "confidence": types.Schema(
                            type=types.Type.STRING, enum=["high", "med", "low"]
                        ),
                        "source": types.Schema(
                            type=types.Type.STRING,
                            description="根拠（例: 2回同じ修正があった）",
                        ),
                    },
                    required=["text", "scope", "tags", "confidence"],
                ),
            )
        },
        required=["rules"],
    ),
)


# ---- プロンプト組み立て（§7.3〜§7.5 テンプレート） ---------------------------------


def _rules_section(rules: list[dict]) -> str:
    """§7.3 の「# 適用ルール」節。retrieval 結果が空なら空文字（節ごと省く）。"""
    entries = []
    for rule in rules:
        text = rule.get("text")
        if not text:
            continue
        scope = rule.get("scope", "")
        confidence = rule.get("confidence", "")
        source = rule.get("source")
        suffix = f"（出典: {source}）" if source else ""
        entries.append(f"- [{scope}/{confidence}] {text}{suffix}")
    if not entries:
        return ""
    return "\n".join(["# 適用ルール（優先度: 高→低）", *entries])


def _transcript(entries: list[dict]) -> str:
    """コメント/チャット履歴を時系列テキストに変換する（who: text 形式）。"""
    lines = [f"{e.get('who', '')}: {e.get('text', '')}" for e in entries]
    return "\n".join(lines) if lines else "（履歴なし）"


def _labels(task: dict) -> str:
    return ", ".join(task.get("labels") or [])


def _execute_system(task: dict, rules: list[dict], comments: list[dict]) -> str:
    """§7.3 の system プロンプト（テンプレート忠実）。"""
    parts = ["あなたは Grow のワークエージェントです。ユーザーの代わりにタスクを実行します。"]
    rules_section = _rules_section(rules)
    if rules_section:
        parts += [
            "以下は、これまでの履歴から学習した「このユーザー／チームの働き方のルール」です。",
            "明示的に指示されなくても、必ずこれらを前提に作業してください。",
            "",
            rules_section,
        ]
    parts += [
        "",
        "# タスク",
        f"タイトル: {task.get('title', '')}",
        f"ラベル: {_labels(task)}",
        "",
        "# これまでのやり取り（時系列）",
        _transcript(comments),
        "",
        "# 指示",
        "- ルールに従って成果物を作成する。",
        "- 人にしか判断できない点・レビューが必要な点に達したら、勝手に決めず、"
        "その旨を明記して人にハンドオフする。",
        "- 進捗は簡潔に共有する。絵文字は使わない（※ルールに従う）。",
    ]
    return "\n".join(parts)


_EXECUTE_USER_MESSAGE = (
    "上記のタスクを実行してください。情報収集は Google 検索（読み取りのみ）で行い、"
    "成果物は Markdown レポート（冒頭3行サマリー → 本文 → 比較表 → 出典URL）として"
    "出力してください。"
)


def _planning_system(task: dict, rules: list[dict]) -> str:
    """§7.4a の壁打ち system プロンプト（テンプレート忠実）。"""
    parts = [
        "あなたは Grow の計画エージェントです。大きい/抽象的なタスクを、"
        "実行可能な小さいサブタスクへ分解する手伝いをします。",
        "まず、分解に必要な前提を 3 点以内の的確な質問で確認してください（冗長にしない）。",
    ]
    rules_section = _rules_section(rules)
    if rules_section:
        parts += ["", rules_section, ""]
    parts.append(f"タスク: {task.get('title', '')} / ラベル: {_labels(task)}")
    return "\n".join(parts)


def _propose_subtasks_system(task: dict, rules: list[dict]) -> str:
    """§7.4b の分解 system プロンプト（owner 判定基準は §7.4 の運用に従う）。"""
    parts = [
        "あなたは Grow の計画エージェントです。壁打ちで合意した内容に基づき、"
        "タスクを実行可能なサブタスクへ分解します。",
        "owner は「人にしかできないか（意思決定・レビュー・対外・実世界作業）」で "
        "ai / human を振り分けてください。",
        f"必ず {PROPOSE_SUBTASKS_TOOL_NAME} ツールを呼び出して結果を返してください。",
    ]
    rules_section = _rules_section(rules)
    if rules_section:
        parts += ["", rules_section, ""]
    parts.append(f"タスク: {task.get('title', '')} / ラベル: {_labels(task)}")
    return "\n".join(parts)


def _propose_rules_system(task: dict, comments: list[dict], chat: list[dict]) -> str:
    """§7.5 の蒸留 system プロンプト（テンプレート忠実）。"""
    return "\n".join(
        [
            "あなたは Grow の学習エージェントです。1つのタスクの履歴を読み、",
            "「今後の作業に再利用できる、このユーザー／チームの働き方のルール」を抽出します。",
            "",
            "# 抽出の原則",
            "- 再利用可能で検証可能な、一般化された指示だけを抽出する"
            "（この1回限りの内容は除く）。",
            "- 差し戻し・修正・繰り返された指示は特に良い材料。",
            "- 固有名詞・機密・個人情報はルール文に含めない（一般化する）。",
            "- 各ルールに scope（personal/team）と tags（対象を絞るなら）と "
            "confidence を付ける。",
            "- 3件以内。無ければ空で良い（無理に作らない）。",
            "",
            "# タスク履歴",
            f"タイトル: {task.get('title', '')}",
            f"ラベル: {_labels(task)}",
            "",
            "## コメント（時系列）",
            _transcript(comments),
            "",
            "## 壁打ちチャット（時系列）",
            _transcript(chat),
        ]
    )


def _chat_contents(chat: list[dict], *, empty_prompt: str) -> list[types.Content]:
    """会話履歴を contents に写像する（human→user / ai→model）。空なら開始メッセージ。"""
    contents = [
        types.Content(
            role="model" if message.get("who") == "ai" else "user",
            parts=[types.Part(text=message.get("text", ""))],
        )
        for message in chat
    ]
    if not contents:
        contents = [types.Content(role="user", parts=[types.Part(text=empty_prompt)])]
    return contents


def _user_content(text: str) -> list[types.Content]:
    return [types.Content(role="user", parts=[types.Part(text=text)])]


# ---- 応答の解釈 --------------------------------------------------------------------


def _usage_from(response: types.GenerateContentResponse) -> TokenUsage:
    """usage_metadata から TokenUsage を作る（欠損時は 0。コスト算定はしない）。"""
    meta = response.usage_metadata
    if meta is None:
        return TokenUsage(input_tokens=0, output_tokens=0)
    return TokenUsage(
        input_tokens=meta.prompt_token_count or 0,
        output_tokens=meta.candidates_token_count or 0,
    )


def _grounding_sources(response: types.GenerateContentResponse) -> list[tuple[str, str | None]]:
    """grounding metadata から出典 (uri, title) を重複なしで抽出する（§7.3 運用）。"""
    sources: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    candidates = response.candidates or []
    for candidate in candidates[:1]:  # 出典はテキストと同じ先頭候補から取る
        metadata = candidate.grounding_metadata
        if metadata is None or not metadata.grounding_chunks:
            continue
        for chunk in metadata.grounding_chunks:
            web = chunk.web
            if web is None or not web.uri or web.uri in seen:
                continue
            seen.add(web.uri)
            sources.append((web.uri, web.title))
    return sources


def _append_sources(content_md: str, sources: list[tuple[str, str | None]]) -> str:
    """レポート末尾に「## 出典」節を付加する（本文に既に出典があっても重複は許容）。"""
    if not sources:
        return content_md
    lines = [content_md.rstrip("\n"), "", "## 出典"]
    lines += [f"- [{title}]({uri})" if title else f"- {uri}" for uri, title in sources]
    return "\n".join(lines)


def _require_text(response: types.GenerateContentResponse, purpose: str) -> str:
    text = response.text
    if not text:
        raise GeminiResponseError(f"Gemini の {purpose} 応答にテキストが含まれていません")
    return text


def _function_call_args(response: types.GenerateContentResponse, name: str) -> dict:
    """指定名の function_call の引数 dict を取り出す（無ければ明確な例外）。"""
    for call in response.function_calls or []:
        if call.name == name and isinstance(call.args, dict):
            return call.args
    raise GeminiResponseError(f"Gemini 応答に function_call {name} が含まれていません")


def _parse_subtasks(args: dict) -> list[SubtaskProposal]:
    """propose_subtasks の引数を SubtaskProposal に変換する（自由文パース禁止）。"""
    items = args.get("subtasks")
    if not isinstance(items, list):
        raise GeminiResponseError("propose_subtasks の引数 subtasks が配列ではありません")
    proposals: list[SubtaskProposal] = []
    for item in items:
        if not isinstance(item, dict):
            raise GeminiResponseError(
                "propose_subtasks の subtasks 要素がオブジェクトではありません"
            )
        title = item.get("title")
        owner = item.get("owner")
        rationale = item.get("rationale")
        if not isinstance(title, str) or not title:
            raise GeminiResponseError("propose_subtasks の title が不正です")
        if owner not in ("ai", "human"):
            raise GeminiResponseError(f"propose_subtasks の owner が不正です: {owner!r}")
        if rationale is not None and not isinstance(rationale, str):
            raise GeminiResponseError("propose_subtasks の rationale が不正です")
        proposals.append(SubtaskProposal(title=title, owner=owner, rationale=rationale))
    return proposals


def _parse_rules(args: dict) -> list[RuleProposal]:
    """propose_rules の引数を RuleProposal に変換する（自由文パース禁止）。"""
    items = args.get("rules")
    if not isinstance(items, list):
        raise GeminiResponseError("propose_rules の引数 rules が配列ではありません")
    proposals: list[RuleProposal] = []
    for item in items:
        if not isinstance(item, dict):
            raise GeminiResponseError("propose_rules の rules 要素がオブジェクトではありません")
        text = item.get("text")
        scope = item.get("scope")
        tags = item.get("tags")
        confidence = item.get("confidence")
        source = item.get("source")  # §7.5 スキーマ上は任意
        if not isinstance(text, str) or not text:
            raise GeminiResponseError("propose_rules の text が不正です")
        if scope not in ("personal", "team"):
            raise GeminiResponseError(f"propose_rules の scope が不正です: {scope!r}")
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise GeminiResponseError("propose_rules の tags が不正です")
        if confidence not in ("high", "med", "low"):
            raise GeminiResponseError(f"propose_rules の confidence が不正です: {confidence!r}")
        if source is not None and not isinstance(source, str):
            raise GeminiResponseError("propose_rules の source が不正です")
        proposals.append(
            RuleProposal(
                text=text,
                scope=scope,
                tags=list(tags),
                confidence=confidence,
                source=source or "",
            )
        )
    return proposals


# ---- プロバイダ本体 ----------------------------------------------------------------


class GeminiProvider(AiProvider):
    """Vertex AI Gemini プロバイダ（Function Calling / Google Search グラウンディング）。"""

    def __init__(self) -> None:
        # 遅延初期化: コンストラクタでは Client を作らない（認証不要のテストを可能に）
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            settings = get_settings()
            self._client = genai.Client(
                vertexai=True,
                project=settings.gcp_project,
                location=settings.gcp_location,
            )
        return self._client

    async def _generate(
        self,
        *,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> types.GenerateContentResponse:
        client = self._get_client()
        return await client.aio.models.generate_content(
            model=model, contents=contents, config=config
        )

    async def execute(
        self, task: dict, rules: list[dict], comments: list[dict]
    ) -> ExecuteResult:
        """実作業（§7.3）: グラウンディング付きで Markdown レポートを生成する。"""
        settings = get_settings()
        response = await self._generate(
            model=settings.gemini_model_execute,
            contents=_user_content(_EXECUTE_USER_MESSAGE),
            config=types.GenerateContentConfig(
                system_instruction=_execute_system(task, rules, comments),
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        content_md = _append_sources(
            _require_text(response, "execute"), _grounding_sources(response)
        )
        return ExecuteResult(content_md=content_md, usage=_usage_from(response))

    async def propose_subtasks(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ProposeSubtasksResult:
        """分解（§7.4b）: Function Calling を強制して構造化 JSON を受け取る。"""
        settings = get_settings()
        contents = _chat_contents(chat, empty_prompt="このタスクの分解を提案してください。")
        contents += _user_content(
            f"これまでの壁打ち内容に基づき、{PROPOSE_SUBTASKS_TOOL_NAME} ツールで"
            "サブタスク分解を提案してください。"
        )
        response = await self._generate(
            model=settings.gemini_model_light,
            contents=contents,
            config=self._forced_function_config(
                system=_propose_subtasks_system(task, rules),
                declaration=PROPOSE_SUBTASKS_DECLARATION,
            ),
        )
        args = _function_call_args(response, PROPOSE_SUBTASKS_TOOL_NAME)
        return ProposeSubtasksResult(subtasks=_parse_subtasks(args), usage=_usage_from(response))

    async def propose_rules(
        self, task: dict, comments: list[dict], chat: list[dict]
    ) -> ProposeRulesResult:
        """蒸留（§7.5）: タスク履歴から再利用可能なルールを Function Calling で抽出する。"""
        settings = get_settings()
        response = await self._generate(
            model=settings.gemini_model_light,
            contents=_user_content(
                f"上記のタスク履歴から、{PROPOSE_RULES_TOOL_NAME} ツールで再利用可能な"
                "ルールを抽出してください（無ければ空配列で返す）。"
            ),
            config=self._forced_function_config(
                system=_propose_rules_system(task, comments, chat),
                declaration=PROPOSE_RULES_DECLARATION,
            ),
        )
        args = _function_call_args(response, PROPOSE_RULES_TOOL_NAME)
        return ProposeRulesResult(rules=_parse_rules(args), usage=_usage_from(response))

    async def chat_reply(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ChatReplyResult:
        """壁打ち応答（§7.4a）: 会話履歴を写像して応答テキストを得る。"""
        settings = get_settings()
        response = await self._generate(
            model=settings.gemini_model_light,
            contents=_chat_contents(
                chat,
                empty_prompt=(
                    "壁打ちを開始します。このタスクの分解に必要な前提を確認してください。"
                ),
            ),
            config=types.GenerateContentConfig(system_instruction=_planning_system(task, rules)),
        )
        return ChatReplyResult(
            text=_require_text(response, "chat_reply"), usage=_usage_from(response)
        )

    # ---- 内部ヘルパ ----------------------------------------------------------------

    @staticmethod
    def _forced_function_config(
        *, system: str, declaration: types.FunctionDeclaration
    ) -> types.GenerateContentConfig:
        """指定ツールの function calling を強制する設定（mode=ANY + 許可名限定）。"""
        return types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[declaration])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=[declaration.name or ""],
                )
            ),
        )
