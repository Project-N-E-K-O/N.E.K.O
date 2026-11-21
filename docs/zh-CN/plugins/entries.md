# 入口与参数

入口点是已加载插件暴露给插件管理器、其他插件和 Agent（猫爪，NekoClaw）的可调用操作。它由 `@plugin_entry(id=...)` 产生的运行时 `entry_id` 标识。

不要把它与 `plugin.toml` 的 `[plugin].entry` 混淆：后者是宿主导入插件类时使用的 `module.path:ClassName`。也不要与 `@llm_tool` 混淆：LLM 工具注册到 main_server 的 `/api/tools` registry，由主对话模型在对话中调用；Agent 则是在一件事被交给它之后，才去选择插件的运行时入口。

Agent 先筛选插件，再读取入口元数据。精选必须返回精确存在的 `plugin_id` 和 `entry_id`；缺失或未知 ID 不会追问，也不会直达调用任何入口：这件事交给 task agent，task agent 不可用时回「办不了」。入口什么时候被调用、由谁调用、结果怎么处理，见下文「Agent 怎么调用入口」。

---

## 最简单的入口

你想让插件做一件事，就给方法加上 `@plugin_entry`：

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):

    @plugin_entry(id="hello", name="打招呼", description="说一声 hello")
    async def hello(self):
        return Ok({"message": "Hello!"})
```

这个入口没有参数。在插件管理面板中点击执行，就会返回 `{"message": "Hello!"}`。

---

## 加上参数

大多数入口需要接收输入。直接在函数签名里写参数就行：

```python
@plugin_entry(id="greet", name="问候", description="按名字问候")
async def greet(self, name: str, times: int = 1):
    messages = [f"Hello, {name}!" for _ in range(times)]
    return Ok({"messages": messages})
```

SDK 会自动做这些事：
- 从 `name: str` 和 `times: int = 1` 生成输入表单（在面板中显示）
- `name` 没有默认值 → 必填
- `times` 有默认值 `1` → 选填
- 类型注解会作为表单/schema 提示。直接调用入口时普通参数会原样传入；需要运行时校验时请使用 Pydantic 模型。

你不需要手写任何 JSON Schema。

---

## 给参数加描述

面板中显示的参数名默认就是变量名（`name`、`times`）。想让用户看到更友好的说明，用 `Annotated`：

```python
from typing import Annotated

@plugin_entry(id="greet", name="问候", description="按名字问候")
async def greet(
    self,
    name: Annotated[str, "要问候的人的名字"],
    times: Annotated[int, "重复几次"] = 1,
):
    messages = [f"Hello, {name}!" for _ in range(times)]
    return Ok({"messages": messages})
```

现在面板中 `name` 旁边会显示"要问候的人的名字"，`times` 旁边显示"重复几次"。

调用这个入口的 task agent 也能看到这些描述，因为它拿到的是入口完整的 `input_schema`。直达调用入口的精选只能看到参数名、类型和 `enum` 取值，所以它必须知道的事要写进入口的 `description`。两种调用方的区别见下文「Agent 怎么调用入口」。

---

## 参数很多时：用 Pydantic 模型

如果你的入口有很多参数，或者需要复杂的验证规则（最小值、最大值、正则匹配等），把参数定义成一个 Pydantic 模型：

```python
from pydantic import BaseModel, Field

class SearchParams(BaseModel):
    query: str = Field(..., description="搜索关键词")
    max_results: int = Field(default=10, ge=1, le=50, description="最大结果数")
    language: str = Field(default="zh-CN", description="结果语言")
    include_images: bool = Field(default=False, description="是否包含图片结果")

@plugin_entry(id="search", name="搜索", description="搜索内容")
async def search(self, params: SearchParams):
    self.logger.info("搜索: {} (最多{}条)", params.query, params.max_results)
    results = await self._do_search(params.query, params.max_results)
    return Ok({"results": results, "count": len(results)})
```

SDK 看到函数只有一个参数且类型是 BaseModel，会自动：
1. 从模型生成输入表单（带描述、默认值、约束提示）
2. 调用时用 `model_validate()` 验证输入（比如 `max_results=100` 会被拒绝，因为 `le=50`）
3. 把验证通过的模型实例传给你的函数

**什么时候用 Pydantic，什么时候用普通参数？**

- 参数 ≤ 3 个，没有复杂验证 → 直接写在函数签名里
- 参数多、需要验证约束、想要更好的文档 → 用 Pydantic 模型

---

## 返回成功和失败

入口点必须返回 `Ok(...)` 或 `Err(...)`：

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="divide", name="除法", description="两数相除")
async def divide(self, a: float, b: float):
    if b == 0:
        return Err(SdkError("不能除以零"))
    return Ok({"result": a / b})
```

- `Ok(data)` — 成功。`data` 可以是字典、列表、字符串、数字
- `Err(SdkError("原因"))` — 失败。错误信息会显示在面板中，也会告诉 AI "这次调用失败了"

**为什么不直接 raise 异常？** 因为插件运行在独立进程中，异常不会传播到主系统。`Ok`/`Err` 是跨进程安全的通信方式。当然，如果你的代码意外抛了异常，框架会自动捕获并转成 `Err`，不会崩溃。

---

## 控制 AI 看到的返回内容

用 `llm_result_fields` 声明 AI 读结果里的哪些字段。返回值里常常有大量原始数据（比如完整的搜索结果列表），而 AI 只需要看到摘要：

```python
@plugin_entry(
    id="search",
    name="搜索",
    description="网络搜索",
    llm_result_fields=["summary", "count"],
)
async def search(self, query: str):
    results = await self._do_search(query)
    summary = self._build_summary(results)
    return Ok({
        "summary": summary,          # ← AI 能看到
        "count": len(results),        # ← AI 能看到
        "raw_results": results,       # ← AI 看不到，但数据仍然存储
    })
```

`llm_result_fields` 列出的字段才会发送给 AI。其他字段正常存储，面板中也能看到，只是不会塞进 AI 的上下文里（省 token）。

没有声明 `llm_result_fields` 时，AI 拿到什么取决于是谁调用了入口，见下文「Agent 怎么调用入口」里的结果对照表。

---

## 输出契约

`output_schema` 声明入口返回值的结构，让别的插件和工具可以依赖它。它和 `llm_result_fields` 是两回事：后者选 AI 读哪些字段，`output_schema` 描述整个返回值。

**从返回注解推导。** 处理函数的返回注解是 Pydantic 模型（或 `Model | None`）时，输出契约就是这个模型，不用另外写：

```python
class SearchReply(BaseModel):
    summary: str
    count: int

@plugin_entry(id="search")
async def search(self, query: str) -> SearchReply:
    ...
```

**显式声明。** 返回普通 dict 的入口，传一个 JSON Schema：

```python
@plugin_entry(
    id="search",
    output_schema={
        "type": "object",
        "properties": {"summary": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["summary", "count"],
    },
)
async def search(self, query: str):
    return Ok({"summary": "...", "count": 3})
```

显式的 `output_schema` 优先于返回注解。两者都没有的入口没有输出契约，行为和以前完全一样。

**检查什么：和入参对称。** 入参写 `params=Model` 时运行时会校验，只写 `input_schema` 字典时只发布、不校验；输出也一样：

- **返回注解是 Pydantic model**（`-> Model` 或 `-> Model | None`；`list[Model]` 等其他写法不推导）：每次调用成功后、自动保存状态之前，用返回的数据构造这个 model 来校验（和 `params` 的做法相同），所以 field / model validator 和 `HttpUrl` 这类类型都会生效。直接返回 model 实例时视为已校验（构造时已经校验过）；`Model | None` 时返回 `None` 也能通过。检查的是 `Ok(...)` 里的值、直接返回的值，或 `finish()`/`ok()` 信封里的 `data`；`Err(...)` 和 `fail()` 信封本身就是失败，不检查。发布出去的 schema 是 model 的 `model_json_schema()`，和 `params` 的发布方式相同。
- **显式写的 `output_schema` 字典**：和 `input_schema` 一样只发布，运行时不校验，`strict_output` 对它不起作用。
- 默认不符合时只在插件日志里记一条告警，返回值原样返回。告警只列字段路径和错误类型（例如 `$.count (int_parsing)`），不记录返回的内容。同一个入口的同一种不符合，一分钟内最多记一次，下一条会写明期间被抑制了多少条。
- 设 `strict_output=True` 时，不符合会让本次调用失败，错误码是 `PLUGIN_OUTPUT_SCHEMA_VIOLATION`，其他插件经 `call_entry` 调用时能拿到这个错误码；`/runs` 按现有行为把插件错误显示为 `INTERNAL`。字段路径记在插件日志的告警里。这时会跳过自动保存状态，但 handler 已经做过的事不会回滚。validator 自身崩溃时只记告警，不会让调用失败。

**请返回 model 本身，不要返回序列化后的字典。** 检查是用返回的数据、按 model 的输入规则构造 model。model 已经序列化过的数据不一定能还原：`finish(data=model)` 输出的是字段名（字段设了别名时会被报成缺失），`@field_serializer`、`exclude=True`、校验别名和序列化别名不同，也都会改变形状。这类 model 请直接返回实例，或者不开 `strict_output`。

插件的入口列表里会带上 `output_schema`（和 `strict_output`）。`register_dynamic_entry()` 和 Router 的 `add_entry()` 也能声明显式的 `output_schema`（只发布）；运行时检查只来自 `@plugin_entry` 上的 Pydantic 返回注解。写在 `[[plugin.entries]]` 表里的 `output_schema`/`strict_output` 会被忽略（`neko-plugin validate` 会提示）。任务 Agent 的能力网关不对外发布它。

---

## 入口的其他选项

`@plugin_entry` 还支持这些选项：

```python
@plugin_entry(
    id="process",              # 入口 ID（默认用方法名）
    name="处理数据",            # 显示名称
    description="处理并转换数据", # 描述（给人和 AI 看）
    timeout=60.0,              # 宿主侧执行超时（秒）
    kind="service",            # 类型标记（默认 "action"）
)
async def process(self, data: str):
    ...
```

大多数情况下你只需要 `id`、`name`、`description`。其他选项按需使用。需要启动时初始化逻辑时，请定义 `startup` 生命周期钩子，不要期待入口会自动执行。

---

## 动态入口：运行时注册

有时候你不知道插件会有哪些入口——可能取决于配置文件、用户设置、或者外部服务返回的能力列表。这时候用动态注册：

```python
from plugin.sdk.plugin import lifecycle, Ok

@lifecycle(id="startup")
async def on_startup(self):
    # 假设配置里定义了一组命令
    cfg = await self.config.dump()
    commands = cfg.get("commands", {})

    for cmd_id, cmd_info in commands.items():
        self.register_dynamic_entry(
            entry_id=cmd_id,
            handler=self._make_handler(cmd_info),
            name=cmd_info.get("name", cmd_id),
            description=cmd_info.get("description", ""),
            input_schema=cmd_info.get("input_schema", {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "命令输入"},
                },
            }),
        )

    self.logger.info("注册了 {} 个动态入口", len(commands))
    return Ok({"status": "ready"})

def _make_handler(self, cmd_info):
    template = cmd_info.get("template", "执行了: {cmd}")
    async def handler(cmd: str = ""):
        return Ok({"output": template.format(cmd=cmd)})
    return handler
```

动态注册的入口和静态的一样——在面板中可见、可执行、可被 AI 调用。如果 handler 接收参数，需要显式传入 `input_schema`；动态注册不会从 handler 签名推导。

用 `self.unregister_dynamic_entry(entry_id)` 可以移除。

---

## Agent 怎么调用入口

只有主对话模型把一件事交出来时，Agent（猫爪，NekoClaw）才会去找入口：模型调用了 `dispatch_task` 工具，或者口头答应了、由事后检查替它派发。系统不再在每轮对话结束后自动分析。整条路径见[智能体系统](/zh-CN/architecture/agent-system)，插件怎么筛选见 [plugin.toml](./plugin-toml)。ID 以 `__llm_tool__` 开头的入口（由 `@llm_tool` 生成）不参与这套筛选，只由主对话模型调用。

### Agent 能看到哪些入口

插件设置了 `passive = true`，或者入口 `metadata` 里的 `agent_auto`、`agent_exposed`、`llm_exposed` 为 `false`，或 `agent_hidden` 为 `true` 时，这个入口不参与：

```python
@plugin_entry(id="internal_sync", metadata={"agent_hidden": True})
async def internal_sync(self):
    ...
```

这些开关只影响 Agent。入口仍然可以从插件管理器和其他插件调用。

### 两种调用方

- **直达调用（主路径）。** 一个入口就能办完的事，由精选选中这个入口并填好参数。它看到的是插件的 `description`，以及每个可见入口的 ID、`description` 和一段简短的参数提示：前 8 个参数的类型和 `enum` 取值，加上 `required`。参数的描述不在其中。
- **task agent。** 选不中入口，或者要分多步的事，交给 task agent，也就是界面上叫「使魔」的执行会话。它经能力网关把入口当工具调用：工具的描述由插件名、入口名和入口的 `description` 组成（超过 200 token 截断），工具的参数就是入口的 `input_schema`。task agent 拿到的是交办的内容，不是对话历史。筛选时入围的插件，其入口会直接列给它；其余可见入口要通过一个按描述匹配的查找工具取得。

两种调用方发出的是同一种 `/runs` 请求，注入 `args._ctx` 的键也相同：

| `_ctx` 的键 | 值 |
|---|---|
| `lanlan_name` | 这件事所属的角色 |
| `conversation_id` | 随派发带来的标识；没有就不注入 |
| `entry_timeout` | 这次调用适用的超时：入口的 `timeout`，或宿主默认值 |
| `latest_user_request` | 那一轮的用户原话，不改写 |

插件入口这一类权限（宿主 `permissions.json` 里的 `plugin_entry`）默认是 `allow`。设成询问类的模式后，task agent 的调用会先等用户批准；设成 `deny` 时，两种调用方都不会用到插件入口。

### 结果怎么处理

| | 直达调用 | task agent 调用 |
|---|---|---|
| 谁读结果 | 主对话模型，作为这件事的汇报 | task agent，作为工具结果；整件事结束时用户才得知 |
| 声明了 `llm_result_fields` | 只有列出的字段 | 只有列出的字段 |
| 没声明 `llm_result_fields` | 一句话：`data.reply` 是非空字符串时用它，否则用 Run 的 message | 这句话，后面跟上 JSON 形式的 `data` |
| `meta.agent.delivery`、`result_kind`、`expires_in_s` | 生效 | 不生效；对用户的汇报按这件事自己的设置 |
| `data.needs_confirmation`（或 `status: "confirm_required"`） | 这件事以 `blocked` 结束，并汇报插件给出的信息 | 网关把插件给出的信息作为提问交给用户，再把回答交给 task agent；task agent 不能代用户回答 |
| `data.action: "clarify"`（或 `status: "clarify"` / `"blocked"`） | 这件事以 `blocked` 结束，并汇报插件给出的信息 | 加一句「这次调用没有执行」的说明后回给 task agent |
| `data.deferred: true` | 这件事保持未结束，直到插件回调；一小时没有回调就标为失败 | 加一句「插件稍后自行完成」的说明后回给 task agent；不调用 `bind_task` |

直达调用这一列和旧版 Agent 相同。结果里带 `observation_only` 时，两种调用方都不再按 `needs_confirmation` 或 `clarify` 处理。

### 叫停

一件事被叫停时，宿主会对它名下仍在进行的每个 Run 调用插件服务的 `POST /runs/{run_id}/cancel`。直达调用和 task agent 的调用都一样。

---

## 小结

| 场景 | 做法 |
|------|------|
| 简单功能，几个参数 | `@plugin_entry` + 函数签名类型注解 |
| 想给参数加描述 | 用 `Annotated[类型, "描述"]` |
| 参数多/需要验证 | 定义 Pydantic 模型作为单参数 |
| 返回成功 | `return Ok({...})` |
| 返回失败 | `return Err(SdkError("原因"))` |
| 限制 AI 看到的字段 | `llm_result_fields=["field1", "field2"]` |
| 声明入口的返回结构 | 返回注解写 Pydantic 模型，或 `output_schema={...}` |
| 运行时才知道有哪些功能 | `self.register_dynamic_entry(...)` |
