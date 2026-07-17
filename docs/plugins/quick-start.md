# Plugin Quick Start

This guide walks you through creating your first plugin from scratch. No prior plugin development experience needed.

## Prerequisites

- N.E.K.O is installed and can start normally
- Basic Python knowledge (functions, classes)

## What you'll build

A simple "Hello World" plugin with one function: greet someone by name.

When you're done, the file structure will look like this:

```
plugin/
└── plugins/
    └── hello_world/          ← your new plugin
        ├── plugin.toml       ← config: tells N.E.K.O what this plugin is
        └── __init__.py       ← code: your plugin logic
```

## Step 1: Create the folder

Find the `plugin/plugins/` directory in your N.E.K.O project. Create a new folder called `hello_world` inside it.

## Step 2: Create `plugin.toml`

Inside `hello_world/`, create a file called `plugin.toml`. This is the config file that tells N.E.K.O about your plugin.

Paste this content:

```toml
[plugin]
id = "hello_world"
name = "Hello World"
description = "My first plugin — greets people by name"
version = "0.1.0"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = true
```

Key things:
- `id` is the unique plugin ID. Keep it equal to the folder name (`hello_world`) so profile lookup and tooling do not need to handle a mismatch.
- `[plugin].entry` tells the host which class to load — format is `module.path:ClassName`. It is not the runtime `entry_id` below.
- `auto_start = true` means it starts automatically with N.E.K.O

## Step 3: Create `__init__.py`

Inside `hello_world/`, create a file called `__init__.py`. This is where your plugin code lives.

Paste this content:

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok
from typing import Annotated


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """My first plugin."""

    @plugin_entry(id="greet", name="Greet", description="Say hello to someone")
    async def greet(self, name: Annotated[str, "Name to greet"] = "World"):
        return Ok({"message": f"Hello, {name}!"})
```

What each line does:

| Code | What it does |
|------|------|
| `@neko_plugin` | Marks this class as a plugin |
| `NekoPluginBase` | Base class — gives you logging, config, storage, etc. |
| `@plugin_entry(...)` | Makes this function callable from the Plugin Manager |
| `Annotated[str, "Name to greet"]` | A string parameter with a description |
| `= "World"` | Default value if nothing is passed |
| `Ok({...})` | Returns a successful result |

The runtime entry ID here is `greet`. When the user-plugin Agent route selects this operation, it returns `plugin_id="hello_world"` and `entry_id="greet"`; the host validates both. This is separate from registering a conversation-time LLM tool with `@llm_tool`.

## Step 4: Run it

1. Start (or restart) N.E.K.O
2. Open the **Plugin Manager** panel from the main interface
3. "Hello World" appears in the plugin list, status: running
4. Click on it → you see the **Greet** entry point
5. Click execute, type a name, see the result

::: tip Already running?
No need to restart. Open Plugin Manager → click **Refresh** → find your plugin → click **Start**.
:::

## Step 5: Edit and reload

Change the message in `__init__.py`:

```python
return Ok({"message": f"Hey {name}, welcome to N.E.K.O!"})
```

Save → click **Reload** in Plugin Manager → done. No restart needed.

## Next steps

| I want to... | Read |
|---|---|
| Add more functions and parameters | [SDK Reference](./sdk-reference) |
| Run code on startup/shutdown | [Decorators](./decorators) |
| Let the user-plugin Agent route select an entry | [Entries & Parameters](./entries) |
| Register a conversation-time LLM tool | [LLM Tool Calling](./tool-calling) |
| Build a UI panel for my plugin | [Hosted UI](./hosted-ui) |
| See real-world plugin examples | [Examples](./examples) |
| Handle errors properly | [Best Practices](./best-practices) |
