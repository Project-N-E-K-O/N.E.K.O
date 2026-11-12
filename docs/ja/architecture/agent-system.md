# エージェントシステム

エージェントシステム（画面上の名前は「ネコクロー」、NekoClaw）は、キャラクターが会話の外で用件を片付けるための仕組みです。やるかどうかはメインの対話モデルが決め、ツール呼び出しで伝えます。記録、誰に任せるかの判断、実行、報告は、Agent Server 内の NekoClaw ホストが担当します。ターンが終わるたびに自動で解析する処理はもうありません。以前のターン単位の解析器と、その実行チャネルは削除されました。

以下では二つの用語を使います。**依頼**（コード名 `commission`）はキャラクターが任せた一件の用件、**task agent** は一件の依頼を複数ステップで進める実行セッションです。画面上では task agent を「使い魔」と呼び、コード、ルート、フィールド値では `task_agent` と書きます。

## ランタイム構成

```text
Main Server                                   Agent Server (:48915)
┌───────────────────────────┐                 ┌──────────────────────────────────┐
│ LLMSessionManager         │  HTTP           │ NekoClaw host (nekoclaw/host/)   │
│  tools: dispatch_task     │ ──────────────> │  ├─ task brain                   │
│         query_tasks       │ <── receipt ─── │  │   ledger · decision model     │
│ cross_server.py           │                 │  ├─ task agent manager           │
│ MainServerAgentBridge     │  ZeroMQ         │  ├─ capability gateway           │
│ pending_agent_callbacks   │ ─ turn check ─> │  └─ permissions                  │
│                           │ <── reports ─── │ embedded plugin service (:48916) │
└───────────────────────────┘                 └──────────────────────────────────┘
```

ホストは Agent プロセスごとに一つです。起動時には、プロセスのスイッチの読み方を登録するだけです（`app/agent_server/nekoclaw_mount.py`）。台帳ファイル、agent runtime、ゲートウェイの待ち受けは、最初に必要になった時点で作られます。

## 二つのタスクツール

マスタースイッチと、機能スイッチの少なくとも一つがオンのとき、`main_logic/core/tool_calling.py` がメインの対話モデルに組み込みツールを二つ登録します。どちらも引数は自然文の一文だけで、番号は渡しません。その一文がどの依頼の話かはホストが判断します。定義は `config/prompts/prompts_task_tools.py` にあります。

| ツール | 引数 | 用途 |
|---|---|---|
| `dispatch_task` | `task` | 用件を任せる。追加、変更、中止、質問へのユーザーの回答の伝達にも使う |
| `query_tasks` | `question` | 進行中の用件、ある依頼の進み具合、ある用件ができるかを尋ねる。空なら進行中の用件を一覧する |

ハンドラーは Agent Server の `POST /nekoclaw/dispatch` と `POST /nekoclaw/query` を呼び、最大 2.5 秒待ちます。Agent Server に接続できなかったときは、ツール結果に「用件は渡せていない」と書かれます。リクエストは送れたが時間内に応答がなかったときは、「渡せたかどうか分からない」（Agent Server 側では処理が進んでいる可能性があります）と書かれ、もう一度任せるのではなく、少ししてから照会で確かめるよう促します。自発的な話しかけのリクエストには、この二つのツールは付きません。

## ホストの内部

### タスクブレイン（task brain）

- **台帳。** SQLite ファイル `<データルート>/nekoclaw/ledger.db` に、依頼、依頼ごとのイベント、送信待ちの報告（outbox）、タスクの好みを保存します。依頼の状態は `queued`、`working`、`waiting_user`、`done`、`blocked`、`failed`、`cancelled` のいずれかで、遷移は一つの関数だけが行います。`done` と `cancelled` は終端なので、遅れて届いた結果が中止を上書きすることはありません。
- **dispatch の第一ラウンド（待つ）。** その一文が中止、回答、追加、進行中の用件の繰り返しのどれかをローカルで判定し、キーワードと BM25 で候補プラグインを探し、task agent を起動できるかを確かめます。レシートは「すでに進行中」「対応できる機能がない」（理由付き）「受理」のいずれかです。モデルは呼びません。例外は、ローカルに候補がなく task agent も起動できない場合の、小型モデルによる粗選別 1 回だけです。1 秒を超えるとレシートは「受け取った」になり、結論は報告として後から届きます。
- **dispatch の第二ラウンド（バックグラウンド）。** 判断モデル（`nekoclaw/host/decision.py`）が、モデル呼び出し 1 回でプラグインエントリを選び、引数を埋めます。全プラグインの説明の合計が 3000 token を超えるときは、先に粗選別を行い、キーワード一致、BM25 上位 10 件、小型モデルの選別結果の和集合に絞ります。プラグインが優先です。エントリ一つで終わる用件は、プラグインサービスの `/runs` プロトコルで直接呼びます。合うエントリがない、または複数ステップが必要な場合に、依頼を task agent へ渡します。モデルは、プラグインでも task agent でもできない、またはそもそも片付ける用件ではない、と答えることもでき、その場合は何も始まりません。存在しないプラグインやエントリを答えたときは聞き直さず、合うエントリがない場合と同じに扱います。このラウンドがレシートを覆したときは、すぐキャラクターに知らせます。
- **query。** モデルは一切呼びません。進み具合は台帳から読み、「できるか」は第一ラウンドのローカル照合を使って答えます。依頼は登録しません。

### task agent

task agent は、組み込みの agent runtime（`nekoclaw/runtime/`）の一つのセッションです。agent runtime は必要になったときに起動するサブプロセスで動きます。依頼一件につきセッション一つで、同時に動くのは最大 10 個、残りの依頼は `queued` のまま待ちます。task agent は agent モデルを使い、会話履歴ではなく、任された一文とユーザーの元の発言を受け取ります。自分の task agent をさらに起動することもできます。task agent がユーザーに質問したり権限の承認を求めたりすると、依頼は `waiting_user` になり、キャラクターが `dispatch_task` で回答を伝えるまで待ちます。

### 能力ゲートウェイと権限

task agent は、自前のファイル・コマンド系ツール以外の機能を、能力ゲートウェイ（`nekoclaw/host/gateway.py`）経由で使います。ゲートウェイはループバックアドレス上の MCP サービスで、最初にそれを必要とする task agent が起動した時点で待ち受けを始め、依頼ごとにトークンを一つ発行します。提供するものは次のとおりです。

- 選択の対象になるプラグインエントリ。エントリごとにツール一つで、直接呼び出しと同じ `/runs` のコードを通ります。
- `mcp_adapter` プラグインで設定した MCP サーバーのツール。
- キーボード・マウス操作とブラウザー操作の二つの組み込み機能。どちらも指示を一つ受け取り、`brain/computer_use.py`、`brain/browser_use_adapter.py` にある既存のループで実行します。呼び出せるのは task agent だけです。

権限は `<データルート>/nekoclaw/permissions.json` に保存します。カテゴリごとに `first_ask`（初回に確認）、`always_ask`（毎回確認）、`allow`（許可）、`deny`（禁止）のいずれかです。既定では、プラグインエントリと作業ディレクトリ内のファイル操作は許可、コマンド実行、作業ディレクトリ外への書き込み、キーボード・マウス、ブラウザー、各 MCP サーバーは初回に確認します。`deny` にしたプラグインや MCP サーバーは、直接呼び出しでも使われません。

## 会話への報告

報告はすべて、まず台帳の outbox に書き、その後 ZeroMQ で Main Server へ送ります。送信に失敗すると 1 秒後に再試行し、以後は間隔を倍にして最長 5 分まで延ばします。前のプロセスが送れなかった報告は、ホストが次に使われたときに送ります。

| outbox の種類 | 意味 | 送信時のイベント |
|---|---|---|
| `task_result` | 依頼がどう終わったか | `task_result` |
| `question` | 依頼がユーザーの回答または承認を待っている | `proactive_message` |
| `correction` | 第二ラウンドまたはターン後チェックで、キャラクターの発言が事実と合わないと分かった | `proactive_message` |
| `receipt` | 1 秒を超えた第一ラウンドの結論 | `proactive_message` |

Main Server は報告を該当する `LLMSessionManager` のキューに入れ、既存の配信方式で届けます。`proactive` はキャラクターを起こして話させ、`passive` は次のターンに添え、`silent` の結果は報告しません。テキストセッションでは即時に配信でき、音声セッションでは注入またはホットスワップの境界まで遅れる場合があります。キャラクターが報告を伝えるターンは、後述のターン後チェックには送られません。

対話セッションは数ターンごとに入れ替わり、タスクの記録を持ち越せません。新しいセッションは毎回 `GET /nekoclaw/brief` を呼び、まだ進行中の用件を受け取ります。依頼の変化はすべて `task_update` イベントとしても送られ、[Task HUD](/architecture/task-hud-system) に表示されます。

## ターン後チェック

ユーザーが始めたターンが終わり、そのターンでキャラクターが「実際に手を動かす」ツールを呼んでいない場合（記憶の想起、知識の検索、`query_tasks` は数えません）、`main_logic/cross_server.py` がユーザーの発言とキャラクターの返答を載せた `turn_check_request` を送ります。`publish_turn_check_reliably()` は `turn_check_ack` を短時間待ち、タイムアウト時に一度だけ再試行します。キャラクターが自分から始めたターン、エージェントのコールバックのターン、画像だけのターンは送りません。

ホストはまず、費用のかからない関門を通します。入力時の意図スコアが分かっていて 0.2 未満、ユーザーの発言がどのプラグインのキーワードにも一致しない、選択対象のプラグインがすべてキーワードを宣言している、の三つがそろえばスキップします。通過したターンは小型モデルを 1 回呼び、次の四つのいずれかに判定します。

| 判定 | その後 |
|---|---|
| 用件はない、または意図して断った | 何もしない |
| 「できない」と言ったが、ローカル照合では手段が見つかる | キャラクターに知らせ、本人が訂正する |
| 引き受けたのに dispatch していない | 代わりに dispatch し、本人に知らせる |
| 「終わった」と言ったが、台帳にその依頼がない | キャラクターに知らせ、本人が訂正する |

チェック自体に実行の権限はありません。依頼が登録されるのは、キャラクターが dispatch したとき、または口頭で引き受けたときだけです。

## トランスポート

制御と照会は HTTP（`127.0.0.1:48915`）で行い、組み込みのプラグインサービスは `127.0.0.1:48916` で待ち受けます。イベントは同期 ZeroMQ socket とバックグラウンドの受信スレッドで扱うため、Windows Proactor イベントループでも動作します。

| 既定アドレス | パターン | 方向 | 用途 |
|---|---|---|---|
| `tcp://127.0.0.1:48961` | PUB / SUB | Main → Agent | セッション・ライフサイクルイベント |
| `tcp://127.0.0.1:48963` | PUSH / PULL | Main → Agent | 信頼性付き `turn_check_request` キュー |
| `tcp://127.0.0.1:48962` | PUSH / PULL | Agent → Main | ACK、状態、タスク更新、報告 |

各ポートは `NEKO_ZMQ_SESSION_PUB_PORT`、`NEKO_ZMQ_ANALYZE_PUSH_PORT`、`NEKO_ZMQ_AGENT_PUSH_PORT` で上書きできます。Agent → Main のイベントに HTTP フォールバックはありません。報告はブリッジが受け取るまで outbox に残り、`task_update` と状態イベントはベストエフォートです。

## 機能状態

権威ある状態は Main Server の `/api/agent/*` プロキシから取得します。フロントエンドは `/api/agent/command` で変更し、`/api/agent/state` で更新します。ローカルのチェックボックス状態を正として扱ってはいけません。

| 状態 | 意味 |
|---|---|
| `analyzer_enabled` / UI `agent_enabled` | マスタースイッチ |
| `computer_use_enabled` | task agent が使うキーボード・マウス機能 |
| `browser_use_enabled` | task agent が使うブラウザー機能 |
| `user_plugin_enabled` | プラグインエントリ。直接呼び出しとゲートウェイ経由の両方に効く |

Manager と Agent Server は構築時にすべてオフです。ただし最初の実ユーザー `greeting_check` の後に、永続化された runtime intent が復元されるため、ページを更新するたびに設定がリセットされるわけではありません。キーボード・マウスとブラウザーは、さらに agent モデルの接続確認が通っている必要があります。

## 中止・上限・再起動

- 中止では、先に状態を `cancelled` と書き、その後プラグインの Run を取り消す（プラグインサービスの `POST /runs/{run_id}/cancel`）か、task agent のセッションを閉じます。`POST /admin/control` の `end_all` と、マスタースイッチをオフにする操作は、進行中の依頼をすべて中止します。
- キーボード・マウスとブラウザーは、それぞれ同時に 1 件の呼び出しだけを実行します。
- プラグインは `deferred: true` を返せます。依頼は `/api/agent/tasks/{task_id}/complete` が呼ばれるまで `working` のままで、1 時間たっても呼ばれなければ `failed` になります。
- 再起動後にホストが起動すると、前のプロセスが `queued`、`working`、`waiting_user` のまま残した依頼は `failed` になり、キャラクターの次のターンで報告されます。deferred の依頼は期限まで残ります。
- Agent プロセスは、HUD 用のタスク一覧に、終了したタスクを 5 分間残します。

## 実装マップ

| 関心事 | 現在の実装 |
|---|---|
| タスクツールとハンドラー | `main_logic/core/tool_calling.py`、`config/prompts/prompts_task_tools.py` |
| ターン終了時のターン後チェックの送信 | `main_logic/cross_server.py` |
| ZeroMQ ブリッジと ACK/再試行 | `main_logic/agent_event_bus.py` |
| dispatch、query、ターン後チェック | `nekoclaw/host/task_brain.py` |
| プラグインの選択と判断モデル | `nekoclaw/host/selection.py`、`decision.py` |
| 台帳、outbox、報告 | `nekoclaw/host/ledger.py`、`outbox.py`、`reports.py` |
| task agent と agent runtime | `nekoclaw/host/task_agents.py`、`nekoclaw/runtime/` |
| 能力一覧、ゲートウェイ、権限 | `nekoclaw/host/capabilities.py`、`gateway.py`、`permissions.py` |
| プラグインエントリの呼び出し（`/runs`） | `nekoclaw/host/plugin_runs.py` |
| ホストのマウント、HUD 用の一覧、組み込み機能 | `app/agent_server/nekoclaw_mount.py`、`nekoclaw_builtins.py` |
| Agent lifecycle / HTTP API | `app/agent_server/api_runtime.py`、`api_routes.py` |
| チャットへの結果配信 | `main_logic/core/proactive.py` |

公開制御 API は [Agent REST API](/ja/api/rest/agent)、内部ルートは [Agent Server API](/ja/api/agent-server) を参照してください。
