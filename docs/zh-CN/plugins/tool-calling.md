# 注册 LLM 工具调用（Tool Calling）

让 LLM 可以在对话过程中"调用"插件提供的功能。例如插件提供 `get_weather`，
LLM 在用户问"北京天气怎么样"时会自动调用，等待返回结果，再用返回值生成
最终回复。

本机制由 `main_logic/tool_calling.py` 的 `ToolRegistry` 支撑，对所有支持工具
调用的 provider（OpenAI / Gemini / GLM / Qwen Omni / StepFun 等）统一抽象。

## 架构

```
┌──────────────────┐  HTTP /api/tools/register   ┌──────────────────────┐
│  Plugin (process)│ ───────────────────────────▶│  Main Server         │
│                  │                             │  - ToolRegistry      │
│  callback_url    │ ◀──── HTTP POST tool ──────│  - Realtime / Offline│
│  /tool_invoke    │       call invocation      │    LLM clients       │
└──────────────────┘                             └──────────────────────┘
```

- 插件**通过 HTTP 注册**工具到 main_server 的 `LLMSessionManager.tool_registry`
- LLM 触发工具调用时，main_server **POST 到插件的 `callback_url`**
- 插件返回 JSON 结果，main_server 把结果喂回 LLM 继续生成

## 注册接口

所有端点都挂在 `MAIN_SERVER_PORT`（默认 `48911`），并强制 `verify_local_access`
（仅允许 `127.0.0.1` / `::1` / `localhost`）。

### `POST /api/tools/register`

```json
{
  "name": "get_weather",
  "description": "查询指定城市的天气",
  "parameters": {
    "type": "object",
    "properties": {
      "city": {"type": "string", "description": "城市名称，如 '北京'"}
    },
    "required": ["city"]
  },
  "callback_url": "http://127.0.0.1:<plugin_port>/tool_invoke",
  "role": null,
  "source": "my_plugin",
  "timeout_seconds": 30
}
```

| 字段 | 说明 |
|---|---|
| `name` | 工具名（≤64 字符），LLM 看到的就是它 |
| `description` | 描述给 LLM 看，决定它什么时候调用 |
| `parameters` | JSON Schema（OpenAI 风格） |
| `callback_url` | LLM 触发调用时 main_server POST 到的地址 |
| `role` | `null` = 注册到所有猫娘；指定字符串 = 只给那个猫娘用 |
| `source` | 自定义来源标签，方便后续按来源批量 `clear` |
| `timeout_seconds` | 单次调用超时（≤300，默认 30） |

返回：

```json
{ "ok": true, "registered": "get_weather", "affected_roles": ["小八"], "failed_roles": [] }
```

`affected_roles` 为空则 `ok=false`，并附带 `failed_roles[*].error` 详细原因。

### `POST /api/tools/unregister`

```json
{ "name": "get_weather", "role": null }
```

### `POST /api/tools/clear`

```json
{ "role": null, "source": "my_plugin" }
```

`source` 是**必填字段**（≥1 字符），HTTP 接口只支持按来源清理。空值会
被 422 拒绝。如果你需要"清空全部"语义，应该按来源逐个 `clear`，或者
直接调内部 `mgr.clear_tools()` —— 后者支持 `source=None`。

### `GET /api/tools[?role=<name>]`

返回当前已注册的工具列表。

## callback_url 协议

main_server 在 LLM 触发工具调用时会向 `callback_url` 发 `POST`：

**请求体**：

```json
{
  "name": "get_weather",
  "arguments": {"city": "北京"},
  "call_id": "call_abc123",
  "raw_arguments": "{\"city\":\"北京\"}"
}
```

`arguments` 是已 JSON-parse 的字典；`raw_arguments` 是原始字符串（极少数
情况下 LLM 流出的 arguments 是非法 JSON 时可以从这里救）。

**响应体**：

```json
{ "output": {"temp_c": 22, "weather": "晴"}, "is_error": false }
```

或失败：

```json
{ "output": null, "is_error": true, "error": "city not found" }
```

**`output` 字段提取规则**：main_server 调用 `body.get("output", body)`，
即响应体里**有 `output` 这个 key 时取它的值**喂给 LLM；没有 key 时把
整个 body 当 output。所以建议**始终显式包一层 `{"output": ...}`**，
否则 `is_error` / `error` 这些元数据会和你的真实结果混在一起被模型
当 output 看见——这一般会让模型困惑。

`output` 自身可以是任意 JSON（dict / list / 字符串 / 数字）。
`is_error: true` 时 LLM 会感知到工具调用失败，会选择跳过或换工具。

`callback_url` 可以是 `127.0.0.1:<plugin_port>` 上任意 path，由插件自己
开 HTTP server 接收。

## 完整生命周期 pattern

```python
import asyncio
import httpx

MAIN_SERVER = "http://127.0.0.1:48911"
MY_PORT = 9876
TOOL_NAME = "get_weather"

async def register_with_retry():
    """启动时调用：等 main_server 起来后注册工具，最多无限重试。"""
    payload = {
        "name": TOOL_NAME,
        "description": "查询指定城市的天气",
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
                pass  # main_server 还没起，等等再来
            await asyncio.sleep(2)

async def unregister_on_shutdown():
    """退出前调用：撤销工具，避免 LLM 撞到死掉的 callback_url。"""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.post(f"{MAIN_SERVER}/api/tools/unregister",
                              json={"name": TOOL_NAME, "role": None})
    except Exception:
        pass  # main_server 也死了就算了
```

绑定到 plugin lifecycle hook：

```python
from plugin.sdk.plugin import NekoPluginBase, plugin

@plugin
class WeatherPlugin(NekoPluginBase):
    async def on_start(self):
        # plugin 进程起来后异步注册，不阻塞 plugin 启动主流程
        asyncio.create_task(register_with_retry())
        # 同时起一个 HTTP server 接收 callback（FastAPI / aiohttp 都行）
        ...

    async def on_shutdown(self):
        await unregister_on_shutdown()
```

## main_server 重启会发生什么

⚠️ **重要**：`tool_registry` 是 `LLMSessionManager` 的内存属性，**main_server
重启会全部丢失**。需要 plugin 自己应对：

- **plugin 比 main_server 长寿**（更常见）：plugin 需要监听 main_server
  心跳/连接断开事件，重连后**重新调 register**。最简单的做法是 plugin 内
  起一个后台任务，定期 `GET /api/tools?role=...` 检查自己的工具是否还在，
  不在就重新 register
- **plugin 跟 main_server 同生死**：只要 plugin 启动 hook 里调了
  `register_with_retry`，main_server 重启时 plugin 也会被重启，自然会重新
  注册

## 切换猫娘

每个猫娘有独立的 `LLMSessionManager` 实例，但它们共享 plugin 注册的工具
（取决于 `role` 字段）：

- `role: null` 注册到所有猫娘 → 切换不需要重新注册
- `role: "小八"` 只注册到指定猫娘 → 切到别的猫娘后这个工具不可用，需要
  另外给那个猫娘也注册

切换猫娘**不会重启** main_server，所以不会丢失 registry。

## 同进程注册（高级）

如果你的 plugin 跑在同一 Python 进程（例如 extension 模式或内置功能），
可以绕过 HTTP 直接调 `LLMSessionManager.register_tool(...)`，让 `handler`
是个本地 callable，省掉 HTTP 转发：

```python
from main_logic.tool_calling import ToolDefinition

async def handle_get_weather(args: dict) -> dict:
    return {"temp_c": 22, "weather": "晴"}

mgr.register_tool(ToolDefinition(
    name="get_weather",
    description="查询指定城市的天气",
    parameters={...},
    handler=handle_get_weather,             # in-process callable
    metadata={"source": "my_extension"},    # source 标签塞 metadata
))
```

需要 await 直到 wire 同步完成时用 `await mgr.register_tool_and_sync(...)`。

## 注意事项

- **不要在工具名里放敏感信息**：LLM 会在生成时把工具名写进 tool_calls，
  最终持久化进对话历史
- **`callback_url` 必须指向本机 loopback**：服务端会用 `urlparse` +
  `ipaddress.ip_address` 校验 host 在 `127.0.0.0/8` / `::1` / 字面量
  `localhost` 之内，否则注册请求会被 422 拒绝。这是**两道独立闸门**：
  - `verify_local_access` 限制谁能调用 `/api/tools/register`（只允许
    本机来源）
  - `callback_url` host 白名单限制注册的回调地址（防止本地 caller 用
    main_server 当 SSRF 出站代理）
  跨主机的合法场景需要走独立的反向代理 + 显式授权流程
- **`timeout_seconds ≤ 300`**：超过 5 分钟的同步工具应该改成"立即返回 +
  通过 plugin 自己的事件机制异步推送结果"模式，否则会让对话整体卡死
- **工具失败要返回明确的错误**：`is_error: true` + 一句人类可读的 `error`，
  让 LLM 知道发生了什么；不要静默返回空结果，LLM 会困惑
- **重复 register 是覆盖语义**：同名工具会被新的覆盖，可以用来热更新参数
  schema
