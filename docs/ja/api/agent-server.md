# Agent Server API

**既定アドレス:** `http://127.0.0.1:48915`

**設定:** `TOOL_SERVER_PORT`

Agent Server は内部ループバックサービスで、agent ランタイム、追跡対象タスク、実行アダプター、プラグインの直接実行を所有します。メインサーバーはブラウザー向けの対応済みサブセットを `/api/agent` で公開します。ローカルプロセスグループ外の呼び出し側は、以下のルートではなくそのプロキシを使用してください。

Agent Server は ZeroMQ を介して、非同期のセッション・タスクイベントもメインサーバーと交換します。HTTP が制御/照会プレーン、ZeroMQ がイベントプレーンであり、HTTP の代わりに ZeroMQ だけを使うわけではありません。

## HTTP エンドポイント一覧

| メソッド | パス | 契約 |
|---|---|---|
| `GET` | `/health` | N.E.K.O ヘルスフィンガープリントと現在の agent flags |
| `GET` | `/capabilities` | 現在の capability スナップショット |
| `GET` | `/agent/flags` | 現在のマスター/サブ機能フラグ |
| `POST` | `/agent/flags` | サブ機能フラグを部分更新 |
| `GET` | `/agent/state` | 正式な revision、flags、capabilities、notification、タスク状態 |
| `POST` | `/agent/command` | `set_agent_enabled`、`set_flag`、`refresh_state` コマンド |
| `GET` | `/computer_use/availability` | Computer Use の準備状態と理由 |
| `POST` | `/computer_use/run` | 追跡対象の Computer Use タスクを開始。ボディに `instruction` が必須、`screenshot_b64` と `lanlan_name` は任意 |
| `GET` | `/browser_use/availability` | Browser Use の依存関係/モデル準備状態 |
| `POST` | `/browser_use/run` | ブラウザー指示を 1 件実行。`instruction` が必須 |
| `GET` | `/openclaw/availability` | OpenClaw/QwenPaw capability チェック |
| `GET` | `/openfang/availability` | OpenFang capability チェック |
| `POST` | `/openfang/run` | 追跡対象の OpenFang タスクを開始 |
| `POST` | `/openfang/sync_config` | OpenFang ランタイム設定を更新 |
| `GET` | `/mcp/availability` | 互換応答。MCP は `brain/` から削除済みで、ここでは常に利用不可 |
| `POST` | `/plugin/execute` | ユーザープラグイン entry を直接スケジュール。`plugin_id` は必須、`entry_id`、`args`、キャラクター/会話 ID は任意 |
| `GET` | `/tasks` | タスクレジストリのスナップショット一覧 |
| `GET` | `/tasks/{task_id}` | 追跡対象タスクを 1 件取得。存在しなければ `404` |
| `POST` | `/tasks/{task_id}/cancel` | 追跡対象タスクをキャンセル。存在しなければ `404` |
| `POST` | `/api/agent/tasks/{task_id}/correction` | タスク結果の内部修正コールバック |
| `POST` | `/api/agent/tasks/{task_id}/complete` | タスク結果の内部完了コールバック |
| `POST` | `/admin/control` | ランタイム管理。現在は `action: "end_all"` で活動中の作業をキャンセル |
| `POST` | `/notify_config_changed` | モデル/API 設定変更後の内部通知 |

多くの run エンドポイントは `{"success":true,"task_id":"...","status":"running","start_time":"..."}` を返し、非同期で継続します。検証エラーは `400`、マスター/機能無効は `403`、アダプター利用不可は `503`、Computer Use の重複検出は `409` です。タスク/プラグイン結果は内部スキーマであり、フィールドが追加されることがあります。

2 つの `/api/agent/tasks/*` コールバックと `/notify_config_changed` はプロセス内部用です。`/admin/control`、直接 run ルート、`/plugin/execute` を信頼できないネットワークに公開しないでください。

## ZeroMQ イベントプレーン

アドレスはループバック限定で、対応する `NEKO_ZMQ_*_PORT` 環境変数により上書きできます。

| Socket | 既定アドレス | 種別 | 方向 |
|---|---|---|---|
| セッションイベント | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| タスク/状態イベント | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| 信頼性付き分析キュー | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

イベントには、セッションライフサイクル/意図復元シグナル、分析リクエスト、タスク更新、タスク結果、プロアクティブメッセージ、agent 状態スナップショットが含まれます。payload は main/agent の組でバージョン管理される内部形式であり、公開プラグインプロトコルではありません。

## 実行アダプター

| アダプター | 現在の役割 |
|---|---|
| Computer Use | スクリーンショットに基づくマウス/キーボード操作 |
| Browser Use | 任意の `browser-use` 依存関係によるブラウザー自動化 |
| OpenClaw | OpenClaw/QwenPaw スタンドアロン agent チャネルへの委譲 |
| OpenFang | OpenFang スタンドアロン agent チャネルへの委譲 |
| ユーザープラグイン | プラグインランタイム経由の直接実行 |

MCP 呼び出しは `brain/` から削除され、インストール可能な MCP 連携は `plugin/plugins/mcp_adapter/` にあります。

詳細は [Agent System](/ja/architecture/agent-system) と、メインサーバープロキシの [Agent API](/ja/api/rest/agent) を参照してください。
