# Agent Server API

**既定アドレス:** `http://127.0.0.1:48915`

**設定:** `TOOL_SERVER_PORT`

Agent Server は内部ループバックサービスです。NekoClaw ホスト（タスクブレイン、task agent、能力ゲートウェイ）、HUD 用のタスク一覧、キーボード・マウスとブラウザーの二つの組み込み機能、組み込みのプラグインサービスを持ちます。メインサーバーはブラウザー向けの対応済みサブセットを `/api/agent` で公開します。ローカルプロセスグループ外の呼び出し側は、以下のルートではなくそのプロキシを使用してください。

Agent Server は ZeroMQ を介して、非同期のセッション・タスクイベントもメインサーバーと交換します。HTTP が制御/照会プレーン、ZeroMQ がイベントプレーンであり、HTTP の代わりに ZeroMQ だけを使うわけではありません。

## HTTP エンドポイント一覧

| メソッド | パス | 契約 |
|---|---|---|
| `GET` | `/health` | N.E.K.O ヘルスフィンガープリントと現在の agent flags |
| `GET` | `/agent/flags` | 現在のマスター/サブ機能フラグ |
| `POST` | `/agent/flags` | `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled` を部分更新 |
| `GET` | `/agent/state` | 正式な revision、flags、capabilities、notification、進行中のタスク |
| `POST` | `/agent/command` | `set_agent_enabled`、`set_flag`、`refresh_state` コマンド |
| `GET` | `/computer_use/availability` | キーボード・マウス機能の準備状態と理由 |
| `GET` | `/browser_use/availability` | ブラウザー機能の依存関係/モデル準備状態 |
| `POST` | `/nekoclaw/dispatch` | メイン対話の一文をタスクブレインへ渡し、第一ラウンドのレシートを返す |
| `POST` | `/nekoclaw/query` | 進行中の用件、またはある用件ができるかを尋ねる。モデルは呼ばない |
| `GET` | `/nekoclaw/brief` | 進行中のタスクを 1 件 1 行で返す。新しい対話セッションの冒頭プロンプト用 |
| `GET` | `/tasks` | 進行中のタスクと、直近 5 分以内に終了したタスクの一覧 |
| `GET` | `/tasks/{task_id}` | タスクを 1 件取得。存在しなければ `404` |
| `POST` | `/tasks/{task_id}/cancel` | タスクを 1 件中止。存在しなければ `404`、すでに終了していれば `success: false` とその `status` |
| `POST` | `/api/agent/tasks/{task_id}/complete` | プラグインの deferred 結果の完了コールバック |
| `POST` | `/admin/control` | 管理操作。`action: "end_all"` で進行中のタスクをすべて中止 |
| `POST` | `/notify_config_changed` | モデル/API 設定変更後の内部通知 |

`/tasks` が返すタスクオブジェクトは [Agent API](/ja/api/rest/agent) の「タスクオブジェクト」を参照してください。フィールドは今後追加されることがあります。

`/api/agent/tasks/{task_id}/complete` と `/notify_config_changed` はプロセス内部用です。`/admin/control` と `/nekoclaw/*` ルートを信頼できないネットワークに公開しないでください。

## NekoClaw ルート

この三つのルートは、同じマシン上のメインサーバーが、メインの対話モデルの代わりに呼び出します。ブラウザーへはプロキシされません。

### `POST /nekoclaw/dispatch`

ボディ: `{"text":"...","lanlan_name":"...","user_text":"...","conversation_id":"...","language":"..."}`。`text` はモデルが `dispatch_task` ツールに渡した一文、`lanlan_name` はキャラクター名で、どちらも必須です（欠けると `400`）。`user_text` はそのターンのユーザーの元の発言で、プラグインエントリには `_ctx.latest_user_request` として届きます。

レスポンスは `{"success":true,"kind":"...","reason":"...","text":"...","commission_id":"...","model_calls":0}` です。`text` はモデルが読むレシートです。`kind` は `accepted`（受理）、`received`（第一ラウンドが 1 秒を超えたため、結論は報告として後から届く）、`duplicate`、`no_capability`、`stopped`、`told`、`answered`、`ambiguous`、`nothing_active`、`unclear_answer` のいずれかです。`no_capability` のとき、`reason` は `disabled`、`not_installed`、`not_enabled`、`denied`、`no_match` のいずれかです。この呼び出しは第一ラウンドの後に戻り、作業そのものはバックグラウンドで続きます。

### `POST /nekoclaw/query`

ボディ: `{"text":"...","lanlan_name":"...","language":"..."}`。`lanlan_name` は必須です。レスポンスは `{"success":true,"text":"...","can_do":true|false|null}` です。進み具合を尋ねた場合と、モデルなしでは答えられない場合、`can_do` は `null` になります。

### `GET /nekoclaw/brief`

クエリ: `lanlan_name`、任意の `language`。`{"success":true,"lines":[...]}` を返します。そのキャラクターの依頼のうち、待機中、作業中、ユーザーの回答待ちのものが 1 件 1 行で入ります。マスタースイッチがオフのときは空です。

## ZeroMQ イベントプレーン

アドレスはループバック限定で、対応する `NEKO_ZMQ_*_PORT` 環境変数により上書きできます。

| Socket | 既定アドレス | 種別 | 方向 |
|---|---|---|---|
| セッションイベント | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| タスク/状態イベント | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| 信頼性付きターン後チェックキュー | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

イベントには、セッションライフサイクル/意図復元シグナル、ターン後チェックの要求とその ACK、タスク更新、タスク結果、プロアクティブメッセージ、agent 状態スナップショットが含まれます。依頼についての報告は、送信前にホストの outbox に保存され、ブリッジが受け取るまで再試行されます。payload は main/agent の組でバージョン管理される内部形式であり、公開プラグインプロトコルではありません。

## 作業を実行するもの

| 部分 | 現在の役割 |
|---|---|
| プラグインエントリ | エントリ一つで終わる用件を、タスクブレインがプラグインサービスの `/runs` プロトコルで直接呼び出す |
| task agent | 複数ステップが必要な用件の実行セッション（画面上では「使い魔」）。組み込みの agent runtime（`nekoclaw/runtime/`）で動く |
| 能力ゲートウェイ | ループバック上の MCP サービス。task agent はここからプラグインエントリ、MCP ツール、二つの組み込み機能を呼ぶ |
| キーボード・マウス、ブラウザー | 組み込み機能。`brain/computer_use.py` と任意の `browser-use` 依存関係で実装され、呼び出せるのは task agent だけ |

MCP サーバーは `plugin/plugins/mcp_adapter/` の `mcp_adapter` プラグインで設定し、ゲートウェイがそのツールを task agent に提供します。

詳細は [Agent System](/ja/architecture/agent-system) と、メインサーバープロキシの [Agent API](/ja/api/rest/agent) を参照してください。
