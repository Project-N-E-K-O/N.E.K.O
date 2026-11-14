# Agent Server API

**Default address:** `http://127.0.0.1:48915`

**Configuration:** `TOOL_SERVER_PORT`

The agent server is an internal loopback service. It owns the NekoClaw host (task brain, task agents, capability gateway), the task view shown in the HUD, the built-in keyboard-and-mouse and browser capabilities, and the embedded plugin service. The main server exposes the supported browser-facing subset under `/api/agent`; callers outside the local process group should use that proxy instead of these routes.

The agent server also exchanges asynchronous session and task events with the main server over ZeroMQ. HTTP is therefore the control/query plane, while ZeroMQ is the event plane; the service does not use ZeroMQ *instead of* HTTP.

## HTTP endpoint inventory

| Method | Path | Contract |
|---|---|---|
| `GET` | `/health` | N.E.K.O health fingerprint plus current agent flags |
| `GET` | `/agent/flags` | Current master/sub-feature flags |
| `POST` | `/agent/flags` | Partially update `computer_use_enabled`, `browser_use_enabled`, `user_plugin_enabled` |
| `GET` | `/agent/state` | Authoritative revision, flags, capabilities, notification, and active tasks |
| `POST` | `/agent/command` | `set_agent_enabled`, `set_flag`, or `refresh_state` command |
| `GET` | `/computer_use/availability` | Keyboard-and-mouse readiness and reasons |
| `GET` | `/browser_use/availability` | Browser dependency/model readiness |
| `POST` | `/nekoclaw/dispatch` | Hand one sentence of the main dialogue to the task brain; returns the receipt of its first round |
| `POST` | `/nekoclaw/query` | Ask what is under way or whether something can be done; no model is called |
| `GET` | `/nekoclaw/brief` | One line per task under way, for a new dialogue session's opening prompt |
| `GET` | `/tasks` | List tasks under way and those that ended in the last five minutes |
| `GET` | `/tasks/{task_id}` | Read one task; unknown task returns `404` |
| `POST` | `/tasks/{task_id}/cancel` | Stop one task; unknown task returns `404`, an already ended one returns `success: false` with its `status` |
| `POST` | `/api/agent/tasks/{task_id}/complete` | Completion callback for a plugin's deferred result |
| `POST` | `/admin/control` | Administrative control; `action: "end_all"` stops every task under way |
| `POST` | `/notify_config_changed` | Internal notification after model/API configuration changes |

The task object returned by `/tasks` is described under "Task object" in [Agent API](/api/rest/agent). Its fields may grow.

`/api/agent/tasks/{task_id}/complete` and `/notify_config_changed` are process-internal. `/admin/control` and the `/nekoclaw/*` routes must not be exposed to an untrusted network.

## NekoClaw routes

These three routes are called by the main server on the same machine, on behalf of the main dialogue model. They are not proxied to the browser.

### `POST /nekoclaw/dispatch`

Body: `{"text":"...","lanlan_name":"...","user_text":"...","conversation_id":"...","language":"..."}`. `text` is the sentence the model passed to its `dispatch_task` tool and `lanlan_name` is the character; both are required (`400` otherwise). `user_text` is the user's own words of that turn, which plugin entries receive as `_ctx.latest_user_request`.

The response is `{"success":true,"kind":"...","reason":"...","text":"...","commission_id":"...","model_calls":0}`. `text` is the receipt as the model reads it. `kind` is one of `accepted`, `received` (the first round outlasted one second; the conclusion follows as a report), `duplicate`, `no_capability`, `stopped`, `told`, `answered`, `ambiguous`, `nothing_active`, or `unclear_answer`. For `no_capability`, `reason` is `disabled`, `not_installed`, `not_enabled`, `denied`, or `no_match`. The call returns after the first round; the work itself continues in the background.

### `POST /nekoclaw/query`

Body: `{"text":"...","lanlan_name":"...","language":"..."}`; `lanlan_name` is required. The response is `{"success":true,"text":"...","can_do":true|false|null}`. `can_do` is `null` when the question was about progress, or when it cannot be answered without a model.

### `GET /nekoclaw/brief`

Query: `lanlan_name`, optional `language`. Returns `{"success":true,"lines":[...]}`, one line per commission of that character that is queued, being worked on, or waiting for the user. The list is empty when the master switch is off.

## ZeroMQ event plane

Addresses are loopback-only and can be overridden by the corresponding `NEKO_ZMQ_*_PORT` environment variables.

| Socket | Default address | Type | Direction |
|---|---|---|---|
| Session events | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| Task and status events | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| Reliable turn-check queue | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

Events include session lifecycle/intent-restore signals, turn-check requests and their acknowledgements, task updates, task results, proactive messages, and agent status snapshots. Reports about a commission are stored in the host's outbox before they are sent and are retried until the bridge takes them. Event payloads are internal and versioned with the main/agent pair; they are not a public plugin wire protocol.

## What carries out the work

| Part | Current role |
|---|---|
| Plugin entries | Called directly by the task brain when one entry can finish the task, through the plugin service's `/runs` protocol |
| Task agents | Execution sessions for tasks that need several steps (shown to the user as familiars); they run in the built-in agent runtime (`nekoclaw/runtime/`) |
| Capability gateway | Loopback MCP service through which task agents call plugin entries, MCP tools, and the two built-in capabilities |
| Keyboard-and-mouse, browser | Built-in capabilities backed by `brain/computer_use.py` and the optional `browser-use` dependency; only task agents can call them |

MCP servers are configured in the `mcp_adapter` plugin under `plugin/plugins/mcp_adapter/`; the gateway serves their tools to task agents.

See [Agent System](/architecture/agent-system) for the architecture and [Agent API](/api/rest/agent) for the main-server proxy.
