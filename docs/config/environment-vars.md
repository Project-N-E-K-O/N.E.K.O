# Environment Variables

Only variables explicitly read by current code are supported. A `NEKO_` prefix is preferred; selected network helpers also accept the bare name for compatibility.

## Ports

| Preferred variable | Default | Service |
| --- | ---: | --- |
| `NEKO_MAIN_SERVER_PORT` | 48911 | Main Web/API server |
| `NEKO_MEMORY_SERVER_PORT` | 48912 | Memory server |
| `NEKO_MONITOR_SERVER_PORT` | 48913 | Monitor service |
| `NEKO_COMMENTER_SERVER_PORT` | 48914 | Commenter service |
| `NEKO_TOOL_SERVER_PORT` | 48915 | Agent/tool server |
| `NEKO_USER_PLUGIN_SERVER_PORT` | 48916 | User-plugin host |
| `NEKO_AGENT_MQ_PORT` | 48917 | Agent message transport |
| `NEKO_MAIN_AGENT_EVENT_PORT` | 48918 | Main/agent event transport |
| `NEKO_OPENFANG_PORT` | 50051 | OpenFang A2A service |

Electron stores port overrides in `port_config.json` under `%APPDATA%\N.E.K.O` on Windows, macOS Application Support, or `$XDG_CONFIG_HOME/N.E.K.O` on Linux. Explicit environment values win.

## Runtime identity and origins

| Variable | Meaning |
| --- | --- |
| `NEKO_INSTANCE_ID` | Shared instance ID; normally created by the launcher |
| `NEKO_AUTOSTART_CSRF_TOKEN` | Autostart request token; defaults to the instance ID |
| `NEKO_AUTOSTART_ALLOWED_ORIGINS` | Comma-separated extra allowed origins |
| `NEKO_TRUSTED_HOSTS` | Comma-separated extra hostnames accepted by local HTTP/WebSocket services. Loopback names and IP literals are accepted automatically. |
| `NEKO_TRUSTED_ORIGINS` | Comma-separated extra browser origins accepted for WebSocket connections. Same-origin connections and loopback aliases are accepted automatically. |
| `NEKO_BEHIND_PROXY` | Enables proxy-header handling in supported entrypoints |
| `NEKO_LOG_LEVEL` | Main-server log level |
| `NEKO_MERGED` | Launcher merged-mode override |

Trusted hosts are exact names (optionally with a port); `*.example.com` permits
subdomains but not the bare suffix. A global `*` is deliberately ignored. The
Docker entrypoint adds `SSL_DOMAIN` to `NEKO_TRUSTED_HOSTS` by default. Trusted
origins must be complete `http://` or `https://` origins and affect browser
WebSocket handshakes, not CORS.

Most shared boolean helpers accept `1/true/yes/on` and `0/false/no/off`.
`NEKO_MERGED` itself accepts `1/true/yes` and `0/false/no`.

## Process model and single instance

The launcher is a foreground process: it never daemonizes, and it tears its whole
service topology down when the process that owns it disappears. It also proves it
is the only running runtime with an OS file lock, and publishes an authoritative
record (pid, instance id, negotiated ports) next to that lock.

| Variable | Default | Description |
| --- | --- | --- |
| `NEKO_OWNER_PID` | this process's parent | The pid the parent-death guard watches. Set it when the owner is *not* the direct parent — for example the replacement launcher of a storage-migration handoff, whose spawner exits on purpose. Owners that identify the runtime by reading `launcher.json` should set this: it becomes the record's `owner_pid`, which is the field to match against. Do not match `parent_pid` — on a Windows dev run `Popen(sys.executable)` starts a shim that re-launches the interpreter, so `parent_pid` names the shim rather than the owner (measured in CI; macOS and Linux match directly, and a frozen build has no shim). |
| `NEKO_OWNER_RELAUNCH` | unset | `1` declares that the owner will restart the runtime itself. A storage-migration restart then exits cleanly and waits to be relaunched instead of spawning its own replacement. Strongly recommended on Windows: without it the launcher respawns itself, and the outgoing Job has to be un-managed to spare the replacement, which leaves any process that outlived cleanup (plugins, MCP, Chromium) unreaped. |
| `NEKO_PARENT_DEATH_GUARD` | `1` | Set to `0` to disable the parent-death guard entirely. Only for debuggers and profilers that re-parent their target; a runtime with the guard off can outlive its owner. |
| `NEKO_LAUNCHER_RESTART_HANDOFF` | unset | Set by the outgoing launcher on its replacement so the replacement waits out the single-instance lock instead of concluding another instance is running. Not meant to be set by hand. |
| `NEKO_RUNTIME_STATE_DIR` | per-user runtime dir | Overrides where `launcher.lock` and `launcher.json` live. Defaults to `%LOCALAPPDATA%\N.E.K.O.runtime` on Windows, `~/Library/Application Support/N.E.K.O.runtime` on macOS, and `~/.local/state/N.E.K.O/runtime` on Linux. The Windows and macOS directories are siblings of the cloudsave-managed `N.E.K.O` data root so an atomic root replacement cannot block on or unlink the live single-instance lock. The Linux path deliberately ignores `XDG_RUNTIME_DIR`: that variable is present in a desktop session but absent under cron, plain SSH, `su`, system units and most containers, so deriving the lock path from it let one user hold two different locks and run two runtimes at once. The override is used verbatim — no per-user suffix is appended — so it must point somewhere private to one user. On POSIX the directory is still validated: one owned by another uid (or a symlink to it) is refused with EPERM, and one carrying group or world bits is chmod'd to 0700 in place. Windows does neither. A refusal is treated as unknown — the launcher starts with a warning and no uniqueness proof. A directory shared between users breaks the single-instance proof: on Windows two users contend for one lock, and on POSIX the second user cannot open the first user's lock file and starts with no uniqueness proof at all. |

## Runtime topology

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_MERGED` | Source: `0`; frozen package: `1` | `1` runs main, memory, and agent HTTP services in one process while preserving their contracts; `0` keeps three service processes. A partial or mixed existing backend is never reused and forces a three-process launch on isolated fallback ports, even when merged mode would otherwise be selected. |

Keep multi-process mode for development, independent service supervision, or
agent-failure isolation. `NEKO_MERGED=0` is the immediate rollback for packaged
deployments.

## Realtime voice escape hatches

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_REALTIME_ARBITER_FAIL_OPEN` | unset (off) | Changes what the realtime response arbiter does when a response cannot reach a terminal state. By default it tears the realtime WebSocket down, which the user sees as a disconnect and session rebuild. Set to `1`, `true`, `yes`, or `on` to end only the stuck turn and keep the connection. Read once when a voice session's client is constructed, so a change needs a restart. |

Leave this unset unless you are actually hitting the failure. The default
exists because the arbiter escalates precisely when its own bookkeeping about
which response owns the connection has become untrustworthy, and continuing on
a connection in that state can produce overlapping responses.

The symptom worth setting it for is repeated disconnect-and-rebuild during
voice conversation, with backend logs carrying:

```text
response arbiter failing closed: <reason> (current=... owner=... queue_depth=...)
```

That line distinguishes an arbiter-initiated teardown from an upstream
provider disconnect — absent it, the disconnect came from somewhere else and
this variable will not help.

### It still tears down sometimes, on purpose

Keeping the connection is only defensible when the transport is still usable
**and** the arbiter can still tell whose events are whose. When either half
fails it tears down anyway and logs why:

```text
response arbiter cannot keep the connection (<blocker>); failing closed despite the escape hatch
```

Seeing that with the variable set is expected behaviour, not a broken switch.
The blockers are:

| Blocker | Meaning |
| --- | --- |
| the queue consumer is suspended inside a transport write | Nothing the arbiter does to its own state unwinds that wait, and closing the transport is what releases it. |
| a transport write just failed | The connection refused a send moments earlier; on the fatal branch it has already dropped its socket. |
| the abandoned response has no id to attribute later events by | Its `response.create` reached the provider, so it may still be announced — and without an id that announcement is indistinguishable from the next turn's. |
| an announced server-VAD response has no id yet | The same, for a response the provider announced but has not yet identified. |
| an unowned response is live with no id at all | A response nobody requested announced itself without an id. Nothing identifies it, so its terminal cannot be told from the next turn's and would end whichever turn holds the lane by then. |

If every escalation in your logs carries one of these, the variable is working
as designed and the disconnects have a different cause — attach those lines to
the report rather than assuming the switch had no effect.

### What it cannot protect you from

The blockers above cover what the arbiter can *see*. One thing it cannot:
whether the events that follow will carry a response id at all.

A provider that identifies its `response.created` but omits the id from the
events after it puts the arbiter in a position with no good answer. The
abandoned response's late audio, text and — worst — tool calls are then
indistinguishable from the next turn's, so they are delivered as the next
turn's. Keeping the connection is what exposes this: the default teardown
takes the socket with it, and those late events never arrive.

The window is bounded, though — an earlier version of this note claimed it
could not be narrowed at all, and that was wrong. A release only happens when
the abandoned response HAD an id, and ids come only from `response.created`,
so any provider that can reach this state does announce identified responses.
The successor's own announcement therefore always arrives before the
successor's id-less events, on one ordered socket. The leak is confined to the
gap between the release and that announcement.

Tool calls are gated in exactly that gap, because a leaked sentence is wrong
words while a leaked tool call is a side effect executed for a turn nobody is
having. You will see:

```text
quarantined an id-less tool call arriving after a stuck-turn release
```

Audio and text are deliberately NOT gated there: suppressing them risks a mute
host on a provider that never announces the successor, which is a worse
failure than a stray half-sentence. So if you enable this on a provider whose
streaming events lack `response_id`, still treat a stuck turn as possibly
leaking its remaining words into the next one — just not its side effects.
Tracked in issue #2611.

### A third line: kept, but nothing was released

Some escalations are raised over a response the arbiter never owned — a
server-initiated one it can only cancel blindly. There is no turn to give up
there, so the transport survives but the lane stays held by that response:

```text
response arbiter kept the transport but had no lifecycle to release: <reason> (lane held by server response ids: ..., queue_depth=N); the lane reopens when their terminal events arrive, or after 60s if none do
```

This is a delay, not a wedge, and in the common case a short one — the lane
reopens the moment that response's terminal arrives. The full 60s only elapses
when the terminal is lost outright. The ids are not retired early on purpose:
the arbiter has no per-response activity signal, so it cannot tell a lost
terminal from a response that is simply still speaking, and dispatching over a
live one costs a silently dropped turn.

## Storage and local vectors

| Variable | Meaning |
| --- | --- |
| `NEKO_STORAGE_SELECTED_ROOT` | Launcher-supplied writable data root |
| `NEKO_STORAGE_ANCHOR_ROOT` | Launcher-supplied anchor root |
| `NEKO_VECTORS_ENABLED` | Enable local vectors; default true |
| `NEKO_VECTORS_QUANTIZATION` | `auto`, `int8`, or `fp32` |

Vector settings also accept bare compatibility names.
The available-RAM gate is currently the fixed `VECTORS_MIN_RAM_GB = 4.0` runtime constant; there is no environment override for it.

## Docker-only API initialization

The Docker entrypoint consumes these while generating its initial `/app/config/core_config.json`:

- `NEKO_CORE_API_KEY`, `NEKO_CORE_API`, `NEKO_ASSIST_API`
- `NEKO_ASSIST_API_KEY_QWEN`, `_OPENAI`, `_GLM`, `_STEP`, `_SILICON`, `_GROK`, `_DOUBAO`
- `NEKO_MCP_TOKEN`
- `NEKO_FORCE_ENV_UPDATE` to request regeneration

These are not a general source-mode API environment. Configure source/desktop providers in the Web UI.

::: warning
Old `docker/env.template` comments show model variables that `entrypoint.sh` does not consume. Do not rely on a variable unless current runtime code reads it.
:::
