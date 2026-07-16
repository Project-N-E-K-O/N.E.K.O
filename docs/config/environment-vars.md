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
| `NEKO_BEHIND_PROXY` | Enables proxy-header handling in supported entrypoints |
| `NEKO_LOG_LEVEL` | Main-server log level |
| `NEKO_MERGED` | Launcher merged-mode override |

The shared boolean parser accepts `1/true/yes/on` and `0/false/no/off`.

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
