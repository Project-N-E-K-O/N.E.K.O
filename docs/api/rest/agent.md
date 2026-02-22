# Agent API

**Prefix:** `/api/agent`

Manages the background agent system — capability flags, task state, and health monitoring.

## Flags

### `GET /api/agent/flags`

Get current agent capability flags.

**Response:**

```json
{
  "agent_enabled": false,
  "computer_use_enabled": false,
  "mcp_enabled": false,
  "browser_use_enabled": false
}
```

### `POST /api/agent/flags`

Update agent flags. Changes are forwarded to the tool server.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "flags": {
    "agent_enabled": true,
    "mcp_enabled": true
  }
}
```

## State & health

### `GET /api/agent/state`

Get a snapshot of the agent's current state (running tasks, pending requests).

### `GET /api/agent/health`

Agent health check endpoint.

## Capability checks

### `GET /api/agent/computer_use/availability`

Check if Computer Use is available (requires vision model configuration).

### `GET /api/agent/mcp/availability`

Check if MCP (Model Context Protocol) is available.

### `GET /api/agent/user_plugin/availability`

Check if user plugins are available.

### `GET /api/agent/browser_use/availability`

Check if Browser Use is available.

## Tasks

### `GET /api/agent/tasks`

List all agent tasks (active and completed).

### `GET /api/agent/tasks/{task_id}`

Get details for a specific task.

## Commands

### `POST /api/agent/command`

Send a control command to the agent.

**Body:**

```json
{
  "lanlan_name": "character_name",
  "command": "pause",
  "task_id": "optional_task_id"
}
```

**Available commands:** `pause`, `resume`, `cancel`

## Internal endpoints

### `POST /api/agent/internal/analyze_request`

Internal endpoint for submitting analyze requests. Used by the main server's session manager.

### `POST /api/agent/admin/control`

Admin control commands (e.g., kill process). Use with caution.
