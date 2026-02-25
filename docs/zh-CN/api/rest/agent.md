# 智能体 API

**前缀：** `/api/agent`

管理后台智能体系统 — 能力标志位、任务状态和健康监控。

## 标志位

### `GET /api/agent/flags`

获取当前智能体能力标志位。

**响应：**

```json
{
  "agent_enabled": false,
  "computer_use_enabled": false,
  "mcp_enabled": false,
  "browser_use_enabled": false
}
```

### `POST /api/agent/flags`

更新智能体标志位。更改将转发到工具服务器。

**请求体：**

```json
{
  "lanlan_name": "character_name",
  "flags": {
    "agent_enabled": true,
    "mcp_enabled": true
  }
}
```

## 状态与健康

### `GET /api/agent/state`

获取智能体当前状态的快照（运行中的任务、待处理的请求）。

### `GET /api/agent/health`

智能体健康检查端点。

## 能力检查

### `GET /api/agent/computer_use/availability`

检查 Computer Use 是否可用（需要配置视觉模型）。

### `GET /api/agent/mcp/availability`

检查 MCP（模型上下文协议）是否可用。

### `GET /api/agent/user_plugin/availability`

检查用户插件是否可用。

### `GET /api/agent/browser_use/availability`

检查 Browser Use 是否可用。

## 任务

### `GET /api/agent/tasks`

列出所有智能体任务（活动中和已完成的）。

### `GET /api/agent/tasks/{task_id}`

获取特定任务的详细信息。

## 命令

### `POST /api/agent/command`

向智能体发送控制命令。

**请求体：**

```json
{
  "lanlan_name": "character_name",
  "command": "pause",
  "task_id": "optional_task_id"
}
```

**可用命令：** `pause`、`resume`、`cancel`

## 内部端点

### `POST /api/agent/internal/analyze_request`

用于提交分析请求的内部端点。由主服务器的会话管理器使用。

### `POST /api/agent/admin/control`

管理控制命令（例如终止进程）。请谨慎使用。
