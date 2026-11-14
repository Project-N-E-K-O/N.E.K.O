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
| `GET` | `/api/agent/computer_use/availability` | Agent Server の準備状態と理由 |
| `GET` | `/api/agent/browser_use/availability` | ブラウザー依存関係/モデルの準備状態 |
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

## UI ヘルパールート

| メソッド | パス | 境界 |
|---|---|---|
| `GET` | `/api/agent/user_plugin/dashboard` | ローカルプラグイン dashboard へリダイレクト。検証済みの `v` とループバック `yui_opener_origin` を受理 |

Dashboard はブラウザーヘルパーであり、タスク API ではありません。ブラウザーからタスクを送るルートはありません。タスクはメインの対話モデルが `dispatch_task` ツールで任せ、メインサーバーが Agent Server の内部ルート `/nekoclaw/dispatch` へ転送します。[Agent Server API](/ja/api/agent-server) を参照してください。
