# 智能体服务器 API

**端口：** 48915（内部）

智能体服务器处理后台任务执行。它通过 ZeroMQ 套接字（而非 HTTP）与主服务器通信。

## ZeroMQ 接口

| 套接字 | 地址 | 类型 | 方向 |
|--------|------|------|------|
| 会话事件 | `tcp://127.0.0.1:48961` | PUB/SUB | 主服务器 → 智能体 |
| 任务结果 | `tcp://127.0.0.1:48962` | PUSH/PULL | 智能体 → 主服务器 |
| 分析队列 | `tcp://127.0.0.1:48963` | PUSH/PULL | 主服务器 → 智能体 |

## 消息类型

### 主服务器 → 智能体

**分析请求：**

当主服务器检测到可操作的对话上下文时发布。

### 智能体 → 主服务器

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

## 执行适配器

智能体服务器使用三个适配器来执行任务：

| 适配器 | 模块 | 能力 |
|--------|------|------|
| MCP 客户端 | `brain/mcp_client.py` | 通过模型上下文协议调用外部工具 |
| Computer Use | `brain/computer_use.py` | 截图分析、鼠标/键盘自动化 |
| Browser Use | `brain/browser_use_adapter.py` | 网页浏览、表单填写、内容提取 |

详见[智能体系统](/zh-CN/architecture/agent-system)了解详细架构。
