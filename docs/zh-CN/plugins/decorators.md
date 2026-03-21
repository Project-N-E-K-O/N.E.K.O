# 装饰器

所有装饰器均从 `plugin.sdk.plugin` 导入。

```python
from plugin.sdk.plugin import (
    neko_plugin, plugin_entry, lifecycle, timer_interval, message,
    on_event, custom_event,
    hook, before_entry, after_entry, around_entry, replace_entry,
    plugin,  # 命名空间风格的替代方式
)
```

## @neko_plugin

将类标记为 N.E.K.O. 插件。所有插件类都**必须**使用此装饰器。

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

## @plugin_entry

定义一个可外部调用的入口点。

```python
@plugin_entry(
    id="process",                # 入口点 ID（如果省略则从方法名自动生成）
    name="Process Data",         # 显示名称
    description="Process data",  # 描述
    input_schema={...},          # 用于验证的 JSON Schema
    params=MyParamsModel,        # 替代方式：用于输入的 Pydantic 模型（自动生成 schema）
    kind="action",               # "action" | "service" | "hook" | "custom"
    auto_start=False,            # 加载时自动启动
    persist=False,               # 跨重载持久化
    model_validate=True,         # 启用 Pydantic 验证
    timeout=30.0,                # 执行超时时间（秒）
    llm_result_fields=["text"],  # 为 LLM 消费提取的字段
    llm_result_model=MyResult,   # 用于结果 schema 的 Pydantic 模型
    metadata={"category": "data"}  # 附加元数据
)
def process(self, data: str, **_):
    return Ok({"result": data})
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | `str` | 方法名 | 唯一入口点标识符 |
| `name` | `str` | `None` | 显示名称 |
| `description` | `str` | `""` | 描述 |
| `input_schema` | `dict` | `None` | 用于输入验证的 JSON Schema |
| `params` | `type` | `None` | Pydantic 模型（自动生成 `input_schema`） |
| `kind` | `str` | `"action"` | 入口类型 |
| `auto_start` | `bool` | `False` | 加载后自动启动 |
| `persist` | `bool` | `None` | 跨重载持久化状态 |
| `model_validate` | `bool` | `True` | 启用 Pydantic 验证 |
| `timeout` | `float` | `None` | 执行超时时间（秒） |
| `llm_result_fields` | `list[str]` | `None` | 用于 LLM 结果提取的字段 |
| `llm_result_model` | `type` | `None` | 用于结果 schema 的 Pydantic 模型 |
| `fields` | `type` | `None` | `params` 的别名 |
| `metadata` | `dict` | `None` | 附加元数据 |

::: tip
始终在函数签名中包含 `**_`，以便优雅地捕获未使用的参数。
:::

## @lifecycle

定义生命周期事件处理器。

```python
@lifecycle(id="startup")
def on_startup(self, **_):
    self.logger.info("Starting up...")
    return Ok({"status": "ready"})

@lifecycle(id="shutdown")
def on_shutdown(self, **_):
    self.logger.info("Shutting down...")
    return Ok({"status": "stopped"})

@lifecycle(id="reload")
def on_reload(self, **_):
    self.logger.info("Reloading config...")
    return Ok({"status": "reloaded"})
```

有效的生命周期 ID：`startup`、`shutdown`、`reload`、`freeze`、`unfreeze`、`config_change`。

## @timer_interval

定义按固定间隔执行的定时任务。

```python
@timer_interval(
    id="cleanup",
    seconds=3600,           # 每小时执行一次
    name="Cleanup Task",
    auto_start=True          # 自动启动（默认值：True）
)
def cleanup(self, **_):
    # 在独立线程中运行
    return Ok({"cleaned": True})
```

::: info
定时任务在独立线程中运行。异常会被记录但不会停止计时器。
:::

## @message

定义处理来自宿主系统消息的处理器。

```python
@message(
    id="handle_chat",
    source="chat",           # 按消息来源过滤
    auto_start=True
)
def handle_chat(self, text: str, sender: str, **_):
    return Ok({"handled": True})
```

## @on_event

通用事件处理器，用于自定义事件类型。

```python
@on_event(
    event_type="custom_event",
    id="my_handler",
    kind="hook"
)
def custom_handler(self, event_data: str, **_):
    return Ok({"processed": True})
```

## @custom_event

带触发方法控制的专用事件处理器。

```python
@custom_event(
    event_type="data_refresh",
    id="refresh_handler",
    trigger_method="message",  # 此事件的触发方式
    auto_start=False
)
def on_refresh(self, source: str, **_):
    return Ok({"refreshed": True})
```

---

## 钩子装饰器（AOP）

钩子装饰器提供面向切面编程（AOP）能力，用于拦截入口点的执行。

### @before_entry

在目标入口点之前运行。可以修改参数或中止执行。

```python
@before_entry(target="process", priority=0)
def validate_input(self, *, args, entry_id, **_):
    if not args.get("data"):
        return Err(SdkError("data is required"))
    # 返回 None 继续执行，或返回 Err 中止执行
```

### @after_entry

在目标入口点之后运行。可以修改或替换结果。

```python
@after_entry(target="process", priority=0)
def log_result(self, *, result, entry_id, **_):
    self.logger.info(f"Entry {entry_id} returned: {result}")
    # 返回 None 保留原始结果，或返回新值替换结果
```

### @around_entry

包装目标入口点。完全控制执行流程。

```python
@around_entry(target="process", priority=0)
async def timing_wrapper(self, *, proceed, args, **_):
    import time
    start = time.time()
    result = await proceed(**args)
    elapsed = time.time() - start
    self.logger.info(f"Took {elapsed:.2f}s")
    return result
```

### @replace_entry

完全替换目标入口点。

```python
@replace_entry(target="old_entry", priority=0)
def new_implementation(self, **kwargs):
    return Ok({"replaced": True})
```

### 钩子参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `target` | `str` | `"*"` | 要钩住的入口 ID（`"*"` = 所有入口） |
| `priority` | `int` | `0` | 执行顺序（值越小越先执行） |
| `condition` | `str` | `None` | 可选的条件表达式 |

---

## 命名空间风格替代方式：`plugin.*`

为了更简洁的语法，可以使用 `plugin` 命名空间对象：

```python
from plugin.sdk.plugin import plugin

@plugin.entry(id="greet", description="Say hello")
def greet(self, name: str = "World", **_):
    return Ok({"message": f"Hello, {name}!"})

@plugin.lifecycle(id="startup")
def on_startup(self, **_):
    return Ok({"status": "ready"})

@plugin.hook(target="greet", timing="before")
def validate(self, *, args, **_):
    pass

@plugin.timer(id="heartbeat", seconds=60)
def heartbeat(self, **_):
    return Ok({"alive": True})

@plugin.message(id="on_chat", source="chat")
def on_chat(self, text: str, **_):
    return Ok({"handled": True})
```
