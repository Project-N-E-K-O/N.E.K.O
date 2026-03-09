# 智能体 API

**前缀：** `/api/agent`

管理后台智能体系统 — 能力标志位、任务状态和健康监控。所有请求代理到智能体服务器（端口 48915）。

## 标志位

### `GET /api/agent/flags`

获取当前智能体能力标志位。

**响应：**

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

更新智能体标志位。更改被级联到会话管理器并转发到工具服务器。

**请求体：**

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

## 状态与健康

### `GET /api/agent/state`

获取智能体当前状态的权威快照，包括修订号、标志位和能力。

### `GET /api/agent/health`

智能体健康检查端点（代理到工具服务器）。

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

统一的命令入口，用于从前端控制智能体。

**请求体：**

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

## 内部端点

### `POST /api/agent/internal/analyze_request`

内部桥接端点：从子进程接收分析请求并通过 EventBus 发布。

**请求体：**

```json
{
  "trigger": "conversation",
  "lanlan_name": "character_name",
  "messages": [ ... ]
}
```

### `POST /api/agent/admin/control`

管理控制命令，代理到工具服务器。请谨慎使用。
