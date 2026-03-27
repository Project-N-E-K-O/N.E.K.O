# Decorators

All decorators are imported from `plugin.sdk.plugin`.

```python
from plugin.sdk.plugin import (
    neko_plugin, plugin_entry, lifecycle, timer_interval, message,
    on_event, custom_event,
    hook, before_entry, after_entry, around_entry, replace_entry,
    plugin,  # namespace-style alternative
)
```

## @neko_plugin

Marks a class as a N.E.K.O. plugin. **Required** on all plugin classes.

```python
@neko_plugin
class MyPlugin(NekoPluginBase):
    pass
```

## @plugin_entry

Defines an externally callable entry point.

```python
@plugin_entry(
    id="process",                # Entry point ID (auto-generated from method name if omitted)
    name="Process Data",         # Display name
    description="Process data",  # Description
    input_schema={...},          # JSON Schema for validation
    params=MyParamsModel,        # Alternative: Pydantic model for input (auto-generates schema)
    kind="action",               # "action" | "service" | "hook" | "custom"
    auto_start=False,            # Auto-start on load
    persist=False,               # Persist across reloads
    model_validate=True,         # Enable Pydantic validation
    timeout=30.0,                # Execution timeout in seconds
    llm_result_fields=["text"],  # Fields to extract for LLM consumption
    llm_result_model=MyResult,   # Pydantic model for result schema
    metadata={"category": "data"}  # Additional metadata
)
def process(self, data: str, **_):
    return Ok({"result": data})
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `id` | `str` | method name | Unique entry point identifier |
| `name` | `str` | `None` | Display name |
| `description` | `str` | `""` | Description |
| `input_schema` | `dict` | `None` | JSON Schema for input validation |
| `params` | `type` | `None` | Pydantic model (auto-generates `input_schema`) |
| `kind` | `str` | `"action"` | Entry type |
| `auto_start` | `bool` | `False` | Auto-start when loaded |
| `persist` | `bool` | `None` | Persist state across reloads |
| `model_validate` | `bool` | `True` | Enable Pydantic validation |
| `timeout` | `float` | `None` | Execution timeout (seconds) |
| `llm_result_fields` | `list[str]` | `None` | Fields for LLM result extraction |
| `llm_result_model` | `type` | `None` | Pydantic model for result schema |
| `fields` | `type` | `None` | Alias for `params` |
| `metadata` | `dict` | `None` | Additional metadata |

::: tip
Always include `**_` in your function signature to capture unused parameters gracefully.
:::

## @lifecycle

Defines lifecycle event handlers.

```python
@lifecycle(id="startup")
def on_startup(self, **_):
    self.logger.info("Starting up...")
    return Ok({"status": "ready"})

@lifecycle(id="shutdown")
def on_shutdown(self, **_):
    self.logger.info("Shutting down...")
    return Ok({"status": "stopped"})

@lifecycle(id="reload")
def on_reload(self, **_):
    self.logger.info("Reloading config...")
    return Ok({"status": "reloaded"})
```

Valid lifecycle IDs: `startup`, `shutdown`, `reload`, `freeze`, `unfreeze`, `config_change`.

## @timer_interval

Defines a scheduled task that executes at fixed intervals.

```python
@timer_interval(
    id="cleanup",
    seconds=3600,           # Execute every hour
    name="Cleanup Task",
    auto_start=True          # Start automatically (default: True)
)
def cleanup(self, **_):
    # Runs in a separate thread
    return Ok({"cleaned": True})
```

::: info
Timer tasks run in separate threads. Exceptions are logged but don't stop the timer.
:::

## @message

Defines a handler for messages from the host system.

```python
@message(
    id="handle_chat",
    source="chat",           # Filter by message source
    auto_start=True
)
def handle_chat(self, text: str, sender: str, **_):
    return Ok({"handled": True})
```

## @on_event

Generic event handler for custom event types.

```python
@on_event(
    event_type="custom_event",
    id="my_handler",
    kind="hook"
)
def custom_handler(self, event_data: str, **_):
    return Ok({"processed": True})
```

## @custom_event

Specialized event handler with trigger method control.

```python
@custom_event(
    event_type="data_refresh",
    id="refresh_handler",
    trigger_method="message",  # How this event is triggered
    auto_start=False
)
def on_refresh(self, source: str, **_):
    return Ok({"refreshed": True})
```

---

## Hook Decorators (AOP)

Hook decorators provide Aspect-Oriented Programming capabilities. They intercept entry point execution.

### @before_entry

Runs before the target entry point. Can modify arguments or abort execution.

```python
@before_entry(target="process", priority=0)
def validate_input(self, *, args, entry_id, **_):
    if not args.get("data"):
        return Err(SdkError("data is required"))
    # Return None to continue, or Err to abort
```

### @after_entry

Runs after the target entry point. Can modify or replace the result.

```python
@after_entry(target="process", priority=0)
def log_result(self, *, result, entry_id, **_):
    self.logger.info(f"Entry {entry_id} returned: {result}")
    # Return None to keep original result, or a new value to replace it
```

### @around_entry

Wraps the target entry point. Full control over execution.

```python
@around_entry(target="process", priority=0)
async def timing_wrapper(self, *, proceed, args, **_):
    import time
    start = time.time()
    result = await proceed(**args)
    elapsed = time.time() - start
    self.logger.info(f"Took {elapsed:.2f}s")
    return result
```

### @replace_entry

Completely replaces the target entry point.

```python
@replace_entry(target="old_entry", priority=0)
def new_implementation(self, **kwargs):
    return Ok({"replaced": True})
```

### Hook parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target` | `str` | `"*"` | Entry ID to hook (`"*"` = all entries) |
| `priority` | `int` | `0` | Execution order (lower = earlier) |
| `condition` | `str` | `None` | Optional condition expression |

---

## Namespace-Style Alternative: `plugin.*`

For cleaner syntax, use the `plugin` namespace object:

```python
from plugin.sdk.plugin import plugin

@plugin.entry(id="greet", description="Say hello")
def greet(self, name: str = "World", **_):
    return Ok({"message": f"Hello, {name}!"})

@plugin.lifecycle(id="startup")
def on_startup(self, **_):
    return Ok({"status": "ready"})

@plugin.hook(target="greet", timing="before")
def validate(self, *, args, **_):
    pass

@plugin.timer(id="heartbeat", seconds=60)
def heartbeat(self, **_):
    return Ok({"alive": True})

@plugin.message(id="on_chat", source="chat")
def on_chat(self, text: str, **_):
    return Ok({"handled": True})
```
