# SDK Reference

## NekoPluginBase

All plugins must inherit from `NekoPluginBase`.

```python
from plugin.sdk.base import NekoPluginBase

class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
```

### Methods

#### `get_input_schema() → dict`

Returns the plugin's input JSON Schema. By default reads from the class attribute. Override for dynamic schemas.

#### `report_status(status: dict) → None`

Report plugin status to the main process.

```python
self.report_status({
    "status": "running",
    "progress": 50,
    "message": "Processing..."
})
```

#### `collect_entries() → dict`

Collect all entry points (methods decorated with `@plugin_entry`). Called automatically by the system.

## PluginContext

The `ctx` object passed to plugin constructors.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `ctx.plugin_id` | `str` | Plugin identifier |
| `ctx.config_path` | `Path` | Path to `plugin.toml` |
| `ctx.logger` | `Logger` | Logger instance |

### Methods

#### `ctx.update_status(status: dict) → None`

Update plugin status in the main process.

#### `ctx.push_message(...) → None`

Push a message to the main system.

```python
ctx.push_message(
    source="my_feature",          # Message source identifier
    message_type="text",          # "text" | "url" | "binary" | "binary_url"
    description="Task complete",  # Human-readable description
    priority=5,                   # 0-10 (0=low, 10=emergency)
    content="Result text",        # For text/url types
    binary_data=b"...",           # For binary type
    binary_url="https://...",     # For binary_url type
    metadata={"key": "value"}    # Additional metadata
)
```

### Message types

| Type | Use case |
|------|----------|
| `text` | Plain text messages |
| `url` | URL links |
| `binary` | Small binary data (transmitted directly) |
| `binary_url` | Large files (referenced by URL) |

### Priority levels

| Range | Level | Use case |
|-------|-------|----------|
| 0-2 | Low | Informational messages |
| 3-5 | Medium | General notifications |
| 6-8 | High | Important notifications |
| 9-10 | Emergency | Needs immediate handling |
