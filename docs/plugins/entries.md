# Entries & Parameters

An entry point is a callable operation a loaded plugin exposes to Plugin Manager, other plugins, and the Agent (NekoClaw). It is identified by the runtime `entry_id` from `@plugin_entry(id=...)`.

Do not confuse this with `[plugin].entry` in `plugin.toml`, which is the `module.path:ClassName` used by the host to import the plugin class. Also do not confuse it with `@llm_tool`: LLM tools are registered with main_server's `/api/tools` registry and are called by the main dialogue model during a conversation, while the Agent selects a plugin runtime entry when a task has been handed over to it.

The Agent filters plugins first and looks at entry metadata afterwards. The fine selection must return an exact `plugin_id` and `entry_id`; an unknown or missing ID is not asked about again and no entry is called directly: the task goes to a task agent, or is reported as not doable when none is available. See [How the Agent calls an entry](#how-the-agent-calls-an-entry) for when entries are called, by whom, and what happens to their results.

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

A task agent that calls the entry also sees these descriptions, because it is given the entry's full `input_schema`. The selection that calls an entry directly sees only parameter names, types, and `enum` values, so anything it has to know belongs in the entry's `description`. Both callers are explained in [How the Agent calls an entry](#how-the-agent-calls-an-entry).

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

Declare `llm_result_fields` to say which fields of the result the AI reads. A return value often contains large raw data (like a full search results list) while the AI only needs a summary:

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

Without `llm_result_fields`, what the AI gets depends on who called the entry; see the Results table in [How the Agent calls an entry](#how-the-agent-calls-an-entry).

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

## How the Agent calls an entry

The Agent (NekoClaw) looks for an entry only when the main dialogue model hands a task over: the model called its `dispatch_task` tool, or it agreed out loud and the turn check dispatched for it. Finished turns are not analysed automatically. [Agent System](/architecture/agent-system) describes the whole path; how plugins are filtered is described in [plugin.toml](./plugin-toml). Entries whose ID starts with `__llm_tool__` (the ones `@llm_tool` creates) never take part; only the main dialogue model calls them.

### Which entries the Agent can see

An entry is left out when its plugin sets `passive = true`, or when the entry's `metadata` has `agent_auto`, `agent_exposed`, or `llm_exposed` set to `false`, or `agent_hidden` set to `true`:

```python
@plugin_entry(id="internal_sync", metadata={"agent_hidden": True})
async def internal_sync(self):
    ...
```

These switches only affect the Agent. The entry stays callable from Plugin Manager and from other plugins.

### Two callers

- **Direct call (the main path).** When one entry can finish the task, the fine selection picks it and fills in the arguments. It is shown the plugin's `description` and, for each visible entry, its ID, its `description`, and a compact argument hint: the first eight parameters with their types and `enum` values, plus `required`. Parameter descriptions are not included.
- **Task agent.** When no entry fits or the task needs several steps, the task goes to a task agent, an execution session that the interface calls a familiar. It reaches entries as tools through the capability gateway. The tool description is the plugin name, the entry name, and the entry's `description` (cut at 200 tokens), and the tool parameters are the entry's `input_schema`. A task agent is given the task as it was handed over, not the conversation history. Entries of the plugins that the selection shortlisted are listed to it up front; other visible entries are found through a search tool that matches their descriptions.

Both callers send the same `/runs` request and inject the same keys into `args._ctx`:

| `_ctx` key | Value |
|---|---|
| `lanlan_name` | The character the task belongs to |
| `conversation_id` | The identifier that came with the dispatch, when there is one |
| `entry_timeout` | The timeout that applies to this call: the entry's `timeout`, or the host default |
| `latest_user_request` | The user's own words of that turn, unchanged |

The permission category for plugin entries (`plugin_entry` in the host's `permissions.json`) is `allow` by default. When it is set to one of the asking modes, a task agent's call waits for the user's approval; `deny` keeps both callers away from plugin entries.

### What happens to the result

| | Direct call | Called by a task agent |
|---|---|---|
| Who reads the result | The main dialogue model, as the report of the task | The task agent, as the tool result. The user hears about it when the whole task ends |
| With `llm_result_fields` | Only the listed fields | Only the listed fields |
| Without `llm_result_fields` | One sentence: `data.reply` if it is a non-empty string, otherwise the run's message | That sentence, followed by `data` as JSON |
| `meta.agent.delivery`, `result_kind`, `expires_in_s` | Applied | Not applied. The report to the user follows the task's own setting |
| `data.needs_confirmation` (or `status: "confirm_required"`) | The task ends as `blocked` and the plugin's message is reported | The gateway puts the plugin's message to the user as a question and gives the answer to the task agent, which cannot answer in the user's place |
| `data.action: "clarify"` (or `status: "clarify"` / `"blocked"`) | The task ends as `blocked` and the plugin's message is reported | Returned to the task agent with a note that the call was not carried out |
| `data.deferred: true` | The task stays open until the plugin calls back, and fails after one hour without a callback | Returned to the task agent with a note that the plugin finishes it later. `bind_task` is not called |

The direct-call column is unchanged from the previous Agent. A result with `observation_only` set is not read for `needs_confirmation` or `clarify` by either caller.

### Stopping

When a task is stopped, the host calls `POST /runs/{run_id}/cancel` on the plugin service for every run of that task that is still going. This applies to direct calls and to calls made by a task agent.

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
