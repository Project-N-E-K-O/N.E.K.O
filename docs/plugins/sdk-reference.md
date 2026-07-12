# SDK Reference

All plugin development APIs are imported from `plugin.sdk.plugin`.

```python
from plugin.sdk.plugin import (
    # Base
    NekoPluginBase, PluginMeta,
    # Decorators
    neko_plugin, plugin_entry, lifecycle, timer_interval, message, on_event,
    custom_event, hook, before_entry, after_entry, around_entry, replace_entry,
    # Result types
    Ok, Err, Result, unwrap, unwrap_or,
    # Runtime helpers
    Plugins, PluginRouter, PluginConfig, PluginStore,
    SystemInfo,
    # Errors
    SdkError, TransportError,
    # Logging
    get_plugin_logger,
)
```

## NekoPluginBase

All plugins must inherit from `NekoPluginBase`.

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `self.ctx` | `PluginContext` | The runtime context (injected by host) |
| `self.plugin_id` | `str` | This plugin's unique identifier |
| `self.config_dir` | `Path` | Directory containing `plugin.toml` |
| `self.metadata` | `dict` | Plugin metadata from `plugin.toml` |
| `self.bus` | `SdkBusContext` | Read/watch facade over host state; it has no publish/emit API |
| `self.plugins` | `Plugins` | Cross-plugin call helper |
| `self.system_info` | `SystemInfo` | Host system metadata |

### Methods

#### `report_status(status: dict) -> None`

Report plugin status to the host process.

```python
self.report_status({
    "status": "processing",
    "progress": 50,
    "message": "Halfway done..."
})
```

#### `push_message(**kwargs) -> object`

Push a message to the host system with the v2 schema.

```python
self.push_message(
    source="my_feature",
    visibility=["chat"],       # [], ["chat"], ["hud"], or both
    ai_behavior="blind",       # "respond", "read", or "blind"
    parts=[{"type": "text", "text": "Task complete"}],
    priority=5,
)
```

The v1 fields (`message_type`, `content`, `delivery`, `reply`, and the other legacy aliases) are deprecated and scheduled for removal in v0.9. See the [migration guide](./migration-v0.9#push-message-v2).

#### `data_path(*parts) -> Path`

Get a path under the plugin's `data/` directory.

```python
db_path = self.data_path("cache.db")  # → <plugin_dir>/data/cache.db
```

#### `register_dynamic_entry(entry_id, handler, ...) -> bool`

Register an entry point at runtime (not via decorator).

```python
self.register_dynamic_entry(
    entry_id="dynamic_greet",
    handler=lambda name="World", **_: Ok({"msg": f"Hi {name}"}),
    name="Dynamic Greet",
    description="A dynamically registered greeting",
)
```

#### `unregister_dynamic_entry(entry_id) -> bool`

Remove a dynamically registered entry.

#### `list_entries(include_disabled=False) -> list[dict]`

List all entry points (static + dynamic).

#### `enable_entry(entry_id) / disable_entry(entry_id) -> bool`

Enable or disable a dynamic entry at runtime.

#### `register_static_ui(directory, *, index_file, cache_control) -> bool`

Register a static web UI directory for this plugin.

```python
self.register_static_ui("static")  # serves <plugin_dir>/static/index.html
```

#### `include_router(router, *, prefix) -> None`

Mount a `PluginRouter` to organize a large or feature-split normal plugin.

#### `run_update(**kwargs) -> object` (async)

Send an update to the host during long-running operations.

#### `export_push(**kwargs) -> object` (async)

Push export data to the host.

#### `finish(**kwargs) -> Any` (async)

Signal task completion to the host.

### Reply Control

The `finish()` method accepts a `reply` parameter (default `True`) that controls whether the plugin result triggers the main character to speak.

```python
# Normal: character will announce the result
return await self.finish(data={"summary": "Done"}, reply=True)

# Silent: result is recorded but character stays quiet
return await self.finish(data={"summary": "Done"}, reply=False)
```

### LLM Result Field Filtering

Use `llm_result_fields` on `@plugin_entry` (static entries) or `register_dynamic_entry()` (dynamic entries) to control which fields of the result the main LLM can see. Fields not listed are excluded from the LLM prompt but still stored in the task registry.

```python
# Static entry
@plugin_entry(llm_result_fields=["summary"])
async def search(self, query: str):
    return await self.finish(data={"summary": "3 results", "raw_results": [...]})

# Dynamic entry
self.register_dynamic_entry(
    entry_id="my-tool",
    handler=handler,
    llm_result_fields=["summary"],
)
```

---

## Result Types: Ok / Err

The SDK uses Rust-inspired Result types for error handling instead of exceptions.

```python
from plugin.sdk.plugin import Ok, Err, unwrap, unwrap_or

# Returning success
return Ok({"data": result})

# Returning error
return Err(SdkError("something went wrong"))

# Consuming results
result = await self.plugins.call_entry("other:do_stuff")
if isinstance(result, Ok):
    data = result.value
else:
    error = result.error
    self.logger.error(f"Call failed: {error}")

# Helper functions
value = unwrap(result)           # raises if Err
value = unwrap_or(result, None)  # returns default if Err
```

---

## Plugins (Cross-Plugin Calls)

Access via `self.plugins`.

```python
# List all plugins
result = await self.plugins.list()

# List only enabled plugins
result = await self.plugins.list(enabled=True)

# Get plugin IDs
result = await self.plugins.list_ids()

# Check if a plugin exists
result = await self.plugins.exists("other_plugin")

# Call another plugin's entry point
result = await self.plugins.call_entry("other_plugin:do_work", {"key": "value"})

# Call and ensure JSON object response
result = await self.plugins.call_entry_json("other_plugin:get_data")

# Require a plugin to be present and enabled
result = await self.plugins.require_enabled("dependency_plugin")
```

All methods return `Result` types — check with `isinstance(result, Ok)` before using `.value`.

---

## PluginStore (Persistent Storage)

Access via `self.store` (the host pre-builds and injects it at plugin construction time — you do not instantiate `PluginStore` yourself).

All `PluginStore` methods return a `Result`; unwrap with `unwrap_or(...)`.

```python
unwrap_or(await self.store.set("key", {"count": 42}), None)
value = unwrap_or(await self.store.get("key"), None)  # → {"count": 42}
```

---

## SystemInfo

Access via `self.system_info`. These methods all return a `Result`; unwrap with `unwrap_or(...)`.

```python
config = unwrap_or(await self.system_info.get_system_config(), {})
settings = unwrap_or(await self.system_info.get_server_settings(), {})
python_env = unwrap_or(await self.system_info.get_python_env(), {})
```

---

## PluginContext (ctx)

The `ctx` object is injected by the host at construction time.

| Property | Type | Description |
|----------|------|-------------|
| `ctx.plugin_id` | `str` | Plugin identifier |
| `ctx.config_path` | `Path` | Path to `plugin.toml` |
| `ctx.logger` | `Logger` | Logger instance |
| `ctx.bus` | `SdkBusContext` | Read/watch facade over host state |
| `ctx.metadata` | `dict` | Plugin metadata |

### Bus and memory

Inside async entries, await `get()` before applying the local list operations:

```python
events = await self.bus.events.get(plugin_id=self.plugin_id, max_count=50)
recent = events.filter(priority_min=1).sort(by="timestamp", reverse=True).limit(20)

records = await self.bus.memory.get(bucket_id="default", limit=20)
matches = await self.ctx.query_memory("default", "user preferences")
```

The list surface is `filter` / `where`, `sort`, `limit`, and `watch`. Callable `filter(predicate)`, `where(predicate)`, and `sort(key=...)` are local-only; replayable watcher chains must use structured `filter(field=value, ...)` and `sort(by=...)`. Only `messages`, `events`, and `lifecycle` support `watch()`; `conversations` and `memory` are read-only snapshots. Watcher subscriptions accept only `add`, `del`, or `change`.

### Priority levels

| Range | Level | Use case |
|-------|-------|----------|
| 0-2 | Low | Informational messages |
| 3-5 | Medium | General notifications |
| 6-8 | High | Important notifications |
| 9-10 | Emergency | Needs immediate handling |
