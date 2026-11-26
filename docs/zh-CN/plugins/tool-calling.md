# 注册 LLM 工具调用（Tool Calling）

让 LLM 可以在对话过程中"调用"插件提供的功能。例如插件提供 `get_weather`，
LLM 在用户问"北京天气怎么样"时会自动调用，等待返回结果，再用返回值生成
最终回复。

## 选择 `@plugin_entry` 还是 `@llm_tool` {#choosing-an-entry-type}

| | `@plugin_entry` | `@llm_tool` |
|---|---|---|
| 是什么 | 插件的运行时入口 | 主对话模型工具表里的工具（main_server 的 `/api/tools` registry） |
| 谁来用 | 插件管理器、其他插件、Hosted UI 动作，以及 Agent（喵能力，NekoPower）的插件选择：由 Agent 侧的模型挑选后调用 | 主对话模型，在聊天当下直接调用 |
| 什么时候用 | **默认、推荐的形式**，插件对外提供能力都用它 | **克制使用**，只用于少数必须让主对话模型直接调用的能力 |

每个已注册 LLM 工具的 schema 都会随请求发给模型，多一个工具，模型的工具列表就长一截。
一个插件通常只需要很少几个 LLM 工具；能用 `@plugin_entry` 表达的，就保持为插件入口。
SDK 内部为了复用 IPC，会把 LLM 工具的 handler 存成保留前缀的动态入口（`__llm_tool__...`）；
带这个前缀的入口不会进入 Agent 的插件选择，也不会提供给 task agent，两套 API 不会混在一起。

本机制由 `main_logic/tool_calling.py` 的 `ToolRegistry` 支撑，对所有支持工具
调用的 provider（OpenAI / Gemini / GLM / Qwen Omni / StepFun 等）统一抽象。

## 需要把工具暴露给模型时：用 `@llm_tool`（不要走原生 HTTP 注册）

常规 `NekoPluginBase` 插件需要把工具暴露给对话模型时，**直接用 SDK 的 `@llm_tool` 装饰器**。
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
插件 reload 或重新注册工具才能恢复，安全的做法见本页末尾的
「main_server 重启会发生什么」一节。

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

这一层是宿主内部的契约，插件代码不应使用。只有宿主侧代码或运行在插件进程之外的
集成（例如从非 NekoPluginBase 上下文注册工具的外部服务）才直接走这一层。

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

::: warning 不供插件代码使用
插件进程不能直接调用 `main_server`（或 localhost 上任何 N.E.K.O 服务），也不能
自己起 HTTP server 接收回调：端口和地址随安装和开发实例而不同，这些端点是内部
接口、可能随时变化，而且会绕过 SDK 的权限检查。插件里请用 `@llm_tool` 或
`self.register_llm_tool(...)`。下面的端点说明供宿主侧和外部集成参考。
:::

所有端点都挂在 `MAIN_SERVER_PORT`（默认 `48911`），并强制 `verify_local_access`
（仅允许本机回环 `127.0.0.1` / `::1` / `localhost`；启用 `NEKO_BEHIND_PROXY` 时，
调用方必须通过本机回环直连且不附加 `X-Forwarded`、`X-Forwarded-*`、`X-Real-IP`
或 `Forwarded` 转发头——未携带有效实例凭据的请求会先被实例认证中间件以 HTTP `401`
拒绝，通过实例认证后仍携带转发头的请求则会被 `verify_local_access` 以 HTTP `403`
拒绝）。启用
`NEKO_BEHIND_PROXY` 或 `NEKO_ACTIVITY_TRACKER_REMOTE` 时，本机回环外部调用方同样
需要携带有效的实例凭据（例如来自 `NEKO_INSTANCE_ACCESS_KEY` 或 `instance_access.key`
的 `Authorization: Bearer <instance_key>`），否则请求会返回 HTTP `401`。如果同时启用了
`NEKO_REQUIRE_HTTPS`，即使密钥有效，明文 `http://` 请求也会被 HTTP `401` 拒绝，必须通过
HTTPS 连接——仅当同时启用 `NEKO_BEHIND_PROXY`、外层网关终止 TLS 且私有 HTTP 上游不对
不可信客户端暴露时，才允许 `Host` 匹配 `https://` 的 `NEKO_INSTANCE_PUBLIC_ORIGIN`
（仍需本机回环直连且不携带转发头）。

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

可选字段：`expected_source`（只删这个 source 的工具，其他归属的放进 `refused_roles`）和
`expected_registration_id`（只删 `/register` 返回的那个 `registration_id` 对应的那次注册；同名的其他注册放进
`refused_roles`，带 `"reason": "different registration"` 和那次注册的 `registration_id`；只有传了 `expected_source` 且与工具的 source 一致、并且传了的 `expected_host_instance_id` 也一致时，才会带这两个字段）。`/register` 接受可选的 `registration_id`，并总是返回
实际存下的那个。不带这些字段时行为不变。

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

`callback_url` 可以是回环地址上的任意 path。使用 `@llm_tool` 时，这个路由由
插件服务器提供（`/api/llm-tools/callback/{plugin_id}/{tool_name}`）；只有通过原始
HTTP 注册的外部集成才需要自己起 server 接收。

## 完整生命周期 pattern（原始 HTTP，外部集成）

这个 pattern 适用于运行在插件进程之外的集成。重试循环是这个示例自己实现的：`@llm_tool`
为插件提供注册、回调路由和清理，但只注册一次，不会重试。

```python
import asyncio
import logging
import os
import httpx

# 须本机回环直连且不得携带 X-Forwarded-*/X-Real-IP/Forwarded 头；启用 NEKO_REQUIRE_HTTPS 时
# 请改用 HTTPS 入口（明文 http:// 仅在启用 NEKO_BEHIND_PROXY、外层终止 TLS、私有上游不对外暴露且 Host 匹配 https:// NEKO_INSTANCE_PUBLIC_ORIGIN 时被接受）
MAIN_SERVER = "http://127.0.0.1:48911"
MY_PORT = 9876
TOOL_NAME = "get_weather"
# 仅当启用 NEKO_BEHIND_PROXY 或 NEKO_ACTIVITY_TRACKER_REMOTE 时需要
INSTANCE_KEY = os.environ.get("NEKO_INSTANCE_ACCESS_KEY", "")

async def register_with_retry():
    """启动时调用：等 main_server 起来后注册工具，遇到凭据拒绝则记日志并停止。"""
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
                pass  # main_server 还没起，等等再来
            await asyncio.sleep(2)

async def unregister_on_shutdown() -> bool:
    """退出前调用：撤销工具，避免 LLM 撞到死掉的 callback_url。"""
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
        return False  # main_server 也死了就算了
```

不要把它接到 `NekoPluginBase` 插件里。生命周期方法只有加了 `@lifecycle(id=...)`
才会运行（普通的 `on_start` / `on_shutdown` 方法永远不会被调用）；`startup` 在独立的
`asyncio.run` 里运行，在里面用 `asyncio.create_task` 创建的任务会在 `startup` 返回时
被取消；插件进程也不能调用 `main_server` 或自己起 HTTP server（见「注册接口」开头的
警告）。插件里用 `@llm_tool` 就能完成注册、回调路由和清理。

## main_server 重启会发生什么

⚠️ **重要**：`tool_registry` 是 `LLMSessionManager` 的内存属性，**main_server
重启会全部丢失**。

用 `@llm_tool` 的插件，恢复要由用户触发（或由用户调用的某个入口触发）：插件没有
合规的办法探测 main_server 是否重启过，也不能自己去轮询 main_server。

- 首选：重载插件。新进程会重新注册所有 `@llm_tool`。
- 不重载时：先调用 `self.unregister_llm_tool(name)`，再调用
  `self.register_llm_tool(...)`（本地仍登记着同名工具时直接注册，会在通知宿主
  前抛 `EntryConflictError`）。原来注册时的参数都要重新传一遍，尤其是 `role`
  和 `reply_tail`：默认值是 `role=None`（对所有角色可见）和 `reply_tail=False`。

无论哪种做法，只有注册到达时至少有一个匹配角色的会话管理器（`role=None` 时
匹配任意角色，否则匹配 `role` 指定的角色），宿主才会记录这次注册；之后它会生效
到这些角色上，并在之后新建的匹配角色上自动恢复。如果当时还没有匹配的角色（还没有
加载任何角色，或 `role` 指定的角色不可用），这次注册不会被记录，之后也不会自动
出现：等角色可用后再恢复。

运行在插件进程之外的原始 HTTP 集成需要自己应对：

- **集成比 main_server 长寿**（更常见）：外部进程需要监听 main_server
  心跳/连接断开事件，重连后**重新调 register**。最简单的做法是在这个外部
  进程里起一个后台任务，定期 `GET /api/tools?role=...` 检查自己的工具是否
  还在，不在就重新 register
- **集成跟 main_server 同生死**：只要外部进程启动时调了
  `register_with_retry`，并且会随 main_server 一起重启，自然会重新注册

## 切换猫娘

每个猫娘有独立的 `LLMSessionManager` 实例，但它们共享 plugin 注册的工具
（取决于 `role` 字段）：

- `role: null` 注册到所有猫娘 → 切换不需要重新注册
- `role: "小八"` 只注册到指定猫娘 → 切到别的猫娘后这个工具不可用，需要
  另外给那个猫娘也注册

切换猫娘**不会重启** main_server，所以不会丢失 registry。

## 同进程注册（高级）

如果代码运行在同一 Python 进程（例如内置功能），
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
    metadata={"source": "builtin_weather"}, # source 标签塞 metadata
))
```

需要 await 直到 wire 同步完成时用 `await mgr.register_tool_and_sync(...)`。

## 注意事项

- **不要在工具名里放敏感信息**：LLM 会在生成时把工具名写进 tool_calls，
  最终持久化进对话历史
- **不要返回或抛出密钥**：工具输出和错误文本会送回 LLM，也可能进入对话历史。请返回脱敏后的稳定错误码，并且不要把原始隐私 payload 写进 logger。
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
    reply_tail: bool = False,       # 选择接收原回复的图片上下文（_ctx.host_reply）
)
```

`reply_tail=True` 会把原回复的图片上下文作为 `_ctx.host_reply` 传给处理函数，
所以处理函数必须接受 `_ctx` 或 `**kwargs`。实时语音和旧版前端不会提供它。见
[原回复尾图接口](/plugin-reply-tail)。

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
    reply_tail=False,
)
```

`unregister_llm_tool(name)` 反向，`list_llm_tools()` 以 dict list 返回当前已
注册的工具集合。重名会抛 `EntryConflictError`。

这两个同步调用都是发出去就不管：返回 `True` 只表示插件本地已登记、已通知 host，
不代表 `main_server` 真的接受了。需要真实结果时用 async 版本：

```python
result = await self.register_llm_tool_async(
    name="custom_tool",
    description="...",
    parameters={"type": "object", "properties": {...}},
    handler=my_async_callable,
    request_timeout=40.0,  # 最多等多少秒，不超过 60（默认 40）
)
if not result["ok"]:
    self.logger.warning("tool not registered: {} {}", result["error"], result["failed_roles"])

result = await self.unregister_llm_tool_async("custom_tool")
```

host 走和通知相同的 `main_server` 调用，把 `main_server` 的答复带回来：

| 字段 | `register_llm_tool_async` | `unregister_llm_tool_async` |
|---|---|---|
| `ok` | 至少一个 role 接受了这个工具 | 已经没有 role 还挂着本插件的这个工具 |
| `removed` | — | `main_server` 确实删掉了东西 |
| `affected_roles` | 现在有这个工具的 role | 从中删掉了的 role |
| `failed_roles` | `[{"role", "error"}]` 没接受的 role（部分成功时也会有） | `[{"role", "error"}]` 删除失败的 role |
| `refused_roles` | — | `[{"role", "owned_by"}]` 同名工具属于其他 source 的 role，或（带 `"reason": "different registration"`）属于本插件另一次注册的 role，不会动它 |
| `error` | `None` 或下面的错误码 | `None` 或下面的错误码 |
| `message` | 可读的说明，或 `None` | 可读的说明，或 `None` |
| `cleanup` | `None`，或注册失败后清理用的那次注销的结果（见下） | — |

`error` 错误码：`rejected`（注册：没有 role 接受，例如名字已被其他 source 占用）、
`partial_failure`（注销：部分 role 失败）、`main_server_unavailable`（请求没能送到 `main_server`，什么都没生效）、
`main_server_error`（HTTP 状态码 >= 400，例如 role 不存在）、`timeout`（没拿到能用的回复：`request_timeout` 内 host 没回复、请求已发出后
`main_server` 没回复，或回复不是 JSON 对象；结果未知）、`host_unavailable`（请求没送到 host）、`busy`（同名前面的调用用掉了预算，这次来不及发出；什么都没发，`cleanup` 为 `None`。注销或 `cleanup` 已经发出的请求用掉了预算、剩下的不够再删它们报告的过期注册（见下文）时也是 `busy`：那次删除没有发出，这个注册仍列在 `refused_roles` 里）、`not_registered`（注销：插件本地没有这个名字的记录，
`main_server` 上也没删到本插件的这个名字；本地有记录时同样的答复是 `ok=True, removed=False`，并删掉本地 handler）、`invalid_request`。

本地状态跟着 `ok` 走，插件和 host 两边一致：

- `register_llm_tool_async` 先装好 handler 再问 host（`main_server` 一登记模型就可能调用），
  `ok` 为假或调用被取消时回滚，之后同名可以再注册。
- 注册失败不一定代表 `main_server` 上什么都没发生：它先存下工具再逐个 role 同步，所有 role 同步都
  失败时工具仍然留着；`timeout` 也说明不了到底生效没有。所以注册以 `rejected`、`timeout` 或
  `host_unavailable` 失败后，SDK 会对这个名字补发一次注销，和这个名字的其他 async 调用按顺序排队，
  结果放在 `cleanup` 里（最多让这次调用多花 15 秒）。其他错误说明什么都没存下，`cleanup` 为 `None`。
  `cleanup["ok"]` 为假时，之后再调 `unregister_llm_tool_async(name)`。`timeout` 之后的清理只能尽力：
  `main_server` 可能还在处理那次注册、在清理之后才存下工具，所以 `removed=False` 不能证明什么都没留下；
  在意的话过一会儿再调一次 `unregister_llm_tool_async(name)`。被取消的注册只回滚本地，不补发注销。
- 每次 `register_llm_tool_async` 都带一个自己的注册 ID，它的补发清理和之后的 `unregister_llm_tool_async`
  只删这一次注册。所以超时了、但还在 `main_server` 上执行的清理或注销，删不掉同名的新注册；那个 role 会出现在
  `refused_roles` 里，带 `"reason": "different registration"`。这样的注册已经过期（比如超时的注册后来才落地），
  所以补发清理和 `unregister_llm_tool_async` 都会按 `main_server` 报告的 ID 把它也删掉（不会发不带 ID 的宽泛注销），
  报告的注册全部删掉后才返回 `ok`。补发清理到这一步时如果插件本地又有了这个名字的 handler（例如别的线程用同步接口注册了它），
  报告的注册可能正是那个在用的，补发清理就不删它，它仍留在 `refused_roles` 里。通过同步接口或 `@llm_tool` 注册的工具，以及本地已经
  没有的名字，注销时不带 ID（删本插件的任何一次注册），和以前一样。
- `unregister_llm_tool_async` 只在 `ok` 为真时删掉 handler；否则工具在插件里照旧登记，
  和 host 一致，还挂着它的 role 照常能用，再调一次就是重试。
- host 注销时总带上 `expected_source=plugin:{plugin_id}`（plugin_id 取自插件连接本身），
  插件不可能删掉其他 source 的同名工具。
- `unregister_llm_tool_async(name)` 对本地已经没有的名字也有效：照样发给 host、按全部 role 注销，
  `main_server` 上的残留会被删掉。插件停止时也会尝试清掉本插件的所有工具，但只是尽力而为：
  那一刻 `main_server` 连不上的话，工具可能还留着，等它恢复后要确认（或手动清掉）。
- 同一个名字的 async 调用按调用顺序逐个执行，每个都等上一个的答复；来自不同事件循环（例如入口和定时器）的调用也一样。例如
  `gather(register_llm_tool_async("t", ...), unregister_llm_tool_async("t"))` 里，注销要等注册拿到答复后才发出。
  这里只保证顺序：每个调用的结果要各自检查（注销返回 `ok=False` 时本地 handler 会保留）。
  不同名字之间互不等待。`request_timeout` 管整次调用，包括排在同名前面调用后面等待的时间；剩下的不够发请求时（少于 5 秒，预算很小时少于一半），返回 `busy`，这时什么都没发给 host。
  从有截止时间的入口里调用时，每个请求（包括补发的注销）实际可用的时间取它自己的预算和入口剩余时间中较小的那个；
  入口时间用完时补发的注销发不出去（`cleanup` 里是 `timeout`）。

参数错误和同步版本一样抛异常（`ValueError`、`TypeError`、`EntryConflictError`）。async 版本
另外在 `request_timeout` 不在 (0, 60] 内（host 的上限）、名字全是点、或注册时 `timeout` 不在 (0, 300]
内（`main_server` 的上限）时抛 `ValueError`；同步版本保持原有行为。host 和 `main_server` 的失败不抛异常。
同一个工具名只用一种写法：同步调用不参与按名字的排队，且走不同的 IPC 通道，混用没有顺序保证。
`@llm_tool` 装饰的方法在启动时走的就是这条通知通道；如果插件可能在启动后马上用
`unregister_llm_tool_async` 注销它，就改用 `register_llm_tool_async` 注册，不要用装饰器。

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
  —— 等 `main_server` 起来后，按「main_server 重启会发生什么」一节的做法恢复。
- 插件停止时，`lifecycle_service.stop_plugin` 会调
  `plugin/server/messaging/llm_tool_registry.py::clear_plugin_tools`，发
  `POST /api/tools/clear`，body 为
  `{"source": "plugin:{plugin_id}", "role": null}`，一次性清掉该插件注册的
  所有工具。这步是 best-effort：如果当时 `main_server` 不可达，记日志后
  继续走 —— 进程重启或手动 `clear` 会自动收敛。

### 各文件的角色

| 文件 | 作用 |
|---|---|
| `plugin/sdk/plugin/llm_tool.py` | `@llm_tool` 装饰器、`LlmToolMeta`、name 校验、方法收集器、async 结果类型（`LlmToolRegisterResult` / `LlmToolUnregisterResult`） |
| `plugin/sdk/plugin/base.py` | `NekoPluginBase.register_llm_tool` / `unregister_llm_tool` / `list_llm_tools` 实例方法及其 `_async` 版本 + `__init__` 末尾自动注册 |
| `plugin/core/communication.py` | host 端 IPC handler `_handle_llm_tool_register` / `_handle_llm_tool_unregister`（在 `_MESSAGE_ROUTING` 里按 type 路由） |
| `plugin/server/messaging/handlers/llm_tools.py` | `_async` 版本背后的 host 端请求-响应 handler `LLM_TOOL_REGISTER_REQUEST` / `LLM_TOOL_UNREGISTER_REQUEST` |
| `plugin/server/messaging/llm_tool_registry.py` | 进程级 (plugin_id → tool 名集合) 索引 + httpx 包装 main_server 的 `/api/tools/{register,unregister,clear}` |
| `plugin/server/routes/llm_tools.py` | `/api/llm-tools/callback/{plugin_id}/{tool_name}` 路由，接收 main_server dispatch、走 `host.trigger` 转发到对应插件 |
| `plugin/server/application/plugins/lifecycle_service.py` | 插件停止时调 `clear_plugin_tools` |
