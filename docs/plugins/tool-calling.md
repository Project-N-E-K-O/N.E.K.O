# Registering LLM Tool Calls

Allow the LLM to "call" functionality provided by your plugin during a
conversation. For example, if your plugin offers `get_weather`, the LLM
will automatically invoke it when the user asks "what's the weather in
Beijing", wait for the result, then use the returned value to generate
its final reply.

## Choosing between `@plugin_entry` and `@llm_tool` {#choosing-an-entry-type}

| | `@plugin_entry` | `@llm_tool` |
|---|---|---|
| What it is | A runtime entry of the plugin | A tool in the main dialogue model's tool list (main_server's `/api/tools` registry) |
| Who uses it | Plugin Manager, other plugins, Hosted UI actions, and the Agent (NekoPower) plugin selection, where the Agent's model picks and calls it | The main dialogue model, which calls it directly during a chat |
| When to use | **The default, recommended form** for a plugin's capabilities | **Sparingly**, only for the few capabilities the main dialogue model must call directly |

The schema of every registered LLM tool is sent to the model with its
requests, so each extra tool lengthens the model's tool list. A plugin usually
needs only a few LLM tools; if a capability can be expressed as a
`@plugin_entry`, keep it one. Internally the SDK stores an LLM tool's handler as
a reserved dynamic entry (`__llm_tool__...`) for IPC reuse; entries with this
prefix are not offered to the Agent's plugin selection or to task agents, so
the two APIs stay separate.

This mechanism is backed by `ToolRegistry` in
`main_logic/tool_calling.py` and unified across every provider that
supports tool calling (OpenAI / Gemini / GLM / Qwen Omni / StepFun, etc.).

## When exposing a tool to the model: use `@llm_tool` (not raw HTTP registration)

When a regular `NekoPluginBase` plugin needs to expose a tool to the
dialogue model, **use the `@llm_tool` decorator from the SDK**. It handles registration,
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
model until the plugin reloads or the tool is registered again. How to
do that safely is in the "What Happens When main_server Restarts" section near
the end of this page.

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

This layer is documented in detail in the rest of this page. It is the
host's internal contract: plugin code should not use it. Use it directly
only from host-side code or an integration that runs outside the plugin
process (for example, an external service registering tools from a
non-NekoPluginBase context).

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

::: warning Not for plugin code
A plugin process must not call `main_server` (or any other N.E.K.O
server on localhost) directly, and must not start its own HTTP server to
receive callbacks: ports and addresses differ per installation and per dev
instance, these endpoints are internal and can change, and they bypass the
SDK's permission checks. In a plugin, use `@llm_tool` or
`self.register_llm_tool(...)`. The endpoints below are documented for
host-side and external integrations.
:::

All endpoints are mounted on `MAIN_SERVER_PORT` (default `48911`) and
are guarded by `verify_local_access` (loopback only — `127.0.0.1`, `::1`,
or `localhost`; when `NEKO_BEHIND_PROXY` is enabled, any request carrying
forwarding headers such as `X-Forwarded`, `X-Forwarded-*`, `X-Real-IP`, or
`Forwarded` is rejected with HTTP `403`, so callers must connect directly
over loopback without forwarding metadata). When `NEKO_BEHIND_PROXY` or
`NEKO_ACTIVITY_TRACKER_REMOTE` is enabled, external loopback callers must
also include valid instance credentials (such as
`Authorization: Bearer <instance_key>` from `NEKO_INSTANCE_ACCESS_KEY` or
`instance_access.key`), otherwise requests fail with HTTP `401`. If
`NEKO_REQUIRE_HTTPS` is also enabled, plaintext `http://` requests fail with
HTTP `401` even with a valid key unless sent over HTTPS — or, only when
`NEKO_BEHIND_PROXY` is enabled in a deployment with an outer TLS gateway
and a private HTTP upstream unexposed to untrusted clients, when `Host`
matches an `https://` `NEKO_INSTANCE_PUBLIC_ORIGIN` (still directly over
loopback without forwarding headers).

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
| `role` | `null` = register to all nekos; specific name = only that one. |
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

Optional fields: `expected_source` (remove only a tool owned by that
source; other owners are reported in `refused_roles`) and
`expected_registration_id` (remove only the registration whose
`registration_id` `/register` returned; a different registration of the
name is reported in `refused_roles` with `"reason": "different
registration"` and that registration's `registration_id`; these two
fields are only added when `expected_source` is given and matches the
tool's source, and `expected_host_instance_id`, if given, matches too).
`/register` accepts an optional `registration_id` and
always returns the one it stored. Without these fields the behaviour is
unchanged.

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

The `callback_url` may be any path on a loopback address. With
`@llm_tool` the plugin server provides this route for you
(`/api/llm-tools/callback/{plugin_id}/{tool_name}`); only an external
integration that registers over raw HTTP needs its own server to receive
it.

## Full Lifecycle Pattern (raw HTTP, external integrations)

This pattern is for an integration that runs outside a plugin process.
The retry loop is part of this example: `@llm_tool` gives plugins
registration, callback routing, and cleanup, but registers only once and
does not retry.

```python
import asyncio
import logging
import os
import httpx

# Direct loopback without X-Forwarded-*/X-Real-IP/Forwarded headers; when NEKO_REQUIRE_HTTPS
# is enabled, connect over HTTPS (plain http:// is accepted only under NEKO_BEHIND_PROXY
# with outer TLS, a private unexposed upstream, and Host matching https:// NEKO_INSTANCE_PUBLIC_ORIGIN)
MAIN_SERVER = "http://127.0.0.1:48911"
MY_PORT = 9876
TOOL_NAME = "get_weather"
# Required only when NEKO_BEHIND_PROXY or NEKO_ACTIVITY_TRACKER_REMOTE is enabled
INSTANCE_KEY = os.environ.get("NEKO_INSTANCE_ACCESS_KEY", "")

async def register_with_retry():
    """Call on plugin startup: register the tool once main_server is up,
    retrying until accepted or rejected for missing/invalid credentials."""
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
    headers = {"Authorization": f"Bearer {INSTANCE_KEY}"} if INSTANCE_KEY else {}
    async with httpx.AsyncClient(trust_env=False) as client:
        while True:
            try:
                r = await client.post(f"{MAIN_SERVER}/api/tools/register",
                                       json=payload, headers=headers, timeout=5)
                if r.status_code in (401, 403):
                    logging.error("Tool registration rejected with HTTP %s: %s", r.status_code, r.text)
                    return
                if r.is_success and r.json().get("ok"):
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass  # main_server isn't up yet, try again later
            await asyncio.sleep(2)

async def unregister_on_shutdown() -> bool:
    """Call before exit: unregister so the LLM doesn't hit a dead callback_url."""
    headers = {"Authorization": f"Bearer {INSTANCE_KEY}"} if INSTANCE_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            r = await client.post(f"{MAIN_SERVER}/api/tools/unregister",
                                  json={"name": TOOL_NAME, "role": None},
                                  headers=headers)
            if r.status_code in (401, 403):
                logging.error("Tool unregistration rejected with HTTP %s: %s", r.status_code, r.text)
                return False
            if not r.is_success:
                logging.error("Tool unregistration failed with HTTP %s: %s", r.status_code, r.text)
                return False
            data = r.json()
            failed_roles = data.get("failed_roles") or []
            if failed_roles:
                logging.error("Tool unregistration failed for roles: %s", failed_roles)
                return False
            return bool(data.get("ok", True))
    except Exception:
        return False  # main_server is down too — nothing we can do
```

Do not wire this into a `NekoPluginBase` plugin. Lifecycle methods run
only when decorated with `@lifecycle(id=...)` (a plain `on_start` /
`on_shutdown` method is never called); `startup` runs in its own
`asyncio.run`, so a task created there with `asyncio.create_task` is
cancelled as soon as `startup` returns; and a plugin process must not
call `main_server` or host its own HTTP server (see the warning under
Registration Endpoints). In a plugin, `@llm_tool` covers registration,
callback routing, and cleanup.

## What Happens When main_server Restarts

⚠️ **Important**: `tool_registry` is an in-memory attribute on
`LLMSessionManager`, so **everything is lost when main_server
restarts**.

For an `@llm_tool` plugin, recovery has to be triggered by the user (or
by an entry the user calls): a plugin has no supported way to detect that
main_server restarted, and it must not poll main_server itself.

- Preferred: reload the plugin. The new process registers every
  `@llm_tool` again.
- Without a reload: call `self.unregister_llm_tool(name)`, then
  `self.register_llm_tool(...)` (registering a name that is still
  registered locally raises `EntryConflictError` before the host is
  notified). Pass every argument of the original registration again,
  especially `role` and `reply_tail`: they default to `role=None`
  (visible to every character) and `reply_tail=False`.

Either way, the host records a registration only if at least one
matching character session manager exists when it arrives (any
character for `role=None`, otherwise the `role` character). It then
reaches those characters and is restored for matching characters created
later. If none matches yet (no character loaded, or the `role` character
is not available), nothing is recorded and the tool does not appear later
by itself: recover once the character is available.

A raw HTTP integration running outside the plugin process must handle
this itself:

- **Integration outlives main_server** (more common): the external
  process needs to detect the heartbeat / connection drop and
  **re-register** when main_server comes back up. The simplest approach
  is a background task in that process that periodically
  `GET /api/tools?role=...` to verify the tool is still there, and
  re-registers if not.
- **Integration lifetime tied to main_server**: if the external process
  runs `register_with_retry` when it starts and is restarted together
  with main_server, registration happens naturally.

## Switching Nekos

Each neko has its own `LLMSessionManager` instance, but they share
plugin-registered tools (depending on the `role` field):

- `role: null` registers to all nekos → switching needs no
  re-registration.
- `role: "小八"` registers only to that neko → switching to a
  different one means the tool isn't available; you'd need to register
  for that neko separately.

Switching nekos **does not restart** main_server, so the registry is
preserved.

## Same-Process Registration (Advanced)

If code runs in the same Python process (for example, built-in functionality),
it can bypass HTTP and call
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
    metadata={"source": "builtin_weather"}, # source goes into metadata
))
```

To `await` until the wire-level sync completes, use
`await mgr.register_tool_and_sync(...)`.

## Caveats

- **Don't put sensitive information in tool names**: the LLM writes
  tool names into `tool_calls`, which gets persisted into conversation
  history.
- **Don't return or raise secrets**: tool output and error text are fed back
  to the LLM and can be persisted in conversation history. Return a redacted,
  stable error code and keep raw private payloads out of the logger.
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
    role: str | None = None,        # None = global, or specific neko name
    reply_tail: bool = False,       # opt in to original-reply image context in _ctx.host_reply
)
```

`reply_tail=True` passes the original reply's image context to the
handler as `_ctx.host_reply`, so the handler must accept `_ctx` or
`**kwargs`. Realtime voice and older frontends do not provide it. See
[original-reply image tail](/plugin-reply-tail).

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
    reply_tail=False,
)
```

`unregister_llm_tool(name)` reverses it; `list_llm_tools()` returns the
current set as plain dicts. Duplicate names raise `EntryConflictError`.

Both calls are fire-and-forget: `True` means the tool was recorded in
the plugin and the host was notified, not that `main_server` accepted
it. When the plugin needs the real outcome, use the async variants:

```python
result = await self.register_llm_tool_async(
    name="custom_tool",
    description="...",
    parameters={"type": "object", "properties": {...}},
    handler=my_async_callable,
    request_timeout=40.0,  # seconds to wait, at most 60 (default 40)
)
if not result["ok"]:
    self.logger.warning("tool not registered: {} {}", result["error"], result["failed_roles"])

result = await self.unregister_llm_tool_async("custom_tool")
```

The host makes the same `main_server` call as for the notification and
returns what `main_server` answered:

| Field | `register_llm_tool_async` | `unregister_llm_tool_async` |
|---|---|---|
| `ok` | At least one role accepted the tool. | No role still holds this plugin's tool. |
| `removed` | — | `main_server` actually removed something. |
| `affected_roles` | Roles that now have the tool. | Roles it was removed from. |
| `failed_roles` | `[{"role", "error"}]` roles that did not accept it (also on partial success). | `[{"role", "error"}]` roles where removal failed. |
| `refused_roles` | — | `[{"role", "owned_by"}]` roles where the name belongs to another source, or (with `"reason": "different registration"`) to another registration of this plugin; left untouched. |
| `error` | `None` or a code (below). | `None` or a code (below). |
| `message` | Human-readable detail, or `None`. | Human-readable detail, or `None`. |
| `cleanup` | `None`, or the unregister result of the cleanup after a failed register (below). | — |

`error` codes: `rejected` (register: no role accepted, e.g. the name is
owned by another source), `partial_failure` (unregister: some roles
failed), `main_server_unavailable` (the request could not reach
`main_server`; nothing was applied), `main_server_error`
(HTTP status >= 400, e.g. unknown role), `timeout` (no usable answer: none
from the host within `request_timeout`, none from `main_server` after the
request was sent, or an answer that is not a JSON object; the outcome is
unknown), `host_unavailable` (the request could
not reach the host), `busy` (earlier calls for the same name used up the
budget before this one could be sent; nothing was sent and `cleanup` is
`None`), `not_registered` (unregister: the plugin had no local
record of the name and `main_server` removed nothing of this plugin's under
it; with a local record the same answer is `ok=True, removed=False` and the
local handler is dropped), `invalid_request`.

Local state follows `ok`, so the plugin and the host agree:

- `register_llm_tool_async` installs the handler before asking the host
  (the model may call the tool as soon as `main_server` has it) and
  rolls it back when `ok` is false or the call is cancelled. The same
  name can then be registered again.
- A failed register is not always a no-op on `main_server`: it stores the
  tool before syncing it to each role and keeps it if every role's sync
  fails, and a `timeout` says nothing about what was applied. So after a
  register fails with `rejected`, `timeout` or `host_unavailable`, the SDK
  sends one unregister for that name, in order with the name's other
  async calls, and reports it in `cleanup` (it may add up to 15 s to the
  call). Other errors mean nothing was stored, so `cleanup` stays `None`.
  If `cleanup["ok"]` is false, call `unregister_llm_tool_async(name)`
  later. After a `timeout` the cleanup is best effort: `main_server` may
  still be processing the register and store the tool after the cleanup
  ran, so `removed=False` does not prove nothing is left; call
  `unregister_llm_tool_async(name)` again a little later when that
  matters. A cancelled register rolls back locally but sends no cleanup.
- Each `register_llm_tool_async` call carries its own registration id,
  and its cleanup and a later `unregister_llm_tool_async` remove only
  that registration. So a cleanup or unregister that timed out but is
  still running on `main_server` cannot remove a newer registration of
  the same name; that role is reported in `refused_roles` with
  `"reason": "different registration"`. Such a registration is stale
  (e.g. a register that timed out and landed late), so both the cleanup
  and `unregister_llm_tool_async` then remove it too, by the id
  `main_server` reported (never with an unfiltered request), and report
  `ok` only once every reported registration is gone. Tools registered through the
  sync call or `@llm_tool`, and names the plugin no longer holds, are
  unregistered without an id (any registration of this plugin), as
  before.
- `unregister_llm_tool_async` removes the handler only when `ok` is
  true. Otherwise the tool stays registered in the plugin, as it does on
  the host, so the roles that still have it keep working and calling it
  again retries.
- The host always unregisters with `expected_source=plugin:{plugin_id}`
  taken from the plugin's connection, so a plugin can never remove
  another source's tool of the same name.
- `unregister_llm_tool_async(name)` also works for a name the plugin no
  longer holds locally: it is still sent to the host, for every role, so
  a leftover on `main_server` is removed. Stopping the plugin also tries
  to clear all of the plugin's tools, but only best effort: if
  `main_server` is unreachable at that moment, tools can stay there, so
  check (or clear them) once it is back.
- Async calls for the same name run one at a time, in call order, each
  waiting for the previous one's answer, also when they come from
  different event loops (e.g. an entry and an interval timer). For example,
  in `gather(register_llm_tool_async("t", ...), unregister_llm_tool_async("t"))`
  the unregister is sent only after the register got its answer. The
  ordering is all this promises: check each call's own result (an
  unregister with `ok=False` keeps the local handler). Different names do not wait for each
  other. `request_timeout` covers the whole call, including the time spent
  waiting behind earlier calls for the same name; if too little of it is
  left to send the request (under 5 s, or under half of it for small
  values), the result is `busy` and nothing was sent to the host. Called
  from an entry that has a deadline, the effective budget of each request,
  including the cleanup, is the smaller of its own budget and the entry's
  remaining time; when the entry's time is used up, no cleanup is sent
  (`cleanup` then reports `timeout`).

Argument errors raise like the sync calls (`ValueError`, `TypeError`,
`EntryConflictError`). The async calls also raise `ValueError` for a
`request_timeout` outside (0, 60] (the host's limit), a name made only of
dots, and, for register, a `timeout` outside (0, 300] (`main_server`'s
limit); the sync calls keep their existing behaviour. Host and
`main_server` failures never raise. Pick one style per tool name: the
sync calls are not part of the per-name ordering and travel on a
different IPC channel, so mixing them for the same name has no ordering
guarantee. `@llm_tool` methods are registered through that notification
channel at startup; if the plugin may unregister one with
`unregister_llm_tool_async` right after startup, register it with
`register_llm_tool_async` instead of the decorator.

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
  logs a warning — once `main_server` is healthy, recover as described in
  "What Happens When main_server Restarts".
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
| `plugin/sdk/plugin/llm_tool.py` | `@llm_tool` decorator, `LlmToolMeta`, name validation, method collector, async result types (`LlmToolRegisterResult` / `LlmToolUnregisterResult`). |
| `plugin/sdk/plugin/base.py` | `NekoPluginBase.register_llm_tool` / `unregister_llm_tool` / `list_llm_tools`, their `_async` variants, and auto-registration in `__init__`. |
| `plugin/core/communication.py` | Host-side IPC handlers `_handle_llm_tool_register` / `_handle_llm_tool_unregister` (routed by message type in `_MESSAGE_ROUTING`). |
| `plugin/server/messaging/handlers/llm_tools.py` | Host-side request-response handlers `LLM_TOOL_REGISTER_REQUEST` / `LLM_TOOL_UNREGISTER_REQUEST` behind the `_async` variants. |
| `plugin/server/messaging/llm_tool_registry.py` | Process-global tracking + httpx wrappers around `main_server`'s `/api/tools/{register,unregister,clear}`. |
| `plugin/server/routes/llm_tools.py` | The `/api/llm-tools/callback/{plugin_id}/{tool_name}` route that receives main_server's dispatches and forwards them through `host.trigger`. |
| `plugin/server/application/plugins/lifecycle_service.py` | Calls `clear_plugin_tools` on plugin stop. |
