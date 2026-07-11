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
from collections.abc import Awaitable, Callable

from app.ai.provider import (
    REVIEW_FINDINGS_MARKER,
    AiProvider,
    ChatReplyResult,
    CiProposal,
    ExecuteResult,
    NextAction,
    NextActionResult,
    ProposeRulesResult,
    ProposeSubtasksResult,
    ReconcileResult,
    ReviewResult,
    RuleConflictResult,
    RuleProposal,
    SubtaskProposal,
    TokenUsage,
    latest_reject_reason,
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
    "review": "成果物のセルフレビューが未実施のため、レビューAIに検査させます",
    "handoff_review": "成果物のレビューと承認は人の判断が必要です",
    "handoff_unknown": "AIだけでは進められない状態のため、人にお返しします",
    "done": "成果物まで完了済みのため、これ以上の作業はありません",
}

# --- セルフレビュー（#23。デモで確実に「1回 revise → 修正 → approve」が見える分岐） ---

# 修正版レポートに現れる対応セクション（review_artifact はこれで修正済みと判定する）
REVIEW_FIXED_SECTION = "## レビュー対応"
REJECT_FIXED_SECTION = "## 差し戻し対応"

# revise 時の指摘（適用ルールがあれば先頭ルールを審査基準として引用する）
REVIEW_FINDING_WITH_RULE_TEMPLATE = (
    "比較表に出典URL列がありません。ルール「{rule}」に沿って追記してください"
)
REVIEW_FINDING_GENERIC = "比較表に出典URL列がありません。出典を明記して追記してください"

# --- ライブ実況（#24）: ストリーム模擬の分割点（決定的。文字数固定で数チャンクになる） ---
STREAM_CHUNK_CHARS = 120


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
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ExecuteResult:
        allow_web_search = bool((policy or {}).get("allowWebSearch", True))
        if plan_only:
            # L0（#21）: 成果物は作らず「実行プラン」だけを返す（実況しない #24）
            content_md = self._build_plan(task, rules)
        else:
            content_md = self._build_report(
                task, rules, comments, allow_web_search=allow_web_search
            )
            if on_delta is not None:
                # #24 ライブ実況の模擬: 固定幅で分割した増分を順次届ける
                # （決定的。全増分の連結 = content_md）
                for chunk in self._stream_chunks(content_md):
                    await on_delta(chunk)
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
        （status / hasChat / hasArtifact / hasReview）だけで判断する
        （同じ入力には常に同じ出力）。
        """
        status = task.get("status")
        has_chat = bool(task.get("hasChat"))
        has_artifact = bool(task.get("hasArtifact"))
        has_review = bool(task.get("hasReview"))

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
        elif status in ("you_review", "reviewing") and has_artifact and not has_review:
            # 成果物はあるがセルフレビュー未実施 → 人に渡す前にレビューAIへ（#23）
            action, reason_key = "review", "review"
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

    async def review_artifact(
        self, task: dict, artifact_md: str, rules: list[dict]
    ) -> ReviewResult:
        """セルフレビュー（#23）: 決定的な approve / revise 判定。

        - 成果物に対応セクション（## レビュー対応 / ## 差し戻し対応）がある =
          指摘・差し戻し既往に対応済み → approve
        - 初回 execute 直後（対応セクションなし = v1 相当）→ revise 1回。
          指摘は適用ルールを審査基準として引用する（デモで必ず1往復見える）。
        """
        if REVIEW_FIXED_SECTION in artifact_md or REJECT_FIXED_SECTION in artifact_md:
            verdict: str = "approve"
            findings: list[str] = []
        else:
            verdict = "revise"
            rule_texts = [r.get("text", "") for r in rules if r.get("text")]
            # 指摘は「出典」に言及するルールを優先して引用する（指摘文との整合）
            cited = next((t for t in rule_texts if "出典" in t), None)
            if cited is None and rule_texts:
                cited = rule_texts[0]
            findings = [
                REVIEW_FINDING_WITH_RULE_TEMPLATE.format(rule=cited)
                if cited
                else REVIEW_FINDING_GENERIC
            ]
        return ReviewResult(
            verdict=verdict,  # type: ignore[arg-type]
            findings=findings,
            usage=self._usage(f"{verdict}:{findings}", task, artifact_md, rules),
        )

    async def check_rule_conflicts(
        self, reason: str, rules: list[dict]
    ) -> RuleConflictResult:
        """矛盾検出（#23）: 理由が「ルール」「逆」に言及していれば先頭ルールを返す決定的分岐。"""
        rule_ids: list[str] = []
        if rules and any(keyword in reason for keyword in ("ルール", "逆")):
            first_id = rules[0].get("id")
            if first_id:
                rule_ids = [first_id]
        return RuleConflictResult(
            rule_ids=rule_ids,
            usage=self._usage(",".join(rule_ids) or "none", reason, rules),
        )

    # --- 内部ヘルパ ---

    @staticmethod
    def _stream_chunks(content_md: str) -> list[str]:
        """成果物を固定幅（STREAM_CHUNK_CHARS）で分割する（#24。決定的・分割点固定）。"""
        return [
            content_md[i : i + STREAM_CHUNK_CHARS]
            for i in range(0, len(content_md), STREAM_CHUNK_CHARS)
        ]

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
    def _revision_sections(cls, comments: list[dict]) -> list[str]:
        """差し戻し・レビュー指摘への対応セクション（#23。該当履歴がなければ空）。

        - 人の差し戻し（【差し戻し理由】…）→「## 差し戻し対応」に理由の全文を引用
          （再実行の成果物に理由が反映されたことを検証可能にする）
        - レビューAIの指摘（【レビュー指摘】…）→「## レビュー対応」
        review_artifact はこれらのセクションの有無で修正済みかを判定する。
        """
        lines: list[str] = []
        reject_reason = latest_reject_reason(comments)
        if reject_reason is not None:
            lines += [
                REJECT_FIXED_SECTION,
                f"差し戻し理由「{reject_reason}」を最優先で反映した。",
                "",
            ]
        if any(REVIEW_FINDINGS_MARKER in c.get("text", "") for c in comments):
            lines += [
                REVIEW_FIXED_SECTION,
                "レビューAIの指摘に対応し、比較表に出典URL列を追記した。",
                "",
            ]
        return lines

    @classmethod
    def _build_report(
        cls,
        task: dict,
        rules: list[dict],
        comments: list[dict],
        *,
        allow_web_search: bool = True,
    ) -> str:
        """確定ユースケース「調査 → Markdown レポート化」の固定レポート（§7.3 運用）。

        構成: 冒頭3行サマリー → 本文セクション → 比較表 → 出典URL。
        task の title と、渡された rules のテキストを織り込む。
        allow_web_search=False（#21 ポリシー）では出典URLの代わりに
        「要確認事項」を明記する（決定的に文言が変わる）。
        履歴に差し戻し・レビュー指摘があれば対応セクションを差し込む（#23）。
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
            *cls._revision_sections(comments),
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

    # --- 夜間ナレッジCI（#26。4種の提案を決定的に返す） ------------------------------

    async def reconcile_rules(
        self,
        existing_rules: list[dict],
        recent_tasks: list[dict],
        feedback: list[dict],
        signals: list[dict],
    ) -> ReconcileResult:
        """ナレッジCIの決定的モック（#26 §6.4b/c・§6.6）。

        (a) conflict: 「敬体」を含むルールと「常体」を含むルールの併存を矛盾として検出
            （新しい方を優先した置き換え文案付き。デモの矛盾シナリオを確実に再現する）
        (b) merge: 同一 tags かつ text の先頭10字が一致する2ルールを統合提案
        (c) demote: applied==0・last_applied_at なし・signals なしのルールを棚卸し提案
        (d) distill: done かつ未蒸留のタスク（先頭1件）から新規ルールを蒸留提案
        重複フラグを避けるため、(a)(b) が対象にしたルールは (b)(c) の走査から除外する。
        """
        proposals: list[CiProposal] = list(self._ci_conflict_proposals(existing_rules))
        claimed = {rid for p in proposals for rid in p.target_rule_ids}
        proposals += self._ci_merge_proposals(existing_rules, claimed)
        claimed = {rid for p in proposals for rid in p.target_rule_ids}
        proposals += self._ci_demote_proposals(existing_rules, signals, claimed)
        proposals += self._ci_distill_proposals(recent_tasks)
        return ReconcileResult(
            proposals=proposals,
            usage=self._usage(str(proposals), existing_rules, recent_tasks, feedback, signals),
        )

    @staticmethod
    def _ci_conflict_proposals(existing_rules: list[dict]) -> list[CiProposal]:
        """(a) 敬体 vs 常体の矛盾検出（両方を target に、新しい方の置き換え文案付き）。"""
        keitai = next((r for r in existing_rules if "敬体" in r.get("text", "")), None)
        joutai = next((r for r in existing_rules if "常体" in r.get("text", "")), None)
        if keitai is None or joutai is None or keitai.get("id") == joutai.get("id"):
            return []
        # 新しい方（createdAt 降順。欠損・同時刻はリスト後方 = 後に採用された方）を優先
        older, newer = keitai, joutai
        if (keitai.get("createdAt") or "") > (joutai.get("createdAt") or ""):
            older, newer = joutai, keitai
        return [
            CiProposal(
                kind="conflict",
                text=newer.get("text", ""),
                scope=newer.get("scope", "personal"),
                tags=list(newer.get("tags") or []),
                confidence=newer.get("confidence", "med"),
                source=f"ナレッジCI: {older.get('id')} と {newer.get('id')} の矛盾検出",
                target_rule_ids=[str(older.get("id")), str(newer.get("id"))],
                note=CI_CONFLICT_NOTE,
            )
        ]

    @staticmethod
    def _ci_merge_proposals(existing_rules: list[dict], claimed: set[str]) -> list[CiProposal]:
        """(b) 同一 tags かつ text 先頭10字一致の2ルールを統合提案（先頭ペアのみ）。"""
        candidates = [r for r in existing_rules if str(r.get("id")) not in claimed]
        for i, first in enumerate(candidates):
            for second in candidates[i + 1 :]:
                same_tags = sorted(first.get("tags") or []) == sorted(second.get("tags") or [])
                head = CI_MERGE_PREFIX_CHARS
                similar = (
                    (first.get("text") or "")[:head] == (second.get("text") or "")[:head]
                    and (first.get("text") or "") != ""
                )
                if not (same_tags and similar):
                    continue
                # 統合文案 = 情報量の多い方（長い方。同長は先勝ち）
                longer = (
                    first
                    if len(first.get("text", "")) >= len(second.get("text", ""))
                    else second
                )
                rank = {"high": 0, "med": 1, "low": 2}
                stronger = min(
                    (first, second), key=lambda r: rank.get(r.get("confidence", "med"), 1)
                )
                return [
                    CiProposal(
                        kind="merge",
                        text=longer.get("text", ""),
                        scope=(
                            "team"
                            if "team" in (first.get("scope"), second.get("scope"))
                            else "personal"
                        ),
                        tags=list(first.get("tags") or []),
                        confidence=stronger.get("confidence", "med"),
                        source=f"ナレッジCI: {first.get('id')} と {second.get('id')} を統合",
                        target_rule_ids=[str(first.get("id")), str(second.get("id"))],
                        note=CI_MERGE_NOTE,
                    )
                ]
        return []

    @staticmethod
    def _ci_demote_proposals(
        existing_rules: list[dict], signals: list[dict], claimed: set[str]
    ) -> list[CiProposal]:
        """(c) 適用実績なし（applied==0・last_applied_at なし・signals なし）の棚卸し提案。"""
        signalled = {str(s.get("ruleId")) for s in signals}
        proposals: list[CiProposal] = []
        for rule in existing_rules:
            rule_id = str(rule.get("id"))
            if rule_id in claimed or rule_id in signalled:
                continue
            if (rule.get("applied") or 0) > 0 or rule.get("lastAppliedAt"):
                continue
            proposals.append(
                CiProposal(
                    kind="demote",
                    text="",
                    scope=rule.get("scope", "personal"),
                    tags=list(rule.get("tags") or []),
                    confidence=rule.get("confidence", "med"),
                    source=f"ナレッジCI: {rule_id} の棚卸し",
                    target_rule_ids=[rule_id],
                    note=CI_DEMOTE_NOTE,
                )
            )
        return proposals

    @staticmethod
    def _ci_distill_proposals(recent_tasks: list[dict]) -> list[CiProposal]:
        """(d) done かつ未蒸留のタスク（先頭1件）からの新規蒸留提案。"""
        target = next(
            (
                t
                for t in recent_tasks
                if t.get("status") == "done" and not t.get("distilled")
            ),
            None,
        )
        if target is None:
            return []
        human_id = target.get("humanId") or "不明なタスク"
        return [
            CiProposal(
                kind="distill",
                text=CI_DISTILL_TEXT_TEMPLATE.format(title=target.get("title", "")),
                scope="personal",
                tags=list(target.get("labels") or []),
                confidence="med",
                source=f"{human_id} の完了履歴から自動蒸留",
                target_rule_ids=[],
                note=CI_DISTILL_NOTE,
                source_task_id=str(human_id),
            )
        ]


# --- 夜間ナレッジCI（#26）の決定的な文言・しきい値 -----------------------------------
# （#27 との並行開発のためファイル末尾に追記。クラスからは名前参照で解決される）

# merge 判定の「text 類似」= 先頭10字一致（決定的な単純規則）
CI_MERGE_PREFIX_CHARS = 10

CI_CONFLICT_NOTE = (
    "「敬体」と「常体」の方針が矛盾しています。新しいルールを優先した置き換え案です"
)
CI_MERGE_NOTE = "同じタグ・似た文面のルールが併存しています。1件に統合する案です"
CI_DEMOTE_NOTE = "適用実績がないため、アーカイブ（棚卸し）を提案します"
CI_DISTILL_NOTE = "完了タスクに未蒸留の学びがあります"
CI_DISTILL_TEXT_TEMPLATE = (
    "「{title}」で確立した進め方を再利用する（要点サマリー→本文→確認事項の順でまとめる）"
)
