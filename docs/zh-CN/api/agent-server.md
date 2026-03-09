# 智能体服务器 API

**端口：** 48915（内部）

智能体服务器处理后台任务执行。它通过 ZeroMQ 套接字和 HTTP 与主服务器通信。

## REST 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/health` | GET | 服务健康检查（N.E.K.O. 签名） |
| `/status` | GET | 智能体状态快照（任务、标志、能力） |
| `/flag_set/{flag_name}/{value}` | POST | 控制智能体功能标志 |
| `/capabilities` | GET | 可用的智能体能力 |
| `/task/spawn_computer_use` | POST | 排队 Computer Use 任务 |
| `/task/{task_id}` | GET | 获取任务状态和结果 |
| `/task/{task_id}` | DELETE | 取消/删除任务 |
| `/reload_config` | POST | 重新加载 API 配置 |

## ZeroMQ 接口

| 套接字 | 地址 | 类型 | 方向 |
|--------|------|------|------|
| 会话事件 | `tcp://127.0.0.1:48961` | PUB/SUB | 主服务器 -> 智能体 |
| 任务结果 | `tcp://127.0.0.1:48962` | PUSH/PULL | 智能体 -> 主服务器 |
| 分析队列 | `tcp://127.0.0.1:48963` | PUSH/PULL | 主服务器 -> 智能体 |

## 消息类型

### 主服务器 -> 智能体

**分析请求：**

当主服务器检测到可操作的对话上下文时发布。

### 智能体 -> 主服务器

**任务结果：**

```json
{
  "type": "task_result",
  "task_id": "uuid",
  "lanlan_name": "character_name",
  "result": { ... },
  "status": "completed"
}
```

**主动消息：**

```json
{
  "type": "proactive_message",
  "lanlan_name": "character_name",
  "text": "I found something interesting...",
  "source": "web_search"
}
```

## DirectTaskExecutor

智能体服务器使用 `DirectTaskExecutor`（位于 `brain/task_executor.py`）进行并行评估和优先级执行：

1. **并行评估**四个适配器（`asyncio.gather`）：
   - `_assess_mcp()` —— MCP 工具能否处理？
   - `_assess_browser_use()` —— 浏览器自动化能否处理？
   - `_assess_computer_use()` —— 桌面 GUI 自动化能否处理？
   - `_assess_user_plugin()` —— 本地插件能否处理？
2. **优先级选择**：MCP > Browser Use > Computer Use > 用户插件
3. 选定的适配器执行任务

## 执行适配器

| 适配器 | 模块 | 能力 |
|--------|------|------|
| McpRouterClient | `brain/mcp_client.py` | 通过模型上下文协议调用外部工具 |
| ComputerUseAdapter | `brain/computer_use.py` | 截图分析、鼠标/键盘自动化 |
| BrowserUseAdapter | `brain/browser_use_adapter.py` | 网页浏览、表单填写、内容提取 |
| 用户插件 | `POST /plugin/trigger` | 本地插件执行 |

详见[智能体系统](/zh-CN/architecture/agent-system)了解详细架构。
