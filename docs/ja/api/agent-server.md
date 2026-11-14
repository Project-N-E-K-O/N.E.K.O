# Agent Server API

**既定アドレス:** `http://127.0.0.1:48915`

**設定:** `TOOL_SERVER_PORT`

Agent Server は内部ループバックサービスです。NekoClaw ホスト（タスクブレイン、task agent、能力ゲートウェイ）、HUD 用のタスク一覧、キーボード・マウスとブラウザーの二組の組み込みツール、組み込みのプラグインサービスを持ちます。メインサーバーはブラウザー向けの対応済みサブセットを `/api/agent` で公開します。ローカルプロセスグループ外の呼び出し側は、以下のルートではなくそのプロキシを使用してください。

Agent Server は ZeroMQ を介して、非同期のセッション・タスクイベントもメインサーバーと交換します。HTTP が制御/照会プレーン、ZeroMQ がイベントプレーンであり、HTTP の代わりに ZeroMQ だけを使うわけではありません。

## HTTP エンドポイント一覧

| メソッド | パス | 契約 |
|---|---|---|
| `GET` | `/health` | N.E.K.O ヘルスフィンガープリントと現在の agent flags |
| `GET` | `/agent/flags` | 現在のマスター/サブ機能フラグ |
| `POST` | `/agent/flags` | `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled` を部分更新 |
| `GET` | `/agent/state` | 正式な revision、flags、capabilities、notification、進行中のタスク |
| `POST` | `/agent/command` | `set_agent_enabled`、`set_flag`、`refresh_state` コマンド |
| `GET` | `/computer_use/availability` | キーボード・マウスの準備状態と理由 |
| `GET` | `/browser_use/availability` | ブラウザーの準備状態と理由 |
| `GET` | `/builtin/activity` | いま、どの依頼がキーボード・マウスを、どの依頼がブラウザーを占有しているか |
| `POST` | `/builtin/stop` | キーボード・マウスまたはブラウザーを占有している依頼を中止 |
| `GET` | `/builtin/browser/settings` | ブラウザーツールがどのブラウザーをどう使うかの設定と、このマシンで見つかったブラウザー |
| `POST` | `/builtin/browser/settings` | 上記の設定を変更 |
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

## 組み込みツールのルート

task agent が呼び出すキーボード・マウスのツールと、ブラウザーのツールに関するルートです。メインサーバーは、これらすべてを `/api/agent` の下でプロキシします。

### `GET /computer_use/availability` と `GET /browser_use/availability`

どちらも `{"enabled":true,"ready":true|false,"reasons":[...],"provider":"nekoclaw"}` を返し、キーボード・マウスの応答には `model` も入ります。agent モデルが未設定の間は、`{"ready":false,"reasons":[...]}` だけを返します。

- キーボード・マウスは、agent モデルの接続確認が通っていて、このマシンで入力を送れるときに準備完了になります。入力を送れない場合、`reasons` には `AGENT_PYAUTOGUI_*` のいずれかのコードが入ります。たとえばディスプレイのないマシンでは `AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE` です。
- ブラウザーは、使えるブラウザーがインストールされていて、agent モデルの接続確認が通っているときに準備完了になります。アプリケーションにブラウザーは同梱されません。Chrome、Edge、Brave、Chromium のどれも見つからない場合、`reasons` は `["AGENT_BROWSER_NOT_FOUND"]` です。探索は数回のファイル確認だけで、結果は 30 秒間保持されます。

### `GET /builtin/activity`

`{"activity":[...]}` を返します。いま依頼に占有されている組ごとに 1 件が入り、どちらも使われていなければ空のリストです。各項目は `{"group":"keyboard_mouse"|"browser","state":"started","commission_id":"...","window":null}` です。ブラウザーでは、操作対象のウィンドウが分かった後、`window` が `{"browser":"...","pid":...,"window_id":...,"bounds":{...}}` になります。キーボード・マウスはデスクトップ全体が対象なので、`window` は常に `null` です。項目の形は `builtin_activity` イベントと同じで、後から起動してイベントを受け取れなかった表示側が現状を取得するために使います。

### `POST /builtin/stop`

ボディ: 任意で `{"group":"keyboard_mouse"}` または `{"group":"browser"}`。`group` を省くと両方を中止します。不明な group は `400` です。レスポンスは `{"success":true,"stopped":["<依頼 ID>", ...]}` で、何も占有されていなければリストは空です。中止の処理は、その依頼に対する `POST /tasks/{task_id}/cancel` と同じです。先に状態を書き、実行中の呼び出しを取り消し、デスクトップまたはブラウザーを解放します。

### `GET /builtin/browser/settings`

`{"settings":{"mode":"own_profile","executable":null,"headless":false},"modes":["own_profile","attach"],"browsers":[{"kind":"chrome","name":"Google Chrome","executable":"..."}]}` を返します。`settings` は `<データルート>/nekoclaw/browser.json` の内容で、ファイルがなければ既定値です。`browsers` はこのマシンで見つかったブラウザーで、優先して使うものが先頭です。

| フィールド | 意味 |
|---|---|
| `mode` | `own_profile`（既定）: ユーザーのブラウザーを、専用のプロファイルディレクトリ `<データルート>/nekoclaw/browser_profile` で起動する。`attach`: ユーザーが今使っているブラウザーを操作する。Chrome 144 以降で、`chrome://inspect/#remote-debugging` からリモートデバッグをオンにしておく必要がある |
| `executable` | 自動で見つけたものの代わりに使うブラウザーのパス。`null` なら自動で探す |
| `headless` | ウィンドウを出さずにブラウザーを起動する。`own_profile` のときだけ有効 |

### `POST /builtin/browser/settings`

ボディ: `mode`、`executable`、`headless` のうち変更したいもの。省いたフィールドは現在の値のままです。不明な `mode`、または存在しないファイルを指す `executable` は `400` です。レスポンスは `GET` と同じオブジェクトに `"success":true` を加えたものです。ブラウザーツールが起動したブラウザーは閉じられ、次の呼び出しで新しい設定のブラウザーが起動します。

## ZeroMQ イベントプレーン

アドレスはループバック限定で、対応する `NEKO_ZMQ_*_PORT` 環境変数により上書きできます。

| Socket | 既定アドレス | 種別 | 方向 |
|---|---|---|---|
| セッションイベント | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| タスク/状態イベント | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| 信頼性付きターン後チェックキュー | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

イベントには、セッションライフサイクル/意図復元シグナル、ターン後チェックの要求とその ACK、タスク更新、タスク結果、プロアクティブメッセージ、agent 状態スナップショット、`builtin_activity`（依頼がキーボード・マウスまたはブラウザーの占有を始めた、または手放した）が含まれます。依頼についての報告は、送信前にホストの outbox に保存され、ブリッジが受け取るまで再試行されます。payload は main/agent の組でバージョン管理される内部形式であり、公開プラグインプロトコルではありません。

## 作業を実行するもの

| 部分 | 現在の役割 |
|---|---|
| プラグインエントリ | エントリ一つで終わる用件を、タスクブレインがプラグインサービスの `/runs` プロトコルで直接呼び出す |
| task agent | 複数ステップが必要な用件の実行セッション（画面上では「使い魔」）。組み込みの agent runtime（`nekoclaw/runtime/`）で動く |
| 能力ゲートウェイ | ループバック上の MCP サービス。task agent はここからプラグインエントリ、MCP ツール、組み込みツールを呼ぶ |
| キーボード・マウス、ブラウザー | 二組の組み込みツール（`nekoclaw/builtin/`）。task agent が自分のループの中から一歩ずつ呼び出す。キーボード・マウスは `computer` ツール一つ、ブラウザーは 15 個の `browser_*` ツールで、このマシンにインストールされたブラウザーをデバッグプロトコル経由で操作する。呼び出せるのは task agent だけ |

MCP サーバーは `plugin/plugins/mcp_adapter/` の `mcp_adapter` プラグインで設定し、ゲートウェイがそのツールを task agent に提供します。

詳細は [Agent System](/ja/architecture/agent-system) と、メインサーバープロキシの [Agent API](/ja/api/rest/agent) を参照してください。
