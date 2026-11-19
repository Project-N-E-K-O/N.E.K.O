# Runtime Model

How the host runs a plugin. Read this before writing `startup`/`shutdown`,
timers, background work, or anything that keeps a connection or client open.

Source of truth: [`plugin/core/host.py`](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/core/host.py)
(child process main) and [`plugin/core/entry_executor.py`](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/core/entry_executor.py)
(entry calls). When this page and the code disagree, the code wins.

## One Process, Four Phases, Separate Event Loops

Every running plugin is its own child process. Inside it the host runs, in order:

| Phase | How it runs | Loop |
|---|---|---|
| import + `__init__` | plain Python | none |
| `@lifecycle(id="startup")` | `asyncio.run(...)` | loop 1, closed when startup returns |
| `@timer_interval` handlers | one daemon thread per timer, `asyncio.run(...)` **per tick** | a new loop every tick |
| entries, UI context, messages | one `asyncio.run(...)` around the command loop | loop 2, lives until stop |
| `@lifecycle(id="shutdown")` | `asyncio.run(...)` after the command loop ends | loop 3 |

Consequences:

- `asyncio.create_task(...)` in `startup` does **not** keep running: `asyncio.run`
  cancels every task still pending when `startup` returns.
- Do not create loop-bound objects in `startup` (or a timer tick) and use them
  later: async HTTP clients/sessions, `asyncio.Queue`/`Event`/`Lock`, streams.
  They belong to a loop that is already closed. Prefer one per call
  (`async with httpx.AsyncClient() as client:` inside the handler); a client
  cached across calls cannot be closed cleanly later, because no later phase
  runs on its loop.
- Timers run in other threads, concurrently with entries. Share state between
  a timer and entries with `threading.Lock` or plain atomic values, never with
  `asyncio` primitives or a shared async client.

## startup

- The host waits for the whole start, up to `[plugin_runtime].timeout`
  seconds (default 10): process spawn, importing the entry module and its
  `vendor/` dependencies, `__init__`, state restore, and `startup`. Past that,
  the process is killed and the start fails with a startup timeout. Keep
  module top level and `__init__` light (no heavy imports or I/O there), and
  keep `startup` short: read config, validate, return.
- `startup` must be `async def`. A sync one still runs, but the host then
  reports it as a startup error.
- An exception in `startup` is logged. With the default
  `[plugin_runtime].startup_failure = "warn"` (or `"ignore"`) the plugin keeps
  running without whatever startup failed to set up; with `"fail"` the
  process stops and no timer starts.
- Timers are started only after `startup` has returned.

## Entries

- All entry calls, Hosted UI context requests, and cross-plugin replies run as
  tasks on the **single** command-loop thread. Handlers must be `async def`;
  the host rejects a sync handler at call time ("Entry must be 'async def'").
- A synchronous blocking call inside an `async def` (`requests.get`,
  `time.sleep`, `subprocess.run`, heavy CPU work, sync file/DB I/O on large
  data) freezes the whole loop: every other entry, UI panel, and the stop
  request wait for it. A long stall is often, but not always, logged
  afterward as `Plugin event-loop stall`; a missing log line does not prove
  the loop never blocked. The entry timeout cannot interrupt it either, because
  timeouts fire only when the loop gets control back. Use an async library, or
  `await asyncio.to_thread(blocking_fn, ...)`.
- Each call has a timeout: the smaller of `@plugin_entry(timeout=...)` and
  the caller's, otherwise the host default (10 s). A caller value <= 0 falls
  back to the decorator's value (no timeout if it sets none).
  `@plugin_entry(timeout=0)` (or any value <= 0) disables the entry
  timeout; outer limits still apply, such as the caller's
  RPC deadline or, for calls made as runs, the run timeout (300 s by
  default). Long jobs should return quickly and report progress
  instead of holding the call open.
- `Err(...)` and uncaught exceptions both reach the caller as a failed call.
- On timeout the handler's task is cancelled. Let `CancelledError` propagate:
  a handler that swallows it and returns makes its late result count as a
  success. Work inside `asyncio.to_thread` cannot be cancelled and keeps
  running after the timeout; give it its own deadline.
- Calls to other plugins (`self.plugins.call_entry`) are capped at 60 s.
  There is no cycle detection between plugins, only a call-depth limit (32).
  A call that can only finish once the caller moves on (calling while holding
  a lock the callee needs, or two plugins waiting on each other) is not
  noticed and waits until it times out.

## Other Threads

- Only entry calls carry `_ctx` and the current character. Timers and threads
  you start have neither: save `_ctx["lanlan_name"]` in the entry and pass it
  as `target_lanlan=` to `push_message` (which never fills it in itself, not
  even inside an entry).
- SDK async calls work from any thread that runs its own loop, for example
  `asyncio.run(self.config.get("x"))` in your own thread; replies are routed
  back to the calling loop. `push_message` is a plain call and works from any
  thread.

## Timers

```python
from plugin.sdk.plugin import timer_interval

@timer_interval(id="poll", seconds=60)
async def poll(self, **_):
    ...
```

- One daemon thread per `auto_start=True` timer (the default). Each tick runs
  the handler in a fresh `asyncio.run`, then waits `seconds`. The first tick
  runs right after startup; ticks never overlap, so the real period is
  handler time + `seconds`.
- The handler must be `async def` (a sync one runs, then every tick logs an
  error). A failing tick is logged and the next tick still runs.
- No entry timeout applies to a tick. Bound your own network calls.
- On stop, the timer is told not to start another tick; a tick already
  running is not awaited and ends with the process.
- A timer is the default place for periodic polling. For a long-lived
  connection (websocket, stream), start your own thread in `startup` that runs
  its own `asyncio.run(...)`, and stop it from `shutdown` (for example with a
  `threading.Event`).

## shutdown

- Runs after the command loop ends, in its own `asyncio.run`. The entry loop
  is already closed, so do not `await client.aclose()` on async clients or
  sessions created in entries there (it can fail with `Event loop is
  closed`); close loop-bound resources in the call that opened them. Use
  `shutdown` for plain resources: stop your own threads, close files, and
  flush state.
- The host waits about 1.5 s for the process to exit, then terminates and
  finally kills it; a crash or kill skips `shutdown` entirely. Never rely on it for data you
  cannot lose: write durable state when it changes.

## Reload, Crash, Restart

- Reload (the Reload button, or source hot reload when it is enabled) stops
  the process and starts a **new** one. Everything in memory is gone; module
  globals, caches, and open clients are rebuilt by `__init__`/`startup`.
  Persist what must survive under `self.data_path(...)`.
- If the process crashes, the plugin shows as crashed/stopped. The host does
  **not** restart it automatically; the user starts it again.
- `[plugin_runtime].auto_start = false` means "start manually", not
  "disabled": the entry module can still be imported for metadata scanning.
  Keep module top level free of network calls and side effects.
