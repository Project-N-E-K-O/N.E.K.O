# Plugin Quick Start

## Step 1: Create plugin directory

```bash
mkdir -p plugin/plugins/hello_world
```

## Step 2: Create `plugin.toml`

```toml
[plugin]
id = "hello_world"
name = "Hello World Plugin"
description = "A simple example plugin"
version = "1.0.0"
entry = "plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

### Configuration fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique plugin identifier |
| `name` | No | Display name |
| `description` | No | Plugin description |
| `version` | No | Plugin version |
| `entry` | Yes | Entry point: `module_path:ClassName` |

### SDK version fields

| Field | Description |
|-------|-------------|
| `recommended` | Recommended SDK version range |
| `supported` | Minimum supported range (rejected if not met) |
| `untested` | Allowed but warns on load |
| `conflicts` | Rejected version ranges |

## Step 3: Create `__init__.py`

```python
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err,
)
from typing import Any

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World plugin example."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.counter = 0

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("HelloWorldPlugin started!")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("HelloWorldPlugin stopped!")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="greet",
        name="Greet",
        description="Return a greeting message",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name to greet",
                    "default": "World"
                }
            }
        }
    )
    def greet(self, name: str = "World", **_):
        self.counter += 1
        message = f"Hello, {name}! (call #{self.counter})"
        self.logger.info(f"Greeting: {message}")
        return Ok({"message": message, "count": self.counter})
```

### Key points

- **`@neko_plugin`** — Required class decorator, registers the class as a plugin
- **`NekoPluginBase`** — Base class all plugins must inherit
- **`@plugin_entry`** — Defines an externally callable entry point
- **`@lifecycle`** — Handles lifecycle events (`startup`, `shutdown`, `reload`)
- **`Ok(...)` / `Err(...)`** — Return Result types for type-safe error handling
- **`**_`** — Always include in entry point signatures to capture extra parameters

## Step 4: Test

After starting the plugin server, call your plugin via HTTP:

```bash
curl -X POST http://localhost:48916/plugin/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_id": "hello_world",
    "entry_id": "greet",
    "args": {"name": "N.E.K.O"}
  }'
```

## Next steps

- [SDK Reference](./sdk-reference) — Learn about `NekoPluginBase`, Result types, and runtime helpers
- [Decorators](./decorators) — All available decorator types including hooks
- [Examples](./examples) — Complete working plugin examples
