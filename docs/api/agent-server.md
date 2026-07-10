# Agent Server API

**Port:** 48915 (internal)

The agent server handles background task execution. It communicates with the main server through ZeroMQ sockets rather than HTTP.

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

## Execution adapters

The agent server uses the following execution adapters for task execution:

| Adapter | Module | Capabilities |
|---------|--------|-------------|
| Computer Use | `brain/computer_use.py` | Screenshot analysis, mouse/keyboard automation |
| Browser Use | `brain/browser_use_adapter.py` | Web browsing, form filling, content extraction |
| OpenClaw | `brain/openclaw_adapter.py` | Delegates a task to the OpenClaw standalone agent channel |
| OpenFang | `brain/openfang_adapter.py` | Delegates a task to the OpenFang standalone agent channel |

MCP (Model Context Protocol) tool calls have been removed from the `brain/` layer and are now provided by the user-installable plugin `plugin/plugins/mcp_adapter/` (`MCPClient` / `MCPPluginInvoker`).

See [Agent System](/architecture/agent-system) for the detailed architecture.
