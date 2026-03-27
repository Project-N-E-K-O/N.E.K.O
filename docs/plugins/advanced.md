# Advanced Topics

## Extensions

Extensions add routes and hooks to existing plugins without modifying them. They run inside the host plugin's process (not in a separate process).

### When to use Extensions

- You want to add new commands to an existing plugin
- You want to hook into another plugin's entry points
- You want modular code organization within a plugin

### Creating an Extension

```python
from plugin.sdk.extension import (
    NekoExtensionBase, extension, extension_entry, extension_hook,
    Ok, Err,
)

@extension
class MyExtension(NekoExtensionBase):
    """Adds extra commands to a host plugin."""

    @extension_entry(id="extra_command", description="An extra command added by extension")
    def extra_command(self, param: str = "", **_):
        return Ok({"extended": True, "param": param})

    @extension_hook(target="original_entry", timing="before")
    def validate(self, *, args, **_):
        # Runs before the host plugin's "original_entry"
        if not args.get("required_field"):
            return Err("Missing required_field")
```

### How Extensions work

1. The host registers extensions in its configuration
2. At startup, the host injects extensions as `PluginRouter` instances
3. Extension entries become accessible under the host plugin's namespace
4. Extension hooks intercept the host's entry points

---

## Adapters

Adapters bridge external protocols (MCP, NoneBot, etc.) to internal plugin calls. They implement a **gateway pipeline** pattern.

### When to use Adapters

- You want to expose N.E.K.O plugins via MCP (Model Context Protocol)
- You want to accept NoneBot messages and route them to plugins
- You want to bridge any external protocol to the plugin system

### Adapter Gateway Pipeline

```
External Request → Normalizer → PolicyEngine → RouteEngine → PluginInvoker → ResponseSerializer → External Response
```

| Stage | Responsibility |
|-------|---------------|
| **Normalizer** | Convert external protocol format to `GatewayRequest` |
| **PolicyEngine** | Access control, rate limiting, validation |
| **RouteEngine** | Decide which plugin/entry to call |
| **PluginInvoker** | Execute the actual plugin call |
| **ResponseSerializer** | Convert result back to external protocol format |

### Creating an Adapter

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

### Adapter Modes

| Mode | Description |
|------|-------------|
| `GATEWAY` | Full pipeline processing |
| `ROUTER` | Route-only (skip policy) |
| `BRIDGE` | Direct pass-through |
| `HYBRID` | Mode selected per-request |

### Built-in Reference: MCP Adapter

See `plugin/plugins/mcp_adapter/` for a complete adapter implementation that bridges MCP protocol to N.E.K.O plugins. It demonstrates:
- Custom normalizer (`MCPRequestNormalizer`)
- Custom route engine (`MCPRouteEngine`)
- Custom invoker (`MCPPluginInvoker`)
- Custom serializer (`MCPResponseSerializer`)
- Custom transport (`MCPTransportAdapter`)

---

## Cross-Plugin Communication

### Direct entry calls

```python
# Call another plugin's entry point
result = await self.plugins.call_entry("target_plugin:entry_id", {"arg": "value"})

if isinstance(result, Ok):
    data = result.value
else:
    self.logger.error(f"Call failed: {result.error}")
```

### Discovery

```python
# List all available plugins
plugins = await self.plugins.list(enabled=True)

# Check if a dependency exists
exists = await self.plugins.exists("required_plugin")

# Require a plugin (fail fast if missing)
dep = await self.plugins.require_enabled("required_plugin")
```

### Event bus

```python
# Publish an event via the bus
self.bus.emit("my_event", {"key": "value"})

# Subscribe to events (typically in startup)
self.bus.on("some_event", self._handle_event)
```

---

## Async Programming

Entry points can be either sync or async:

```python
# Sync entry (runs in thread pool)
@plugin_entry(id="sync_task")
def sync_task(self, **_):
    return Ok({"result": "done"})

# Async entry (runs on event loop)
@plugin_entry(id="async_task")
async def async_task(self, url: str, **_):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return Ok({"data": await response.json()})
```

---

## Thread Safety

Timer tasks run in separate threads. Protect shared state:

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

## Custom Configuration

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

Or use `PluginConfig` for structured configuration with profiles:

```python
from plugin.sdk.plugin import PluginConfig

config = PluginConfig(self.ctx)
timeout = config.get("timeout", default=30)
```

---

## Data Persistence with SQLite

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
