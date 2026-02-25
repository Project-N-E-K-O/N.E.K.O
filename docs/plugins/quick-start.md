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
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry
from typing import Any

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World plugin example"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.logger.info("HelloWorldPlugin initialized")

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
        """Greeting function"""
        message = f"Hello, {name}!"
        self.logger.info(f"Greeting: {message}")
        return {
            "message": message
        }
```

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

- [SDK Reference](./sdk-reference) — Learn about `NekoPluginBase` and `PluginContext`
- [Decorators](./decorators) — All available decorator types
- [Examples](./examples) — Complete working plugin examples
