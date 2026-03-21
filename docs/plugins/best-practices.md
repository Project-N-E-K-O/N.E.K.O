# Best Practices

## Use Result types consistently

Always return `Ok`/`Err` instead of raising exceptions in entry points:

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="process")
def process(self, data: str, **_):
    if not data:
        return Err(SdkError("data is required"))

    try:
        result = self._do_work(data)
        return Ok({"result": result})
    except ValueError as e:
        return Err(SdkError(f"Validation error: {e}"))
    except Exception as e:
        self.logger.exception(f"Unexpected error: {e}")
        return Err(SdkError(f"Internal error"))
```

## Code organization

Separate initialization, helpers, and public entry points:

```python
@neko_plugin
class WellOrganizedPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._initialize()

    # --- Lifecycle ---
    @lifecycle(id="startup")
    def on_startup(self, **_):
        return Ok({"status": "ready"})

    # --- Private helpers ---
    def _initialize(self):
        """Setup resources."""
        pass

    def _validate(self, data):
        """Internal validation."""
        pass

    # --- Public entry points ---
    @plugin_entry(id="process")
    def process(self, data: str, **_):
        self._validate(data)
        return Ok({"result": self._do_work(data)})
```

## Logging

Use appropriate log levels:

| Level | When to use |
|-------|------------|
| `debug` | Detailed diagnostic information |
| `info` | Normal operation milestones |
| `warning` | Unexpected but handled situations |
| `error` | Errors that need attention |
| `exception` | Errors with full stack trace |

```python
self.logger.debug(f"Processing item {item_id}")
self.logger.info(f"Plugin started successfully")
self.logger.warning(f"Retry attempt {attempt}/3")
self.logger.error(f"Failed to connect: {err}")
self.logger.exception(f"Unexpected error in process()")
```

## Status updates

Report progress during long-running operations:

```python
@plugin_entry(id="batch_job")
def batch_job(self, items: list, **_):
    total = len(items)
    for i, item in enumerate(items):
        self._process(item)
        self.report_status({
            "status": "processing",
            "progress": (i + 1) / total * 100,
            "message": f"Processing {i+1}/{total}"
        })

    self.report_status({"status": "completed", "progress": 100})
    return Ok({"processed": total})
```

## Input validation

Use `input_schema` for automatic JSON Schema validation, or `params` for Pydantic models:

```python
# Option A: JSON Schema
@plugin_entry(
    id="validated",
    input_schema={
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "age": {"type": "integer", "minimum": 0, "maximum": 150}
        },
        "required": ["email", "age"]
    }
)
def validated(self, email: str, age: int, **_):
    return Ok({"email": email, "age": age})

# Option B: Pydantic model (auto-generates schema)
from pydantic import BaseModel, Field

class UserInput(BaseModel):
    email: str = Field(..., description="User email")
    age: int = Field(..., ge=0, le=150)

@plugin_entry(id="validated_v2", params=UserInput)
def validated_v2(self, email: str, age: int, **_):
    return Ok({"email": email, "age": age})
```

## Working directory

Use `self.config_dir` and `self.data_path()` instead of hardcoded paths:

```python
# Plugin directory (where plugin.toml lives)
config_file = self.config_dir / "config.json"

# Data directory (auto-created subdirectory)
db_path = self.data_path("cache.db")       # → <plugin_dir>/data/cache.db
logs_dir = self.data_path("logs")          # → <plugin_dir>/data/logs/
```

## Cross-plugin call error handling

Always handle `Err` when calling other plugins:

```python
@plugin_entry(id="orchestrate")
async def orchestrate(self, **_):
    # Check dependency first
    dep = await self.plugins.require_enabled("dependency_plugin")
    if isinstance(dep, Err):
        return Err(SdkError("Required plugin 'dependency_plugin' is not available"))

    # Make the call
    result = await self.plugins.call_entry("dependency_plugin:do_work", {"key": "val"})
    if isinstance(result, Err):
        self.logger.error(f"Cross-plugin call failed: {result.error}")
        return Err(SdkError("Dependency call failed"))

    return Ok({"combined": result.value})
```

## Graceful shutdown

Clean up resources in the shutdown lifecycle:

```python
@lifecycle(id="shutdown")
async def on_shutdown(self, **_):
    # Close network connections
    if self.session:
        await self.session.close()

    # Flush pending data
    await self.store.flush()

    # Cancel timers (handled automatically, but log it)
    self.logger.info("Plugin shutting down gracefully")
    return Ok({"status": "stopped"})
```

## Plugin checklist

Before shipping your plugin:

- [ ] All entry points return `Ok`/`Err` (not raw dicts or exceptions)
- [ ] `@lifecycle(id="startup")` and `@lifecycle(id="shutdown")` are implemented
- [ ] `input_schema` is defined for all entry points that accept parameters
- [ ] `**_` is included in all entry point signatures
- [ ] Logger is used instead of `print()`
- [ ] Shared state is protected with locks if timers are used
- [ ] Cross-plugin calls handle `Err` results
- [ ] `plugin.toml` has correct `entry` path and SDK version constraints
