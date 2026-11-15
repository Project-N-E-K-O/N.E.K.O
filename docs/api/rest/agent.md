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
| `GET` | `/api/agent/computer_use/availability` | Keyboard-and-mouse readiness and reasons from the Agent Server: the agent model can be reached and input can be sent on this machine |
| `GET` | `/api/agent/browser_use/availability` | Browser readiness and reasons: a usable browser is installed and the agent model can be reached. Without a browser the reason is `AGENT_BROWSER_NOT_FOUND` |
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

## Keyboard-and-mouse and browser tools

Keyboard-and-mouse control and browser control are two groups of built-in tools that a task agent calls one step at a time; see [Agent System](/architecture/agent-system). Browser control drives a Chromium-based browser that is installed on the machine (Chrome, Edge, Brave, or Chromium). No browser is shipped with the application and none is downloaded at start.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agent/builtin/activity` | Which task is using the keyboard and mouse, and which the browser, right now |
| `POST` | `/api/agent/builtin/stop` | Stop the task that is using the keyboard and mouse or the browser |
| `GET` | `/api/agent/builtin/browser/settings` | Which browser the browser tools use and how, with the browsers found on this machine |
| `POST` | `/api/agent/builtin/browser/settings` | Change those settings |

All four return `501` when the backend is deployed remotely.

### `GET /api/agent/builtin/activity`

Returns `{"activity":[...]}` with one item for each group in use, and an empty list when neither is:

```json
{"group": "browser", "state": "started", "commission_id": "...", "window": null}
```

| Field | Meaning |
|---|---|
| `group` | `keyboard_mouse` (the whole desktop) or `browser` |
| `state` | `started` in this response; `started` or `ended` in the WebSocket message below |
| `commission_id` | The task that occupies the group; the same value as the task's `id` |
| `window` | For the browser, `{"browser","pid","window_id","bounds"}` once the window being driven is known, otherwise `null`. Always `null` for the keyboard and mouse |

A proxy failure returns `502` with an empty `activity` list and an `error`.

The main server sends the same object to every connected page as a WebSocket message, `{"type":"agent_builtin_activity","activity":{...}}`, when a task begins or stops using a group. `started` can arrive more than once for one use: the browser is started by the task's first call, so the message is repeated when the window is first known and when it has moved. `ended` is sent when the task agent's run is over, when the task ends or fails, when it starts waiting for the user, and when it is stopped. The page keeps the latest object in `window._agentBuiltinActivity[group]` and dispatches a `neko-agent-builtin-activity` DOM event whose `detail` is the object; it draws nothing itself.

### `POST /api/agent/builtin/stop`

Body: optional `{"group":"keyboard_mouse"}` or `{"group":"browser"}`; without `group`, both are stopped. The response is `{"success":true,"stopped":["<task id>", ...]}`, with an empty list when nothing was in use. It does what `POST /api/agent/tasks/{task_id}/cancel` does for the task that occupies the group. An unknown group returns `400`; `504` means the request reached the Agent Server but its response timed out.

### `GET /api/agent/builtin/browser/settings`

```json
{
  "settings": {"mode": "own_profile", "executable": null, "headless": false},
  "modes": ["own_profile", "attach"],
  "browsers": [{"kind": "chrome", "name": "Google Chrome", "executable": "..."}]
}
```

`browsers` lists what was found on this machine, the preferred one first; `kind` is `chrome`, `edge`, `brave`, `chromium`, or `custom`.

| Setting | Meaning |
|---|---|
| `mode` | `own_profile` (default): the user's browser is started with a profile directory of its own under the data root (`nekoclaw/browser_profile`), so sites are logged into there once. `attach`: the browser the user is already using is driven; this needs Chrome 144 or later, the user switches remote debugging on at `chrome://inspect/#remote-debugging`, and the browser asks for approval at each connection |
| `executable` | Path of a browser to use instead of the one found; `null` searches |
| `headless` | Start the browser without a window; applies to `own_profile` only |

### `POST /api/agent/builtin/browser/settings`

Body: any of `mode`, `executable`, and `headless`; a field that is left out keeps its value. The response is the object above with `"success":true` added. An unknown `mode`, or an `executable` that is not an existing file, returns `400`. The change applies to the next browser that is started: one that the tools started is closed.

## UI-helper route

| Method | Path | Boundary |
|---|---|---|
| `GET` | `/api/agent/user_plugin/dashboard` | Redirect to the local plugin dashboard; accepts validated `v` and loopback `yui_opener_origin` query values |

The dashboard route is a browser helper, not a task API. There is no browser-facing route for submitting a task: the main dialogue model hands tasks over with its `dispatch_task` tool, which the main server forwards to the Agent Server's internal `/nekoclaw/dispatch` route. See [Agent Server API](/api/agent-server).
