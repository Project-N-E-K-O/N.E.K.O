# LLM ツール呼び出し（Tool Calling）の登録

LLM が会話中にプラグインの機能を「呼び出せる」ようにします。例えば
プラグインが `get_weather` を提供している場合、ユーザーが「東京の
天気は？」と聞いたときに LLM が自動的に呼び出し、結果を待って最終
応答を生成します。

## `@plugin_entry` と `@llm_tool` の選び方 {#choosing-an-entry-type}

| | `@plugin_entry` | `@llm_tool` |
|---|---|---|
| 何か | プラグインのランタイムエントリー | メインの対話モデルのツール一覧に入るツール（main_server の `/api/tools` registry） |
| 誰が使うか | Plugin Manager、他のプラグイン、Hosted UI のアクション、Agent（にゃんパワー、NekoPower）の plugin 選択（Agent 側のモデルが選んで呼び出す） | メインの対話モデル（会話中に直接呼び出す） |
| いつ使うか | **既定の、推奨される形**。プラグインの機能はこれで公開する | **控えめに**。メインの対話モデルが直接呼び出す必要がある少数の機能だけ |

登録された LLM tool の schema はすべてリクエストと一緒にモデルへ送られるため、
ツールが増えるほどモデルのツール一覧が長くなります。1 つのプラグインに必要な
LLM tool は通常ごく少数です。`@plugin_entry` で表現できる機能は plugin entry の
ままにしてください。SDK 内部は IPC reuse のため LLM tool の handler を reserved
dynamic entry（`__llm_tool__...`）として保持しますが、この prefix を持つ entry は
Agent の plugin 選択にも task agent にも渡されないため、2 つの API は混ざりません。

このメカニズムは `main_logic/tool_calling.py` の `ToolRegistry` に
よって支えられ、ツール呼び出しをサポートする全ての provider
（OpenAI / Gemini / GLM / Qwen Omni / StepFun など）に対して統一的に
抽象化されています。

## ツールをモデルに公開するとき：`@llm_tool` を使う（生の HTTP 登録は使わない）

通常の `NekoPluginBase` プラグインがツールを対話モデルに公開するときは、**SDK の `@llm_tool`
デコレーターを使ってください**。登録 / 解除 / コールバックルーティング /
shutdown クリーンアップを SDK が肩代わりするので、ボイラープレートはゼロ：

```python
from plugin.sdk.plugin import neko_plugin, NekoPluginBase, llm_tool, lifecycle, Ok

@neko_plugin
class WeatherPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready"})

    @llm_tool(
        name="get_weather",
        description="指定都市の天気を検索する。",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "都市名（例: '東京'）"},
            },
            "required": ["city"],
        },
    )
    async def get_weather(self, *, city: str):
        return {"city": city, "temp_c": 22, "weather": "晴れ"}
```

統合作業はこれだけです。デコレーターはプラグイン構築時に SDK 基底クラスに
よって自動発見され、プラグインサーバーが `main_server` に HTTP で登録し、
モデルからの dispatch は既存 IPC 経由でプラグインプロセスへ戻されます。
プラグインが停止すると、登録済みの全ツールは `main_server` から best-effort
で除去されます。

helper が **やらない** こと：`main_server` の再起動や初回起動レースからの
自動復旧。登録はプラグインスタートアップ時に一度だけ発火し、その瞬間
`main_server` が到達不能ならツールはモデルから不可視のままです —
プラグインを reload するか、ツールを登録し直すまで復活しません。安全な手順は
本ページ末尾の「main_server 再起動時の挙動」を参照してください。

このドキュメントの残りは下層の HTTP コントラクトと、それを直接叩く必要が
ある場面を説明します。

## アーキテクチャ

統合は 2 層に分かれます：

### 第 1 層 — 生 HTTP（汎用）

```text
┌──────────────────┐  HTTP /api/tools/register   ┌──────────────────────┐
│  Plugin (process)│ ───────────────────────────▶│  Main Server         │
│                  │                             │  - ToolRegistry      │
│  callback_url    │ ◀──── HTTP POST tool ──────│  - Realtime / Offline│
│  /tool_invoke    │       call invocation      │    LLM clients       │
└──────────────────┘                             └──────────────────────┘
```

- プラグインは **HTTP でツールを登録** します
  （`LLMSessionManager.tool_registry` に格納）
- LLM がツール呼び出しを発火すると、main_server が **プラグインの
  `callback_url` に POST**
- プラグインが JSON 結果を返し、main_server が LLM にフィードバック
  して生成を続行

この層はホスト内部の契約で、プラグインのコードからは使いません。直接叩くのは
ホスト側のコードや、プラグインプロセスの外で動く連携（NekoPluginBase 以外の
コンテキストからツールを登録する外部サービスなど）だけです。

### 第 2 層 — `@llm_tool` SDK helper（プラグインの推奨）

```text
                 (1) IPC: LLM_TOOL_REGISTER
                          ┌──────────────────────────────┐
                          ▼                              │
┌────────────────────┐         ┌──────────────────────┐  │  ┌─────────────────┐
│ Plugin process     │         │ user_plugin_server   │──┼─▶│  Main Server    │
│  @llm_tool methods │         │ /api/llm-tools/      │  │  │  ToolRegistry   │
│                    │◀────────│  callback/{pid}/{n}  │◀─┼──│ POSTs callback  │
│  IPC trigger       │  (3)    │ POST main_server     │  │  │ when LLM picks  │
└────────────────────┘  via    └──────────────────────┘  │  │ the tool        │
                       host.trigger      ▲               │  └─────────────────┘
                                          │              │           │
                                          └──────────────┘           │
                                              (2) HTTP /api/tools/register
                                                  with callback_url pointing
                                                  back at user_plugin_server
```

プラグインプロセスは `main_server` と直接 HTTP で話しません。IPC を 1 本
発行するだけで、ホストがそれを第 1 層の HTTP 呼び出しに翻訳します。
main_server からの dispatch も同じ IPC trigger 配管（`@plugin_entry`
と完全に同じ）でプラグインへ戻ります。

## 登録エンドポイント

::: warning プラグインのコード向けではない
プラグインプロセスから `main_server`（や localhost 上の他の N.E.K.O サーバー）を
直接呼んだり、コールバック受信用に独自の HTTP server を立てたりしないでください。
ポートやアドレスはインストールや開発インスタンスごとに異なり、これらのエンド
ポイントは内部用で予告なく変わり、SDK の権限チェックも迂回してしまいます。
プラグインでは `@llm_tool` か `self.register_llm_tool(...)` を使ってください。
以下はホスト側と外部連携のための説明です。
:::

すべてのエンドポイントは `MAIN_SERVER_PORT`（デフォルト `48911`）に
マウントされ、`verify_local_access` がループバック（`127.0.0.1` / `::1` /
`localhost`）のみを許可します（`NEKO_BEHIND_PROXY` 有効時は `X-Forwarded`、
`X-Forwarded-*`、`X-Real-IP`、`Forwarded` などの転送ヘッダーなしで直接ループバック
接続する必要があります。有効なインスタンス認証情報がないリクエストはまずインスタンス認証
ミドルウェアにより HTTP `401` で拒否され、認証通過後も転送ヘッダーが残っているリクエストは
`verify_local_access` により HTTP `403` で拒否されます）。
`NEKO_BEHIND_PROXY` または `NEKO_ACTIVITY_TRACKER_REMOTE` が有効な場合は、
ループバック上の外部連携でも有効なインスタンス認証情報（`NEKO_INSTANCE_ACCESS_KEY`
または `instance_access.key` 由来の `Authorization: Bearer <instance_key>` など）を
送信する必要があり、未認証のリクエストには HTTP `401` が返ります。さらに
`NEKO_REQUIRE_HTTPS` も有効な場合は、有効なキーがあっても平文の `http://`
リクエストは HTTP `401` で拒否されるため、HTTPS で接続する必要があります（平文
`http://` が許可されるのは、`NEKO_BEHIND_PROXY` が有効で外層 TLS ゲートウェイが
TLS を終端し、非公開の HTTP アップストリームが信頼できないクライアントに公開されていない
構成で、`Host` が `https://` の `NEKO_INSTANCE_PUBLIC_ORIGIN` に一致し、かつ転送ヘッダーなしの
直接ループバック接続である場合のみです）。

### `POST /api/tools/register`

```json
{
  "name": "get_weather",
  "description": "指定都市の天気を検索する",
  "parameters": {
    "type": "object",
    "properties": {
      "city": {"type": "string", "description": "都市名（例: '東京'）"}
    },
    "required": ["city"]
  },
  "callback_url": "http://127.0.0.1:<plugin_port>/tool_invoke",
  "role": null,
  "source": "my_plugin",
  "timeout_seconds": 30
}
```

| フィールド | 説明 |
|---|---|
| `name` | ツール名（≤64 文字）。LLM が見るのはこの名前 |
| `description` | LLM 向けの説明。いつ呼ぶかを決定する |
| `parameters` | JSON Schema（OpenAI スタイル） |
| `callback_url` | LLM が呼び出したとき main_server が POST する先 |
| `role` | `null` = 全猫娘に登録 / 名前指定 = 特定猫娘のみ |
| `source` | カスタム送信元タグ。後でまとめて `clear` するときに便利 |
| `timeout_seconds` | 1 回の呼び出しタイムアウト（≤300、デフォルト 30） |

レスポンス：

```json
{ "ok": true, "registered": "get_weather", "affected_roles": ["小八"], "failed_roles": [] }
```

`affected_roles` が空のとき `ok=false` となり、`failed_roles[*].error`
に詳細が入ります。

### `POST /api/tools/unregister`

```json
{ "name": "get_weather", "role": null }
```

任意フィールド：`expected_source`（その source のツールだけを削除し、他の所有者のものは `refused_roles` に入れる）と
`expected_registration_id`（`/register` が返した `registration_id` の登録だけを削除し、同じ名前の別の登録は
`"reason": "different registration"` とその登録の `registration_id` 付きで `refused_roles` に入れる。この 2 つのフィールドは、`expected_source` が指定されツールの source と一致し、`expected_host_instance_id` を指定した場合はそれも一致するときだけ付きます）。`/register` は任意の `registration_id` を
受け付け、実際に保存した ID を常に返します。これらのフィールドがなければ動作は変わりません。

### `POST /api/tools/clear`

```json
{ "role": null, "source": "my_plugin" }
```

`source` は **必須**（≥1 文字）。HTTP エンドポイントは source 指定での
クリアのみサポート、空値は 422 で拒否されます。「全部クリア」が
必要な場合は source ごとに繰り返すか、インプロセスの
`mgr.clear_tools()`（`source=None` 可）を直接呼んでください。

### `GET /api/tools[?role=<name>]`

現在登録されているツールリストを返します。

## callback_url プロトコル

LLM がツール呼び出しを発火すると、main_server が `callback_url` に
`POST` します：

**リクエストボディ**：

```json
{
  "name": "get_weather",
  "arguments": {"city": "東京"},
  "call_id": "call_abc123",
  "raw_arguments": "{\"city\":\"東京\"}"
}
```

`arguments` は JSON パース済み dict、`raw_arguments` は元の文字列
（LLM が無効な JSON を生成したまれな場合に使えます）。

**レスポンスボディ**：

```json
{ "output": {"temp_c": 22, "weather": "晴れ"}, "is_error": false }
```

または失敗時：

```json
{ "output": null, "is_error": true, "error": "city not found" }
```

**`output` 抽出ルール**：main_server は `body.get("output", body)` を
呼び出します — レスポンスボディに `output` キーがあればその値を LLM
に渡し、なければボディ全体を output として扱います。**常に
`{"output": ...}` で明示的にラップ** することを推奨します。そうしない
と `is_error` / `error` などのメタデータが実結果と同列に並び、モデル
が混乱します。

`output` 自体は任意の JSON（dict / list / 文字列 / 数値）が可能。
`is_error: true` の場合、LLM は呼び出し失敗を認識してスキップしたり
別のツールを選んだりします。

`callback_url` はループバックアドレス上の任意のパスで OK。`@llm_tool` を使う
場合、このルートはプラグインサーバーが提供します
（`/api/llm-tools/callback/{plugin_id}/{tool_name}`）。自前の server が必要なのは、
生 HTTP で登録する外部連携だけです。

## 完全なライフサイクル例（生 HTTP、外部連携向け）

このパターンはプラグインプロセスの外で動く連携向けです。リトライループは
この例自身の実装です。`@llm_tool` はプラグインに登録、コールバックの
ルーティング、後片付けを提供しますが、登録は一度だけでリトライはしません。

```python
import asyncio
import logging
import os
import httpx

# X-Forwarded-*/X-Real-IP/Forwarded なしの直接ループバック接続が必要。NEKO_REQUIRE_HTTPS 有効時は
# HTTPS エントリを使用（平文 http:// は NEKO_BEHIND_PROXY 有効・外層 TLS 終端・非公開アップストリームかつ Host が https:// の NEKO_INSTANCE_PUBLIC_ORIGIN に一致する場合のみ許可）
MAIN_SERVER = "http://127.0.0.1:48911"
MY_PORT = 9876
TOOL_NAME = "get_weather"
# NEKO_BEHIND_PROXY または NEKO_ACTIVITY_TRACKER_REMOTE が有効な場合のみ必要
INSTANCE_KEY = os.environ.get("NEKO_INSTANCE_ACCESS_KEY", "")

async def register_with_retry():
    """起動時に呼ぶ：main_server が立ち上がるまでリトライで登録（認証拒否時はログを出して終了）。"""
    payload = {
        "name": TOOL_NAME,
        "description": "指定都市の天気を検索する",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        "callback_url": f"http://127.0.0.1:{MY_PORT}/tool_invoke",
        "role": None,
        "source": "my_plugin",
        "timeout_seconds": 30,
    }
    headers = {"Authorization": f"Bearer {INSTANCE_KEY}"} if INSTANCE_KEY else {}
    async with httpx.AsyncClient(trust_env=False) as client:
        while True:
            try:
                r = await client.post(f"{MAIN_SERVER}/api/tools/register",
                                       json=payload, headers=headers, timeout=5)
                if r.status_code in (401, 403):
                    logging.error("Tool registration rejected with HTTP %s: %s", r.status_code, r.text)
                    return
                if r.is_success and r.json().get("ok"):
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass  # main_server がまだ起動していない、後で再試行
            await asyncio.sleep(2)

async def unregister_on_shutdown() -> bool:
    """終了前に呼ぶ：ツールを取り消し、LLM が死んだ callback_url に当たらないように。"""
    headers = {"Authorization": f"Bearer {INSTANCE_KEY}"} if INSTANCE_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            r = await client.post(f"{MAIN_SERVER}/api/tools/unregister",
                                  json={"name": TOOL_NAME, "role": None},
                                  headers=headers)
            if r.status_code in (401, 403):
                logging.error("Tool unregistration rejected with HTTP %s: %s", r.status_code, r.text)
                return False
            if not r.is_success:
                logging.error("Tool unregistration failed with HTTP %s: %s", r.status_code, r.text)
                return False
            data = r.json()
            failed_roles = data.get("failed_roles") or []
            if failed_roles:
                logging.error("Tool unregistration failed for roles: %s", failed_roles)
                return False
            return True
    except Exception:
        return False  # main_server も死んでいるなら諦める
```

これを `NekoPluginBase` プラグインに組み込まないでください。ライフサイクル
メソッドは `@lifecycle(id=...)` を付けたときだけ実行され（普通の `on_start` /
`on_shutdown` メソッドは呼ばれません）、`startup` は独立した `asyncio.run` で
実行されるため、その中で `asyncio.create_task` で作成したタスクは `startup` が
戻った時点でキャンセルされます。また、プラグインプロセスは `main_server` を呼んだり
自前の HTTP server を立てたりしてはいけません（「登録エンドポイント」冒頭の警告を
参照）。プラグインでは `@llm_tool` が登録、コールバックのルーティング、
後片付けをまとめて行います。

## main_server 再起動時の挙動

⚠️ **重要**：`tool_registry` は `LLMSessionManager` のインメモリ属性
なので、**main_server 再起動で全部失われます**。

`@llm_tool` を使うプラグインの復旧は、ユーザー（またはユーザーが呼ぶエントリー）
が起点になります。プラグインには main_server の再起動を検知する正規の手段がなく、
main_server を自分でポーリングしてもいけません。

- 推奨：プラグインを再読み込みする。新しいプロセスがすべての `@llm_tool` を
  登録し直します。
- 再読み込みしない場合：`self.unregister_llm_tool(name)` の後に
  `self.register_llm_tool(...)` を呼ぶ（ローカルに同名ツールが残ったまま
  登録すると、ホストへ通知する前に `EntryConflictError` になります）。元の
  登録時の引数はすべて渡し直してください。特に `role` と `reply_tail` は、
  省略するとデフォルトの `role=None`（全キャラクターから見える）と
  `reply_tail=False` になります。

どちらの方法でも、ホストが登録を記録するのは、登録が届いた時点で該当する
キャラクターのセッションマネージャーが少なくとも 1 つ存在する場合だけです
（`role=None` なら任意のキャラクター、それ以外は `role` のキャラクター）。記録された
登録はそれらのキャラクターに反映され、後から作られる該当キャラクターにも自動で
復元されます。該当するキャラクターがまだない場合（キャラクターが未読み込み、または
`role` のキャラクターが利用できない）、登録は記録されず、後からツールが自動で
出てくることもありません。キャラクターが利用可能になってから復旧してください。

プラグインプロセスの外で動く生 HTTP の連携は、自分で対応が必要です：

- **連携が main_server より長生き**（より一般的）：外部プロセスは
  ハートビート / 接続切断を監視し、復旧後に **再登録** する必要があり
  ます。最も簡単な方法は、その外部プロセスのバックグラウンドタスクで定期的に
  `GET /api/tools?role=...` を確認し、無ければ再登録すること
- **連携が main_server と運命共同体**：外部プロセスが起動時に
  `register_with_retry` を呼び、main_server と一緒に再起動されるなら、
  自動的に再登録される

## 猫娘切り替え

各猫娘は独立した `LLMSessionManager` インスタンスを持ちますが、
プラグインで登録されたツールは（`role` フィールドにより）共有可能：

- `role: null` で全猫娘に登録 → 切り替え時に再登録不要
- `role: "小八"` で特定猫娘のみ登録 → 別の猫娘ではそのツールは使えず、
  別途その猫娘にも登録する必要あり

猫娘切り替えは main_server を **再起動しません** ので、registry は
保持されます。

## 同プロセス登録（高度な使い方）

コードが同じ Python process で動く場合（組み込み機能など）は、HTTP をバイパスして `LLMSessionManager.register_tool(...)`
を直接呼び、`handler` をローカルの callable にできます：

```python
from main_logic.tool_calling import ToolDefinition

async def handle_get_weather(args: dict) -> dict:
    return {"temp_c": 22, "weather": "晴れ"}

mgr.register_tool(ToolDefinition(
    name="get_weather",
    description="指定都市の天気を検索する",
    parameters={...},
    handler=handle_get_weather,             # in-process callable
    metadata={"source": "builtin_weather"}, # source タグは metadata へ
))
```

wire 同期完了まで `await` したい場合は
`await mgr.register_tool_and_sync(...)` を使用。

## 注意事項

- **ツール名に機密情報を含めない**：LLM が `tool_calls` にツール名を
  書き込み、最終的に会話履歴に永続化されます
- **secret を return/raise しない**：tool output と error text は LLM に戻され、conversation history に残る場合があります。redacted stable error code を返し、raw private payload を logger に書かないでください。
- **`callback_url` はローカル loopback でなければならない**：サーバー
  は `urlparse` + `ipaddress.ip_address` で host が `127.0.0.0/8` /
  `::1` / 字面 `localhost` の範囲内かを検証し、それ以外は 422 で
  拒否します。これは **2 つの独立したゲート**：
  - `verify_local_access` は誰が `/api/tools/register` を呼べるかを
    制限（呼び出し元）
  - `callback_url` host ホワイトリストは登録される callback アドレス
    を制限（ローカル caller が main_server を SSRF 出口プロキシとして
    悪用するのを防ぐ）
  クロスホストの正当ユースケースは独立した reverse proxy + 明示的
  認可フローを通すべき
- **`timeout_seconds ≤ 300`**：5 分を超える同期ツールは「即座に返し、
  プラグイン独自のイベント機構で非同期にプッシュ」に再設計すべき。
  そうしないと会話全体が固まります
- **失敗時は明確なエラーを返す**：`is_error: true` ＋ 人間可読な
  `error` で LLM に状況を伝えること。空結果を黙って返すと LLM が
  混乱します
- **重複 `register` は上書き意味論**：同名ツールは新しいもので
  上書きされ、パラメータ schema のホット更新に使えます

## SDK Helper リファレンス（`@llm_tool`）

### `@llm_tool` デコレーター

`plugin/sdk/plugin/llm_tool.py` で定義。SDK トップレベルから import：
`from plugin.sdk.plugin import llm_tool`。

```python
@llm_tool(
    *,
    name: str | None = None,        # デフォルトはメソッドの __name__
    description: str = "",          # LLM に見せる説明
    parameters: dict | None = None, # JSON Schema、デフォルトは引数なし
    timeout: float = 30.0,          # 1 回の呼び出しタイムアウト（秒、≤ 300）
    role: str | None = None,        # None = 全グローバル、または特定キャラ名
    reply_tail: bool = False,       # 元の返信の画像コンテキスト（_ctx.host_reply）を受け取る
)
```

`reply_tail=True` にすると、元の返信の画像コンテキストが `_ctx.host_reply`
としてハンドラーに渡されるため、ハンドラーは `_ctx` か `**kwargs` を受け取る
必要があります。リアルタイム音声と古いフロントエンドでは提供されません。
[元の返信の末尾画像](/plugin-reply-tail)を参照してください。

デコレートされたメソッドは、解析済み JSON 引数を kwargs として受け取ります。
シグネチャに `*` を入れて keyword-only を強制すると、誤って位置引数で
呼ばれた時に即エラーになります：

```python
@llm_tool(name="search", parameters={...})
async def search(self, *, query: str, limit: int = 10):
    ...
```

`name` は `[A-Za-z0-9_.\-]{1,64}` にマッチする必要があります — エンコード
無しでコールバック URL のパスセグメントに直接埋め込めるようにするためです。

### `NekoPluginBase` インスタンスメソッド

スキーマがランタイムにしか決まらないツール（設定駆動など）は命令的 API を
使ってください：

```python
self.register_llm_tool(
    name="custom_tool",
    description="...",
    parameters={"type": "object", "properties": {...}},
    handler=my_async_callable,
    timeout=30.0,
    role=None,
    reply_tail=False,
)
```

`unregister_llm_tool(name)` が逆操作。`list_llm_tools()` は現在登録中の
ツールを dict のリストで返します。重複名は `EntryConflictError` を投げます。

この 2 つの同期呼び出しは送りっぱなしです。`True` はプラグイン側で登録してホストへ通知した
という意味で、`main_server` が受け付けたことは意味しません。実際の結果が必要なら async 版を使います：

```python
result = await self.register_llm_tool_async(
    name="custom_tool",
    description="...",
    parameters={"type": "object", "properties": {...}},
    handler=my_async_callable,
    request_timeout=40.0,  # 待つ秒数、最大 60（既定 40）
)
if not result["ok"]:
    self.logger.warning("tool not registered: {} {}", result["error"], result["failed_roles"])

result = await self.unregister_llm_tool_async("custom_tool")
```

ホストは通知と同じ `main_server` 呼び出しを行い、`main_server` の応答を返します：

| フィールド | `register_llm_tool_async` | `unregister_llm_tool_async` |
|---|---|---|
| `ok` | 少なくとも 1 つの role がツールを受け付けた | このプラグインのツールを持つ role がもう残っていない |
| `removed` | — | `main_server` が実際に何かを削除した |
| `affected_roles` | 現在ツールを持つ role | 削除された role |
| `failed_roles` | `[{"role", "error"}]` 受け付けなかった role（部分成功時も含む） | `[{"role", "error"}]` 削除に失敗した role |
| `refused_roles` | — | `[{"role", "owned_by"}]` 同名ツールが別 source のもの、または（`"reason": "different registration"` 付きで）このプラグインの別の登録のもので、触れなかった role |
| `error` | `None` または下記のコード | `None` または下記のコード |
| `message` | 人が読める説明、または `None` | 人が読める説明、または `None` |
| `cleanup` | `None`、または登録失敗後の後片付けとして送った解除の結果（下記） | — |

`error` コード：`rejected`（登録：どの role も受け付けなかった。例：名前が別 source に使われている）、
`partial_failure`（解除：一部の role が失敗）、`main_server_unavailable`（リクエストが `main_server` に届かず、何も適用されていない）、
`main_server_error`（HTTP ステータス >= 400。例：存在しない role）、`timeout`（使える応答がなかった：`request_timeout` 内にホストが応答しなかった、
リクエスト送信後に `main_server` が応答しなかった、または応答が JSON オブジェクトでなかった。結果は不明）、`host_unavailable`（リクエストがホストに届かなかった）、
`busy`（同じ名前の先行呼び出しが予算を使い切り、この呼び出しは送られなかった。何も送られておらず `cleanup` は `None`）、`not_registered`（解除：プラグイン側にその名前の記録がなく、`main_server` 上でもこのプラグインのその名前の
ツールが何も削除されなかった。ローカルに記録がある場合、同じ応答は `ok=True, removed=False` となりローカルのハンドラを外します）、
`invalid_request`。

ローカル状態は `ok` に従うので、プラグインとホストの状態が一致します：

- `register_llm_tool_async` はホストに問い合わせる前にハンドラを設置し（`main_server` が登録した
  瞬間からモデルが呼べるため）、`ok` が偽のとき、または呼び出しがキャンセルされたときに取り消します。
  その後、同じ名前を再登録できます。
- 登録の失敗は `main_server` 上で何も起きなかったことを意味するとは限りません。`main_server` はツールを
  保存してから role ごとに同期し、全 role の同期が失敗してもツールは残ります。`timeout` も何が適用された
  かは分かりません。そのため登録が `rejected`、`timeout`、`host_unavailable` で失敗すると、SDK はその名前の
  解除を 1 回送り（同じ名前の他の async 呼び出しと順番に実行）、結果を `cleanup` に入れます（呼び出しが
  最大 15 秒延びることがあります）。それ以外のエラーは何も保存されていないので `cleanup` は `None` です。
  `cleanup["ok"]` が偽なら、後で `unregister_llm_tool_async(name)` を呼んでください。`timeout` 後の解除は
  ベストエフォートです。`main_server` がまだ登録を処理中で、解除の後にツールを保存することがあるため、
  `removed=False` は何も残っていないことの証明になりません。重要なら少し後でもう一度
  `unregister_llm_tool_async(name)` を呼んでください。キャンセルされた登録はローカルを取り消すだけで、解除は送りません。
- `register_llm_tool_async` の呼び出しごとに専用の登録 ID が付き、その後片付けと後の
  `unregister_llm_tool_async` はその登録だけを削除します。そのため、タイムアウトしたがまだ `main_server` 上で
  実行中の後片付けや解除が、同じ名前の新しい登録を消すことはありません。その role は
  `"reason": "different registration"` 付きで `refused_roles` に入ります。そうした登録は古いもの（例：タイムアウトした登録が
  後から届いたもの）なので、後片付けと `unregister_llm_tool_async` はどちらも `main_server` が報告した ID を指定して
  それも削除し（ID なしの広い解除は送りません）、報告された登録がすべて消えてから `ok` を返します。同期版や `@llm_tool` で登録したツール、
  ローカルにもう無い名前は、従来どおり ID なし（このプラグインのどの登録でも）で解除されます。
- `unregister_llm_tool_async` は `ok` が真のときだけハンドラを外します。それ以外ではホスト側と同様に
  プラグイン側でも登録が残り、まだツールを持つ role は動き続け、再度呼べばリトライになります。
- ホストは解除時に常に `expected_source=plugin:{plugin_id}`（plugin_id はプラグインの接続から取得）
  を付けるため、プラグインが別 source の同名ツールを消すことはできません。
- `unregister_llm_tool_async(name)` はローカルにない名前にも使えます。ホストへ送られ、全 role を対象に
  解除されるため、`main_server` 上の残留は削除されます。プラグイン停止時にもこのプラグインのツールの消去を
  試みますが、ベストエフォートです。その時点で `main_server` に接続できないとツールが残ることがあるため、
  復旧後に確認（または手動で消去）してください。
- 同じ名前の async 呼び出しは呼び出し順に 1 つずつ実行され、それぞれ前の呼び出しの応答を待ちます。
  異なるイベントループ（例：エントリとインターバルタイマー）からの呼び出しも同様です。
  例えば `gather(register_llm_tool_async("t", ...), unregister_llm_tool_async("t"))` では、解除は登録の応答が
  返ってから送られます。保証されるのは順序だけなので、それぞれの呼び出しの結果を確認してください（解除が
  `ok=False` を返した場合、ローカルのハンドラは残ります）。異なる名前同士は待ち合わせません。`request_timeout` は呼び出し全体に
  適用され、同じ名前の先行呼び出しを待つ時間も含みます。リクエストを送るのに残りが足りない場合（5 秒未満、
  予算が小さいときはその半分未満）は `busy` となり、ホストには何も送られていません。期限のあるエントリから
  呼んだ場合、各リクエスト（後片付けの解除を含む）の実際の予算は自身の予算とエントリの残り時間の短い方です。
  エントリの時間を使い切ると後片付けの解除は送られません（`cleanup` は `timeout`）。

引数エラーは同期版と同じく例外になります（`ValueError`、`TypeError`、`EntryConflictError`）。
async 版はさらに、`request_timeout` が (0, 60] の範囲外（ホストの上限）、名前が点だけ、登録時の `timeout`
が (0, 300] の範囲外（`main_server` の上限）の場合にも `ValueError` を投げます。同期版は従来の動作のままです。
ホストと `main_server` の失敗は例外になりません。同じツール名には一方の書き方だけを使ってください。同期版は
名前ごとの順序付けに参加せず、別の IPC チャネルを通るため、混在させると順序は保証されません。
`@llm_tool` で装飾したメソッドは起動時にこの通知チャネルで登録されます。起動直後に
`unregister_llm_tool_async` で解除する可能性があるなら、デコレーターではなく `register_llm_tool_async` で登録してください。

### エラーの返し方

普通の値（`str` / `dict` / `int` 等）を返せば成功扱いで LLM に返ります。
例外を投げずに LLM へツールレベルのエラーを通知したい場合は、この shape
の dict を返してください：

```python
return {"output": {"reason": "city not found"}, "is_error": True, "error": "CITY_NOT_FOUND"}
```

handler 内で `raise` した場合も LLM にエラーが返されます（例外クラス名 +
メッセージが error として転送される）。プラグインはクラッシュしません —
そのツール呼び出しだけが失敗扱いになります。

### ライフサイクルと順序

- `@llm_tool` 付きメソッドは `NekoPluginBase.__init__` の末尾、つまり
  `super().__init__(ctx)` がリターンした直後に自動登録されます。handler
  自体は LLM がツールを選ぶまで実行されないので、サブクラスの `__init__`
  が完了する前に登録される時点で安全です（config dict、サービス
  クライアント等は handler 初実行までに揃っています）。
- IPC 通知（`LLM_TOOL_REGISTER`）はプラグインホストキューに buffer
  されます。通知到着時 `main_server` がまだ起動していない場合、登録呼び
  出しは失敗してホスト側で warning ログが出ます — `main_server` が
  起動した後に「main_server 再起動時の挙動」の手順で復旧してください。
- プラグイン停止時、`lifecycle_service.stop_plugin` が
  `plugin/server/messaging/llm_tool_registry.py::clear_plugin_tools` を
  呼び、`POST /api/tools/clear` をボディ
  `{"source": "plugin:{plugin_id}", "role": null}` で送信して、その
  プラグインが登録した全ツールを 1 往復で削除します。クリーンアップは
  best-effort：その瞬間 `main_server` が到達不能ならログだけ残して
  処理続行 — プロセス再起動か手動 `clear` で reconcile されます。

### コードの所在

| ファイル | 役割 |
|---|---|
| `plugin/sdk/plugin/llm_tool.py` | `@llm_tool` デコレーター、`LlmToolMeta`、name バリデーション、メソッドコレクター、async 結果型（`LlmToolRegisterResult` / `LlmToolUnregisterResult`） |
| `plugin/sdk/plugin/base.py` | `NekoPluginBase.register_llm_tool` / `unregister_llm_tool` / `list_llm_tools` インスタンスメソッドとその `_async` 版 + `__init__` 末尾の自動登録 |
| `plugin/core/communication.py` | ホスト側 IPC ハンドラ `_handle_llm_tool_register` / `_handle_llm_tool_unregister`（`_MESSAGE_ROUTING` でメッセージタイプから dispatch） |
| `plugin/server/messaging/handlers/llm_tools.py` | `_async` 版の裏側にあるホスト側リクエスト／レスポンスハンドラ `LLM_TOOL_REGISTER_REQUEST` / `LLM_TOOL_UNREGISTER_REQUEST` |
| `plugin/server/messaging/llm_tool_registry.py` | プロセスレベルの (plugin_id → tool 名集合) インデックス + main_server の `/api/tools/{register,unregister,clear}` を叩く httpx ラッパー |
| `plugin/server/routes/llm_tools.py` | `/api/llm-tools/callback/{plugin_id}/{tool_name}` ルート、main_server の dispatch を `host.trigger` で対応プラグインへ転送 |
| `plugin/server/application/plugins/lifecycle_service.py` | プラグイン停止時に `clear_plugin_tools` を呼ぶ |
