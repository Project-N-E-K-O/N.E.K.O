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
