# LLM ツール呼び出し（Tool Calling）の登録

LLM が会話中にプラグインの機能を「呼び出せる」ようにします。例えば
プラグインが `get_weather` を提供している場合、ユーザーが「東京の
天気は？」と聞いたときに LLM が自動的に呼び出し、結果を待って最終
応答を生成します。

このメカニズムは `main_logic/tool_calling.py` の `ToolRegistry` に
よって支えられ、ツール呼び出しをサポートする全ての provider
（OpenAI / Gemini / GLM / Qwen Omni / StepFun など）に対して統一的に
抽象化されています。

## TL;DR — 推奨パス：`@llm_tool`

通常の `NekoPluginBase` プラグインを書いているなら、**SDK の `@llm_tool`
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
プラグインを reload するか、命令的に `register_llm_tool` を呼び直すまで
復活しません。本ページ末尾の "What Happens When main_server Restarts"
セクションが resilience パターンを詳述しています。

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

この層を直接叩くのは SDK helper で済まないケースのみ — プラグインプロセス
の外で独自の HTTP server を立てる場合や、NekoPluginBase 以外のコンテキスト
（外部スクリプト、extension モジュール等）から登録する場合など。

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

すべてのエンドポイントは `MAIN_SERVER_PORT`（デフォルト `48911`）に
マウントされ、`verify_local_access` が `127.0.0.1` / `::1` /
`localhost` のみ許可します。

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

`callback_url` は `127.0.0.1:<plugin_port>` 上の任意のパスで OK。
プラグインは自前で HTTP server を立てて受信します。

## 完全なライフサイクル例

```python
import asyncio
import httpx

MAIN_SERVER = "http://127.0.0.1:48911"
MY_PORT = 9876
TOOL_NAME = "get_weather"

async def register_with_retry():
    """起動時に呼ぶ：main_server が立ち上がるまで無限リトライで登録。"""
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
    async with httpx.AsyncClient() as client:
        while True:
            try:
                r = await client.post(f"{MAIN_SERVER}/api/tools/register",
                                       json=payload, timeout=5)
                if r.json().get("ok"):
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass  # main_server がまだ起動していない、後で再試行
            await asyncio.sleep(2)

async def unregister_on_shutdown():
    """終了前に呼ぶ：ツールを取り消し、LLM が死んだ callback_url に当たらないように。"""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.post(f"{MAIN_SERVER}/api/tools/unregister",
                              json={"name": TOOL_NAME, "role": None})
    except Exception:
        pass  # main_server も死んでいるなら諦める
```

プラグインのライフサイクルフックにバインド：

```python
from plugin.sdk.plugin import NekoPluginBase, plugin

@plugin
class WeatherPlugin(NekoPluginBase):
    async def on_start(self):
        # 非同期で登録（プラグイン起動メインフローをブロックしない）
        asyncio.create_task(register_with_retry())
        # コールバック受信用の HTTP server も起動（FastAPI / aiohttp など）
        ...

    async def on_shutdown(self):
        await unregister_on_shutdown()
```

## main_server 再起動時の挙動

⚠️ **重要**：`tool_registry` は `LLMSessionManager` のインメモリ属性
なので、**main_server 再起動で全部失われます**。プラグイン側で対応が
必要：

- **プラグインが main_server より長生き**（より一般的）：プラグインは
  ハートビート / 接続切断を監視し、復旧後に **再登録** する必要があり
  ます。最も簡単な方法はバックグラウンドタスクで定期的に
  `GET /api/tools?role=...` を確認し、無ければ再登録すること
- **プラグインが main_server と運命共同体**：プラグイン起動フックで
  `register_with_retry` を呼んでいれば、main_server 再起動時に
  プラグインも再起動するので、自動的に再登録される

## 猫娘切り替え

各猫娘は独立した `LLMSessionManager` インスタンスを持ちますが、
プラグインで登録されたツールは（`role` フィールドにより）共有可能：

- `role: null` で全猫娘に登録 → 切り替え時に再登録不要
- `role: "小八"` で特定猫娘のみ登録 → 別の猫娘ではそのツールは使えず、
  別途その猫娘にも登録する必要あり

猫娘切り替えは main_server を **再起動しません** ので、registry は
保持されます。

## 同プロセス登録（高度な使い方）

プラグインが同じ Python プロセスで動く場合（extension モード、
組み込み機能など）は、HTTP をバイパスして `LLMSessionManager.register_tool(...)`
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
    metadata={"source": "my_extension"},    # source タグは metadata へ
))
```

wire 同期完了まで `await` したい場合は
`await mgr.register_tool_and_sync(...)` を使用。

## 注意事項

- **ツール名に機密情報を含めない**：LLM が `tool_calls` にツール名を
  書き込み、最終的に会話履歴に永続化されます
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
)
```

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
)
```

`unregister_llm_tool(name)` が逆操作。`list_llm_tools()` は現在登録中の
ツールを dict のリストで返します。重複名は `EntryConflictError` を投げます。

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
  起動した後にプラグイン reload か命令的 API で登録し直してください。
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
| `plugin/sdk/plugin/llm_tool.py` | `@llm_tool` デコレーター、`LlmToolMeta`、name バリデーション、メソッドコレクター |
| `plugin/sdk/plugin/base.py` | `NekoPluginBase.register_llm_tool` / `unregister_llm_tool` / `list_llm_tools` インスタンスメソッド + `__init__` 末尾の自動登録 |
| `plugin/core/communication.py` | ホスト側 IPC ハンドラ `_handle_llm_tool_register` / `_handle_llm_tool_unregister`（`_MESSAGE_ROUTING` でメッセージタイプから dispatch） |
| `plugin/server/messaging/llm_tool_registry.py` | プロセスレベルの (plugin_id → tool 名集合) インデックス + main_server の `/api/tools/{register,unregister,clear}` を叩く httpx ラッパー |
| `plugin/server/routes/llm_tools.py` | `/api/llm-tools/callback/{plugin_id}/{tool_name}` ルート、main_server の dispatch を `host.trigger` で対応プラグインへ転送 |
| `plugin/server/application/plugins/lifecycle_service.py` | プラグイン停止時に `clear_plugin_tools` を呼ぶ |
