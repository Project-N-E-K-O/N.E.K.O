# Agent API

**Prefix:** `/api/agent`

The main server's browser-facing proxy for the loopback Agent Server. It owns character/session synchronization and remote-deployment safety checks, then forwards runtime operations to `TOOL_SERVER_PORT`. Proxy failures normally return `502`; mutation routes return `501` when the backend is deployed remotely because controlling the server machine would be unsafe.

## State and commands

### `GET /api/agent/flags`

Returns the Agent Server flag snapshot, including the master switch and supported sub-features: Computer Use, Browser Use, user plugins, OpenClaw, and OpenFang. A proxy failure returns `502` with `success: false`.

### `POST /api/agent/flags`

Legacy partial flag update. The body is `{"lanlan_name":"...","flags":{...}}`. The route updates the character session and forwards recognized sub-flags. A missing character returns `404`; forwarding failure resets the local flags to a safe disabled state and returns `502`.

### `GET /api/agent/state`

Returns the authoritative Agent Server state snapshot: revision, flags, capabilities, notification state, and task summary.

### `POST /api/agent/command`

Preferred mutation entry point. The current commands are:

| Command | Additional fields | Purpose |
|---|---|---|
| `set_agent_enabled` | `enabled`, optional `profile` | Toggle the master runtime gate |
| `set_flag` | `key`, `value` | Toggle one of `computer_use_enabled`, `browser_use_enabled`, `user_plugin_enabled`, `openclaw_enabled`, `openfang_enabled` |
| `refresh_state` | none | Return and broadcast a fresh state snapshot |

`request_id` and `lanlan_name` are optional. Unknown commands or flag keys are rejected by the Agent Server; upstream/proxy failure is reported as `502`.

## Health and capabilities

| Method | Path | Response boundary |
|---|---|---|
| `GET` | `/api/agent/health` | `{"status":"ok","tool":{...}}`; Agent Server unavailable returns `502` and `status: "down"` |
| `GET` | `/api/agent/computer_use/availability` | Readiness and reasons from the Agent Server |
| `GET` | `/api/agent/browser_use/availability` | Browser dependency/model readiness |
| `GET` | `/api/agent/user_plugin/availability` | Plugin service reachability; unavailable returns `502` |
| `GET` | `/api/agent/openclaw/availability` | OpenClaw/QwenPaw readiness; unavailable returns `502` |
| `GET` | `/api/agent/mcp/availability` | Compatibility response: always unavailable because MCP left the `brain/` layer |

## Tasks and administration

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agent/tasks` | List Agent Server task snapshots |
| `GET` | `/api/agent/tasks/{task_id}` | Read one task |
| `POST` | `/api/agent/tasks/{task_id}/cancel` | Cancel one task; preserves upstream `404`, uses `504` when the request reached the server but its response timed out |
| `POST` | `/api/agent/admin/control` | Forward administrative control such as `{"action":"end_all"}`; local-only, destructive to active work |

## Internal and UI-helper routes

| Method | Path | Boundary |
|---|---|---|
| `POST` | `/api/agent/internal/analyze_request` | Internal fallback bridge that publishes an `analyze_request` onto the main event bus |
| `GET` | `/api/agent/user_plugin/dashboard` | Redirect to the local plugin dashboard; accepts validated `v` and loopback `yui_opener_origin` query values |
| `GET` | `/api/agent/openclaw/guide` | Render the local OpenClaw guide page |
| `GET` | `/api/agent/openclaw/guide/content` | Return localized guide Markdown; optional `lang` query |
| `GET` | `/api/agent/openclaw/guide/assets/{asset_path:path}` | Serve a file below the fixed guide asset directory; traversal/missing files return `404` |

The analyze bridge is not a public submission API. Dashboard and guide routes are browser helpers, not Agent Server task APIs.
