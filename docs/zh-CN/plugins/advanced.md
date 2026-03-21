# 高级主题

## 扩展（Extension）

扩展可以在不修改现有插件的情况下，为其添加路由和钩子。扩展运行在宿主插件的进程内（而非独立进程）。

### 何时使用扩展

- 你想为现有插件添加新命令
- 你想钩住另一个插件的入口点
- 你想在插件内实现模块化的代码组织

### 创建扩展

```python
from plugin.sdk.extension import (
    NekoExtensionBase, extension, extension_entry, extension_hook,
    Ok, Err,
)

@extension
class MyExtension(NekoExtensionBase):
    """为宿主插件添加额外命令。"""

    @extension_entry(id="extra_command", description="An extra command added by extension")
    def extra_command(self, param: str = "", **_):
        return Ok({"extended": True, "param": param})

    @extension_hook(target="original_entry", timing="before")
    def validate(self, *, args, **_):
        # 在宿主插件的 "original_entry" 之前运行
        if not args.get("required_field"):
            return Err("Missing required_field")
```

### 扩展的工作原理

1. 宿主在其配置中注册扩展
2. 启动时，宿主将扩展作为 `PluginRouter` 实例注入
3. 扩展的入口点在宿主插件的命名空间下变为可访问
4. 扩展的钩子会拦截宿主的入口点

---

## 适配器（Adapter）

适配器将外部协议（MCP、NoneBot 等）桥接到内部插件调用。它们实现了一个**网关管线**模式。

### 何时使用适配器

- 你想通过 MCP（模型上下文协议）暴露 N.E.K.O 插件
- 你想接受 NoneBot 消息并将其路由到插件
- 你想将任何外部协议桥接到插件系统

### 适配器网关管线

```
External Request → Normalizer → PolicyEngine → RouteEngine → PluginInvoker → ResponseSerializer → External Response
```

| 阶段 | 职责 |
|------|------|
| **Normalizer** | 将外部协议格式转换为 `GatewayRequest` |
| **PolicyEngine** | 访问控制、速率限制、验证 |
| **RouteEngine** | 决定调用哪个插件/入口 |
| **PluginInvoker** | 执行实际的插件调用 |
| **ResponseSerializer** | 将结果转换回外部协议格式 |

### 创建适配器

```python
from plugin.sdk.plugin import neko_plugin, plugin_entry, lifecycle, Ok, Err, SdkError
from plugin.sdk.adapter import (
    AdapterGatewayCore, DefaultPolicyEngine, NekoAdapterPlugin,
)
from plugin.sdk.adapter.gateway_models import ExternalRequest

@neko_plugin
class MyProtocolAdapter(NekoAdapterPlugin):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.gateway = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        self.gateway = AdapterGatewayCore(
            normalizer=MyNormalizer(),
            policy_engine=DefaultPolicyEngine(),
            route_engine=MyRouteEngine(),
            invoker=MyInvoker(self.ctx),
            serializer=MySerializer(),
            logger=self.logger,
        )
        return Ok({"status": "ready"})

    @plugin_entry(id="handle_request")
    async def handle_request(self, raw_data: dict, **_):
        external = ExternalRequest(protocol="my_protocol", raw=raw_data)
        response = await self.gateway.process(external)
        return Ok(response.to_dict())
```

### 适配器模式

| 模式 | 说明 |
|------|------|
| `GATEWAY` | 完整管线处理 |
| `ROUTER` | 仅路由（跳过策略） |
| `BRIDGE` | 直接透传 |
| `HYBRID` | 按请求选择模式 |

### 内置参考：MCP 适配器

参见 `plugin/plugins/mcp_adapter/` 获取完整的适配器实现，它将 MCP 协议桥接到 N.E.K.O 插件。其中演示了：
- 自定义规范化器（`MCPRequestNormalizer`）
- 自定义路由引擎（`MCPRouteEngine`）
- 自定义调用器（`MCPPluginInvoker`）
- 自定义序列化器（`MCPResponseSerializer`）
- 自定义传输层（`MCPTransportAdapter`）

---

## 跨插件通信

### 直接入口调用

```python
# 调用另一个插件的入口点
result = await self.plugins.call_entry("target_plugin:entry_id", {"arg": "value"})

if isinstance(result, Ok):
    data = result.value
else:
    self.logger.error(f"Call failed: {result.error}")
```

### 发现

```python
# 列出所有可用的插件
plugins = await self.plugins.list(enabled=True)

# 检查依赖是否存在
exists = await self.plugins.exists("required_plugin")

# 要求某个插件存在（如果缺失则快速失败）
dep = await self.plugins.require_enabled("required_plugin")
```

### 事件总线

```python
# 通过总线发布事件
self.bus.emit("my_event", {"key": "value"})

# 订阅事件（通常在 startup 中进行）
self.bus.on("some_event", self._handle_event)
```

---

## 异步编程

入口点可以是同步或异步的：

```python
# 同步入口（在线程池中运行）
@plugin_entry(id="sync_task")
def sync_task(self, **_):
    return Ok({"result": "done"})

# 异步入口（在事件循环中运行）
@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return Ok({"data": await response.json()})
```

---

## 线程安全

定时任务在独立线程中运行。请保护共享状态：

```python
import threading

@neko_plugin
class ThreadSafePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._lock = threading.Lock()
        self._counter = 0

    @plugin_entry(id="increment")
    def increment(self, **_):
        with self._lock:
            self._counter += 1
            return Ok({"count": self._counter})

    @timer_interval(id="report", seconds=60, auto_start=True)
    def report(self, **_):
        with self._lock:
            count = self._counter
        self.report_status({"count": count})
```

---

## 自定义配置

```python
import json

class ConfigurablePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        config_file = self.config_dir / "config.json"
        if config_file.exists():
            self.config = json.loads(config_file.read_text())
        else:
            self.config = {"timeout": 30}
```

或使用 `PluginConfig` 进行带配置文件的结构化配置：

```python
from plugin.sdk.plugin import PluginConfig

config = PluginConfig(self.ctx)
timeout = config.get("timeout", default=30)
```

---

## 使用 SQLite 进行数据持久化

```python
import sqlite3

class PersistentPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.db_path = self.data_path("records.db")
        self.data_path().mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
```
