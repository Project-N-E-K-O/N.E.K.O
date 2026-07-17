# Entries & Parameters

An entry point is a callable operation a loaded plugin exposes to Plugin Manager, other plugins, and the user-plugin Agent route. It is identified by the runtime `entry_id` from `@plugin_entry(id=...)`.

Do not confuse this with `[plugin].entry` in `plugin.toml`, which is the `module.path:ClassName` used by the host to import the plugin class. Also do not confuse it with `@llm_tool`: LLM tools are registered with main_server's `/api/tools` registry and can be called during a conversation, while user-plugin Agent routing selects a plugin runtime entry.

For Agent routing, entry metadata is considered only after plugin-level filtering. Stage 2 must return an exact `plugin_id` and `entry_id`; an unknown or missing ID gets one corrective retry and is then rejected.

---

## The simplest entry

You want your plugin to do one thing. Add `@plugin_entry` to a method:

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):

    @plugin_entry(id="hello", name="Say Hello", description="Say hello")
    async def hello(self):
        return Ok({"message": "Hello!"})
```

This entry has no parameters. Click execute in Plugin Manager, get back `{"message": "Hello!"}`.

---

## Adding parameters

Most entries need input. Just write parameters in the function signature:

```python
@plugin_entry(id="greet", name="Greet", description="Greet someone by name")
async def greet(self, name: str, times: int = 1):
    messages = [f"Hello, {name}!" for _ in range(times)]
    return Ok({"messages": messages})
```

The SDK automatically:
- Generates an input form from `name: str` and `times: int = 1` (shown in the panel)
- `name` has no default → required
- `times` has default `1` → optional
- Uses type annotations as form/schema hints. Direct calls pass plain values through; use a Pydantic model when you need runtime validation.

You don't need to write any JSON Schema.

---

## Adding descriptions to parameters

By default, the panel shows the variable name (`name`, `times`). To show friendlier descriptions, use `Annotated`:

```python
from typing import Annotated

@plugin_entry(id="greet", name="Greet", description="Greet someone by name")
async def greet(
    self,
    name: Annotated[str, "The person's name"],
    times: Annotated[int, "How many times to repeat"] = 1,
):
    messages = [f"Hello, {name}!" for _ in range(times)]
    return Ok({"messages": messages})
```

Now the panel shows "The person's name" next to `name`, and "How many times to repeat" next to `times`.

The AI agent also sees these descriptions, helping it understand what values to pass.

---

## Many parameters: use a Pydantic model

If your entry has many parameters or needs complex validation (min/max, regex, etc.), define them as a Pydantic model:

```python
from pydantic import BaseModel, Field

class SearchParams(BaseModel):
    query: str = Field(..., description="Search keywords")
    max_results: int = Field(default=10, ge=1, le=50, description="Max results")
    language: str = Field(default="zh-CN", description="Result language")
    include_images: bool = Field(default=False, description="Include image results")

@plugin_entry(id="search", name="Search", description="Search for content")
async def search(self, params: SearchParams):
    self.logger.info("Searching: {} (max {})", params.query, params.max_results)
    results = await self._do_search(params.query, params.max_results)
    return Ok({"results": results, "count": len(results)})
```

When the SDK sees a function with one parameter typed as a BaseModel, it automatically:
1. Generates an input form from the model (with descriptions, defaults, constraint hints)
2. Validates input with `model_validate()` on call (e.g. `max_results=100` is rejected because `le=50`)
3. Passes the validated model instance to your function

**When to use Pydantic vs plain parameters?**

- ≤ 3 parameters, no complex validation → write them in the function signature
- Many parameters, need validation constraints, want better docs → use a Pydantic model

---

## Returning success and failure

Entry points must return `Ok(...)` or `Err(...)`:

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="divide", name="Divide", description="Divide two numbers")
async def divide(self, a: float, b: float):
    if b == 0:
        return Err(SdkError("Cannot divide by zero"))
    return Ok({"result": a / b})
```

- `Ok(data)` — Success. `data` can be a dict, list, string, or number.
- `Err(SdkError("reason"))` — Failure. The error message shows in the panel and tells the AI "this call failed".

**Why not just raise exceptions?** Because plugins run in separate processes — exceptions don't propagate to the main system. `Ok`/`Err` is the cross-process safe communication pattern. That said, if your code unexpectedly raises, the framework catches it and converts to `Err` automatically — no crash.

---

## Controlling what the AI sees

By default, the AI sees all fields you return. But sometimes the return value contains large raw data (like a full search results list) and you only want the AI to see a summary:

```python
@plugin_entry(
    id="search",
    name="Search",
    description="Web search",
    llm_result_fields=["summary", "count"],
)
async def search(self, query: str):
    results = await self._do_search(query)
    summary = self._build_summary(results)
    return Ok({
        "summary": summary,          # ← AI can see this
        "count": len(results),        # ← AI can see this
        "raw_results": results,       # ← AI can't see this, but data is still stored
    })
```

Only fields listed in `llm_result_fields` are sent to the AI. Other fields are stored normally and visible in the panel — they just don't get stuffed into the AI's context (saves tokens).

---

## Other entry options

`@plugin_entry` supports these additional options:

```python
@plugin_entry(
    id="process",              # Entry ID (defaults to method name)
    name="Process Data",       # Display name
    description="Process and transform data",  # Description (for humans and AI)
    timeout=60.0,              # Host-side execution timeout in seconds
    kind="service",            # Type tag (default "action")
)
async def process(self, data: str):
    ...
```

Most of the time you only need `id`, `name`, `description`. Use the others as needed. For startup initialization, define a `startup` lifecycle hook instead of expecting an entry to run automatically.

---

## Dynamic entries: register at runtime

Sometimes you don't know what entries your plugin will have — it might depend on config, user settings, or capabilities returned by an external service. Use dynamic registration:

```python
from plugin.sdk.plugin import lifecycle, Ok

@lifecycle(id="startup")
async def on_startup(self):
    # Suppose config defines a set of commands
    cfg = await self.config.dump()
    commands = cfg.get("commands", {})

    for cmd_id, cmd_info in commands.items():
        self.register_dynamic_entry(
            entry_id=cmd_id,
            handler=self._make_handler(cmd_info),
            name=cmd_info.get("name", cmd_id),
            description=cmd_info.get("description", ""),
            input_schema=cmd_info.get("input_schema", {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Command input"},
                },
            }),
        )

    self.logger.info("Registered {} dynamic entries", len(commands))
    return Ok({"status": "ready"})

def _make_handler(self, cmd_info):
    template = cmd_info.get("template", "Executed: {cmd}")
    async def handler(cmd: str = ""):
        return Ok({"output": template.format(cmd=cmd)})
    return handler
```

Dynamic entries work just like static ones — visible in the panel, executable, callable by the AI. If the handler accepts parameters, pass `input_schema` explicitly; dynamic registration does not infer it from the handler signature.

Use `self.unregister_dynamic_entry(entry_id)` to remove them.

---

## Summary

| Scenario | Approach |
|----------|----------|
| Simple function, few params | `@plugin_entry` + type annotations in signature |
| Want param descriptions | Use `Annotated[type, "description"]` |
| Many params / need validation | Define a Pydantic model as single parameter |
| Return success | `return Ok({...})` |
| Return failure | `return Err(SdkError("reason"))` |
| Limit what AI sees | `llm_result_fields=["field1", "field2"]` |
| Don't know entries until runtime | `self.register_dynamic_entry(...)` |
