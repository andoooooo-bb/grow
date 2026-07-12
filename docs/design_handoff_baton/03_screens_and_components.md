# 03 · 画面 & コンポーネント仕様

採用は **案B「Signal」**。全画面・全コンポーネントを、寸法・色（§04 参照）・タイポ・状態・**正確なコピー文言** 付きで記す。UIは3つの主要面で構成される：**(A) ボード**、**(B) カード詳細ドロワー**（detail / chat の2モード）、**(C) ナレッジ・オーバーレイ**。

レイアウト全体: 縦 flex。上に固定トップバー(54px)、下に `flex:1` のボディ。ボディは横 flex で「ボード(flex:1, 横スクロール)」＋「ドロワー(412px, 選択時のみ)」。ナレッジ・オーバーレイは `position:fixed` で全面。

---

## 3.1 トップバー（TopBar）

高さ54px、背景 `#1f2430`、左右 padding 20px、左右2グループの space-between。

**左グループ**（gap 10px, 中央揃え）:
- ロゴマーク: 26×26, radius 7px, 背景 `#0f9488`, 白文字 "B" 14px/700。
- "Grow" 15px/700 白, letter-spacing -.01em。
- 縦区切り線 1×15px `#3a4150`。
- "workspace / 個人" 12px, `#8a93a3`, monospace。

**右グループ**（gap 8px, 中央揃え）:
- 「あなたの番 {N}」ピル: 文字 `#f0b66a`, 背景 `rgba(219,139,42,.16)`, padding 5px 11px, radius 7px, 11px/600。先頭にドット6px `#db8b2a`（点滅なし）。**N = owner が human の未完了カード数**。
- 「AI稼働 {N}」ピル: 文字 `#5fd6c9`, 背景 `rgba(15,148,136,.18)`。先頭ドット `#2bd6c4`（**batonpulse点滅**）。**N = status が ai_work か queued のカード数**。
- 「◈ ナレッジ {N}」ボタン: 文字 `#c7bff0`, 背景 `rgba(93,78,176,.24)`, 11px/600。**N = ルール総数**。クリックでナレッジ・オーバーレイを開く。
- アバター: 28×28, radius 8px, 背景 `#2c3340`, 文字 `#aeb6c2` 11px/600 "YK", margin-left 4px。

---

## 3.2 ボード（Board）

横スクロール領域（`overflow-x:auto`）。中に5レーンを gap14px で横並び、padding 18px 20px、高さ100%。

### レーン（Lane）
幅272px固定、縦flex、gap10px、高さ100%。

- **レーンヘッダ**（固定, `flex:none`）: 白背景, 枠 `#e4e7ec`, radius8px, padding 7px 11px, space-between。左にレーン名 12.5px/700 `#2c333d`、右に件数ピル（10.5px, `#8a909a`, mono, 背景 `#f1f3f5`, radius5px, padding 1px 7px）。
- **カードリスト**（`flex:1`, `overflow-y:auto`, gap10px, padding 2px 2px 12px）: カードを縦に並べる。**各カードは `flex:none`**（重要 — これが無いと5枚以上で潰れる。過去バグ）。
- **末尾**: 「＋ カードを追加」破線ボタン（枠 `#cfd4db` 破線, 白背景, radius7px, padding8px, 11.5px, `#9aa0a8`, 左寄せ, `flex:none`）。

レーン全体が **ドロップ先**（onDragOver で preventDefault、onDrop でカードを当該レーンへ移動）。

### カード（Card）
- `position:relative`, 白背景, 枠 `#e6e9ee`, radius8px, padding 11px 12px 11px 13px, 縦flex gap8px, 影 `0 1px 2px rgba(20,24,40,.05)`, `overflow:hidden`, cursor pointer。**draggable**。
- hover: 影 `0 5px 16px rgba(20,24,40,.12)` ＋ `translateY(-1px)`（transition .12–.15s）。
- クリックでそのカードのドロワーを開く。

**左端の色バー**（`position:absolute; left0 top0 bottom0; width3px`）— owner/状態で色分け:
- owner=ai かつ 未完了 → `#0f9488`
- owner=human かつ 未完了 → `#db8b2a`
- done → `#4aa876`

**カード上段**（space-between）:
- 左: アバター(18×18, radius5px) — ai は 背景`#0f9488`白文字"AI"8px/700 / human は 背景`#fbe6cd`文字`#a5701f`"YK"8px/700。続けてタスクID（10px, `#aab0b8`, mono, 例 "T-098"）。
- 右: コメント数（10px, `#9aa0a8`, mono, 先頭にドット5px `#cdd2d8`）。

**タイトル**: 12.5px/500 `#2a2f38`, line-height1.5。

**親タグ**（サブタスクのみ）: 「親: {親タイトル先頭12字…}」チップ, 9.5px, `#8a909a`, 背景`#f1f3f5`, radius5px, padding 2px 7px, 左寄せ。

**ステータスバッジ**（tone別, §04）: 例 AI作業中＝背景`#e1f3f1`文字`#0b7a72`＋点滅ドット`#0f9488`。10px/600, padding 3px 8px, radius5px, ドット5px。tone: work=ティール(点滅) / spec=パープル / attention=アンバー(点滅) / neutral=グレー / done=グリーン。

**進捗バー**（`progress` があるとき, 主に ai_work）: 上に "progress" と "{N}%"（9.5px, `#8a909a`, mono）、下に 4px の trackを `#e8ebef`、fill を `#0f9488`（幅 = N%）, radius3px。

**サブタスク進捗**（`childIds` があるとき, 親カード）: "サブタスク" と "{done}/{total}"（9.5px mono）＋ 4px バー（fill = done/total%）。

**ラベル**: チップ群（9.5px, `#7f858f`, 背景`#eef0f3`, radius4px, padding 2px 6px, mono）。flex wrap gap5px。

---

## 3.3 カード詳細ドロワー（Drawer）

選択時のみ表示。幅412px, 白背景, 左枠 `#e2e6ec`, 縦flex, 影 `-8px 0 30px rgba(20,24,40,.06)`, `batonin` でスライドイン。

### 3.3.1 ドロワーヘッダ（共通, `flex:none`, padding 16px 18px 14px, 下線 `#eef0f3`）
- 上段 space-between: 「{ID} · {レーン名}」10.5px `#aab0b8` mono ／ 閉じる✕ボタン(26×26, 背景`#f1f3f5`, radius7px, `#7a818b`, 14px)。
- タイトル 16px/600 `#222831` line-height1.45。
- ステータス行（gap8px wrap）: ステータスバッジ（大きめ: 11px/600, padding 4px 10px, radius6px, ドット6px, tone色）＋「担当: {Grow (AI) | あなた}」11px `#9aa0a8`。

### 3.3.2 detail モード（`panelMode='detail'`）
縦スクロール領域に以下を順に:

**(a) アクションバー**（`flex:none`, padding 14px 18px, gap8px wrap, 下線 `#f1f3f5`）:
- 「AIにまかせる」= プライマリ: 白文字, 背景`#0f9488`, radius8px, padding 9px 13px, 12px/600, 先頭ドット6px `#bff0ea`。
- 「AIと壁打ち / 分解」= セカンダリ: 文字`#5d4eb0`, 背景`#f1edfb`, 枠`#e3dbf6`, radius8px。
- 「完了にする」: 文字`#3a8a5f`, 背景`#e9f4ee`, 枠`#d3ebdd`, radius8px。

**(b) 適用ルールセクション**（該当ルールがある時, `flex:none`, padding 13px 18px, 下線, 背景 `#fbfbfe`）— **この製品の主戦場のUI**:
- 見出し「◈ AIが着手時に前提にするルール {N}」11px/700 `#5d4eb0`＋件数(mono)。
- ルール行（gap7px, 縦）: 各行 = scopeミニバッジ（「チーム」= 文字`#0b7a72`背景`#e1f3f1` / 「個人」= 文字`#a5701f`背景`#fbe6cd`, 8px/700, radius4px, padding 2px 5px）＋ ルール文 11.5px `#555b66` line-height1.5。
- retrieval のロジック: `tags` が空 or カードの `labels` と交差するルール（§06）。**上限8件・confidence 降順で足切り**（§00 #8）。

**(c) 学習セクション**（`you_review`/`reviewing`/`done` の時, `flex:none`, padding 14px 18px, 下線）:
- 上段 space-between: 「このやり取りから**働き方のルール**を学べます」11.5px `#555b66`（太字部 `#3a3f48`）／「✧ 学ぶ」ボタン（文字`#5d4eb0`, 背景`#f1edfb`, 枠`#e3dbf6`, radius8px, 11.5px/700）。
- 「✧ 学ぶ」押下後 → **候補カード**（枠`#e3dbf6` 1.5px, radius11px, `msgin`）:
  - ヘッダ帯「AIが見つけたルール候補 — 採用でナレッジに追加」（背景`#f1edfb`, 11px/700 `#5d4eb0`）。
  - 候補行: scopeミニバッジ ＋ 候補文(12px `#3a3f48`) ＋ ✕（却下, `#b6bcc6`）＋「採用」ボタン（白文字, 背景`#5d4eb0`, radius6px, 11px/700）。採用でナレッジに追加＆この行が消える。

**(c-2) 成果物セクション**（成果物がある時 = `you_review`/`reviewing`/`done` で execute 済み, `flex:none`, padding 14px 18px, 下線）— **§00 #2 で追加**:
- 見出し「成果物（レポート）」11px/700 `#7a818b`。複数版あれば版セレクタ「v{n}」（最新をデフォルト表示）。
- **Markdown プレビュー**（レンダリング表示。背景`#fafbfc`, 枠`#eceef2`, radius8px, padding 12px 14px, `#3a3f48`）。
- 操作: 「編集」（Markdown を textarea で直接編集して保存＝新版）／「再生成」（コメントの修正指示を反映して execute を再実行）。**リッチエディタは持たない**（§00 #12）。
- 保存/取得は `artifacts` テーブル（最大 version が最新, §02.6）。

**(d) サブタスクセクション**（`childIds` がある時, `flex:none`, padding 14px 18px, 下線）:
- 見出し「サブタスク {done}/{total}」11px/700 `#7a818b`。
- 子行（枠`#eceef2`, radius8px, padding 8px 10px, hover `#fafbfc`, クリックで子カードのドロワーへ）: 担当アバター(17×17) ＋ タイトル(12px `#3a3f48`) ＋ 右にステータスラベル(9.5px `#9aa0a8`)。

**(e) アクティビティスレッド**（`flex:1`, padding 16px 18px, gap14px）:
- 見出し「アクティビティ」11px/700 `#7a818b`。
- コメント行（`msgin`）: 左にアバター(24×24, radius7px, ai=ティール"AI" / human=アンバー"YK") ＋ 右に「{Grow | あなた}」10.5px/600 `#8a909a` と 本文バブル（背景`#f6f8fa`, radius9px, padding 9px 11px, 12.5px `#3a3f48` line-height1.6）。

**(f) コンポーザ**（`flex:none`, padding 12px 16px, 上線, 背景`#fafbfc`）:
- 入力欄（枠`#dde2e8`, radius9px, padding 10px 12px, 12.5px, placeholder「コメントで依頼・指示を残す…」）＋「送信」ボタン（ダーク: 背景`#1f2430`白文字, radius9px）。Enterでも送信。

### 3.3.3 chat モード（`panelMode='chat'` — 壁打ち）
- ヘッダ帯（背景`#f1edfb`, 下線`#e7e0f7`）: 「● 壁打ちチャット — 分解の意識合わせ」12px/600 `#5d4eb0`（ドット`#7a6ad0`）＋「← 戻る」ボタン（白背景, `#7a6ad0`, 11px/600）で detail へ戻る。
- メッセージ領域（`flex:1`, scroll, gap14px）: 壁打ちメッセージ（AIアバターは**パープル**`#5d4eb0`"AI", 本文バブル背景`#f6f4fc`, `white-space:pre-wrap`）。
- **分解候補**（AIが提示したら, `msgin`, 枠`#e3dbf6` 1.5px, radius12px）:
  - ヘッダ「提案された分解 — {N} 件」（背景`#f1edfb`, 11.5px/700 `#5d4eb0`）。
  - 候補行: 担当ミニバッジ（AI=`#0b7a72`/`#e1f3f1`、あなた=`#b5781f`/`#fcefe0`）＋ 名称(12px)。
  - 「この内容でボードに反映する」プライマリボタン（白文字, 背景`#5d4eb0`, radius9px, 12.5px/700, 幅いっぱい）＋ 補足「反映後、AIが着手できるサブタスクから自動で進めます」10.5px `#9aa0a8` 中央。
- コンポーザ: placeholder「前提や要望を伝える…」＋「送信」（パープル 背景`#5d4eb0`）。

---

## 3.4 ナレッジ・オーバーレイ（Knowledge Overlay）

トップバー「◈ ナレッジ」で開く。`position:fixed inset0`, 遮蔽`rgba(20,24,40,.42)`, 上寄せ中央（padding 56px 20px）, `msgin`。遮蔽クリックで閉じる（内側はクリック伝播停止）。

**パネル**: 幅720px（max100%, max-height84vh）, 白, radius14px, 影`0 30px 80px rgba(20,24,40,.35)`, 縦flex。

**ヘッダ**（`flex:none`, padding 18px 22px, 下線）:
- 「ナレッジ — 学習した働き方」16px/700 `#222831`。
- 説明「AIは作業を始める前に、ここのルールを自動で読み込んでから動きます。タスクの履歴から少しずつ蓄積され、あなた専用に賢くなっていきます。」12px `#8a909a` line-height1.65。
- 右上 閉じる✕（28×28, 背景`#f1f3f5`）。

**ボディ**（scroll, padding 18px 22px, gap22px）— 2セクション:

*あなたのルール*（見出し: アバター"YK"＋「あなたのルール」12.5px/700＋件数mono）:
- ルールカード（枠`#eceef2`, radius10px, padding 12px 13px, 縦flex gap8px）:
  - 上段: 確度ドット7px（high緑/med琥珀/low灰）＋ ルール文13px `#2a2f38`＋ 採用直後は「NEW」バッジ（`#0b7a72`/`#e1f3f1`）。
  - 下段（padding-left 16px, wrap）: 「出典: {source}」10.5px `#9aa0a8`／「適用 {N}回」mono／右端に「チームへ昇格 ↑」ボタン（`#5d4eb0`/`#f1edfb`/枠`#e3dbf6`, 10.5px/600）。押すと scope→team。

*チームのルール（形式知）*（見出し: "◎"アイコン＋「チームのルール（形式知）」＋件数）:
- ルールカード（背景`#f7fcfb`, 枠`#e0efec`）: 同上だが昇格ボタンは無し。

---

## 3.5 レスポンシブ / スケール注記
- プロトタイプはデスクトップ前提（ボードは横スクロール）。MVPもデスクトップ優先。
- モバイル対応する場合、ドロワーは全画面モーダル化、ボードは1レーンずつのスワイプ表示を推奨（本プロトタイプには未実装）。
- ヒット領域は最小44pxを意識（現状ボタンは9px paddingで確保、モバイル化時は要拡大）。
