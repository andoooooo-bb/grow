# 00 · 確定事項 & プラットフォーム前提（最初に読む）

> このドキュメントは、README §4 で「オープンな論点」として残っていた項目を **依頼者との壁打ちで確定した結果** を集約した、実装の単一の真実（single source of truth）です。他の章（§01〜§08）に旧い記述が残っている場合は **本書の決定が優先** します。

**判断軸（依頼者合意）:** すべての未確定項目は次の2軸で決めた。
1. **コスト最小化** — ローカル開発・MVP運用で追加費用を極力出さない。
2. **有名どころ・順当なリソース** — 奇をてらわず、GCPで標準的に選ばれる構成を採る。

---

## 0.1 プラットフォーム前提（最重要・新規確定）

本プロジェクトは **GCP ハッカソン** を前提とする。したがって旧 README §3.0 の「Anthropic Claude 前提」構成を **GCP ネイティブ構成に置き換える**（§07 も全面的に読み替え）。

### 「開発に使うAI」と「プロダクトが実行時に叩くAI」は別物

| 区分 | 何か | 本プロジェクトでの扱い |
|---|---|---|
| **A. 開発ツールとしての Claude Code** | 実装者が**コードを書くとき**に使うアシスタント | ローカル開発で使用（追加費用なし）。**プロダクトのランタイムAIではない。** |
| **B. プロダクトが実行時に叩くAI** | Grow のバックエンドが「AIにまかせる」等で呼ぶLLM | **GCP = Vertex AI 上の Gemini。** ハッカソンのGCPクレジットで賄う。 |

> Claude Code のサブスクリプションは B には使えない（コーディング支援CLIであり、アプリから叩ける汎用APIではない）。B で Claude を使うには別途 Anthropic API の有料クレジットが要るため、GCP前提の本プロジェクトでは採らない。

### ローカルはモック、本番のみ実LLM（コスト最小化）

- LLM呼び出しは **`AiProvider` インターフェイス** で抽象化し、実装を2つ持つ:
  - **`MockProvider`** — プロトの `proposals[id]` / `learnProposals[id]` / setTimeout進行と同じスクリプト化応答。**費用ゼロ・ネットワーク不要・決定的。** ローカル開発と自動テストはこれ。
  - **`VertexGeminiProvider`** — Vertex AI の Gemini を実際に叩く（Google Search グラウンディング付き）。**本番＆デプロイ後の動作検証のときだけ**使う。
- 切替は環境変数 `AI_PROVIDER=mock | gemini` 1個。呼び出し側コードは不変。
- 注意: §06.8 受け入れ基準③「同じ指示を繰り返さなくても2回目のアウトプットにルールが反映される」は、**Mockでは擬似確認まで。真の検証は本番でGeminiを繋いだときのみ**。役割分担 = ローカルは骨格・フロー検証、本番でAI品質検証。

---

## 0.2 確定した GCP スタック（論点#4 / #5 / #6）

コスト最小化 × 順当なマネージド構成。**Redis(Memorystore) を使わない**ことでコストを抑える点がポイント。

```
┌────────────────────────────────────────────────────────────┐
│  Frontend (SPA)  React + TypeScript + Vite                 │
│  状態: Zustand（正規化: cards/lanes/rules）                │
│  DnD: @dnd-kit/core / データ取得: TanStack Query（楽観更新）│
│  リアルタイム: SSE（AIの進捗をサーバ→クライアントへ push）  │
│  ※ ビルド成果物は下記 Cloud Run が静的配信（サービス1本）    │
└────────────────────────────────────────────────────────────┘
        │ REST / SSE
┌────────────────────────────────────────────────────────────┐
│  Backend API  ── Cloud Run（scale-to-zero でコスト最小）    │
│  FastAPI (Python)  ※ Vertex AI Python SDK と親和 / AI/MLチーム│
│  - タスク/コメント/サブタスク/成果物 の CRUD               │
│  - ステータス遷移バリデーション（§05 ステートマシン）       │
│  - AIジョブの enqueue、ナレッジ CRUD と検索                 │
│  - 静的SPAの配信も同一サービスで兼ねる（デプロイ1本＝安価）  │
└────────────────────────────────────────────────────────────┘
     │ enqueue                          │
┌──────────────────────────┐   ┌──────────────────────────────┐
│  Job Runner              │   │  Data                        │
│  Cloud Tasks → Cloud Run │   │  Cloud SQL for PostgreSQL    │
│  worker エンドポイントへ  │   │  （最小ティア / 未使用時停止） │
│  push（Redis不要＝安価）  │   │  - tasks, comments, rules,   │
│  - AI実作業の非同期実行   │   │    artifacts, ai_jobs …      │
│  - リトライ/タイムアウト  │   │  - pgvector（将来: 意味検索） │
└──────────────────────────┘   └──────────────────────────────┘
     │
┌────────────────────────────────────────────────────────────┐
│  AI: Vertex AI Gemini（Function Calling 使用）              │
│  - 分解 / 実作業 / 蒸留（§07）                             │
│  - Web検索は Grounding with Google Search（読み取りのみ）    │
│  - retrieval で該当ルールを system に注入（§06/§07）        │
└────────────────────────────────────────────────────────────┘

補助: Secret Manager（APIキー等） / Artifact Registry（コンテナ） /
      Cloud Build（任意CI） / Cloud Storage（将来: 成果物ファイル） /
      Firebase Authentication or Identity Platform（フェーズ3: 認証）
```

**選定理由（判断軸に沿って）:**
- **Cloud Run**: scale-to-zero で待機コスト0。フロントの静的配信も同一サービスで兼ね、デプロイ対象を1本に絞ってコスト・運用を最小化。
- **FastAPI (Python)**: Vertex AI Python SDK が最も素直。AI/MLチームの標準言語とも親和。Node(Fastify)でも可だが本書はPythonを既定とする。
- **Cloud SQL for PostgreSQL**: GCP標準のマネージドPostgres。最小ティアで開始し、未使用時はインスタンス停止でコスト圧縮。旧仕様のPostgres前提をそのまま満たす。
- **Cloud Tasks（＋Cloud Run worker）**: ジョブキューを Redis/Memorystore なしで実現。§07.2 の「AI実作業は必ずジョブ化」を最小コストで満たす。BullMQ(Redis) は Memorystore 課金が乗るため採らない。
- **SSE**: AI進捗はサーバ→クライアントの単方向pushで足り、WebSocketより実装が軽い。Cloud Run のストリーミング応答で成立。（論点#5確定）
- **Vertex AI Gemini**: ランタイムAI（前掲）。

### モデル割当（論点#6）
コスト最小化のため軽量モデルを既定にし、品質要求の高い実作業のみ上位へ。

| 役割 | 既定モデル | 補足 |
|---|---|---|
| 分解（breakdown）・蒸留（distill） | **Gemini Flash 系**（構造化出力/Function Calling） | 構造化出力は軽量モデルで十分。コスト最小。 |
| 実作業（execute） | **Gemini Pro 系** | レポート品質要求が高い。まず Flash で試し、品質不足なら Pro へ昇格でも可。 |

> 具体的なモデルID・バージョンは**実装時に Vertex AI で GA の最新モデルを確認して選定**する（例として Gemini 2.5 Flash / 2.5 Pro 系を想定。世代更新に追従）。

---

## 0.3 Tier 1 確定事項（コア体験の核）

| # | 論点 | 確定内容 |
|---|---|---|
| **1** | 最初の1ユースケース | **「競合/市場の調査 → Markdownレポート化」** に確定。プロトのシード `T-098`/`T-104`、README §00/§08 第一候補と一致。ナレッジ機構（アウトプットの質向上）が最も映えるユースケース。 |
| **2** | 成果物の置き場・形式 | **専用 `artifacts` テーブル**（Postgres）に **Markdown** で保存。**バージョン管理あり**（再生成・差し戻しで版が増える）。UIはドロワーに成果物プレビュー。ファイル出力/外部連携は非ゴール。詳細は §02。 |
| **3** | AIの実行権限の範囲 | **Web検索（読み取り）のみ許可。** ファイル書き込み・外部API書き込み・ローカル実行はすべて禁止。手段は本番=Gemini の Google Search グラウンディング、ローカル=Mock。読み取り専用によりジョブが冪等に近くなりリトライが安全。 |

### ユースケース①「調査→レポート化」の入出力仕様（フェーズ0成果物）
- **入力**: カードのタイトル＋ラベル＋コメント履歴（調査対象・観点）＋ retrieval したルール。
- **処理**: Google Search グラウンディングで情報収集 → ルールに沿って Markdown レポート生成。
- **成果物**: Markdown レポート（冒頭3行サマリー → 本文 → 比較表 → 出典URL）。K-01/K-03 のルールがそのまま効く構造。
- **完了の定義**: レポート下書きが揃い、人の確認/意思決定が必要な点を明記して `you_review` へハンドオフ。

---

## 0.4 Tier 2〜4 確定事項（推奨案 × 判断軸で確定）

| # | 論点 | 確定内容 |
|---|---|---|
| **7** | DnD とステータス整合（§05.2） | **「完了レーンへドロップ = `done` 化」のみ自動整合**。それ以外の手動DnDはレーン移動のみでステータスは変えない。いずれも §05.6 ステートマシンに反する遷移は不可。 |
| **8** | retrieval 注入件数の上限（§06.3） | **上限8件・confidence 降順で足切り**。personal / team 両方を対象（チーム化後は「owner の personal ＋ workspace の team」）。 |
| **9** | human_id 採番（§02.5） | **workspace 内連番**。タスク=`T-{seq}`、ルール=`K-{seq}`。DB主キーは別に UUID。 |
| **10** | 空・ローディング・エラー文言（§05.5） | §05.5 の推奨をそのまま採用（AI失敗時は `you_todo` に戻し「再試行」導線＋通知、retrieval 0件は該当セクション非表示、ナレッジ空は空状態文言、等）。 |
| **11** | コンポーザのキー挙動（§05.3） | **Enter = 送信 / Shift+Enter = 改行**。 |
| **12** | リッチ成果物エディタの要否（§08 非ゴール） | **MVPは不要**。Markdown を textarea 編集、または人がコメントで修正指示して再生成。 |
| **13** | プロダクト名（§04-1） | **「Grow」の仮称のまま**MVPを進行。ローンチ前に商標・ドメイン空きを確認して確定。 |
| **14** | 蒸留の主戦場（§04-5） | **「AIの実作業アウトプットの質」で確定**（依頼者明言）。壁打ちの高度化は副次。 |
| **15** | モバイル対応（§03.5） | **MVPはデスクトップ専用、非ゴール**。モバイルは将来検討。 |
| **16** | 認証・コスト管理の初期範囲（§06.7/§07.6） | 認証・権限・課金は**フェーズ3**（Firebase Auth / Identity Platform）。ただし**タスク単位のトークン/コスト記録は `ai_jobs` に早期から仕込む**（将来の上限管理の土台）。 |

---

## 0.5 各章の読み替え早見表

| 章 | 旧記述 | 本書での読み替え |
|---|---|---|
| README §3.0 | Anthropic Claude / Node(Fastify) / BullMQ(Redis) 前提のアーキ図 | §0.2 の GCP スタック（Vertex AI Gemini / FastAPI / Cloud Run / Cloud Tasks / Cloud SQL） |
| README §4 | 5つのオープン論点 | 本書で全確定（§0.3, §0.4） |
| §02 | Rule/Task 等のスキーマ | `artifacts` テーブルを追加（§02.6） |
| §07 | 「Anthropic Claude Messages API」「Tool use」 | 「Vertex AI Gemini」「Function Calling」。Web検索は Google Search グラウンディング。`AiProvider` 抽象（mock/gemini）を前提に |
| §07.2 | BullMQ / Worker | Cloud Tasks → Cloud Run worker |
| §08 | スタック確定（React+TS / Fastify or FastAPI / Postgres / BullMQ / Claude） | GCP スタック（§0.2）で確定。ユースケースは §0.3 で確定 |

> ツールスキーマ（`propose_subtasks` / `propose_rules`）は Claude の Tool use と Gemini の Function Calling で **ほぼ同一の JSON Schema** として通用する。§07 の定義はそのまま Gemini の FunctionDeclaration に写像できる。
