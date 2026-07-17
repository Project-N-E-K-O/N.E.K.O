# 入口与参数

入口点是已加载插件暴露给插件管理器、其他插件和用户插件 Agent 路由的可调用操作。它由 `@plugin_entry(id=...)` 产生的运行时 `entry_id` 标识。

不要把它与 `plugin.toml` 的 `[plugin].entry` 混淆：后者是宿主导入插件类时使用的 `module.path:ClassName`。也不要与 `@llm_tool` 混淆：LLM 工具注册到 main_server 的 `/api/tools` registry，可在对话中被调用；用户插件 Agent 路由选择的则是插件运行时入口。

Agent 路由先做插件级筛选，再读取入口元数据。第二阶段必须返回精确存在的 `plugin_id` 和 `entry_id`；缺失或未知 ID 只纠正重试一次，随后拒绝执行。

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

AI agent 也能看到这些描述，帮助它理解什么时候该传什么值。

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

默认情况下，AI 能看到你返回的所有字段。但有时候返回值里有大量原始数据（比如完整的搜索结果列表），你只想让 AI 看到摘要：

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

## 小结

| 场景 | 做法 |
|------|------|
| 简单功能，几个参数 | `@plugin_entry` + 函数签名类型注解 |
| 想给参数加描述 | 用 `Annotated[类型, "描述"]` |
| 参数多/需要验证 | 定义 Pydantic 模型作为单参数 |
| 返回成功 | `return Ok({...})` |
| 返回失败 | `return Err(SdkError("原因"))` |
| 限制 AI 看到的字段 | `llm_result_fields=["field1", "field2"]` |
| 运行时才知道有哪些功能 | `self.register_dynamic_entry(...)` |
