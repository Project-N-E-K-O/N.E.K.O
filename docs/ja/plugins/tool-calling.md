# LLM ツール呼び出し（Tool Calling）の登録

LLM が会話中にプラグインの機能を「呼び出せる」ようにします。例えば
プラグインが `get_weather` を提供している場合、ユーザーが「東京の
天気は？」と聞いたときに LLM が自動的に呼び出し、結果を待って最終
応答を生成します。

このメカニズムは `main_logic/tool_calling.py` の `ToolRegistry` に
よって支えられ、ツール呼び出しをサポートする全ての provider
（OpenAI / Gemini / GLM / Qwen Omni / StepFun など）に対して統一的に
抽象化されています。

## アーキテクチャ

```
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

`source` が空のときは全部クリア、指定すればその source のみ。

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

`output` は任意の JSON（dict / list / 文字列 / 数値）。main_server は
ボディ全体を `ToolResult.output` として LLM に返します。
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
    handler=handle_get_weather,   # in-process callable
    source="my_extension",
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
