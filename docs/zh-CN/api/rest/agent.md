# Agent API

**前缀：** `/api/agent`

这是主服务器面向浏览器的 Agent Server 环回代理。它负责角色/会话同步和远程部署安全检查，再把运行时操作转发到 `TOOL_SERVER_PORT`。代理失败通常返回 `502`；远程部署下修改类路由返回 `501`，因为控制服务器所在机器并不安全。

## 状态与命令

### `GET /api/agent/flags`

返回 Agent Server 的 flags 快照，包括主开关和 Computer Use、Browser Use、用户插件、OpenClaw、OpenFang 子功能。代理失败返回 `502` 和 `success: false`。

### `POST /api/agent/flags`

旧的部分更新入口。请求体为 `{"lanlan_name":"...","flags":{...}}`。路由会更新角色会话并转发已识别的子开关。角色不存在返回 `404`；转发失败会把本地 flags 重置为安全关闭状态并返回 `502`。

### `GET /api/agent/state`

返回 Agent Server 的权威状态快照：revision、flags、capabilities、notification 状态和任务摘要。

### `POST /api/agent/command`

首选修改入口。当前命令如下：

| 命令 | 附加字段 | 用途 |
|---|---|---|
| `set_agent_enabled` | `enabled`，可选 `profile` | 切换主运行时 gate |
| `set_flag` | `key`、`value` | 切换 `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled`、`openclaw_enabled`、`openfang_enabled` 之一 |
| `refresh_state` | 无 | 返回并广播新的状态快照 |

`request_id` 和 `lanlan_name` 可选。未知命令或 flag key 由 Agent Server 拒绝；上游/代理失败返回 `502`。

## 健康状态与能力

| 方法 | 路径 | 响应边界 |
|---|---|---|
| `GET` | `/api/agent/health` | `{"status":"ok","tool":{...}}`；Agent Server 不可用时返回 `502` 和 `status: "down"` |
| `GET` | `/api/agent/computer_use/availability` | Agent Server 返回的就绪状态与原因 |
| `GET` | `/api/agent/browser_use/availability` | 浏览器依赖/模型就绪状态 |
| `GET` | `/api/agent/user_plugin/availability` | 插件服务可达性；不可用时返回 `502` |
| `GET` | `/api/agent/openclaw/availability` | OpenClaw/QwenPaw 就绪状态；不可用时返回 `502` |
| `GET` | `/api/agent/mcp/availability` | 兼容响应：MCP 已移出 `brain/`，因此始终不可用 |

## 任务与管理

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/agent/tasks` | 列出 Agent Server 任务快照 |
| `GET` | `/api/agent/tasks/{task_id}` | 读取一个任务 |
| `POST` | `/api/agent/tasks/{task_id}/cancel` | 取消一个任务；保留上游 `404`，请求已到达但响应超时时使用 `504` |
| `POST` | `/api/agent/admin/control` | 转发 `{"action":"end_all"}` 等管理操作；仅限本机，会终止活动任务 |

## 内部与 UI 辅助路由

| 方法 | 路径 | 边界 |
|---|---|---|
| `POST` | `/api/agent/internal/analyze_request` | 内部降级桥，把 `analyze_request` 发布到主事件总线 |
| `GET` | `/api/agent/user_plugin/dashboard` | 重定向到本地插件 dashboard；接受经校验的 `v` 和环回 `yui_opener_origin` 查询值 |
| `GET` | `/api/agent/openclaw/guide` | 渲染本地 OpenClaw 指南页面 |
| `GET` | `/api/agent/openclaw/guide/content` | 返回本地化指南 Markdown；可选 `lang` 查询参数 |
| `GET` | `/api/agent/openclaw/guide/assets/{asset_path:path}` | 仅服务固定指南资源目录下的文件；越界或缺失返回 `404` |

analyze bridge 不是公开任务提交 API。Dashboard 和 guide 路由只是浏览器辅助入口，不是 Agent Server 任务 API。
