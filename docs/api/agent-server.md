# Agent Server API

**Default address:** `http://127.0.0.1:48915`

**Configuration:** `TOOL_SERVER_PORT`

The agent server is an internal loopback service. It owns the agent runtime, tracked tasks, execution adapters, and direct plugin execution. The main server exposes the supported browser-facing subset under `/api/agent`; callers outside the local process group should use that proxy instead of these routes.

The agent server also exchanges asynchronous session and task events with the main server over ZeroMQ. HTTP is therefore the control/query plane, while ZeroMQ is the event plane; the service does not use ZeroMQ *instead of* HTTP.

## HTTP endpoint inventory

| Method | Path | Contract |
|---|---|---|
| `GET` | `/health` | N.E.K.O health fingerprint plus current agent flags |
| `GET` | `/capabilities` | Current capability snapshot |
| `GET` | `/agent/flags` | Current master/sub-feature flags |
| `POST` | `/agent/flags` | Partially update sub-feature flags |
| `GET` | `/agent/state` | Authoritative revision, flags, capabilities, notification, and task state |
| `POST` | `/agent/command` | `set_agent_enabled`, `set_flag`, or `refresh_state` command |
| `GET` | `/computer_use/availability` | Computer Use readiness and reasons |
| `POST` | `/computer_use/run` | Start a tracked Computer Use task; body requires `instruction`, with optional `screenshot_b64` and `lanlan_name` |
| `GET` | `/browser_use/availability` | Browser Use dependency/model readiness |
| `POST` | `/browser_use/run` | Run one browser instruction; body requires `instruction` |
| `GET` | `/openclaw/availability` | OpenClaw/QwenPaw capability check |
| `GET` | `/openfang/availability` | OpenFang capability check |
| `POST` | `/openfang/run` | Start a tracked OpenFang task |
| `POST` | `/openfang/sync_config` | Refresh the OpenFang runtime configuration |
| `GET` | `/mcp/availability` | Compatibility response: MCP has been removed from the `brain/` layer and is always unavailable here |
| `POST` | `/plugin/execute` | Directly schedule a user-plugin entry; requires `plugin_id`, optional `entry_id`, `args`, character, and conversation IDs |
| `GET` | `/tasks` | List task-registry snapshots |
| `GET` | `/tasks/{task_id}` | Read one tracked task; missing task returns `404` |
| `POST` | `/tasks/{task_id}/cancel` | Cancel a tracked task; missing task returns `404` |
| `POST` | `/api/agent/tasks/{task_id}/correction` | Internal correction callback for a task result |
| `POST` | `/api/agent/tasks/{task_id}/complete` | Internal completion callback for a task result |
| `POST` | `/admin/control` | Administrative runtime control; currently `action: "end_all"` cancels active work |
| `POST` | `/notify_config_changed` | Internal notification after model/API configuration changes |

Most run endpoints return `{"success":true,"task_id":"...","status":"running","start_time":"..."}` and continue asynchronously. Validation errors use `400`; a disabled master/feature uses `403`; unavailable adapters use `503`; a detected duplicate Computer Use task uses `409`. Task and plugin result bodies are internal schemas and may grow fields.

The two `/api/agent/tasks/*` callback routes and `/notify_config_changed` are process-internal. `/admin/control`, direct run routes, and `/plugin/execute` must not be exposed to an untrusted network.

## ZeroMQ event plane

Addresses are loopback-only and can be overridden by the corresponding `NEKO_ZMQ_*_PORT` environment variables.

| Socket | Default address | Type | Direction |
|---|---|---|---|
| Session events | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| Task and status events | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| Reliable analyze queue | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

Events include session lifecycle/intent-restore signals, analyze requests, task updates, task results, proactive messages, and agent status snapshots. Event payloads are internal and versioned with the main/agent pair; they are not a public plugin wire protocol.

## Execution adapters

| Adapter | Current role |
|---|---|
| Computer Use | Screenshot-guided mouse and keyboard execution |
| Browser Use | Browser automation through the optional `browser-use` dependency |
| OpenClaw | Delegation to the OpenClaw/QwenPaw standalone agent channel |
| OpenFang | Delegation to the OpenFang standalone agent channel |
| User plugins | Direct execution through the plugin runtime |

MCP calls are no longer implemented in `brain/`; installable MCP integration lives under `plugin/plugins/mcp_adapter/`.

See [Agent System](/architecture/agent-system) for the architecture and [Agent API](/api/rest/agent) for the main-server proxy.
