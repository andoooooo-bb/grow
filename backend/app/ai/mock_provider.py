"""MockProvider — プロトタイプの固定応答を移植した決定的モック（§7.0 / §7.7）。

ネットワーク不要・費用ゼロ・決定的（同じ入力には常に同じ出力）。
スクリプト応答の出典:
- 壁打ち文言: §7.4a / 05 §5.3 / プロト `greetings[id]`・`sendChat`
- 分解候補: 02 §2.5 / プロト `proposals[id]`
- 蒸留候補: 02 §2.5 / プロト `learnProposals[id]`
- execute: §7.3 の確定ユースケース「調査 → Markdown レポート化」
usage は入力・出力の文字数から決定的に算出する（文字数 // 4、最低 1）。
"""

import json

from app.ai.provider import (
    AiProvider,
    ChatReplyResult,
    ExecuteResult,
    NextAction,
    NextActionResult,
    ProposeRulesResult,
    ProposeSubtasksResult,
    RuleProposal,
    SubtaskProposal,
    TokenUsage,
)

# --- 壁打ち文言（§7.4a / 05 §5.3 / プロト greetings・sendChat） ---

GREETING_T130 = (
    "「ポートフォリオサイトのリニューアル」ですね。分解の前に確認させてください。\n"
    "① 公開したい時期は？\n"
    "② 既存サイトの資産は流用しますか？\n"
    "③ いちばん見せたい実績は？"
)
GREETING_GENERIC = (
    "このタスクですね。進め方を一緒に詰めましょう。やりたいこと・前提を教えてください。"
)
CHAT_FOLLOWUP = (
    "ありがとうございます、イメージできました。"
    "いただいた前提をふまえ、次のように分解するのはいかがでしょう。"
)

# --- 分解候補（02 §2.5 / プロト proposals） ---

SUBTASKS_T130 = [
    SubtaskProposal(title="情報設計・サイトマップ作成", owner="ai"),
    SubtaskProposal(title="ワイヤーフレーム作成", owner="ai"),
    SubtaskProposal(
        title="掲載する実績コンテンツの選定",
        owner="human",
        rationale="掲載内容の取捨選択は本人の意思決定が必要なため",
    ),
    SubtaskProposal(
        title="デザイン方向性の決定",
        owner="human",
        rationale="好み・ブランドに関わる判断は人が行うため",
    ),
    SubtaskProposal(title="コーディング・実装", owner="ai"),
]
SUBTASKS_GENERIC = [
    SubtaskProposal(title="要件・前提の整理", owner="ai"),
    SubtaskProposal(title="たたき台の作成", owner="ai"),
    SubtaskProposal(
        title="内容の確認・決定", owner="human", rationale="最終的な判断は人が行うため"
    ),
    SubtaskProposal(title="仕上げ", owner="ai"),
]

# --- 蒸留候補（02 §2.5 / プロト learnProposals） ---

RULES_T098 = [
    RuleProposal(
        text="競合ごとにセクションを分け、末尾に横断比較表を置くと差し戻しが減る",
        scope="personal",
        tags=["調査"],
        confidence="med",
        source="T-098 のレビューで差し戻しが繰り返された",
    ),
    RuleProposal(
        text="料金は必ず税抜/税込を明記する",
        scope="personal",
        tags=["調査", "経理"],
        confidence="med",
        source="T-098 で同じ修正指示が2回あった",
    ),
]
RULES_T091 = [
    RuleProposal(
        text="確定申告サマリーは控除候補を別セクションで先に提示する",
        scope="personal",
        tags=["経理"],
        confidence="med",
        source="T-091 のレビュー指摘から抽出",
    ),
]

# --- 指揮者の判断理由（#22。デモで確実に再現できる決定的な文言） ---

DECIDE_REASONS: dict[str, str] = {
    "hearing": "前提が未確認のため、まずヒアリングで要件を確認します",
    "breakdown": "壁打ちで前提が揃ったため、サブタスクへの分解を提案します",
    "execute": "実行可能な状態のため、実行AIに作業を任せます",
    "handoff_review": "成果物のレビューと承認は人の判断が必要です",
    "handoff_unknown": "AIだけでは進められない状態のため、人にお返しします",
    "done": "成果物まで完了済みのため、これ以上の作業はありません",
}


class MockProvider(AiProvider):
    """プロトの固定応答をそのまま返す決定的プロバイダ（費用ゼロ・ネットワーク不要）。"""

    async def execute(
        self,
        task: dict,
        rules: list[dict],
        comments: list[dict],
        *,
        policy: dict | None = None,
        plan_only: bool = False,
    ) -> ExecuteResult:
        allow_web_search = bool((policy or {}).get("allowWebSearch", True))
        if plan_only:
            # L0（#21）: 成果物は作らず「実行プラン」だけを返す
            content_md = self._build_plan(task, rules)
        else:
            content_md = self._build_report(task, rules, allow_web_search=allow_web_search)
        return ExecuteResult(
            content_md=content_md,
            usage=self._usage(content_md, task, rules, comments),
        )

    async def propose_subtasks(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ProposeSubtasksResult:
        subtasks = SUBTASKS_T130 if task.get("humanId") == "T-130" else SUBTASKS_GENERIC
        return ProposeSubtasksResult(
            subtasks=list(subtasks),
            usage=self._usage(str(subtasks), task, chat, rules),
        )

    async def propose_rules(
        self, task: dict, comments: list[dict], chat: list[dict]
    ) -> ProposeRulesResult:
        human_id = task.get("humanId")
        if human_id == "T-098":
            proposals = list(RULES_T098)
        elif human_id == "T-091":
            proposals = list(RULES_T091)
        else:
            proposals = [
                RuleProposal(
                    text="このタスクで繰り返した指示を、今後の既定の進め方にする",
                    scope="personal",
                    tags=list(task.get("labels") or []),
                    confidence="low",
                    source=f"{human_id or '不明なタスク'} の履歴から抽出",
                )
            ]
        return ProposeRulesResult(
            rules=proposals,
            usage=self._usage(str(proposals), task, comments, chat),
        )

    async def chat_reply(
        self, task: dict, chat: list[dict], rules: list[dict]
    ) -> ChatReplyResult:
        if not chat:
            if task.get("humanId") == "T-130":
                text = GREETING_T130
            else:
                text = GREETING_GENERIC
        else:
            text = CHAT_FOLLOWUP
        return ChatReplyResult(text=text, usage=self._usage(text, task, chat, rules))

    async def decide_next_action(
        self, task: dict, history: list[dict], rules: list[dict]
    ) -> NextActionResult:
        """指揮者の次アクション（#22）: タスク現況からの決定的な状態機械分岐。

        LLM を使わず、orchestrate ジョブが集約した現況キー
        （status / hasChat / hasArtifact）だけで判断する（同じ入力には常に同じ出力）。
        """
        status = task.get("status")
        has_chat = bool(task.get("hasChat"))
        has_artifact = bool(task.get("hasArtifact"))

        action: NextAction
        if status == "done":
            action, reason_key = "done", "done"
        elif status in ("breakdown", "spec") and not has_chat:
            action, reason_key = "hearing", "hearing"
        elif status in ("breakdown", "spec"):
            # 壁打ち済みで分解候補が未反映（反映済みなら親は ai_work になっている）
            action, reason_key = "breakdown", "breakdown"
        elif status in ("queued", "you_todo"):
            action, reason_key = "execute", "execute"
        elif status in ("you_review", "reviewing") and has_artifact:
            action, reason_key = "handoff_human", "handoff_review"
        else:
            action, reason_key = "handoff_human", "handoff_unknown"
        reason = DECIDE_REASONS[reason_key]
        return NextActionResult(
            action=action,
            reason=reason,
            usage=self._usage(f"{action}:{reason}", task, history, rules),
        )

    # --- 内部ヘルパ ---

    @staticmethod
    def _usage(output_text: str, *inputs: object) -> TokenUsage:
        """入力・出力の文字数からダミーのトークン数を決定的に算出する（文字数 // 4）。"""
        payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
        return TokenUsage(
            input_tokens=max(1, len(payload) // 4),
            output_tokens=max(1, len(output_text) // 4),
        )

    @staticmethod
    def _rules_section(rules: list[dict]) -> list[str]:
        """「## 適用ルール」節の行（ルールなしなら空リスト = 節ごと省く）。"""
        rule_texts = [r.get("text", "") for r in rules if r.get("text")]
        if not rule_texts:
            return []
        return ["## 適用ルール", *[f"- {text}" for text in rule_texts], ""]

    @classmethod
    def _build_plan(cls, task: dict, rules: list[dict]) -> str:
        """L0（#21 計画のみ）の固定「実行プラン」。成果物本文は作らない。"""
        title = task.get("title", "")
        lines = [
            f"# {title} — 実行プラン",
            "",
            *cls._rules_section(rules),
            "## 進め方（案）",
            "1. 目的と評価軸の整理 — 何を判断するための作業かを確認する",
            "2. 情報収集 — 公開情報から候補・材料を洗い出す",
            "3. 比較・構成 — 評価軸ごとに整理し、成果物の骨子を組む",
            "4. レポート化 — 3行サマリー → 本文 → 比較表 → 出典URL の順でまとめる",
            "",
            "## この時点での確認事項",
            "- 対象範囲（候補数・期間）に指定があれば教えてください。",
            "- 重視する評価軸があれば優先順位を教えてください。",
        ]
        return "\n".join(lines)

    @classmethod
    def _build_report(
        cls, task: dict, rules: list[dict], *, allow_web_search: bool = True
    ) -> str:
        """確定ユースケース「調査 → Markdown レポート化」の固定レポート（§7.3 運用）。

        構成: 冒頭3行サマリー → 本文セクション → 比較表 → 出典URL。
        task の title と、渡された rules のテキストを織り込む。
        allow_web_search=False（#21 ポリシー）では出典URLの代わりに
        「要確認事項」を明記する（決定的に文言が変わる）。
        """
        title = task.get("title", "")
        summary_third = (
            "- 判断の根拠となる出典 URL をレポート末尾に明記した。"
            if allow_web_search
            else "- ポリシーによりWeb検索は使用せず、既知情報のみで作成した。"
        )
        lines = [
            f"# {title} — 調査レポート",
            "",
            "## サマリー",
            f"- 「{title}」について公開情報を調査し、要点を本レポートにまとめた。",
            "- 主要な候補を評価軸ごとに整理し、末尾の比較表で横断比較した。",
            summary_third,
            "",
            *cls._rules_section(rules),
            "## 調査結果",
            "",
            "### 背景と目的",
            f"「{title}」の判断材料とするため、主要な選択肢を横断的に調査した。",
            "",
            "### 要点",
            "1. 候補 A は機能面で最も充実しているが、コストが高い。",
            "2. 候補 B は機能とコストのバランスに優れ、第一候補になり得る。",
            "3. 候補 C は導入実績が多く、サポート体制が充実している。",
            "",
            "## 比較表",
            "",
            "| 評価軸 | 候補 A | 候補 B | 候補 C |",
            "| --- | --- | --- | --- |",
            "| 機能 | ◎ | ○ | ○ |",
            "| コスト | △ | ◎ | ○ |",
            "| サポート | ○ | ○ | ◎ |",
            "",
        ]
        if allow_web_search:
            lines += [
                "## 出典URL",
                "- https://example.com/research/source-1",
                "- https://example.com/research/source-2",
                "- https://example.com/research/source-3",
            ]
        else:
            lines += [
                "## 要確認事項",
                "- Web検索は使用不可のため、既知情報のみで作成した。",
                "- 最新の料金・仕様は人による確認が必要。",
            ]
        return "\n".join(lines)
