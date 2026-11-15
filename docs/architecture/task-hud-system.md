# Task HUD System

The Task HUD is the browser/Electron view of Agent task state. It is a client-side projection of the commissions in the NekoClaw host's ledger, not an independent task scheduler. See [Agent System](/architecture/agent-system) for how a commission is filed and carried out.

## Current data path

```text
NekoClaw host (a commission is filed or changes status)
    │ task_update
    ▼
AgentServerEventBridge ── ZMQ :48962 ──> MainServerAgentBridge
                                              │
                                              ▼
                                      character WebSocket
                                              │
                                              ▼
static/app/app-websocket.js
    │ merge by task.id into window._agentTaskMap
    ▼
static/common-ui-hud.js → window.AgentHUD
```

There is no live `agent_task_snapshot` WebSocket message. On socket open, the frontend fetches `/api/agent/state`; `snapshot.active_tasks` restores the cards of tasks under way (`queued`, `running`, `waiting_user`) after refresh or reconnect. Subsequent changes arrive as `agent_task_update` messages.

The state snapshot is filtered by `lanlan_name` before rebuilding `window._agentTaskMap`, so one character's page does not display another character's tasks.

## Task lifecycle

| State | Meaning | HUD behavior |
|---|---|---|
| `queued` | Filed; not yet being worked on | Active card with queued state |
| `running` | A plugin entry or a task agent is working on it | Active card, timer and optional progress |
| `waiting_user` | Waits for the user's answer or approval; still under way | Active card with waiting state |
| `completed` | Successful terminal result | Dimmed terminal card, then removed |
| `blocked` | Ended without a result; something else is needed first | Terminal card, then removed |
| `failed` | Terminal failure | Error card, then removed |
| `cancelled` | Stopped by user/system | Cancelled card, then removed |

These are the HUD's names for the ledger statuses: the ledger's `working` is sent as `running` and `done` as `completed`.

`app-websocket.js` merges each update into the existing task by `id` and preserves `params` when an update omits it.

Common fields are:

| Field | Purpose |
|---|---|
| `id` | Commission ID |
| `type` | `user_plugin` (a plugin entry called directly), `task_agent` (a task agent is working on it; the HUD labels it as a familiar), or `task` (filed, not yet decided who takes it) |
| `status` | Lifecycle state |
| `start_time`, `end_time` | ISO timestamps; `end_time` once the task has ended |
| `lanlan_name` | Character owner |
| `params` | Display metadata: `description` (title), `instruction` (the dispatched sentence), and for `user_plugin` tasks `plugin_id`, `plugin_name`, and `entry_id` |
| `step`, `step_total`, `progress`, `stage`, `message` | Optional progress of a directly called plugin entry |
| `error` | Reason on `failed`, `blocked`, and `cancelled` tasks |

The HUD shortens `params.description` or `params.instruction` for the card while keeping the raw value as tooltip text.

## Rendering behavior

`window.AgentHUD.updateAgentTaskHUD()` uses request-animation-frame throttling and differential card updates. Running time is refreshed once per second while an active task exists.

Terminal cards remain visible for at least ten seconds. Both the WebSocket map and HUD renderer maintain terminal timestamps/timers so rapid tasks do not disappear before the user can see the result. The Agent process keeps ended tasks in its own HUD view longer—five minutes—so a page that reloads can still read them through the API.

The floating HUD is 320 px wide with a 60 vh maximum height. A standalone Agent HUD page uses the full viewport. Floating mode supports:

- mouse and touch dragging with viewport/display clamping;
- multi-display bounds through the Electron screen bridge when available;
- a persisted position in `localStorage` under `agent-task-hud-position`;
- a persisted collapsed state under `agent-task-hud-collapsed-v2`;
- automatic suppression during goodbye/resource-suspended mode.

Strings are read through the shared frontend i18n keys. Theme colors use the N.E.K.O. popup CSS variables rather than a separate fixed palette.

## Cancellation

An individual card calls:

```text
POST /api/agent/tasks/{task_id}/cancel
```

The Main Server proxies this request to the Agent Server. The host writes `cancelled` to the ledger first, which sends the terminal `task_update`; then it cancels the plugin run on the plugin service or closes the task agent's session. A task that has already ended is answered with `success: false` and its actual `status`.

The title-bar cancel-all button confirms with the user and calls:

```text
POST /api/agent/admin/control
{ "action": "end_all" }
```

`end_all` stops every commission under way. A `504` means the proxy forwarded the request but timed out waiting for teardown, so the HUD may apply its terminal fallback. A connection/proxy failure cannot prove that cancellation reached the Agent Server and must not be treated as success.

## Plugin display names

The Agent Server cannot read another process's plugin runtime state directly. `app/agent_server/plugin_host.py` queries the embedded user-plugin service at `127.0.0.1:48916/plugins` and caches the ID-to-name map for 30 seconds. `app/agent_server/nekoclaw_mount.py` adds the friendly name to `params.plugin_name` when it sends a `user_plugin` task update; the HUD falls back to `plugin_id` or a localized type label.

The embedded plugin service itself is hosted from the Agent Server process in an isolated Uvicorn thread. This replaces the old assumption that plugin state is owned by the Main Server.

## Deferred plugin tasks

A successful user-plugin call can return `data.deferred: true` when scheduling is not the same as completion—for example, a reminder that should complete only when it fires.

```text
plugin run returns deferred + reminder_id
        │
        ├─ commission stays working (HUD: running)
        ├─ deferred deadline = now + 1 hour
        └─ plugin bind_task receives agent_task_id
                         │
                         ▼ later callback
POST :48915/api/agent/tasks/{task_id}/complete
                         │
                         └─ completed task_update → Main → HUD
```

The completion endpoint is accepted only for a `working` commission that was routed to a plugin entry and has a deferred deadline. It is idempotent after the commission has already ended. The host marks a commission whose callback has not arrived after one hour as failed. `agent_task_id` is the commission ID.

The browser-facing cancellation endpoint is proxied under `/api/agent`. The deferred-completion endpoint is an internal Agent Server callback on port 48915 and is not a general browser API.

## Retention and recovery

- `/api/agent/state` returns only tasks under way (`queued`, `running`, `waiting_user`) in `active_tasks` for reconnect recovery.
- Ended tasks leave the Agent process's HUD view after five minutes. The commission itself stays in the ledger.
- The HUD view lives in memory. After the Agent process restarts it is filled again from the commissions still under way in the ledger when the host is first used.
- Frontend terminal cards are removed after ten seconds, independently of backend retention.

## Implementation map

| Concern | Current file |
|---|---|
| Live WebSocket merge and reconnect restore | `static/app/app-websocket.js` |
| HUD rendering, drag, collapse, timers | `static/common-ui-hud.js` |
| Browser-facing Agent proxy | `main_routers/agent_router.py` |
| Authoritative state snapshot | `app/agent_server/capabilities.py` |
| Task cancellation and deferred completion | `app/agent_server/api_runtime.py` |
| HUD task view and `task_update` emission | `app/agent_server/nekoclaw_mount.py` |
| Plugin-name lookup | `app/agent_server/plugin_host.py` |
| Commission ledger and status changes | `nekoclaw/host/ledger.py` |
