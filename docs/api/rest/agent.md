# Agent API

**Prefix:** `/api/agent`

Manages the background agent system — capability flags, task state, and health monitoring. All requests are proxied to the Agent Server (port 48915).

## Flags

### `GET /api/agent/flags`

Get current agent capability flags.

**Response:**

```json
{
  "agent_enabled": false,
  "computer_use_enabled": false,
  "mcp_enabled": false,
  "browser_use_enabled": false,
  "user_plugin_enabled": false
}
```

### `POST /api/agent/flags`

Update agent flags. Changes are cascaded to the session manager and forwarded to the tool server.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "flags": {
    "agent_enabled": true,
    "mcp_enabled": true,
    "user_plugin_enabled": false
  }
}
```

## State & health

### `GET /api/agent/state`

Get the agent's authoritative state snapshot including revision number, flags, and capabilities.

### `GET /api/agent/health`

Agent health check endpoint (proxied to tool server).

## Capability checks

### `GET /api/agent/computer_use/availability`

Check if Computer Use is available (requires vision model configuration).

### `GET /api/agent/mcp/availability`

Check if MCP (Model Context Protocol) is available.

### `GET /api/agent/user_plugin/availability`

Check if User Plugin is available.

### `GET /api/agent/browser_use/availability`

Check if Browser Use is available.

## Tasks

### `GET /api/agent/tasks`

List all agent tasks (active and completed).

### `GET /api/agent/tasks/{task_id}`

Get details for a specific task.

## Commands

### `POST /api/agent/command`

Unified command entry point for controlling the agent from the frontend.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "command": "pause",
  "request_id": "optional_request_id",
  "enabled": true,
  "key": "optional_key",
  "value": "optional_value"
}
```

## Internal endpoints

### `POST /api/agent/internal/analyze_request`

Internal bridge endpoint: receives analyze requests from sub-processes and publishes them through the EventBus.

**Body:**

```json
{
  "trigger": "conversation",
  "lanlan_name": "character_name",
  "messages": [ ... ]
}
```

### `POST /api/agent/admin/control`

Admin control commands proxied to the tool server. Use with caution.
