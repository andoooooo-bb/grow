# 本番動作検証チェックリスト（#16）

デプロイ後（`infra/30_deploy.sh` 完了後）に、**実 Vertex AI Gemini** で受け入れ基準を確認する手順。
基準は `docs/design_handoff_baton/06_knowledge_learning_system.md` §6.8 と
`08_mvp_roadmap.md` フェーズ1/2。特に **§6.8③「2回目のアウトプットへのルール反映」は
Mock では検証不能**（00 §0.1 注記）で、本チェックリストが初めての真の検証となる。

前提: `infra/20_migrate.sh --seed` 済み（シードの T-104 / T-098 が存在すること）。
サービス URL は `gcloud run services describe grow --region $REGION --format 'value(status.url)'`。

## ① ボード表示（フェーズ1 土台）

- [ ] ブラウザでサービス URL を開くと SPA が表示される（Cloud Run 1サービスで静的配信）
- [ ] 5レーン（バックログ/TODO/進行中/レビュー/完了）とシードのカード（T-104, T-098 等）が表示される
- [ ] カードをクリックするとドロワー（詳細）が開き、コメント履歴が見える
- [ ] カードの DnD でレーン移動ができ、リロード後も維持される（DB 永続化）

## ② 実レポート生成（フェーズ1 受け入れ基準 / ユースケース①「調査→レポート化」）

- [ ] T-104 のドロワーで「AIにまかせる」を押す → カードが**進行中**レーンへ移り、進捗が更新される（SSE）
- [ ] 数分以内に**レビュー**レーンへ遷移し、ハンドオフコメント（確認してほしい点の明記）が付く
- [ ] 成果物（Markdown レポート）が開ける。Mock の定型文ではなく**実際の調査内容**であること
- [ ] レポート構成が入出力仕様どおり: 冒頭3行サマリー → 本文 → 比較表 → **出典URL**（§00 §0.3。
      出典があれば Google Search グラウンディングが効いている証拠）
- [ ] 人がコメントを書き込める（人の介入）

## ③ 学ぶ → 採用 → ナレッジ（§6.8-1）

- [ ] ②のタスクを**完了**にし「✧ 学ぶ」を押す → ルール候補（propose_rules）が提案される
- [ ] 候補を1件**採用**する → ナレッジ一覧オーバーレイに **NEW** バッジ付きでルールが増えている
- [ ] 採用したルールの出典（どのタスク由来か）・確度・適用回数が表示される

## ④ 2回目の実行でルールが効く（§6.8-2/3 ← 本番でしか検証できない核心）

- [ ] ③のルールの tags に一致する新タスク（または T-098 等の該当シード）で「AIにまかせる」を押す
- [ ] 着手コメントで AI が「**あなた／チームのルール（◯件）を前提に着手します**」と宣言し、
      UI の適用ルール表示に③で採用したルールが含まれる（§6.8-2）
- [ ] **同じ指示をコメントで繰り返していないのに**、2回目のアウトプット（レポート）に
      ③のルール内容が反映されている（§6.8-3。例: ルールが「比較表を必ず入れる」なら表が入っている）
- [ ] DB でも適用が記録されている:
      `select applied_rule_ids from ai_jobs order by created_at desc limit 1` が空でない

## ⑤ 壁打ち → 分解（フェーズ2 / §01.6, §07.4）

- [ ] 適当なタスクで chat モードに切り替え、壁打ちメッセージを送ると AI が応答する（実 Gemini）
- [ ] 分解を依頼すると `propose_subtasks` によるサブタスク案が提示される
- [ ] 「この分解で進める」（confirmBreakdown）で子カードが生成され、先頭の AI 担当子タスクが自動着手する

## ⑥ 運用面の確認（トークン記録・ジョブ保護）

- [ ] `ai_jobs` にトークン使用量が記録されている（コスト可視化 §07.6）:

  ```bash
  # cloud-sql-proxy 経由で接続して確認（infra/20_migrate.sh と同じ要領）
  select kind, status, input_tokens, output_tokens, cost_usd
  from ai_jobs order by created_at desc limit 5;
  ```

  `input_tokens` / `output_tokens` が null でないこと（実 Gemini の usage が入る）

- [ ] worker エンドポイントが保護されている（#16）: トークン無しの直叩きが **403** になる

  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -X POST "$SERVICE_URL/internal/jobs/run" \
    -H 'Content-Type: application/json' -d '{"jobId":"00000000-0000-4000-8000-000000000000"}'
  # => 403
  ```

- [ ] ジョブ失敗時の再試行が Cloud Tasks に委ねられている:
      `gcloud tasks queues describe grow-jobs --location $REGION` で maxAttempts=4 を確認

## 全項目クリア後

- Issue #16 をクローズし、フェーズ2 受け入れ完了（= §6.8 全達成）を記録する
- 依頼者のドッグフーディング開始（08 フェーズ2: 毎日使ってコンセプトの正否を体感する）
- 未使用時間帯のコスト圧縮は `infra/README.md` の「コスト」節（Cloud SQL 停止）を参照
