# Plugin SDK: `@llm_tool` decorator

**Status**: introduced this release · stable · no deprecations.

## Summary

The plugin SDK now exposes a one-line way to register a model-callable
LLM tool from a `NekoPluginBase` plugin. Decorate a method with
`@llm_tool`, ship it, and the SDK takes care of the registration with
`main_server`, the round-trip when the LLM picks the tool, and the
cleanup on shutdown.

```python
from plugin.sdk.plugin import neko_plugin, NekoPluginBase, llm_tool, lifecycle, Ok

@neko_plugin
class WeatherPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready"})

    @llm_tool(
        name="get_weather",
        description="Look up the weather in a given city.",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
            },
            "required": ["city"],
        },
    )
    async def get_weather(self, *, city: str):
        return {"city": city, "temp_c": 22, "weather": "sunny"}
```

The decorator alone is enough — no need to spin up an HTTP server, no
need to write registration / unregistration / cleanup-on-stop logic
yourself.

> ⚠️ **What this helper doesn't do**: it doesn't auto-recover after a
> `main_server` restart or first-boot race. The IPC notification fires
> once at plugin startup; if `main_server` was unreachable at that
> moment the registration is skipped (with a warning logged) and the
> tool stays invisible to the model until the plugin reloads or
> `register_llm_tool` is called imperatively. Plugins that need
> resilience to `main_server` restarts should detect the condition
> (e.g. via a periodic `GET /api/tools` health probe) and re-register
> themselves.

## Why

Before this release, plugins that wanted the LLM to call into them had
to use the raw `/api/tools/register` HTTP API directly (see
`docs/plugins/tool-calling.md`, layer 1). That meant every plugin had
to:

1. Run its own HTTP server inside the plugin process to receive
   `callback_url` POSTs.
2. Discover `main_server`'s loopback URL and POST registration with a
   correct `callback_url`.
3. Implement retry logic for the inevitable case where `main_server`
   isn't ready when the plugin starts.
4. Track every tool name registered so it can `clear` them on
   shutdown.
5. Handle the JSON shape of the dispatch (`{"name", "arguments",
   "call_id", "raw_arguments"}`) and the response (`{"output",
   "is_error"}`).

That's a lot of boilerplate for what should be "expose this method to
the model." The `@llm_tool` decorator collapses all of it into one
declaration.

## Architecture

```text
                 (1) IPC: LLM_TOOL_REGISTER
                          ┌──────────────────────────────┐
                          ▼                              │
┌────────────────────┐         ┌──────────────────────┐  │  ┌─────────────────┐
│ Plugin process     │         │ user_plugin_server   │──┼─▶│  Main Server    │
│  @llm_tool methods │         │ /api/llm-tools/      │  │  │  ToolRegistry   │
│                    │◀────────│  callback/{pid}/{n}  │◀─┼──│ POSTs callback  │
│  IPC trigger       │  (3)    │ POST main_server     │  │  │ when LLM picks  │
└────────────────────┘  via    └──────────────────────┘  │  │ the tool        │
                       host.trigger      ▲               │  └─────────────────┘
                                          │              │           │
                                          └──────────────┘           │
                                              (2) HTTP /api/tools/register
                                                  with callback_url pointing
                                                  back at user_plugin_server
```

1. **Plugin emits IPC notification.** The decorator stores metadata on
   the method. `NekoPluginBase.__init__` auto-discovers tagged methods
   and emits `LLM_TOOL_REGISTER` over the existing host message
   queue. The handler is also stored as a *dynamic plugin entry*
   under the reserved id `__llm_tool__{name}` so the entry-trigger
   IPC plumbing handles dispatch.

2. **Host registers with `main_server`.**
   `plugin/core/communication.py::_handle_llm_tool_register` consumes
   the IPC message and POSTs to `main_server`'s
   `/api/tools/register` (added in [plugin-tool-calling-unified
   ToolRegistry](https://github.com/Project-N-E-K-O/N.E.K.O/pull/1035)).
   The `callback_url` points at
   `user_plugin_server`'s new
   `/api/llm-tools/callback/{plugin_id}/{tool_name}` route, on the
   actually-bound port (read from
   `NEKO_USER_PLUGIN_SERVER_PORT` so we cope with port-busy fallback).

3. **`main_server` dispatches a model call.** When the LLM picks the
   tool, `main_server` POSTs the call to the callback URL. The
   `user_plugin_server` route looks up the live plugin via
   `state.plugin_hosts[plugin_id]` and calls
   `host.trigger("__llm_tool__{name}", arguments, timeout)` — the
   exact same IPC path used by regular `@plugin_entry`s. The plugin's
   handler runs in its child process and returns a value, which the
   route re-shapes into `{"output": ..., "is_error": ...}` for
   `main_server` to feed back to the model.

4. **Cleanup on plugin stop.**
   `lifecycle_service.stop_plugin` calls
   `plugin/server/messaging/llm_tool_registry.py::clear_plugin_tools`
   which POSTs `/api/tools/clear` with body
   `{"source": "plugin:{plugin_id}", "role": null}` so every tool
   registered by the plugin is dropped in one round-trip. The cleanup
   is best-effort — a transient `main_server` outage at stop time is
   logged and swallowed; a process restart or manual `clear` call
   will reconcile.

## API surface

### Decorator

```python
@llm_tool(
    *,
    name: str | None = None,
    description: str = "",
    parameters: dict | None = None,
    timeout: float = 30.0,
    role: str | None = None,
)
```

* `name` — model-visible name. Defaults to the method's `__name__`.
  Must match `[A-Za-z0-9_.\-]{1,64}` (URL-safe path segment +
  `main_server`'s 64-char cap).
* `description` — free-text shown to the LLM.
* `parameters` — JSON Schema. Defaults to no arguments.
* `timeout` — per-call timeout in seconds, ≤ 300.
* `role` — `None` for global, or a catgirl/character name to scope to
  one role.

The decorated method receives parsed arguments as kwargs. Return any
JSON-serialisable value, or a `{"output": ..., "is_error": True,
"error": "..."}` dict to signal a tool-level error.

### Imperative API

```python
self.register_llm_tool(
    name="custom",
    description="...",
    parameters={"type": "object", "properties": {...}},
    handler=my_callable,
    timeout=30.0,
    role=None,
)

self.unregister_llm_tool("custom")
self.list_llm_tools()  # -> list[dict]
```

Use this when a tool's schema is built at runtime (e.g. from config or
discovered from an external system). The decorator is preferred
otherwise.

## Touched files (this release)

* `plugin/sdk/plugin/llm_tool.py` (new) — decorator, metadata, name
  validation, method collector.
* `plugin/sdk/plugin/base.py` —
  `register_llm_tool` / `unregister_llm_tool` / `list_llm_tools`
  instance methods, plus auto-registration of decorated methods in
  `__init__`.
* `plugin/sdk/plugin/__init__.py` — re-exports `llm_tool` and
  `LlmToolMeta`.
* `plugin/core/communication.py` — adds `LLM_TOOL_REGISTER` /
  `LLM_TOOL_UNREGISTER` message routing and host-side handlers that
  drive `main_server` registration.
* `plugin/server/messaging/llm_tool_registry.py` (new) — process-
  global tracker + httpx wrappers around `main_server`'s
  `/api/tools/{register,unregister,clear}`.
* `plugin/server/routes/llm_tools.py` (new) —
  `/api/llm-tools/callback/{plugin_id}/{tool_name}` route that
  forwards model dispatches into the plugin via `host.trigger`.
* `plugin/server/routes/__init__.py`, `plugin/server/http_app.py` —
  wire the new router.
* `plugin/server/application/plugins/lifecycle_service.py` —
  `clear_plugin_tools` on plugin stop.
* `docs/plugins/tool-calling.md` — adds a "TL;DR" + "SDK Helper
  Reference" section pointing at the decorator as the recommended
  path.

## Backward compatibility

This is purely additive. The pre-existing `/api/tools/register` HTTP
API (PR #1035) is unchanged. Plugins that already roll their own HTTP
server and registration loop keep working — the SDK helper just
removes the need to do that.

There is no deprecation cycle for any existing API in this change.
