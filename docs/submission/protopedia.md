# ProtoPedia 提出下書き — Grow

ProtoPedia の入力項目に対応した下書きです。各項目をそのままコピー＆ペーストできる形にしています。本命案には ⭐ を付けています。

Grow のコンセプトは **「使うほど、人とAIが共に育つタスクボード」**。人とAIが同じカードの上で協働する **場** です。人が指示を残し、AIが作業して履歴（コメント・成果物）を残し、その履歴を見た人がまた指示や判断を重ねる——**やり取りの履歴が積み重なるほど、AIは「自分のやるべきこと」を学んで成長し、人もAIへの任せ方や判断の線引きを磨いていく**。片方だけが賢くなるのではなく、人にもAIにも成長の場になるのが Grow の核です。

---

## 作品タイトル（3案）

1. ⭐ **Grow — 使うほど、人とAIが共に育つタスクボード**
2. Grow — 履歴が積み重なるほど、AIも人も育つ協働タスクボード
3. Grow — 人とAIが履歴を重ねて共に成長する、AIエージェント協働ボード

> メモ: 「Grow」は "使うほど人とAIが共に育つ" というコンセプトを名前にした仮称（商標・ドメインは未確定）。サブタイトルで「双方向の成長」という価値を1行で伝える構成にしています。

---

## 作品概要（100文字以内・SNS表示用）

文字数は Python の `len()` で実測した値です（全角・半角・記号を含む文字数）。

- ⭐ **案A（本命・93字）:**
  使うほど、人とAIが共に育つタスクボード。カードに残る履歴からAIは働き方を学び、人はAIへの任せ方を磨く。Google CloudのGeminiエージェントが出典付きで実務を進めます。

- **案B（91字）:**
  人とAIが履歴を積み重ねて協働するタスクボード。使うほどAIは自分の仕事を学び、人も指示の出し方が磨かれる。共に育つ場をGoogle CloudのGeminiエージェントが支えます。

- **案C（70字）:**
  タスク管理とAI実行を看板に一元化。履歴からAIは働き方のルールを学び、人はAIへの任せ方を磨く。使うほど人とAIが共に育つ協働ボードです。

---

## システム構成

Cloud Run 1サービスで API と SPA 静的配信を兼ね、AIの実作業は Cloud Tasks でジョブ化して同一サービスの worker エンドポイントへ push する構成です（Redis 不要・scale-to-zero でコスト最小）。人とAIが積み重ねた履歴を単一のデータストアに集約し、AIが次に着手するときはその履歴から学んだルールを前提として読み込みます。

**構成図（レイヤ）**

```
[ブラウザ] React SPA（Zustand / dnd-kit / TanStack Query）
    │  REST ／ ← SSE（AIの進捗・成果物ストリーミングを push）
[Cloud Run “grow”（1サービス）]
    ├─ FastAPI：REST API ＋ SPA静的配信
    └─ worker：POST /internal/jobs/run（トークン保護）
    │  enqueue → [Cloud Tasks（grow-jobs）] → worker へ push（指数バックオフ再試行）
    │  [Cloud Scheduler（毎日03:00 JST）] → 夜間ナレッジCI を叩く
    ↓
[Vertex AI Gemini] execute=2.5-pro／判断・分解・蒸留・レビュー・受付=2.5-flash
    │  Function Calling（構造化出力）＋ Grounding with Google Search（読み取りのみ・出典URL取得）
[Cloud SQL for PostgreSQL] tasks / comments / rules / artifacts / ai_jobs …（履歴とナレッジの単一ソース）
[Cloud DLP] 個人→チーム昇格時の機微情報スキャン（ガードレール）
補助：Secret Manager（秘匿値）／ Artifact Registry（コンテナ）／ Cloud Build（ビルド）
```

**データフロー（実作業の一例：調査 → レポート化 → 学習）**

1. ユーザーがカードで「AIにまかせる」または指揮者に「オートパイロット」を任せる。
2. FastAPI がそのカードに関係するルールを retrieval（タグ一致・確度降順で上限8件）して `ai_jobs` を作成し、Cloud Tasks に enqueue して即 202 を返す。
3. Cloud Tasks が worker（`/internal/jobs/run`）を叩く。実行エージェントが**履歴から学んだルールを system プロンプトに注入**し、Vertex AI Gemini（2.5-pro）＋ Google Search grounding で情報収集して Markdown レポートを**ストリーミング生成**。進捗は SSE でブラウザへ流れる。
4. レビューエージェント（2.5-flash）が適用ルールを審査基準に成果物を自己採点。基準未達なら構造化した指摘で実行エージェントへ差し戻し（最大2周）。
5. 承認できる品質になったらステータスを「あなたのレビュー待ち（you_review）」にし、次に手を入れるのが人であることを示して人へ引き継ぐ。トークン数と実コスト（USD）を `ai_jobs` に記録。
6. 人が完了カードで「✧ 学ぶ」を押すと、履歴からルール候補を抽出。採用したルールは次の同種タスクの手順2で自動的に前提注入され、**「使うほど賢くなる」ループが閉じる**。
7. 夜間は Cloud Scheduler がナレッジCIを起動し、ルールの重複統合・矛盾検出・確度の昇降格・棚卸し提案を受信箱へ通知（採否は人）。

---

## ストーリー（スライドモード用・`---` 区切り）

### 課題：AIを日常業務に取り入れるときの3つの摩擦

- **ツール遷移の手間** — タスクは管理ツール、AIは別のチャット。文脈をコピペで往復する。
- **毎回の説明の面倒** — 頼むたびに好みや前提を説明し直す。やり取りは使い捨てで、履歴は積み上がらない。
- **AIが勝手に進めすぎる** — 頼んでいない判断まで独断で進み、手戻りになる。役割の線引きが曖昧。

---

### 着想：協働の履歴が積み重なる「場」をつくる

チャットは終われば消える。だが仕事の価値は、一度きりのやり取りではなく**積み重なった履歴**に宿る。そこで、人とAIが同じカードの上で協働する「場」を用意した。人が指示を残し、AIが作業して履歴（コメント・成果物）を残し、それを見た人がまた指示や判断を重ねる。カードに溜まっていくやり取りそのものが資産になる、という発想が出発点。

**「いま誰が動く番か」も一目で分かる。** カード左端の色バー（AI=ティール／人=アンバー）と上部の「あなたの番 N ／ AI稼働 N」カウンタが、**積み重なる履歴の上で次に手を入れるのが人かAIか**を示す。人とAIが交互に履歴を足していくことで、タスクが前に進む。

---

### 価値の核：人もAIも育つ

Grow の核は **双方向の成長** にある。

- **AI側** — 完了したやり取りの履歴から「働き方のルール」を学び、次からは**言われなくても前提として読んでから**着手する。使うほど成果物が自分にフィットしていく。
- **人側** — AIへの任せ方、指示の出し方、どこまで任せてどこから自分が判断するかの線引きが、やり取りを重ねるほどブラッシュアップされる。

片方だけが賢くなるのではなく、**同じ場で人とAIが互いに育つ**。その学びが、この場での実行（タスク遂行）に結実する。

---

### アーキテクチャとエージェント編成：Google Cloud ネイティブ・コスト最小

Cloud Run 1サービスで API と SPA を兼ね、AIの実作業は Cloud Tasks でジョブ化。Redis を使わずキューを実現し、scale-to-zero で待機コストをゼロにした。ランタイムAIは Vertex AI Gemini。

Grow は単一のAIではなく、**役割の異なる6種のエージェントが履歴の上で順に処理を引き継ぐチーム**として動く。受付エージェント（#27）がルート（実行／ヒアリング／分解）を自己判定し、足りなければ自分から質問する。指揮者エージェント（#22）が `decide_next_action` で次の一手を Gemini 自身に判断させ、実行エージェント（#9 / #24）⇄ レビューエージェント（#23）のループ（AIがAIの成果物を突き返す）を回す。完了後はナレッジ抽出エージェント（#13）が学び、夜間メンテナンスエージェント（#26）がルールを腐らせない。暴走を防ぐため、ボード反映と最終承認には**人の承認ゲート**を必ず残した。

---

### 「使うほど賢くなる」の実証（本番の実 Gemini で実測）

本番環境（実 Gemini 稼働中）で、競合調査タスク **T-104「競合SaaS 5社の料金プランを調査」** を実行した。

- 実行エージェントが、履歴から学んだ**ルール4件**（例: レポートは結論→根拠の順・冒頭に3行サマリー／競合調査は料金を表形式にし各項目に出典URLを付ける、など）を**前提に着手すると宣言**。
- Vertex AI Gemini（2.5-pro）＋ Google Search grounding で **出典付きの Markdown レポート**を生成。
- **AIのセルフレビューが2回差し戻し** → そのつど再生成し、品質基準を満たしてから人のレビューへハンドオフ。
- 成果物は **3版**、実コストは **約 $0.02**。

同じ指示を繰り返していないのに、学習済みルールが次の成果物に反映される——**使うほど成果物が自分にフィットしていく**ことを、本番で実測した。

---

### Google Cloud の活用（要件の二重充足）

- 実行基盤 = **Cloud Run**（scale-to-zero）
- AI = **Vertex AI Gemini**（Function Calling ＋ Google Search grounding）＋ **Cloud DLP**（チーム昇格前の機微情報ガード・#29）→ AIサービス要件を二重に充足
- 支える GCP = **Cloud SQL** / **Cloud Tasks** / **Cloud Scheduler**（夜間CI） / **Secret Manager** / **Artifact Registry** / **Cloud Build**、進捗は **SSE** でストリーミング。

---

### 今後

ベクタ検索（pgvector）による意味的な retrieval で、履歴からの学習をさらに精緻化。チーム展開（認証・権限・監査ログ）、個人ルールが複数人に出現したときの自動チーム昇格提案へ。人とAIが共に育つ場を、個人ツールからチームの共有資産へと広げていく。

---

## タグ（5個）

`生成AI` / `Gemini` / `AIエージェント` / `GoogleCloud` / `タスク管理`

> 予備タグ: `VertexAI` / `CloudRun` / `マルチエージェント` / `ナレッジマネジメント`

---

## 開発素材（使用API・ツール）

**Google Cloud:**
- Vertex AI Gemini（`gemini-2.5-pro` / `gemini-2.5-flash`、Function Calling、Grounding with Google Search）
- Cloud Run（API ＋ SPA 静的配信 ＋ ジョブ worker）
- Cloud SQL for PostgreSQL
- Cloud Tasks
- Cloud Scheduler
- Cloud DLP（Sensitive Data Protection）
- Secret Manager / Artifact Registry / Cloud Build

**フロントエンド:** React 19 / TypeScript / Vite / Zustand / @dnd-kit / TanStack Query / remark-gfm / Server-Sent Events

**バックエンド:** Python 3.13 / FastAPI / asyncpg / Pydantic v2 / uv / google-genai / google-cloud-tasks / google-cloud-dlp

**開発ツール:** Docker（ローカルPostgres）/ pytest / vitest / ruff / GitHub

---

## 関連リンク

- GitHub リポジトリ: `https://github.com/andoooooo-bb/grow`
- 本番環境（実 Gemini で稼働中）: https://grow-c6m2ic6jcq-an.a.run.app
- デモ動画: _（提出時に差し替え）_
