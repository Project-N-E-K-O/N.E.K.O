# SDK 参考

所有插件开发 API 均从 `plugin.sdk.plugin` 导入。

```python
from plugin.sdk.plugin import (
    # 基类
    NekoPluginBase, PluginMeta,
    # 装饰器
    neko_plugin, plugin_entry, lifecycle, timer_interval, message, on_event,
    custom_event, hook, before_entry, after_entry, around_entry, replace_entry,
    # Result 类型
    Ok, Err, Result, unwrap, unwrap_or,
    # 运行时辅助工具
    Plugins, PluginRouter, PluginConfig, PluginStore,
    SystemInfo, MemoryClient,
    # 错误
    SdkError, TransportError,
    # 日志
    get_plugin_logger,
)
```

## NekoPluginBase

所有插件必须继承 `NekoPluginBase`。

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `self.ctx` | `PluginContext` | 运行时上下文（由宿主注入） |
| `self.plugin_id` | `str` | 本插件的唯一标识符 |
| `self.config_dir` | `Path` | 包含 `plugin.toml` 的目录 |
| `self.metadata` | `dict` | 来自 `plugin.toml` 的插件元数据 |
| `self.bus` | `Bus` | 用于发布/订阅的事件总线 |
| `self.plugins` | `Plugins` | 跨插件调用辅助工具 |
| `self.memory` | `MemoryClient` | 访问宿主内存系统 |
| `self.system_info` | `SystemInfo` | 宿主系统元数据 |

### 方法

#### `report_status(status: dict) -> None`

向宿主进程报告插件状态。

```python
self.report_status({
    "status": "processing",
    "progress": 50,
    "message": "Halfway done..."
})
```

#### `push_message(**kwargs) -> object`

向宿主系统推送消息。

```python
self.push_message(
    source="my_feature",
    message_type="text",        # "text" | "url" | "binary" | "binary_url"
    description="Task complete",
    priority=5,                 # 0-10（0=低，10=紧急）
    content="Result text",
)
```

#### `data_path(*parts) -> Path`

获取插件 `data/` 目录下的路径。

```python
db_path = self.data_path("cache.db")  # → <plugin_dir>/data/cache.db
```

#### `register_dynamic_entry(entry_id, handler, ...) -> bool`

在运行时注册入口点（非通过装饰器）。

```python
self.register_dynamic_entry(
    entry_id="dynamic_greet",
    handler=lambda name="World", **_: Ok({"msg": f"Hi {name}"}),
    name="Dynamic Greet",
    description="A dynamically registered greeting",
)
```

#### `unregister_dynamic_entry(entry_id) -> bool`

移除一个动态注册的入口点。

#### `list_entries(include_disabled=False) -> list[dict]`

列出所有入口点（静态 + 动态）。

#### `enable_entry(entry_id) / disable_entry(entry_id) -> bool`

在运行时启用或禁用动态入口点。

#### `register_static_ui(directory, *, index_file, cache_control) -> bool`

为本插件注册一个静态 Web UI 目录。

```python
self.register_static_ui("static")  # 提供 <plugin_dir>/static/index.html 服务
```

#### `include_router(router, *, prefix) -> None`

挂载一个 `PluginRouter`（用于扩展）。

#### `run_update(**kwargs) -> object`（异步）

在长时间运行的操作期间向宿主发送更新。

#### `export_push(**kwargs) -> object`（异步）

向宿主推送导出数据。

#### `finish(**kwargs) -> Any`（异步）

向宿主发送任务完成信号。

### Agent Reply Contract

插件输出可以通过 `agent` 元数据控制是否触发主系统回复，以及 agent / main LLM 实际能看到哪些字段。

```python
return await self.finish(
    data={"summary": "完成", "internal": {"debug": True}},
    reply=True,
    meta={"agent": {"fields": ["summary"], "summary": "任务已完成"}},
)

await self.export_push(
    export_type="json",
    json_data={"summary": "已保存", "internal": "debug"},
    reply=True,
    metadata={"agent": {"fields": ["summary"], "priority": 10}},
)

self.push_message(
    source="memo",
    message_type="proactive_notification",
    content="原始提醒文本",
    metadata={"agent": {"reply": True, "summary": "提醒时间到了"}},
)
```

支持的 `agent` 元数据字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `reply` | `bool` | 是否允许该输出触发主系统回复 |
| `include` | `bool` | 是否允许 agent 读取原始正文 / JSON |
| `fields` | `list[str]` | 对 JSON 输出做字段级可见性裁剪 |
| `summary` | `str` | 主系统回复使用的摘要 |
| `detail` | `str` | 主系统回复使用的详细文本 |
| `priority` | `int` | 多个 reply 候选同时存在时的优先级 |

如果入口点声明了 `llm_result_fields`，而 `agent.fields` 未显式提供，则 `return/finish()` 会回退到这些 `llm_result_fields`。

---

## Result 类型：Ok / Err

SDK 使用受 Rust 启发的 Result 类型进行错误处理，而非异常。

```python
from plugin.sdk.plugin import Ok, Err, unwrap, unwrap_or

# 返回成功
return Ok({"data": result})

# 返回错误
return Err(SdkError("something went wrong"))

# 使用结果
result = await self.plugins.call_entry("other:do_stuff")
if isinstance(result, Ok):
    data = result.value
else:
    error = result.error
    self.logger.error(f"Call failed: {error}")

# 辅助函数
value = unwrap(result)           # 如果是 Err 则抛出异常
value = unwrap_or(result, None)  # 如果是 Err 则返回默认值
```

---

## Plugins（跨插件调用）

通过 `self.plugins` 访问。

```python
# 列出所有插件
result = await self.plugins.list()

# 仅列出已启用的插件
result = await self.plugins.list(enabled=True)

# 获取插件 ID 列表
result = await self.plugins.list_ids()

# 检查插件是否存在
result = await self.plugins.exists("other_plugin")

# 调用另一个插件的入口点
result = await self.plugins.call_entry("other_plugin:do_work", {"key": "value"})

# 调用并确保返回 JSON 对象
result = await self.plugins.call_entry_json("other_plugin:get_data")

# 要求某个插件存在且已启用
result = await self.plugins.require_enabled("dependency_plugin")
```

所有方法返回 `Result` 类型 — 在使用 `.value` 之前，请先用 `isinstance(result, Ok)` 检查。

---

## PluginStore（持久化存储）

```python
from plugin.sdk.plugin import PluginStore

store = PluginStore(self.ctx)
await store.set("key", {"count": 42})
value = await store.get("key")  # → {"count": 42}
```

---

## MemoryClient

通过 `self.memory` 访问。

```python
result = await self.memory.search("keyword")
result = await self.memory.store("key", "value")
```

---

## SystemInfo

通过 `self.system_info` 访问。

```python
info = await self.system_info.get()
```

---

## PluginContext (ctx)

`ctx` 对象在构造时由宿主注入。

| 属性 | 类型 | 说明 |
|------|------|------|
| `ctx.plugin_id` | `str` | 插件标识符 |
| `ctx.config_path` | `Path` | `plugin.toml` 的路径 |
| `ctx.logger` | `Logger` | 日志记录器实例 |
| `ctx.bus` | `Bus` | 事件总线 |
| `ctx.metadata` | `dict` | 插件元数据 |

### 消息类型

| 类型 | 使用场景 |
|------|----------|
| `text` | 纯文本消息 |
| `url` | URL 链接 |
| `binary` | 小型二进制数据（直接传输） |
| `binary_url` | 大文件（通过 URL 引用） |

### 优先级等级

| 范围 | 等级 | 使用场景 |
|------|------|----------|
| 0-2 | 低 | 信息性消息 |
| 3-5 | 中 | 一般通知 |
| 6-8 | 高 | 重要通知 |
| 9-10 | 紧急 | 需要立即处理 |
