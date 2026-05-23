# Plugin Quick Start

## 1. Create the plugin

```bash
mkdir -p plugin/plugins/hello_world
```

Create `plugin/plugins/hello_world/plugin.toml`:

```toml
[plugin]
id = "hello_world"
name = "Hello World"
description = "A simple greeting plugin"
version = "0.1.0"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = true
```

Create `plugin/plugins/hello_world/__init__.py`:

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok
from typing import Annotated

@neko_plugin
class HelloWorldPlugin(NekoPluginBase):

    @plugin_entry(id="greet", name="Greet", description="Say hello")
    async def greet(self, name: Annotated[str, "Name to greet"] = "World"):
        return Ok({"message": f"Hello, {name}!"})
```

## 2. Run it

Start N.E.K.O, open the Plugin Manager panel. Your plugin appears in the list. Click start, open the detail view, and execute the `greet` entry.

Edit code → click **Reload** in the panel → changes take effect immediately.

## 3. What's happening

- `@neko_plugin` + `NekoPluginBase` — makes it a plugin
- `@plugin_entry` — exposes a callable entry point
- Type annotations — SDK auto-generates input schema from them
- `Ok(...)` — wraps the return value (use `Err(...)` for errors)
- The plugin runs in its own process; crashes don't affect N.E.K.O

## Next steps

- [SDK Reference](./sdk-reference) — base class API, Result types, cross-plugin calls
- [Decorators](./decorators) — lifecycle, timers, hooks, messages
- [LLM Tool Calling](./tool-calling) — let the AI invoke your plugin
- [Hosted UI](./hosted-ui) — build interactive panels with TSX
- [Examples](./examples) — real-world plugin examples
