# Decorators

All decorators are imported from `plugin.sdk.decorators`.

## @neko_plugin

Marks a class as a N.E.K.O. plugin. Required on all plugin classes.

```python
from plugin.sdk.decorators import neko_plugin

@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

## @plugin_entry

Defines an externally callable entry point.

```python
from plugin.sdk.decorators import plugin_entry

@plugin_entry(
    id="process",                # Entry point ID (required)
    name="Process Data",         # Display name
    description="Process data",  # Description
    input_schema={...},          # JSON Schema for validation
    kind="action",               # "action" | "service" | "hook"
    auto_start=False,            # Auto-start on load
    extra={"category": "data"}   # Additional metadata
)
def process(self, data: str, **_):
    return {"result": data}
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `id` | str | required | Unique entry point identifier |
| `name` | str | None | Display name |
| `description` | str | `""` | Description |
| `input_schema` | dict | None | JSON Schema for input validation |
| `kind` | str | `"action"` | Entry type |
| `auto_start` | bool | `False` | Auto-start when loaded |
| `extra` | dict | None | Additional metadata |

::: tip
Always include `**_` in your function signature to capture unused parameters gracefully.
:::

## @lifecycle

Defines lifecycle event handlers.

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

Valid lifecycle IDs: `startup`, `shutdown`, `reload`.

## @timer_interval

Defines a scheduled task that executes at fixed intervals.

```python
from plugin.sdk.decorators import timer_interval

@timer_interval(
    id="cleanup",
    seconds=3600,           # Execute every hour
    name="Cleanup Task",
    auto_start=True          # Start automatically
)
def cleanup(self, **_):
    # Runs in a separate thread
    return {"cleaned": True}
```

::: info
Timer tasks run in separate threads. Exceptions are logged but don't stop the timer.
:::

## @message

Defines a handler for messages from the main system.

```python
from plugin.sdk.decorators import message

@message(
    id="handle_chat",
    source="chat",           # Filter by source
    auto_start=True
)
def handle_chat(self, text: str, sender: str, **_):
    return {"handled": True}
```

## @on_event

Generic event handler for custom event types.

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
