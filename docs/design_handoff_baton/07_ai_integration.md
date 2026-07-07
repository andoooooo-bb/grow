# 07 · AI連携（モデル・プロンプト・ツール）

AIが担う3つの役割 **① 実作業（execute）② 分解（breakdown）③ 蒸留（distill）** ごとに、モデル選定・システムプロンプト・ツールスキーマ・実行基盤を示す。プロトタイプのAI応答はすべてスクリプト化モックなので、ここが本実装の中核。

> **プラットフォーム前提（§00 で確定）:** 本プロジェクトは GCP ハッカソン前提。ランタイムAIは **Vertex AI 上の Gemini**（旧記述の「Anthropic Claude」は読み替え）。ローカル開発・自動テストは **費用ゼロの Mock**。両者は `AiProvider` インターフェイスで抽象化し `AI_PROVIDER=mock | gemini` で切替（§7.0）。実LLMを叩くのは本番＆デプロイ後の動作検証時のみ＝コスト最小化。

---

## 7.0 AiProvider 抽象（mock / gemini 切替）

LLMを叩く箇所は必ずこのインターフェイス越しにする。呼び出し側（ジョブ・API）は実装を知らない。

```ts
interface AiProvider {
  execute(task: Task, rules: Rule[], history: Comment[]): Promise<{ md: string; usedRuleIds: string[]; usage: TokenUsage }>;
  proposeSubtasks(task: Task, chat: ChatMessage[], rules: Rule[]): Promise<{ subtasks: SubtaskProposal[]; usage: TokenUsage }>;
  proposeRules(task: Task, history: Comment[], chat: ChatMessage[]): Promise<{ rules: RuleProposal[]; usage: TokenUsage }>;
  chatReply(task: Task, chat: ChatMessage[], rules: Rule[]): Promise<{ text: string; usage: TokenUsage }>; // 壁打ちの応答
}
```

- **`MockProvider`** — プロトの固定応答（`proposals[id]` / `learnProposals[id]` / `greetings[id]` / setTimeout進行）をそのまま返す。ネットワーク不要・決定的・費用ゼロ。§7.7 の対応表がそのまま実装対象。
- **`VertexGeminiProvider`** — 下記 §7.1 の Gemini 実装。`usage`（トークン数）を返し `ai_jobs` に記録（§00 #16 / §7.6）。

---

## 7.1 モデルとSDK
- **Vertex AI Gemini**（Function Calling 使用）。GCP の Vertex AI Python SDK（FastAPI バックエンドから）で呼ぶ。
- **モデル割当（§00 #6・コスト最小化）:** 分解・蒸留のような構造化出力は **Gemini Flash 系**で十分。実作業（レポート生成など品質要求が高いもの）は **Gemini Pro 系**。まず Flash で試し、品質不足なら execute のみ Pro へ昇格でも可。具体モデルIDは実装時に Vertex AI で GA の最新（例: Gemini 2.5 Flash / 2.5 Pro 系）を確認して選定。
- 出力の構造化には **Function Calling（tool/function declaration）** を使い、JSONを直接受け取る（自由文パースを避ける）。Claude の Tool use と**ほぼ同一の JSON Schema** が通用する（§7.4/§7.5 の定義をそのまま FunctionDeclaration に写像）。
- **Web検索は Grounding with Google Search**（Vertex AI Gemini の機能）。自前クローラ不要・読み取りのみ（§00 #3 / §7.3）。出典URLもグラウンディングのメタデータから取得できる。
- サーバサイド（Cloud Run）からのみ呼ぶ。認証は GCP のサービスアカウント / ADC。APIキーをクライアントに出さない。シークレットは Secret Manager。

---

## 7.2 実行基盤（ジョブ）— Cloud Tasks + Cloud Run worker
AIの実作業は同期リクエストで完結させない。**必ずジョブ化**（§00 §0.2 / §03.0）。GCP構成では **Cloud Tasks** にenqueueし、**Cloud Run の worker エンドポイント**へ push する（Redis 不要＝コスト最小）。
- `POST /tasks/:id/assign-ai` → `ai_jobs` を作成し **Cloud Tasks にタスク投入**、即200を返す。
- Cloud Tasks が worker エンドポイント（例 `POST /internal/jobs/run`）を叩き、ジョブを実行:
  1. retrieval（§06.3、上限8件・confidence降順 §00 #8）で該当ルールを収集、`applied_rule_ids` に記録。
  2. execute を `AiProvider` 経由で実行（Gemini + Google Search グラウンディング。複数ステップを伴う場合あり）。
  3. 進捗・中間結果を `comments` に追記 → **SSE** で push。
  4. **成果物（Markdown）を `artifacts` に新版として保存**（§02.6）。
  5. 完了で status=`you_review`（or 継続要人手なら `you_todo`）に更新。トークン/コストを `ai_jobs` に記録（§00 #16）。
- **リトライ**: 失敗時は指数バックオフでN回（Cloud Tasks の再試行設定を利用）。最終失敗はカードを人へ戻し（`you_todo`）、失敗コメント＋「再試行」導線、人へ通知。読み取り専用ゆえ副作用が残らずリトライ安全（§00 #3）。
- **タイムアウト/キャンセル**: 長時間ジョブは上限を設け、人がキャンセル可能に。Cloud Run のリクエストタイムアウトにも留意。
- **レート制御**: Vertex AI のクォータ・同時実行数を Cloud Tasks のディスパッチ設定 / worker 側で制御。

---

## 7.3 ① 実作業（execute）— 主戦場

**system プロンプト（テンプレート）:**
```
あなたは Grow のワークエージェントです。ユーザー（{userName}）の代わりにタスクを実行します。
以下は、これまでの履歴から学習した「このユーザー／チームの働き方のルール」です。
明示的に指示されなくても、必ずこれらを前提に作業してください。

# 適用ルール（優先度: 高→低）
{for each retrieved rule}
- [{scope}/{confidence}] {rule.text}   （出典: {rule.source}）
{end}

# タスク
タイトル: {task.title}
ラベル: {task.labels}

# これまでのやり取り（時系列）
{comments as transcript}

# 指示
- ルールに従って成果物を作成する。
- 人にしか判断できない点・レビューが必要な点に達したら、勝手に決めず、その旨を明記して人にハンドオフする。
- 進捗は簡潔に共有する。絵文字は使わない（※ルールに従う）。
```

**運用:**
- retrieval 結果が空なら「適用ルール」節は省く。
- **成果物は Markdown レポート**（§00 #1 で確定した「調査→レポート化」）。冒頭3行サマリー→本文→比較表→出典URL。`artifacts` に新版保存（§02.6）。ファイル/外部連携は非ゴール。
- **情報収集は Google Search グラウンディング**（読み取りのみ）。出典URLはグラウンディングのメタデータから引き、レポート末尾に明記（K-03等のルールと整合）。
- 出力後、どのルールを使ったかを `rule_applications` に記録し、UI で「K-01, K-03 に基づき作成」と説明可能にする。
- ハンドオフ時のコメント例（プロト準拠）:「完了しました。学習済みのルールに沿って仕上げています。レビューをお願いします。」

---

## 7.4 ② 分解（breakdown）— 壁打ち＋サブタスク提案

2段階。(a) 壁打ちの対話、(b) 分解候補の構造化出力。

**(a) 壁打ち system:**
```
あなたは Grow の計画エージェントです。大きい/抽象的なタスクを、実行可能な小さいサブタスクへ分解する手伝いをします。
まず、分解に必要な前提を 3 点以内の的確な質問で確認してください（冗長にしない）。
{適用ルールがあれば §7.3 と同様に注入}
タスク: {title} / ラベル: {labels}
```
初回質問の例（T-130 ポートフォリオ刷新）: 「①公開時期は？ ②既存資産は流用？ ③一番見せたい実績は？」。

**(b) 分解の構造化出力 — tool `propose_subtasks`:**
```json
{
  "name": "propose_subtasks",
  "description": "壁打ちで合意した内容に基づき、タスクを実行可能なサブタスクへ分解する",
  "input_schema": {
    "type": "object",
    "properties": {
      "subtasks": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "title": { "type": "string", "description": "サブタスクの短い題名" },
            "owner": { "type": "string", "enum": ["ai", "human"],
                       "description": "AIが実行可能なら ai、人の判断/作業が必須なら human" },
            "rationale": { "type": "string", "description": "なぜこの担当か（任意）" }
          },
          "required": ["title", "owner"]
        }
      }
    },
    "required": ["subtasks"]
  }
}
```
- 出力を §05 `confirmBreakdown` の入力にする（人が「反映する」を承認 → 子カード生成）。
- owner 判定は「人にしかできないか（意思決定・レビュー・対外・実世界作業）」で AI/human を振り分ける。

---

## 7.5 ③ 蒸留（distill）— 履歴からルール抽出

**system:**
```
あなたは Grow の学習エージェントです。1つのタスクの履歴を読み、
「今後の作業に再利用できる、このユーザー／チームの働き方のルール」を抽出します。

# 抽出の原則
- 再利用可能で検証可能な、一般化された指示だけを抽出する（この1回限りの内容は除く）。
- 差し戻し・修正・繰り返された指示は特に良い材料。
- 固有名詞・機密・個人情報はルール文に含めない（一般化する）。
- 各ルールに scope（personal/team）と tags（対象を絞るなら）と confidence を付ける。
- 3件以内。無ければ空で良い（無理に作らない）。

# タスク履歴
{title, labels, comments, chat, 差し戻しの有無}
```

**tool `propose_rules`:**
```json
{
  "name": "propose_rules",
  "description": "タスク履歴から再利用可能な働き方のルールを抽出する",
  "input_schema": {
    "type": "object",
    "properties": {
      "rules": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "text": { "type": "string", "description": "命令形・検証可能な粒度のルール文" },
            "scope": { "type": "string", "enum": ["personal", "team"] },
            "tags": { "type": "array", "items": { "type": "string" },
                      "description": "適用を絞るラベル。全タスク共通なら空配列" },
            "confidence": { "type": "string", "enum": ["high", "med", "low"] },
            "source": { "type": "string", "description": "根拠（例: 2回同じ修正があった）" }
          },
          "required": ["text", "scope", "tags", "confidence"]
        }
      }
    },
    "required": ["rules"]
  }
}
```
- 出力を §01.7 の「学ぶ」候補として提示 → 人が採用/却下。
- **フェーズ3の自動蒸留**では、複数タスクをまとめて入力し、既存ルール一覧も渡して「新規/統合/矛盾」を判定させる（§06.6）。矛盾・重複は別ツール（例 `reconcile_rules`）で解決案を出させ、人 or 閾値で確定。

---

## 7.6 レート・コスト・失敗の扱い
- 呼び出しは分解/実作業/蒸留とも**サーバ（Cloud Run）経由・ジョブ内**。クライアント直呼びしない。Vertex AI 認証はサービスアカウント/ADC。
- 実作業は入力（履歴＋ルール）が長くなりがち → 履歴要約やルール件数上限（8件・§00 #8）でトークン管理。
- 失敗（Vertex API/grounding 実行）は is_error として扱い、リトライ→最終失敗で人へハンドオフ（§7.2）。
- **コスト可視化（§00 #16）: タスク単位のトークン/コストを `ai_jobs.input_tokens/output_tokens/cost_usd` に MVP から記録**（`AiProvider` の `usage` を保存）。将来チーム課金・上限管理の土台（§08 フェーズ3）。
- ローカルは Mock なので費用ゼロ。実LLMコストは本番＆動作検証時のみ発生（GCPクレジットで賄う）。

---

## 7.7 プロトタイプのモック対応表（実装時に置換する箇所）
| プロトのモック | 本実装 |
|---|---|
| `assignAI` の setTimeout 進行 | execute ジョブ＋進捗イベント（§7.2/7.3） |
| `sendChat` の 850ms後固定応答 | 壁打ち AI 応答（§7.4a） |
| `proposals[id]`（固定の分解候補） | `propose_subtasks` の出力（§7.4b） |
| `learnProposals[id]`（固定の蒸留候補） | `propose_rules` の出力（§7.5） |
| `relevantRules`（タグ一致） | そのまま流用可。将来ベクタ検索へ拡張（§6.3） |
| `greetings[id]`（固定の初期質問） | 壁打ち初回のAI質問生成（§7.4a） |
