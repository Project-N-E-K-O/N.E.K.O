# Agent Server API

**Port:** 48915 (internal)

The agent server handles background task execution. It communicates with the main server through both ZeroMQ sockets (for real-time event streaming) and HTTP REST endpoints (for management and control).

## REST endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check with N.E.K.O. signature |
| `/status` | GET | Agent state snapshot (tasks, flags, capabilities) |
| `/flag_set/{flag_name}/{value}` | POST | Set agent feature flags |
| `/capabilities` | GET | List available agent capabilities |
| `/task/spawn_computer_use` | POST | Queue a Computer Use task |
| `/task/{task_id}` | GET | Get task status and result |
| `/task/{task_id}` | DELETE | Cancel/delete a task |
| `/reload_config` | POST | Reload API configurations |

## ZeroMQ interface

| Socket | Address | Type | Direction |
|--------|---------|------|-----------|
| Session events | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| Task results | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| Analyze queue | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

## Message types

### Main → Agent

**Analyze request:**

Published when the main server detects an actionable conversation context.

```json
{
  "type": "analyze_request",
  "lanlan_name": "character_name",
  "messages": [ ... ],
  "agent_flags": { ... }
}
```

### Agent → Main

**Task result:**

```json
{
  "type": "task_result",
  "task_id": "uuid",
  "lanlan_name": "character_name",
  "result": { ... },
  "status": "completed"
}
```

**Proactive message:**

```json
{
  "type": "proactive_message",
  "lanlan_name": "character_name",
  "text": "I found something interesting...",
  "source": "web_search"
}
```

**Agent status update:**

```json
{
  "type": "agent_status_update",
  "capabilities": { ... },
  "flags": { ... }
}
```

**Analyze ACK:**

```json
{
  "type": "analyze_ack",
  "lanlan_name": "character_name"
}
```

## Execution engine

The agent server uses the `DirectTaskExecutor` for parallel capability assessment and priority-based execution:

| Adapter | Module | Capabilities |
|---------|--------|-------------|
| MCP Client | `brain/mcp_client.py` | External tool calls via Model Context Protocol (JSON-RPC 2.0 over HTTP+SSE) |
| Computer Use | `brain/computer_use.py` | Vision-based desktop automation (Thought→Action→Code loop) |
| Browser Use | `brain/browser_use_adapter.py` | Web browsing, form filling, content extraction |
| User Plugin | Plugin system | Local plugin execution via `POST /plugin/trigger` |

Execution priority: **MCP → Browser Use → Computer Use → User Plugin**

See [Agent System](/architecture/agent-system) for the detailed architecture.
