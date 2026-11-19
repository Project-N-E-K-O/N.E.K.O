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
  They belong to a loop that is already closed. Create them lazily in the
  phase that uses them, or create them per call.
- Timers run in other threads, concurrently with entries. Share state between
  a timer and entries with `threading.Lock` or plain atomic values, never with
  `asyncio` primitives or a shared async client.

## startup

- The host waits for `startup` to finish, up to `[plugin_runtime].timeout`
  seconds (default 10). Past that, the process is killed and the start fails
  with a startup timeout. Keep `startup` short: read config, validate, return.
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
  request wait for it. A stall over 5 s is logged afterwards as `Plugin
  event-loop stall`. The entry timeout cannot interrupt it either, because
  timeouts fire only when the loop gets control back. Use an async library, or
  `await asyncio.to_thread(blocking_fn, ...)`.
- Each call has a timeout: `@plugin_entry(timeout=...)` or the caller's,
  otherwise the host default (10 s). Long jobs should return quickly and report progress
  instead of holding the call open.
- `Err(...)` and uncaught exceptions both reach the caller as a failed call.

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

- Runs after the command loop ends, in its own `asyncio.run`. Close what you
  opened and flush state.
- The host waits only briefly for the process to exit before forcing it, and
  a crash or kill skips `shutdown` entirely. Never rely on it for data you
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
