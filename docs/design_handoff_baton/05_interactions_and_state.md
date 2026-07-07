# 05 · インタラクション & 状態管理

プロトタイプの挙動を、本実装（正規化ストア＋API＋ジョブ）に落とすための仕様。関数名はプロトタイプの実装名に対応。

---

## 5.1 状態ストア（再掲・要点）

正規化ストア（§02.3）。`cards`（id辞書）/ `lanes`（cardIds配列で順序保持）/ `rules` が真実。UI状態は `selectedId` / `panelMode('detail'|'chat')` / `showKnowledge` / `chat` / `proposal` / `learn` / `drafts`。

**派生値（renderのたび計算, 保存しない）:**
- `youCount` = owner が human かつ status≠done のカード数（トップバー「あなたの番」）。
- `aiCount` = status が `ai_work` または `queued` のカード数（「AI稼働」）。
- `ruleCount` = rules 総数。
- カードの `owner`/`tone`/`label` = `STATUS_META[status]` から導出。
- サブタスク進捗 = `childIds` のうち done の数 / 総数。

---

## 5.2 レーン ↔ ステータスの関係

レーンとステータスは別概念だが、操作は両方を動かす。標準の対応（MVPのバリデーション指針）:

| 操作 | status | lane |
|---|---|---|
| AIにまかせる | `ai_work` | progress |
| AI完了ハンドオフ | `you_review` | review |
| 壁打ち開始 | `spec`（任意, 表示はchat） | 変えない |
| 分解を反映（親） | `ai_work` | progress |
| 分解を反映（AI子・先頭） | `ai_work` | todo→（着手表現）※プロトはtodoに置き進捗10% |
| 分解を反映（AI子・残り） | `queued` | todo |
| 分解を反映（人子） | `you_todo` | todo |
| 完了にする | `done` | done |
| 手動DnD | 変えない※ | ドロップ先 |

※ **DnD整合（§00 #7 で確定）:** **「完了レーンへドロップ = `done` 化」のみ自動整合**する。それ以外の手動DnDはレーン移動のみでステータスは変えない。いずれも §5.6 ステートマシンに反する遷移は不可（違反する移動はロールバック）。

---

## 5.3 主要ハンドラの挙動（プロトタイプ準拠）

### select(id) / closePanel()
`selectedId` を設定し `panelMode='detail'`。閉じるは `selectedId=null`。

### move(id, toLaneKey)
全レーンから id を除去 → 対象レーンの cardIds 末尾へ追加。本実装は `order_in_lane` を再計算し、楽観的更新 → API `PATCH /tasks/:id {laneKey, orderInLane}`。

### assignAI(id) — §01.5
1. `rules = relevantRules(card)`（§06 の retrieval）。
2. patch status=`ai_work`, progress=0 → move(id,'progress')。
3. コメント投稿: ルール有→「承知しました。あなた／チームのルール『{rules[0].text}』ほか計{n}件を前提に着手します。」／無→「承知しました。着手します。」
4. 適用ルールの `applied++`（本実装は `rule_applications` にも記録）。
5. （本実装）AIジョブ enqueue。**プロトタイプの擬似進行**: 1600ms後 progress=45＋「作業を進めています…（途中経過を共有します）」／4200ms後 status=`you_review` progress=100 → move review ＋「完了しました。学習済みのルールに沿って仕上げています。レビューをお願いします。」

### markDone(id)
patch status=`done`, progress=undefined → move(id,'done')。

### startChat(id) / backToDetail()
`panelMode='chat'`。chat 履歴が空なら AI の初期質問を投入（`greetings[id]` があればそれ、無ければ汎用「このタスクですね。進め方を一緒に詰めましょう。やりたいこと・前提を教えてください。」）。戻るは `panelMode='detail'`。

### sendChat() — §01.6
1. 人メッセージを chat[id] に追加、入力クリア。
2. （プロト 850ms後 / 本実装 AI応答後）AI応答「ありがとうございます、イメージできました。いただいた前提をふまえ、次のように分解するのはいかがでしょう。」＋ `proposal[id]` に分解候補セット（`proposals[id]` があればそれ、無ければ汎用4件: 要件・前提の整理(AI)/たたき台の作成(AI)/内容の確認・決定(人)/仕上げ(AI)）。

### confirmBreakdown() — §01.6 step5
- `proposal[id]` の各項目を子カード化（新ID採番）。owner=ai→`queued`、owner=human→`you_todo`。
- **最初の ai 子のみ** `ai_work`＋progress10＋コメント「まずこのサブタスクから着手します。」
- 親: status=`ai_work`, childIds セット, コメント「{n}件のサブタスクに分解してボードに反映しました。着手できるものから進めます。」
- レーン: 子は todo 末尾へ、親は progress 先頭へ。`proposal[id]` を消す。chat に「ボードに反映しました。進行中のサブタスクから順に進めます。」を追記し `panelMode='detail'`。

### learnFrom(id) / adoptLearn(id,tmp) / dismissLearn(id,tmp) — §01.7, §06
- learnFrom: `learnProposals[id]`（無ければ汎用1件, low, tags=カードのlabels）を `learn[id]` にセット（各に一時ID付与）。
- adoptLearn: 候補1件を rules に追加（新ID `K-xx`, applied0, isNew true, source=`{taskId} から学習`）、`learn[id]` から除去、カードにコメント「ナレッジに追加しました:『{text}』次回から自動で前提にします。」
- dismissLearn: `learn[id]` から除去。

### promoteRule(ruleId) — §01.8
対象ルールの scope を `team` にし isNew=true（NEW再表示）。本実装は `PATCH /rules/:id {scope:'team'}`。

### postComment() / onDraftInput / Enterキー
入力を human コメントとして追加、入力クリア。**Enter=送信 / Shift+Enter=改行**（§00 #11 で確定）。

### openKnowledge() / closeKnowledge() / stop(e)
`showKnowledge` トグル。オーバーレイ内クリックは伝播停止で誤クローズ防止。

### addCard(laneKey)
新ID採番、status=`breakdown`、AIコメント「タイトルと、やりたいことを教えてください。大きければ壁打ちで分解しましょう。」を付けて当該レーン末尾へ、ドロワーを開く。

---

## 5.4 リアルタイム & 楽観的更新（本実装）
- 人の操作（DnD・コメント・採用など）は **楽観的更新**（即UI反映→API→失敗ならロールバック＋トースト）。
- AIの進捗・完了は **サーバ起点**。ジョブがコメント/ステータス/成果物を更新し、**SSE**（§00 #5 で確定）でクライアントへ push。開いているドロワーはリアルタイムに増える。
- 同時編集（チーム化後）に備え、更新は `updated_at` で楽観ロック。

---

## 5.5 空・ローディング・エラー状態（本実装で追加 — §00 #10 で採用確定）
プロトタイプは省略。実装では以下をそのまま採用:
- **AI着手中**: 進捗バー＋「作業中」バッジ（点滅ドット）。長時間ならジョブの経過時間表示。
- **AI失敗**: カードを `you_todo` 等に戻し、コメントに失敗要因＋「再試行」ボタン。人へ通知（§07 リトライ）。
- **retrieval 0件**: 適用ルールセクションは非表示（プロト同様）。「まだルールがありません」等は出さない。
- **空レーン**: 「＋ カードを追加」だけ表示。
- **ナレッジ空**: 各セクション見出し＋「まだありません。タスクを完了して『✧ 学ぶ』で追加できます」の空状態文言（MVPで追加推奨）。

---

## 5.6 ステータス・ステートマシン（許可される遷移）

```
breakdown ─(壁打ち)→ spec ─(分解反映)→ [子: queued/you_todo/ai_work], 親: ai_work
queued ─(AIにまかせる/依頼)→ ai_work
spec ─(AIにまかせる)→ ai_work
you_todo ─(人が着手・完了)→ done  /  ─(AIに委任)→ ai_work
ai_work ─(AI完了)→ you_review
you_review ─(レビュー開始)→ reviewing  /  ─(承認)→ done  /  ─(差し戻し)→ ai_work
reviewing ─(承認)→ done  /  ─(差し戻し)→ ai_work / you_todo
done ─(再オープン)→ 任意（管理操作）
```

- **不変条件**: `progress` は `ai_work` のときのみ意味を持つ（それ以外は null 化）。
- `you_review` / `you_todo` / `reviewing` / `breakdown` / `spec` は owner=human（＝「あなたの番」に数える）。
- `ai_work` / `queued` は AI稼働カウンタに数える。`done` はどちらにも数えない。
