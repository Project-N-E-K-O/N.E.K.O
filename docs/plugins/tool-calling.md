# Registering LLM Tool Calls

Allow the LLM to "call" functionality provided by your plugin during a
conversation. For example, if your plugin offers `get_weather`, the LLM
will automatically invoke it when the user asks "what's the weather in
Beijing", wait for the result, then use the returned value to generate
its final reply.

This mechanism is backed by `ToolRegistry` in
`main_logic/tool_calling.py` and unified across every provider that
supports tool calling (OpenAI / Gemini / GLM / Qwen Omni / StepFun, etc.).

## TL;DR — Recommended Path: `@llm_tool`

If you're building a regular `NekoPluginBase` plugin, **use the
`@llm_tool` decorator from the SDK**. It handles registration,
unregistration, callback routing, and shutdown cleanup for you, with
zero boilerplate:

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
                "city": {"type": "string", "description": "City name, e.g. 'Beijing'"},
            },
            "required": ["city"],
        },
    )
    async def get_weather(self, *, city: str):
        return {"city": city, "temp_c": 22, "weather": "sunny"}
```

That's the entire integration. The decorator is auto-discovered by the
SDK base class during plugin construction; the plugin server registers
the tool with `main_server` over HTTP and routes incoming model
dispatches back into the plugin process via existing IPC. When the
plugin stops, every registered tool is cleared from `main_server`
on a best-effort basis.

What the helper *doesn't* do: auto-recover after a `main_server` restart
or first-boot race. Registration is fired once at plugin startup; if
`main_server` was unreachable then, the tool stays invisible to the
model until the plugin reloads or `register_llm_tool` is invoked
imperatively. The "What Happens When main_server Restarts" section near
the end of this page covers the resilience pattern in detail.

The rest of this document explains the underlying HTTP contract and
when you might need to reach for it directly.

## Architecture

There are two integration layers, stacked:

### Layer 1 — Raw HTTP (universal)

```text
┌──────────────────┐  HTTP /api/tools/register   ┌──────────────────────┐
│  Plugin (process)│ ───────────────────────────▶│  Main Server         │
│                  │                             │  - ToolRegistry      │
│  callback_url    │ ◀──── HTTP POST tool ──────│  - Realtime / Offline│
│  /tool_invoke    │       call invocation      │    LLM clients       │
└──────────────────┘                             └──────────────────────┘
```

- The plugin **registers tools via HTTP** to main_server's
  `LLMSessionManager.tool_registry`.
- When the LLM triggers a tool call, main_server **POSTs to the
  plugin's `callback_url`**.
- The plugin returns a JSON result, which main_server feeds back to the
  LLM to continue generation.

This layer is documented in detail in the rest of this page. Use it
directly only when you need behaviour the SDK helper doesn't cover —
for example, hosting your own HTTP server outside the plugin process,
or registering tools from a non-NekoPluginBase context.

### Layer 2 — `@llm_tool` SDK helper (recommended for plugins)

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

The plugin process never speaks HTTP to `main_server` directly. It
emits an IPC message; the host translates it into the layer-1 HTTP
calls; main_server's tool dispatches come back into the plugin via the
same IPC trigger machinery used by regular `@plugin_entry`s.

## Registration Endpoints

All endpoints are mounted on `MAIN_SERVER_PORT` (default `48911`) and
are guarded by `verify_local_access` (only `127.0.0.1` / `::1` /
`localhost` are allowed).

### `POST /api/tools/register`

```json
{
  "name": "get_weather",
  "description": "Look up the weather in a given city",
  "parameters": {
    "type": "object",
    "properties": {
      "city": {"type": "string", "description": "City name, e.g. 'Beijing'"}
    },
    "required": ["city"]
  },
  "callback_url": "http://127.0.0.1:<plugin_port>/tool_invoke",
  "role": null,
  "source": "my_plugin",
  "timeout_seconds": 30
}
```

| Field | Description |
|---|---|
| `name` | Tool name (≤64 chars). This is what the LLM sees. |
| `description` | Description for the LLM. Determines when it gets called. |
| `parameters` | JSON Schema (OpenAI style). |
| `callback_url` | Where main_server POSTs when the LLM invokes the tool. |
| `role` | `null` = register to all catgirls; specific name = only that one. |
| `source` | Custom source tag, useful for batch `clear` later. |
| `timeout_seconds` | Per-call timeout (≤300, default 30). |

Response:

```json
{ "ok": true, "registered": "get_weather", "affected_roles": ["小八"], "failed_roles": [] }
```

If `affected_roles` is empty `ok` becomes `false` and `failed_roles[*].error`
carries the details.

### `POST /api/tools/unregister`

```json
{ "name": "get_weather", "role": null }
```

### `POST /api/tools/clear`

```json
{ "role": null, "source": "my_plugin" }
```

`source` is **required** (≥1 char). The HTTP endpoint only supports
clearing by source — sending an empty `source` returns 422. If you need
"clear all" semantics, either iterate per-source or call the in-process
`mgr.clear_tools()` (which accepts `source=None`).

### `GET /api/tools[?role=<name>]`

Returns the currently registered tools.

## callback_url Protocol

When the LLM triggers a tool call, main_server sends a `POST` to
`callback_url`:

**Request body**:

```json
{
  "name": "get_weather",
  "arguments": {"city": "Beijing"},
  "call_id": "call_abc123",
  "raw_arguments": "{\"city\":\"Beijing\"}"
}
```

`arguments` is the JSON-parsed dict; `raw_arguments` is the raw string
(useful in the rare case the LLM emits invalid JSON).

**Response body**:

```json
{ "output": {"temp_c": 22, "weather": "sunny"}, "is_error": false }
```

Or on failure:

```json
{ "output": null, "is_error": true, "error": "city not found" }
```

**How `output` is extracted**: main_server calls
`body.get("output", body)` — when the response body contains an
`output` key its value is fed to the LLM; otherwise the whole body is
treated as the output. **Always wrap your result in
`{"output": ...}`**, or metadata like `is_error` / `error` will appear
side-by-side with your real result and confuse the model.

`output` itself may be any JSON (dict / list / string / number). When
`is_error: true` is set, the LLM sees the failure and may skip the tool
or pick another one.

The `callback_url` may be any path on `127.0.0.1:<plugin_port>`; the
plugin runs its own HTTP server to receive it.

## Full Lifecycle Pattern

```python
import asyncio
import httpx

MAIN_SERVER = "http://127.0.0.1:48911"
MY_PORT = 9876
TOOL_NAME = "get_weather"

async def register_with_retry():
    """Call on plugin startup: register the tool once main_server is up,
    retrying indefinitely."""
    payload = {
        "name": TOOL_NAME,
        "description": "Look up the weather in a given city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        "callback_url": f"http://127.0.0.1:{MY_PORT}/tool_invoke",
        "role": None,
        "source": "my_plugin",
        "timeout_seconds": 30,
    }
    async with httpx.AsyncClient() as client:
        while True:
            try:
                r = await client.post(f"{MAIN_SERVER}/api/tools/register",
                                       json=payload, timeout=5)
                if r.json().get("ok"):
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass  # main_server isn't up yet, try again later
            await asyncio.sleep(2)

async def unregister_on_shutdown():
    """Call before exit: unregister so the LLM doesn't hit a dead callback_url."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.post(f"{MAIN_SERVER}/api/tools/unregister",
                              json={"name": TOOL_NAME, "role": None})
    except Exception:
        pass  # main_server is down too — nothing we can do
```

Bind it to your plugin lifecycle hooks:

```python
from plugin.sdk.plugin import NekoPluginBase, plugin

@plugin
class WeatherPlugin(NekoPluginBase):
    async def on_start(self):
        # Register asynchronously so plugin startup doesn't block on it.
        asyncio.create_task(register_with_retry())
        # Also start an HTTP server to receive the callback (FastAPI / aiohttp etc.)
        ...

    async def on_shutdown(self):
        await unregister_on_shutdown()
```

## What Happens When main_server Restarts

⚠️ **Important**: `tool_registry` is an in-memory attribute on
`LLMSessionManager`, so **everything is lost when main_server
restarts**. The plugin must handle this:

- **Plugin outlives main_server** (more common): the plugin needs to
  detect the heartbeat / connection drop and **re-register** when
  main_server comes back up. The simplest approach is a background task
  that periodically `GET /api/tools?role=...` to verify the tool is
  still there, and re-register if not.
- **Plugin lifetime tied to main_server**: as long as the plugin's
  startup hook runs `register_with_retry`, restart means plugin restart
  too, so registration happens naturally.

## Switching Catgirls

Each catgirl has its own `LLMSessionManager` instance, but they share
plugin-registered tools (depending on the `role` field):

- `role: null` registers to all catgirls → switching needs no
  re-registration.
- `role: "小八"` registers only to that catgirl → switching to a
  different one means the tool isn't available; you'd need to register
  for that catgirl separately.

Switching catgirls **does not restart** main_server, so the registry is
preserved.

## Same-Process Registration (Advanced)

If your plugin runs in the same Python process (e.g. extension mode or
built-in functionality), you can bypass HTTP and call
`LLMSessionManager.register_tool(...)` directly with a local callable
as `handler`, skipping HTTP forwarding:

```python
from main_logic.tool_calling import ToolDefinition

async def handle_get_weather(args: dict) -> dict:
    return {"temp_c": 22, "weather": "sunny"}

mgr.register_tool(ToolDefinition(
    name="get_weather",
    description="Look up the weather in a given city",
    parameters={...},
    handler=handle_get_weather,             # in-process callable
    metadata={"source": "my_extension"},    # source goes into metadata
))
```

To `await` until the wire-level sync completes, use
`await mgr.register_tool_and_sync(...)`.

## Caveats

- **Don't put sensitive information in tool names**: the LLM writes
  tool names into `tool_calls`, which gets persisted into conversation
  history.
- **`callback_url` must point to local loopback**: the server validates
  the host using `urlparse` + `ipaddress.ip_address`, accepting only
  `127.0.0.0/8` / `::1` / literal `localhost`. Otherwise the request
  returns 422. There are **two independent gates**:
  - `verify_local_access` restricts who can call
    `/api/tools/register` (caller-source check).
  - `callback_url` host whitelist restricts the registered callback
    address (prevents a local caller from using main_server as an SSRF
    egress proxy).
  Cross-host scenarios need an explicit reverse-proxy + auth flow.
- **`timeout_seconds ≤ 300`**: synchronous tools that need more than 5
  minutes should be redesigned as "return immediately, push results
  asynchronously through the plugin's own event channel"; otherwise the
  conversation will stall.
- **Return clear errors on failure**: `is_error: true` + a
  human-readable `error` so the LLM knows what happened. Don't silently
  return an empty result — the LLM will be confused.
- **Repeated `register` is replace-semantics**: a same-name tool is
  overwritten; you can use this to hot-update parameter schemas.

## SDK Helper Reference (`@llm_tool`)

### `@llm_tool` decorator

Defined at `plugin/sdk/plugin/llm_tool.py`. Imported from the SDK
top-level: `from plugin.sdk.plugin import llm_tool`.

```python
@llm_tool(
    *,
    name: str | None = None,        # defaults to the method's __name__
    description: str = "",          # shown to the LLM
    parameters: dict | None = None, # JSON Schema; default = no args
    timeout: float = 30.0,          # per-call timeout (seconds, ≤ 300)
    role: str | None = None,        # None = global, or specific catgirl name
)
```

The decorated method is invoked with the parsed JSON arguments as
keyword arguments. Use `*` in the signature so accidental positional
args fail loudly:

```python
@llm_tool(name="search", parameters={...})
async def search(self, *, query: str, limit: int = 10):
    ...
```

`name` must match `[A-Za-z0-9_.\-]{1,64}` so it stays safe to inline
into the callback URL path segment.

### `NekoPluginBase` instance methods

For tools whose schema isn't known until runtime (e.g. derived from
configuration), call the imperative API:

```python
self.register_llm_tool(
    name="custom_tool",
    description="...",
    parameters={"type": "object", "properties": {...}},
    handler=my_async_callable,
    timeout=30.0,
    role=None,
)
```

`unregister_llm_tool(name)` reverses it; `list_llm_tools()` returns the
current set as plain dicts. Duplicate names raise `EntryConflictError`.

### Returning errors

Returning a plain value (`str`, `dict`, `int`, ...) is treated as
success. To flag a tool-level error to the LLM without raising,
return a dict shaped:

```python
return {"output": {"reason": "city not found"}, "is_error": True, "error": "CITY_NOT_FOUND"}
```

Raising inside the handler also returns an error to the LLM (the
exception's class name and message are forwarded as the error). The
plugin doesn't crash — only that one tool call fails.

### Lifecycle and ordering

- `@llm_tool`-decorated methods are auto-registered at the *end* of
  `NekoPluginBase.__init__`, i.e. as soon as `super().__init__(ctx)`
  returns. The handler doesn't actually execute until the LLM picks
  the tool, so it's safe to register before subclass `__init__`
  finishes setting up state the handler reads (config dicts,
  service clients, etc.).
- The IPC notification (`LLM_TOOL_REGISTER`) is buffered on the
  plugin's host queue. If `main_server` is not yet up when the
  notification arrives, the registration call fails and the host
  logs a warning — re-register later via the imperative API or by
  reloading the plugin once `main_server` is healthy.
- On plugin stop, `lifecycle_service.stop_plugin` calls
  `plugin/server/messaging/llm_tool_registry.py::clear_plugin_tools`,
  which POSTs `/api/tools/clear` with body
  `{"source": "plugin:{plugin_id}", "role": null}` so every tool the
  plugin registered is dropped in one round-trip. The cleanup is
  best-effort: if `main_server` is unreachable at that moment, we log
  and continue — process restart or a manual `clear` call will
  reconcile.

### Where the code lives

| File | Role |
|---|---|
| `plugin/sdk/plugin/llm_tool.py` | `@llm_tool` decorator, `LlmToolMeta`, name validation, method collector. |
| `plugin/sdk/plugin/base.py` | `NekoPluginBase.register_llm_tool` / `unregister_llm_tool` / `list_llm_tools` + auto-registration in `__init__`. |
| `plugin/core/communication.py` | Host-side IPC handlers `_handle_llm_tool_register` / `_handle_llm_tool_unregister` (routed by message type in `_MESSAGE_ROUTING`). |
| `plugin/server/messaging/llm_tool_registry.py` | Process-global tracking + httpx wrappers around `main_server`'s `/api/tools/{register,unregister,clear}`. |
| `plugin/server/routes/llm_tools.py` | The `/api/llm-tools/callback/{plugin_id}/{tool_name}` route that receives main_server's dispatches and forwards them through `host.trigger`. |
| `plugin/server/application/plugins/lifecycle_service.py` | Calls `clear_plugin_tools` on plugin stop. |
