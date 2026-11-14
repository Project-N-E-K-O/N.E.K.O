# Agent API

**Prefix:** `/api/agent`

The main server's browser-facing proxy for the loopback Agent Server. It owns character/session synchronization and remote-deployment safety checks, then forwards runtime operations to `TOOL_SERVER_PORT`. Proxy failures normally return `502`; mutation routes return `501` when the backend is deployed remotely because controlling the server machine would be unsafe.

## State and commands

### `GET /api/agent/flags`

Returns the Agent Server flag snapshot, including the master switch and the three capability switches: keyboard-and-mouse (`computer_use_enabled`), browser (`browser_use_enabled`), and user plugins (`user_plugin_enabled`). A proxy failure returns `502` with `success: false`.

### `POST /api/agent/flags`

Legacy partial flag update. The body is `{"lanlan_name":"...","flags":{...}}`. The route updates the character session and forwards recognized sub-flags. A missing character returns `404`; forwarding failure resets the local flags to a safe disabled state and returns `502`.

### `GET /api/agent/state`

Returns the authoritative Agent Server state snapshot: revision, flags, capabilities, notification state, and the tasks under way (`active_tasks`).

### `POST /api/agent/command`

Preferred mutation entry point. The current commands are:

| Command | Additional fields | Purpose |
|---|---|---|
| `set_agent_enabled` | `enabled`, optional `profile` | Toggle the master switch. Turning it off stops every task under way |
| `set_flag` | `key`, `value` | Toggle one of `computer_use_enabled`, `browser_use_enabled`, `user_plugin_enabled` |
| `refresh_state` | none | Return and broadcast a fresh state snapshot |

`request_id` and `lanlan_name` are optional. Unknown commands or flag keys are rejected by the Agent Server; upstream/proxy failure is reported as `502`.

## Health and capabilities

| Method | Path | Response boundary |
|---|---|---|
| `GET` | `/api/agent/health` | `{"status":"ok","tool":{...}}`; Agent Server unavailable returns `502` and `status: "down"` |
| `GET` | `/api/agent/computer_use/availability` | Readiness and reasons from the Agent Server |
| `GET` | `/api/agent/browser_use/availability` | Browser dependency/model readiness |
| `GET` | `/api/agent/user_plugin/availability` | Plugin service reachability; unavailable returns `502` |

## Tasks and administration

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agent/tasks` | List tasks: those under way and those that ended in the last five minutes |
| `GET` | `/api/agent/tasks/{task_id}` | Read one task |
| `POST` | `/api/agent/tasks/{task_id}/cancel` | Stop one task; preserves upstream `404`, uses `504` when the request reached the server but its response timed out. A task that already ended returns `success: false` with its actual `status` |
| `POST` | `/api/agent/admin/control` | Forward administrative control such as `{"action":"end_all"}`, which stops every task under way; local-only |

### Task object

A task is the HUD's view of one commission in the NekoClaw ledger. `GET /api/agent/tasks` returns `{"tasks":[...]}`; the same object appears in `active_tasks` of the state snapshot and in `agent_task_update` WebSocket messages.

| Field | Meaning |
|---|---|
| `id` | Commission ID |
| `status` | `queued`, `running`, `waiting_user`, `completed`, `blocked`, `failed`, or `cancelled` |
| `type` | `user_plugin` (a plugin entry called directly), `task_agent` (a task agent is working on it; the interface calls it a familiar), or `task` (filed, not yet decided who takes it) |
| `start_time`, `end_time` | ISO timestamps; `end_time` is present once the task has ended |
| `params` | `description` (title) and `instruction` (the sentence that was dispatched); `user_plugin` tasks add `plugin_id`, `entry_id`, and `plugin_name` |
| `lanlan_name` | Character the task belongs to |
| `error` | Reason, on `failed`, `blocked`, and `cancelled` tasks |
| `progress`, `stage`, `message`, `step`, `step_total` | Optional progress of a directly called plugin entry |

`waiting_user` means the task waits for the user's answer or approval and still counts as under way. `blocked` means the work has ended without a result because something else is needed first. Fields may be added.

## UI-helper route

| Method | Path | Boundary |
|---|---|---|
| `GET` | `/api/agent/user_plugin/dashboard` | Redirect to the local plugin dashboard; accepts validated `v` and loopback `yui_opener_origin` query values |

The dashboard route is a browser helper, not a task API. There is no browser-facing route for submitting a task: the main dialogue model hands tasks over with its `dispatch_task` tool, which the main server forwards to the Agent Server's internal `/nekoclaw/dispatch` route. See [Agent Server API](/api/agent-server).
