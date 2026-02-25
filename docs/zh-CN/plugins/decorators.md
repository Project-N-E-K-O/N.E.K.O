# 装饰器

所有装饰器均从 `plugin.sdk.decorators` 导入。

## @neko_plugin

将类标记为 N.E.K.O. 插件。所有插件类都必须使用此装饰器。

```python
from plugin.sdk.decorators import neko_plugin

@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

## @plugin_entry

定义一个可外部调用的入口点。

```python
from plugin.sdk.decorators import plugin_entry

@plugin_entry(
    id="process",                # 入口点 ID（必需）
    name="Process Data",         # 显示名称
    description="Process data",  # 描述
    input_schema={...},          # 用于验证的 JSON Schema
    kind="action",               # "action" | "service" | "hook"
    auto_start=False,            # 加载时自动启动
    extra={"category": "data"}   # 附加元数据
)
def process(self, data: str, **_):
    return {"result": data}
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | str | 必需 | 唯一入口点标识符 |
| `name` | str | None | 显示名称 |
| `description` | str | `""` | 描述 |
| `input_schema` | dict | None | 用于输入验证的 JSON Schema |
| `kind` | str | `"action"` | 入口类型 |
| `auto_start` | bool | `False` | 加载后自动启动 |
| `extra` | dict | None | 附加元数据 |

::: tip
始终在函数签名中包含 `**_`，以便优雅地捕获未使用的参数。
:::

## @lifecycle

定义生命周期事件处理器。

```python
from plugin.sdk.decorators import lifecycle

@lifecycle(id="startup")
def on_startup(self, **_):
    self.logger.info("Starting up...")
    return {"status": "ready"}

@lifecycle(id="shutdown")
def on_shutdown(self, **_):
    self.logger.info("Shutting down...")
    return {"status": "stopped"}

@lifecycle(id="reload")
def on_reload(self, **_):
    self.logger.info("Reloading...")
    return {"status": "reloaded"}
```

有效的生命周期 ID：`startup`、`shutdown`、`reload`。

## @timer_interval

定义按固定间隔执行的定时任务。

```python
from plugin.sdk.decorators import timer_interval

@timer_interval(
    id="cleanup",
    seconds=3600,           # 每小时执行一次
    name="Cleanup Task",
    auto_start=True          # 自动启动
)
def cleanup(self, **_):
    # 在独立线程中运行
    return {"cleaned": True}
```

::: info
定时任务在独立线程中运行。异常会被记录但不会停止计时器。
:::

## @message

定义处理来自主系统消息的处理器。

```python
from plugin.sdk.decorators import message

@message(
    id="handle_chat",
    source="chat",           # 按来源过滤
    auto_start=True
)
def handle_chat(self, text: str, sender: str, **_):
    return {"handled": True}
```

## @on_event

通用事件处理器，用于自定义事件类型。

```python
from plugin.sdk.decorators import on_event

@on_event(
    event_type="custom_event",
    id="my_handler",
    kind="hook"
)
def custom_handler(self, event_data: str, **_):
    return {"processed": True}
```
