# 注册 LLM 工具调用（Tool Calling）

让 LLM 可以在对话过程中"调用"插件提供的功能。例如插件提供 `get_weather`，
LLM 在用户问"北京天气怎么样"时会自动调用，等待返回结果，再用返回值生成
最终回复。

本机制由 `main_logic/tool_calling.py` 的 `ToolRegistry` 支撑，对所有支持工具
调用的 provider（OpenAI / Gemini / GLM / Qwen Omni / StepFun 等）统一抽象。

## TL;DR —— 推荐路径：`@llm_tool`

如果你写的就是常规 `NekoPluginBase` 插件，**直接用 SDK 的 `@llm_tool` 装饰器**。
注册、注销、回调路由、shutdown 清理它全都帮你做了，零样板：

```python
from plugin.sdk.plugin import neko_plugin, NekoPluginBase, llm_tool, lifecycle, Ok

@neko_plugin
class WeatherPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready"})

    @llm_tool(
        name="get_weather",
        description="查询指定城市的天气。",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，如 '北京'"},
            },
            "required": ["city"],
        },
    )
    async def get_weather(self, *, city: str):
        return {"city": city, "temp_c": 22, "weather": "晴"}
```

整个集成就这些。装饰器在插件构造期间被 SDK 基类自动发现；插件服务通过 HTTP
向 `main_server` 注册工具，并把 LLM 的 dispatch 通过既有的 IPC 路由回插件
进程；插件停止时，每个注册的工具会以 best-effort 方式从 `main_server` 清掉。

helper **不**做的事：`main_server` 重启后或首启竞态后的自动重注册。注册仅在
插件启动时触发一次；如果当时 `main_server` 不可达，工具会对模型不可见，需要
插件 reload 或 imperative 调用 `register_llm_tool` 来恢复。本页末尾的
"What Happens When main_server Restarts" 段落讲了 resilience 模式。

下文讲底层 HTTP 协议本身，以及什么时候你需要直接走它。

## 架构

分两层叠在一起：

### 第一层 —— 原始 HTTP（通用）

```text
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

直接走这一层只在 SDK helper 覆盖不到的场景才必要 —— 比如插件进程之外另起一个
HTTP server，或者从非 NekoPluginBase 上下文（如外部脚本、extension 模块）注册。

### 第二层 —— `@llm_tool` SDK helper（插件首选）

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

插件进程不直接和 `main_server` 说 HTTP，它发一条 IPC，host 翻译成第一层的
HTTP 调用；main_server 的 dispatch 也走同一套 IPC trigger 管线（与
`@plugin_entry` 完全一致）回到插件。

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

## SDK Helper 参考（`@llm_tool`）

### `@llm_tool` 装饰器

定义在 `plugin/sdk/plugin/llm_tool.py`，从 SDK 顶层导入：
`from plugin.sdk.plugin import llm_tool`。

```python
@llm_tool(
    *,
    name: str | None = None,        # 默认取方法的 __name__
    description: str = "",          # 给 LLM 看的
    parameters: dict | None = None, # JSON Schema；默认无参数
    timeout: float = 30.0,          # 单次调用超时（秒，≤ 300）
    role: str | None = None,        # None = 全局，或指定猫娘名
)
```

被装饰的方法以 kwargs 形式接收解析后的 JSON 参数。建议在签名里用 `*` 强制
keyword-only，万一传进位置参数会立刻报错：

```python
@llm_tool(name="search", parameters={...})
async def search(self, *, query: str, limit: int = 10):
    ...
```

`name` 必须匹配 `[A-Za-z0-9_.\-]{1,64}`，这样能直接拼进 callback URL 路径段
不需要转义。

### `NekoPluginBase` 实例方法

参数 schema 在运行期才能确定的工具（比如根据配置动态生成）走 imperative API：

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

`unregister_llm_tool(name)` 反向，`list_llm_tools()` 以 dict list 返回当前已
注册的工具集合。重名会抛 `EntryConflictError`。

### 错误返回

普通值（`str` / `dict` / `int` ...）会作为成功结果回给 LLM。要不抛异常但向
LLM 标记工具级错误，返回这个 shape：

```python
return {"output": {"reason": "city not found"}, "is_error": True, "error": "CITY_NOT_FOUND"}
```

handler 里直接 `raise` 也会被翻译成错误回给 LLM（异常类名 + message 作为
error），插件本身不会崩溃 —— 只有那一次工具调用算失败。

### 生命周期与时序

- `@llm_tool` 装饰过的方法在 `NekoPluginBase.__init__` 末尾自动注册，也就是
  `super().__init__(ctx)` 一返回就完成；handler 真正执行要等到 LLM 选中该
  工具，所以在子类 `__init__` 还没 setup 完的时候完成注册是安全的（config
  字典、service client 等都在 handler 第一次被调用前已经就位）。
- IPC 通知（`LLM_TOOL_REGISTER`）会缓存在插件 host 的 message queue 上。
  如果通知到达时 `main_server` 还没起来，注册调用会失败、host 会打 warning
  —— 等 `main_server` 起来后通过 reload 插件或 imperative API 再注册一次。
- 插件停止时，`lifecycle_service.stop_plugin` 会调
  `plugin/server/messaging/llm_tool_registry.py::clear_plugin_tools`，发
  `POST /api/tools/clear`，body 为
  `{"source": "plugin:{plugin_id}", "role": null}`，一次性清掉该插件注册的
  所有工具。这步是 best-effort：如果当时 `main_server` 不可达，记日志后
  继续走 —— 进程重启或手动 `clear` 会自动收敛。

### 各文件的角色

| 文件 | 作用 |
|---|---|
| `plugin/sdk/plugin/llm_tool.py` | `@llm_tool` 装饰器、`LlmToolMeta`、name 校验、方法收集器 |
| `plugin/sdk/plugin/base.py` | `NekoPluginBase.register_llm_tool` / `unregister_llm_tool` / `list_llm_tools` 实例方法 + `__init__` 末尾自动注册 |
| `plugin/core/communication.py` | host 端 IPC handler `_handle_llm_tool_register` / `_handle_llm_tool_unregister`（在 `_MESSAGE_ROUTING` 里按 type 路由） |
| `plugin/server/messaging/llm_tool_registry.py` | 进程级 (plugin_id → tool 名集合) 索引 + httpx 包装 main_server 的 `/api/tools/{register,unregister,clear}` |
| `plugin/server/routes/llm_tools.py` | `/api/llm-tools/callback/{plugin_id}/{tool_name}` 路由，接收 main_server dispatch、走 `host.trigger` 转发到对应插件 |
| `plugin/server/application/plugins/lifecycle_service.py` | 插件停止时调 `clear_plugin_tools` |
