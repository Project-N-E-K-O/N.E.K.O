# Entries & Parameters

An **entry point** is a function in your plugin that can be called from the Plugin Manager, by other plugins, or by the AI agent.

## Defining an entry

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):

    @plugin_entry(id="search", name="Search", description="Search for something")
    async def search(self, query: str, max_results: int = 10):
        results = await self._do_search(query, max_results)
        return Ok({"results": results})
```

That's it. The SDK automatically generates the input schema from your type annotations.

## How parameters work

The SDK looks at your function signature and builds a JSON Schema from it:

```python
async def search(self, query: str, max_results: int = 10):
```

Becomes:

```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string"},
    "max_results": {"type": "integer", "default": 10}
  },
  "required": ["query"]
}
```

- Parameters without defaults → `required`
- Parameters with defaults → optional
- `self` and `**kwargs` are ignored

## Adding descriptions with `Annotated`

```python
from typing import Annotated

async def search(
    self,
    query: Annotated[str, "Search keywords"],
    max_results: Annotated[int, "Max number of results"] = 10,
):
    ...
```

The string inside `Annotated` becomes the `description` in the schema. This description is shown in the Plugin Manager UI and to the AI agent.

## Using Pydantic models for complex inputs

When you have many parameters or need validation (min/max, regex, etc.), use a Pydantic model:

```python
from pydantic import BaseModel, Field

class SearchParams(BaseModel):
    query: str = Field(..., description="Search keywords")
    max_results: int = Field(default=10, ge=1, le=50, description="Max results")
    language: str = Field(default="zh-CN", description="Result language")

@plugin_entry(id="search", name="Search", description="Search for content")
async def search(self, params: SearchParams):
    # params is a validated Pydantic instance
    results = await self._do_search(params.query, params.max_results)
    return Ok({"results": results})
```

The SDK:
1. Generates `input_schema` from the model (including descriptions, defaults, constraints)
2. Validates incoming arguments with `model_validate()` at runtime
3. Passes the validated model instance to your function

## Return values

Always return `Ok(...)` for success or `Err(...)` for errors:

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="divide", name="Divide", description="Divide two numbers")
async def divide(self, a: float, b: float):
    if b == 0:
        return Err(SdkError("Cannot divide by zero"))
    return Ok({"result": a / b})
```

## Controlling what the AI sees

By default, the AI sees the full return value. Use `llm_result_fields` to limit it:

```python
@plugin_entry(
    id="search",
    name="Search",
    description="Search the web",
    llm_result_fields=["summary"],
)
async def search(self, query: str):
    results = await self._do_search(query)
    summary = self._build_summary(results)
    # AI only sees "summary"; "raw_results" is stored but hidden from the AI
    return Ok({"summary": summary, "raw_results": results})
```

## `@plugin_entry` options

| Option | Default | Description |
|--------|---------|-------------|
| `id` | method name | Entry point ID |
| `name` | same as id | Display name |
| `description` | `""` | Description (shown to AI and in UI) |
| `input_schema` | auto-inferred | Manual JSON Schema (overrides auto-inference) |
| `params` | auto-inferred | Explicit Pydantic model class |
| `timeout` | `None` | Execution timeout in seconds |
| `llm_result_fields` | `None` | Fields visible to the AI |
| `kind` | `"action"` | Entry type |
| `auto_start` | `False` | Auto-execute on plugin load |

## Dynamic entries

Register entry points at runtime (e.g. based on config):

```python
from plugin.sdk.plugin import lifecycle, Ok

@lifecycle(id="startup")
async def on_startup(self):
    commands = self.metadata.get("commands", {})
    for cmd_id, cmd_config in commands.items():
        self.register_dynamic_entry(
            entry_id=cmd_id,
            handler=lambda **kwargs: Ok({"executed": cmd_id}),
            name=cmd_config.get("name", cmd_id),
            description=cmd_config.get("description", ""),
        )
    return Ok({"status": "ready"})
```
