# Agent API

**プレフィックス:** `/api/agent`

メインサーバーがブラウザー向けに提供する、ループバック Agent Server のプロキシです。キャラクター/セッションの同期とリモート配備の安全チェックを担当した後、`TOOL_SERVER_PORT` へランタイム操作を転送します。プロキシ障害は通常 `502`、リモート配備での変更ルートはサーバーマシンの操作を防ぐため `501` です。

## 状態とコマンド

### `GET /api/agent/flags`

マスタースイッチと三つの機能スイッチ（キーボード・マウス `computer_use_enabled`、ブラウザー `browser_use_enabled`、ユーザープラグイン `user_plugin_enabled`）を含む Agent Server の flags スナップショットを返します。プロキシ障害は `502` と `success: false` です。

### `POST /api/agent/flags`

旧式の部分更新です。ボディは `{"lanlan_name":"...","flags":{...}}`。キャラクターセッションを更新し、認識したサブフラグを転送します。キャラクター不在は `404`。転送失敗時はローカル flags を安全な無効状態へ戻し、`502` を返します。

### `GET /api/agent/state`

revision、flags、capabilities、notification 状態、進行中のタスク（`active_tasks`）を含む正式な Agent Server 状態を返します。

### `POST /api/agent/command`

推奨される変更入口です。現在のコマンドは次のとおりです。

| コマンド | 追加フィールド | 用途 |
|---|---|---|
| `set_agent_enabled` | `enabled`、任意の `profile` | マスタースイッチを切り替え。オフにすると進行中のタスクをすべて中止 |
| `set_flag` | `key`、`value` | `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled` のいずれかを切り替え |
| `refresh_state` | なし | 新しい状態を返し、ブロードキャスト |

`request_id` と `lanlan_name` は任意です。不明なコマンド/flag key は Agent Server が拒否し、上流/プロキシ障害は `502` です。

## ヘルスと capability

| メソッド | パス | 応答境界 |
|---|---|---|
| `GET` | `/api/agent/health` | `{"status":"ok","tool":{...}}`。Agent Server 不可用は `502` と `status: "down"` |
| `GET` | `/api/agent/computer_use/availability` | Agent Server が返すキーボード・マウスの準備状態と理由。agent モデルに接続でき、このマシンで入力を送れることが条件 |
| `GET` | `/api/agent/browser_use/availability` | ブラウザーの準備状態と理由。使えるブラウザーがインストールされていて、agent モデルに接続できることが条件。ブラウザーが見つからない場合の理由コードは `AGENT_BROWSER_NOT_FOUND` |
| `GET` | `/api/agent/user_plugin/availability` | プラグインサービス到達性。不可用は `502` |

## タスクと管理

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/agent/tasks` | タスク一覧。進行中のものと、直近 5 分以内に終了したもの |
| `GET` | `/api/agent/tasks/{task_id}` | タスクを 1 件取得 |
| `POST` | `/api/agent/tasks/{task_id}/cancel` | タスクを 1 件中止。上流 `404` を保持し、リクエスト到達後の応答タイムアウトは `504`。すでに終了したタスクには `success: false` と実際の `status` を返す |
| `POST` | `/api/agent/admin/control` | `{"action":"end_all"}` などの管理操作を転送。`end_all` は進行中のタスクをすべて中止する。ローカル限定 |

### タスクオブジェクト

タスクは、NekoClaw の台帳にある依頼一件を HUD 向けに表したものです。`GET /api/agent/tasks` は `{"tasks":[...]}` を返します。状態スナップショットの `active_tasks` と WebSocket の `agent_task_update` メッセージにも同じオブジェクトが入ります。

| フィールド | 意味 |
|---|---|
| `id` | 依頼 ID |
| `status` | `queued`、`running`、`waiting_user`、`completed`、`blocked`、`failed`、`cancelled` のいずれか |
| `type` | `user_plugin`（プラグインエントリの直接呼び出し）、`task_agent`（task agent が作業中。画面上では「使い魔」）、`task`（登録済みで担当が未定） |
| `start_time`、`end_time` | ISO タイムスタンプ。`end_time` はタスク終了後に入る |
| `params` | `description`（タイトル）と `instruction`（dispatch された一文）。`user_plugin` のタスクには `plugin_id`、`entry_id`、`plugin_name` も入る |
| `lanlan_name` | タスクの持ち主であるキャラクター |
| `error` | 理由。`failed`、`blocked`、`cancelled` のタスクに入る |
| `progress`、`stage`、`message`、`step`、`step_total` | 直接呼び出したプラグインエントリが報告する進捗（任意） |

`waiting_user` は、ユーザーの回答または承認を待っている状態で、進行中として数えます。`blocked` は、ほかの条件が整わないと続けられず、結果が出ないまま止まった状態です。フィールドは今後追加されることがあります。

## キーボード・マウスとブラウザーのツール

キーボード・マウス操作とブラウザー操作は二組の組み込みツールで、task agent（画面上では「使い魔」）が一歩ずつ呼び出します。[Agent System](/ja/architecture/agent-system) を参照してください。ブラウザー操作には、このマシンにインストールされている Chromium 系ブラウザー（Chrome、Edge、Brave、Chromium）を使います。アプリケーションにブラウザーは同梱されず、起動時にダウンロードもしません。

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/agent/builtin/activity` | いま、どのタスクがキーボード・マウスを、どのタスクがブラウザーを使っているか |
| `POST` | `/api/agent/builtin/stop` | キーボード・マウスまたはブラウザーを使っているタスクを中止 |
| `GET` | `/api/agent/builtin/browser/settings` | ブラウザーツールがどのブラウザーをどう使うかの設定と、このマシンで見つかったブラウザー |
| `POST` | `/api/agent/builtin/browser/settings` | 上記の設定を変更 |

リモート配備では、この四つはいずれも `501` を返します。

### `GET /api/agent/builtin/activity`

`{"activity":[...]}` を返します。使用中の組ごとに 1 件が入り、どちらも使われていなければ空のリストです。

```json
{"group": "browser", "state": "started", "commission_id": "...", "window": null}
```

| フィールド | 意味 |
|---|---|
| `group` | `keyboard_mouse`（デスクトップ全体）または `browser` |
| `state` | このレスポンスでは常に `started`。後述の WebSocket メッセージでは `started` または `ended` |
| `commission_id` | その組を占有しているタスク。タスクオブジェクトの `id` と同じ値 |
| `window` | ブラウザーの場合、操作対象のウィンドウが分かった後は `{"browser","pid","window_id","bounds"}`、それまでは `null`。キーボード・マウスでは常に `null` |

プロキシ障害は `502` で、空の `activity` と `error` を返します。

タスクがある組を使い始めたとき、または使い終えたとき、メインサーバーは同じオブジェクトを WebSocket メッセージ `{"type":"agent_builtin_activity","activity":{...}}` として、接続中のすべてのページへ送ります。一回の使用で `started` が複数回届くことがあります。ブラウザーはタスクの最初の呼び出しで起動するため、ウィンドウが初めて分かったときと、ウィンドウが動いたときに、もう一度送られます。`ended` が送られるのは、task agent の実行が終わったとき、タスクが終了または失敗したとき、ユーザーの回答待ちになったとき、中止されたときです。ページは最新のオブジェクトを `window._agentBuiltinActivity[group]` に保持し、`detail` にそのオブジェクトを入れた `neko-agent-builtin-activity` DOM イベントを発行します。ページ自身は何も描画しません。

### `POST /api/agent/builtin/stop`

ボディ: 任意で `{"group":"keyboard_mouse"}` または `{"group":"browser"}`。`group` を省くと両方を中止します。レスポンスは `{"success":true,"stopped":["<タスク ID>", ...]}` で、何も使われていなければリストは空です。処理の内容は、その組を占有しているタスクに対する `POST /api/agent/tasks/{task_id}/cancel` と同じです。不明な group は `400`、リクエスト到達後の応答タイムアウトは `504` です。

### `GET /api/agent/builtin/browser/settings`

```json
{
  "settings": {"mode": "own_profile", "executable": null, "headless": false},
  "modes": ["own_profile", "attach"],
  "browsers": [{"kind": "chrome", "name": "Google Chrome", "executable": "..."}]
}
```

`browsers` はこのマシンで見つかったブラウザーで、優先して使うものが先頭です。`kind` は `chrome`、`edge`、`brave`、`chromium`、`custom` のいずれかです。

| 設定 | 意味 |
|---|---|
| `mode` | `own_profile`（既定）: ユーザーのブラウザーを、データルート配下の専用プロファイルディレクトリ（`nekoclaw/browser_profile`）で起動する。使うサイトには、そこで一度ログインすればよい。`attach`: ユーザーが今使っているブラウザーを操作する。Chrome 144 以降が必要で、ユーザー自身が `chrome://inspect/#remote-debugging` でリモートデバッグをオンにし、接続のたびにブラウザー側で許可する |
| `executable` | 自動で見つけたものの代わりに使うブラウザーのパス。`null` なら自動で探す |
| `headless` | ウィンドウを出さずにブラウザーを起動する。`own_profile` のときだけ有効 |

### `POST /api/agent/builtin/browser/settings`

ボディ: `mode`、`executable`、`headless` のうち変更したいもの。省いたフィールドは現在の値のままです。レスポンスは上のオブジェクトに `"success":true` を加えたものです。不明な `mode`、または存在しないファイルを指す `executable` は `400` です。変更は次に起動するブラウザーから有効になり、ブラウザーツールが起動していたブラウザーは閉じられます。

## UI ヘルパールート

| メソッド | パス | 境界 |
|---|---|---|
| `GET` | `/api/agent/user_plugin/dashboard` | ローカルプラグイン dashboard へリダイレクト。検証済みの `v` とループバック `yui_opener_origin` を受理 |

Dashboard はブラウザーヘルパーであり、タスク API ではありません。ブラウザーからタスクを送るルートはありません。タスクはメインの対話モデルが `dispatch_task` ツールで任せ、メインサーバーが Agent Server の内部ルート `/nekoclaw/dispatch` へ転送します。[Agent Server API](/ja/api/agent-server) を参照してください。
