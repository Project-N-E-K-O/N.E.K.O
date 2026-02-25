# Best Practices

## Code organization

Separate initialization, helpers, and public entry points:

```python
@neko_plugin
class WellOrganizedPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._initialize()

    # Private helpers
    def _initialize(self):
        """Setup resources"""
        pass

    def _helper(self, data):
        """Internal logic"""
        pass

    # Public entry points
    @plugin_entry(id="process")
    def process(self, data: str, **_):
        result = self._helper(data)
        return {"result": result}
```

## Error handling

Always handle errors gracefully and return structured responses:

```python
@plugin_entry(id="task")
def task(self, param: str, **_):
    try:
        if not param:
            raise ValueError("param is required")
        result = self._do_work(param)
        return {"success": True, "result": result}
    except ValueError as e:
        self.logger.warning(f"Validation error: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        self.logger.exception(f"Unexpected error: {e}")
        return {"success": False, "error": "Internal error"}
```

## Logging

Use appropriate log levels:

| Level | When to use |
|-------|------------|
| `debug` | Detailed diagnostic information |
| `info` | Normal operation milestones |
| `warning` | Unexpected but handled situations |
| `error` | Errors that need attention |
| `exception` | Errors with stack trace |

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
    return {"processed": total}
```

## Input validation

Define `input_schema` with JSON Schema for automatic validation:

```python
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
    # Input is already validated by the framework
    return {"email": email, "age": age}
```

## Working directory

Use `ctx.config_path.parent` as the base for plugin-specific files:

```python
work_dir = self.ctx.config_path.parent / "data"
work_dir.mkdir(exist_ok=True)
```
