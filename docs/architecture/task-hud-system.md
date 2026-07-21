# Task HUD System

The Task HUD is the browser/Electron view of Agent task state. It is a client-side projection of the Agent Server's registry, not an independent task scheduler.

## Current data path

```text
Agent channel
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

There is no live `agent_task_snapshot` WebSocket message. On socket open, the frontend fetches `/api/agent/state`; `snapshot.active_tasks` restores queued/running cards after refresh or reconnect. Subsequent changes arrive as `agent_task_update` messages.

The state snapshot is filtered by `lanlan_name` before rebuilding `window._agentTaskMap`, so one character's page does not display another character's tasks.

## Task lifecycle

| State | Meaning | HUD behavior |
|---|---|---|
| `queued` | Accepted but waiting for an execution slot | Active card with queued state |
| `running` | Provider or plugin is executing | Active card, timer and optional progress |
| `completed` | Successful terminal result | Dimmed terminal card, then removed |
| `failed` | Terminal failure | Error card, then removed |
| `cancelled` | Cancelled by user/system | Cancelled card, then removed |

Live update payloads are partial. `app-websocket.js` merges a new object with the existing task and preserves `params` when a later update omits it.

Common fields are:

| Field | Purpose |
|---|---|
| `id` | Stable registry/task key |
| `type` | Channel such as `computer_use`, `browser_use`, `user_plugin`, `openclaw`, or `openfang` |
| `status` | Lifecycle state |
| `start_time`, `end_time` | ISO timestamps when available |
| `lanlan_name` | Character owner |
| `params` | Display metadata such as `instruction`, `description`, `plugin_id`, `plugin_name`, and `entry_id` |
| `step`, `step_total`, `progress`, `message` | Optional provider/plugin progress |
| `error` | Terminal failure/cancellation detail |

The HUD shortens `params.description` or `params.instruction` for the card while keeping the raw value as tooltip text.

## Rendering behavior

`window.AgentHUD.updateAgentTaskHUD()` uses request-animation-frame throttling and differential card updates. Running time is refreshed once per second while an active task exists.

Terminal cards remain visible for at least ten seconds. Both the WebSocket map and HUD renderer maintain terminal timestamps/timers so rapid tasks do not disappear before the user can see the result. Backend terminal records are retained longer—normally five minutes—for API inspection and deduplication.

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

The Main Server proxies this request to the Agent Server. The Agent Server marks the registry entry `cancelled` first, cancels its async wrapper, emits a terminal `task_update`, and starts provider-specific teardown without blocking the HTTP response.

The title-bar cancel-all button confirms with the user and calls:

```text
POST /api/agent/admin/control
{ "action": "end_all" }
```

A `504` means the proxy forwarded the request but timed out waiting for teardown, so the HUD may apply its terminal fallback. A connection/proxy failure cannot prove that cancellation reached the Agent Server and must not be treated as success.

## Plugin display names

The Agent Server cannot read another process's plugin runtime state directly. `app/agent_server/plugin_host.py` queries the embedded user-plugin service at `127.0.0.1:48916/plugins` and caches the ID-to-name map for 30 seconds. Task dispatch copies the friendly name into `params.plugin_name`; the HUD falls back to `plugin_id` or a localized type label.

The embedded plugin service itself is hosted from the Agent Server process in an isolated Uvicorn thread. This replaces the old assumption that plugin state is owned by the Main Server.

## Deferred plugin tasks

A successful user-plugin call can return `data.deferred: true` when scheduling is not the same as completion—for example, a reminder that should complete only when it fires.

```text
plugin run returns deferred + reminder_id
        │
        ├─ registry stays running
        ├─ deferred timeout = now + 1 hour
        └─ plugin bind_task receives agent_task_id
                         │
                         ▼ later callback
POST :48915/api/agent/tasks/{task_id}/complete
                         │
                         └─ completed task_update → Main → HUD
```

The completion endpoint is accepted only for a running `user_plugin` task that has `deferred_timeout`. It is idempotent after the task has already reached a terminal state. Registry cleanup checks deferred deadlines and marks an uncompleted callback as failed after one hour.

The browser-facing cancellation endpoint is proxied under `/api/agent`. The deferred-completion endpoint is an internal Agent Server callback on port 48915 and is not a general browser API.

## Retention and recovery

- `/api/agent/state` returns only queued/running tasks in `active_tasks` for reconnect recovery.
- Registry cleanup runs at most once per minute.
- Terminal entries with a valid `end_time` are removed after five minutes; legacy terminal entries without one receive a looser window.
- Frontend terminal cards are removed after ten seconds, independently of backend retention.

## Implementation map

| Concern | Current file |
|---|---|
| Live WebSocket merge and reconnect restore | `static/app/app-websocket.js` |
| HUD rendering, drag, collapse, timers | `static/common-ui-hud.js` |
| Browser-facing Agent proxy | `main_routers/agent_router.py` |
| Authoritative state snapshot | `app/agent_server/capabilities.py` |
| Task cancellation and deferred completion | `app/agent_server/api_runtime.py` |
| Registry cleanup | `app/agent_server/registry.py` |
| Plugin-name lookup | `app/agent_server/plugin_host.py` |
| Channel task updates | `app/agent_server/channels/` |
