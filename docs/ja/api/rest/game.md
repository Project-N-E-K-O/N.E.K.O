# ゲーム API

**Prefix:** `/api/game`

アプリ内ミニゲーム（サッカー、バドミントン……）のバックエンドです。各ミニゲームは汎用的な二人「ボケとツッコミ」方式で駆動されます。すなわち、裏方の **A** レイヤー（ゲームイベントを受け取り、セリフと構造化された制御指示を決めるテキスト専用 LLM）と、表舞台の **B** レイヤー（選ばれたセリフを既存のプロジェクト出力チャネル——音声 / TTS / テキスト吹き出し——を通じて話す、またはミラーする）です。

ほとんどのエンドポイントは `{game_type}` パスパラメータ（例: `soccer`、`badminton`）を取り、同じルートが新しいハンドラなしで新しいゲームへ拡張できます。以下のエンドポイントは **Logs**、**Route lifecycle**、**Interaction**、**Leaderboard** ごとにまとめてあります。

::: info
一部のレスポンスフィールドや挙動はゲーム固有です。たとえばリーダーボードは現状バドミントンのみ実装され、`quick-lines` も `soccer` とバドミントンのみ対応します。それ以外の `game_type` 値は、エラーではなく `ok: false` / スキップのレスポンスを返します。
:::

## Logs

ミニゲーム 1 ラウンドごとの診断ログです。ログはオプトインで、上限があります（保持されるセッション数およびセッションあたりのエントリ数が制限されます）。

### `GET /api/game/logs`

あるゲームセッションの診断ログを JSON で読み取ります。`session_id` を指定しない場合は、利用可能なセッションの一覧を返します。

**Query:** `session_id`、`game_type`、`since`（シーケンスカーソル）、`limit`（デフォルト `300`）。

### `GET /api/game/logs/view`

同じ診断ログの人間が読みやすい HTML ビューです。`session_id` を渡すと単一セッションを表示し、指定しない場合は利用可能なセッション一覧を描画します。

**Query:** `session_id`、`game_type`、`limit`（デフォルト `300`）。

**Response:** HTML ページ（JSON ではありません）。

### `POST /api/game/logs`

セッションに診断ログエントリを追記します（フロントエンドからのログ取り込み。CSRF 検証あり）。

**Body:** `session_id`（必須。別名 `sessionId`）、`game_type`（別名 `gameType`、デフォルト `game`）、`lanlan_name`（別名 `lanlanName`）、およびログエントリの内容。`session_id` がない場合は `{ "ok": false, "reason": "missing_session_id" }` を返します。

### `POST /api/game/logs/enable`

あるセッションの診断ログを手動で有効化します。

**Body:**

```json
{
  "session_id": "round-id",
  "game_type": "soccer",
  "lanlan_name": "character_name"
}
```

**Response:** `{ "ok": true, "session_id": "...", "game_type": "...", "seq": <number> }`。`session_id` が欠落しているか有効化に失敗した場合は `{ "ok": false, "reason": "..." }` を返します。

::: info
これはローカルの更新系エンドポイントで CSRF 検証が行われます。検証失敗時は `{ "ok": false, "reason": "csrf_validation_failed" }` を返します。
:::

## Route lifecycle

「ゲームルート」は、ゲームウィンドウが開いていて主要な外部入力（テキスト / 音声）がゲームへ横取りされている期間を追跡します。1 キャラクターにつき同時にアクティブにできるルートは 1 つだけで、新しいルートを開始するとそのキャラクターの他のアクティブなルートを置き換えます。

### `POST /api/game/{game_type}/route/start`

ゲームウィンドウが開いており、メインウィンドウの入力をこのゲームへルーティングすべきことを宣言します。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** 公開ルート状態を含む `{ "ok": true, ... }`、または `{ "ok": false, "reason": "missing_lanlan_name" }`。

::: info
ここで `game_type` が `new_user_icebreaker` の場合は HTTP 400 で拒否されます。専用の `/api/icebreaker/route/start` エンドポイントを使用してください。
:::

### `GET /api/game/{game_type}/route/state`

あるキャラクター + ゲームタイプの現在の公開ルート状態を読み取ります。

**Query:** `lanlan_name`。

**Response:** `{ "ok": true, "state": { ... } }`。

### `GET /api/game/route/active`

遅れて参加した購読者を現在のゲームウィンドウのルート状態と整合させます（ウィンドウ状態変化はエッジトリガーのため、新しく読み込まれた chat/pet クライアントは最初の「opened」イベントを取りこぼすことがあります）。読み取り専用で、`game_type` に紐づきません。

**Query:** `lanlan_name`。

**Response:** `{ "ok": true, "active": false }`、アクティブな場合は `{ "ok": true, "active": true, "game_type": "...", "session_id": "...", "lanlan_name": "..." }`。

### `POST /api/game/{game_type}/route/drain`

ゲームページが横取りしたメインウィンドウ入力によって生じたバックエンド出力を排出します。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, "outputs": [ ... ], "state": { ... } }`。アクティブなルートがない、または `session_id` がアクティブなルートと一致しない場合は、空の `outputs` リストを返します。

### `POST /api/game/{game_type}/route/voice-transcript`

独立した音声認識（STT）ゲートからの確定テキストを受け取り、ユーザー入力としてゲームへルーティングします。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "transcript": "final recognized text"
}
```

**Response:** `{ "ok": true, "handled": <bool>, "state": { ... } }`、または `{ "ok": false, "reason": "..." }`（例: `missing_transcript`、`missing_lanlan_name`、`invalid_body`）。ルートが非アクティブまたはセッションが一致しない場合、`handled` は `false` となり `reason` が付きます。

### `POST /api/game/{game_type}/route/heartbeat`

退出時のクリーンアップ漏れ検出に使うゲームページのハートビートを更新し、ページの可視性を報告します。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, "active": true, "heartbeat_interval_seconds": <number>, "heartbeat_timeout_seconds": <number>, "state": { ... } }`。一致するルートが見つからない場合は `active: false` を返します。

### `POST /api/game/{game_type}/route/end`

公開 game-end エンドポイントと同じクリーンアップ契約を用いてゲームルートを終了します。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "reason": "route_end"
}
```

**Response:** 終了したルートを説明するクリーンアップ結果（該当する場合はアーカイブ / 試合後情報を含む）。

## Interaction

中核となる「ボケとツッコミ」エンドポイントです。イベントを A レイヤーへ送り、得られたセリフを B レイヤーでミラーまたは発話します。

### `POST /api/game/{game_type}/passive-guard`

サッカー route の受動終了判定を行う bounded classifier です。JSON body は active `session_id`、`lanlan_name` と frontend が収集した短い game context を含みます。現在は `game_type=soccer` のみ実装されています。

応答は `ok`、`recommendedAction`、`exitPromptType` を含み、成功時は分類詳細も返します。未対応 game、timeout、例外は HTTP error ではなく application envelope 内で `observe_more` と `none` へ安全に fallback します。

---

### `POST /api/game/{game_type}/chat`

汎用ゲーム LLM チャットエンドポイント。ゲームイベントを裏方の A レイヤーへ送り、セリフ 1 つと任意の制御指示を受け取ります。

**Body:**

```json
{
  "session_id": "round-id",
  "event": { },
  "lanlan_name": "character_name"
}
```

`event` はゲームが定義する dict で、そのまま LLM へ渡されます。

**Response:**

```json
{
  "line": "the character's line",
  "control": { }
}
```

`control` は任意のゲーム制御指示（例: 機嫌 / 難易度）を運びます。リクエストボディが無効、レート制限、またはルートが非アクティブの場合、レスポンスには `error` または `skipped` フィールドが含まれ、`line` / `control` は空になります。

### `POST /api/game/{game_type}/mirror-assistant`

B レイヤーのテキスト出力: A レイヤーのセリフを TTS を呼ばずに通常のチャット表示へミラーします。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "line": "the line to mirror"
}
```

**Response:** `{ "ok": true, "lanlan_name": "...", "method": "project_text_mirror", ... }`、または `{ "ok": false, "reason": "..." }`（例: `missing_line`、`missing_lanlan_name`、`no_session_manager`）。

### `POST /api/game/{game_type}/speak`

正式な B レイヤー出力: A レイヤーのセリフを既存のプロジェクト TTS パイプラインを通じて発話します。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "line": "the line to speak"
}
```

**Response:** 発話の試行を説明する結果（TTS パイプライン状態を含む）、または `{ "ok": false, "reason": "..." }`。

::: info
ここで `game_type` が `new_user_icebreaker` の場合は HTTP 400 で拒否されます。`/api/icebreaker/speak` を使用してください。
:::

### `POST /api/game/{game_type}/realtime-context`

コンパクトなゲームコンテキストをアクティブな Realtime 音声セッションへ注入します——「非音声情報を Realtime へ入れる」ための、意図的にシンプルな最初の橋渡しで、プロバイダの function-calling 対応を必要としません。

**Body:** `lanlan_name` と、現在のコンテキストを記述するゲームの `state` を含みます。

**Response:** `{ "ok": true, ... }`、または `{ "ok": false, "reason": "..." }`（例: `no_active_realtime_session`、`no_session_manager`）。CSRF 検証が行われます。

### `POST /api/game/{game_type}/quick-lines`

ゲーム開始時にキャラクター固有のクイックラインを生成します。成功するとフロントエンドは組み込みのクイックラインを置き換え、失敗時は組み込みを維持します。

**Body:**

```json
{
  "session_id": "round-id",
  "lanlan_name": "character_name"
}
```

**Response:** `{ "ok": true, "character": "...", "lines": { }, "missing": [ ] }`。バドミントンでキャッシュヒットした場合は、さらに `"cached": true` が付きます。

::: info
対応は `soccer` とバドミントンのみです。それ以外の `game_type` 値は `{ "ok": false, "error": "...", "lines": {} }` を返します。
:::

### `GET /api/game/{game_type}/character`

ゲーム内モデル差し替え用に、現在のキャラクターのモデル情報を返します。各ミニゲームは自身の描画サポートに応じて Live2D、VRM、MMD、または明示的なフォールバックを選びます。

**Query:** `lanlan_name`（任意。省略時は現在のキャラクター）。

**Response:**

```json
{
  "lanlan_name": "character_name",
  "model_type": "live2d",
  "live3d_sub_type": "",
  "live2d_path": "/static/...",
  "mmd_path": "",
  "vrm_path": ""
}
```

### `POST /api/game/{game_type}/end`

1 ラウンドのゲームを終了し、対応する LLM セッションをクリーンアップします。

**Body:**

```json
{
  "session_id": "round-id",
  "lanlan_name": "character_name",
  "reason": "game_end"
}
```

**Response:** 終了したラウンドを説明するクリーンアップ結果（該当する場合はアーカイブ / 試合後情報を含む）。

## Leaderboard

ゲームごとのハイスコアリーダーボードです。現状はバドミントンのみストレージで裏付けられており、それ以外のゲームタイプは空 / 非対応のレスポンスを返します。

### `GET /api/game/{game_type}/leaderboard`

リーダーボードの上位エントリと、呼び出し元の自己ベストを読み取ります。

**Query:** `session_id`、`lanlan_name`、`limit`（デフォルト `10`）、`offset`（デフォルト `0`）。

**Response:**

```json
{
  "ok": true,
  "top": [ ],
  "total_players": 0,
  "total_scores": 0,
  "limit": 10,
  "offset": 0,
  "has_more": false,
  "your_best": null
}
```

非対応のゲームタイプの場合は、同じ構造で `top` が空、カウントがゼロのまま返ります。

### `POST /api/game/{game_type}/leaderboard`

リーダーボードにスコアを送信します。

**Body:**

```json
{ "lanlan_name": "character_name", "session_id": "round-id", "mode": "..." }
```

リクエストボディはそのラウンドのスコア合計（例: `finalScore`）を含める必要があり、サーバーは**その `lanlan_name` / `session_id` / `mode` について自身が記録したセッション合計と照合**します（対局中に予約）。不一致、または不明/期限切れのセッションは `{ "ok": false, "reason": "invalid_session" }`、不正なボディは `invalid_body` を返します。したがってクライアントは任意のスコアを送信できず、進行中のラウンドについてサーバーが実際に記録した合計のみが受理されます。

**Response:** `{ "ok": true, "rank": <number>, "total_players": <number>, "is_personal_best": <bool> }`、または `{ "ok": false, "reason": "..." }`（例: `invalid_session`、`invalid_body`、または非対応のゲームタイプ）。
