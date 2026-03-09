# 智能体系统

智能体系统使 N.E.K.O. 角色能够执行后台任务 —— 浏览网页、控制计算机、调用外部工具和触发本地插件 —— 这些任务由对话上下文触发。

## 架构

```
主服务器                              智能体服务器
┌────────────────┐                  ┌─────────────────────────────┐
│ LLMSession     │                  │ DirectTaskExecutor           │
│ Manager        │  ZeroMQ          │   ├── _assess_mcp()         │
│   │            │ ──────────────>  │   ├── _assess_browser_use() │
│   │ agent_flags│  PUB/SUB         │   ├── _assess_computer_use()│
│   │            │                  │   └── _assess_user_plugin() │
│   │ callbacks  │ <──────────────  │                              │
│   │            │  PUSH/PULL       │ 优先级: MCP > BU > CU > UP   │
└────────────────┘                  │                              │
                                    │ 适配器:                      │
                                    │   ├── McpRouterClient        │
                                    │   ├── BrowserUseAdapter      │
                                    │   ├── ComputerUseAdapter     │
                                    │   └── POST /plugin/trigger   │
                                    │                              │
                                    │ 支持模块:                    │
                                    │   ├── Analyzer               │
                                    │   └── Deduper                │
                                    └─────────────────────────────┘
```

## 能力标志

智能体能力通过 `/api/agent/flags` 端点管理的标志进行切换：

| 标志 | 默认值 | 说明 |
|------|--------|------|
| `agent_enabled` | false | 智能体系统主开关 |
| `mcp_enabled` | false | Model Context Protocol 工具调用 |
| `computer_use_enabled` | false | 截图分析、鼠标/键盘操作 |
| `browser_use_enabled` | false | 网页浏览自动化 |
| `user_plugin_enabled` | false | 通过插件系统执行本地插件 |

## 任务执行流水线

`DirectTaskExecutor`（位于 `brain/task_executor.py`）是核心引擎。它用统一的**并行评估 + 优先级执行**模型替代了旧的两步式 Planner->Executor 流程：

1. **触发**：主服务器在对话中检测到可执行的请求，通过 ZeroMQ 发布分析请求。

2. **并行评估**：执行器使用 `asyncio.gather` **并发运行所有四个评估器**：
   - `_assess_mcp()` —— 评估是否有 MCP 工具能处理该请求；返回 `tool_name` + `tool_args`
   - `_assess_browser_use()` —— 评估浏览器自动化是否适合
   - `_assess_computer_use()` —— 评估桌面 GUI 自动化是否适合
   - `_assess_user_plugin()` —— 评估本地插件能否处理；返回 `plugin_id` + `entry_id`

3. **优先级选择**：第一个成功的评估结果胜出，顺序为：**MCP -> Browser Use -> Computer Use -> 用户插件**。

4. **执行**：选定的适配器运行任务：
   - `_execute_mcp()` —— 通过 `McpRouterClient` 调用 MCP 工具
   - `BrowserUseAdapter.run_instruction()` —— 运行浏览器自动化
   - `ComputerUseAdapter.run_instruction()` —— 运行桌面自动化
   - `_execute_user_plugin()` —— `POST /plugin/trigger`，带 plugin_id 和 entry_id

5. **分析**：`Analyzer` 评估多轮对话以识别已完成的任务并确定响应方式。

6. **去重**：`Deduper` 使用 LLM 判断新结果是否与已有结果语义重复。

7. **返回**：结果通过 ZeroMQ PUSH/PULL 流式返回主服务器，类型为 `task_result` 或 `proactive_message`。

## ZeroMQ 套接字映射

| 地址 | 类型 | 方向 | 用途 |
|------|------|------|------|
| `tcp://127.0.0.1:48961` | PUB/SUB | 主服务器 -> 智能体 | 会话事件、任务请求 |
| `tcp://127.0.0.1:48962` | PUSH/PULL | 智能体 -> 主服务器 | 任务结果、状态更新 |
| `tcp://127.0.0.1:48963` | PUSH/PULL | 主服务器 -> 智能体 | 分析请求队列 |

事件桥使用 NOBLOCK 发送带回退、ACK/重试机制（0.5 秒超时、1 次重试）和通过 asyncio 事件循环的后台投递。

## MCP 客户端

`McpRouterClient`（位于 `brain/mcp_client.py`）实现了 MCP（Model Context Protocol）2024-11-05 标准：

- **传输**：HTTP + SSE（Server-Sent Events），JSON-RPC 2.0
- **端点**：可配置的 `MCP_ROUTER_URL` 环境变量，默认路径 `/mcp`
- **缓存**：`TTLCache(ttl=3s)` 用于工具列表，失败冷却（1 秒）
- **工具目录**：`McpToolCatalog` 提供 `get_capabilities()` 用于评估

## Computer Use

Computer Use 适配器（`brain/computer_use.py`）实现了受 Kimi 范式启发的 **Thought -> Action -> Code** 循环：

1. 捕获桌面截图
2. 发送给视觉语言模型（VLM）进行分析
3. VLM 返回结构化输出：`## Thought` / `## Action` / `## Code`
4. 执行生成的 `pyautogui` 代码
5. 循环直到调用 `computer.terminate()` 或达到最大步数（50）

关键特性：
- **归一化坐标**：`[0-999]` 坐标系映射到实际屏幕分辨率（`_ScaledPyAutoGUI`）
- **CJK 文字输入**：使用剪贴板粘贴支持 CJK 字符
- **思考模式**：支持 Claude 扩展思考（reasoning 字段）
- **图像历史**：保留最近 3 张截图作为上下文
- **可用操作**：`click`、`doubleClick`、`rightClick`、`moveTo`、`dragTo`、`scroll`、`write`、`press`、`hotkey`、`computer.wait(seconds)`、`computer.terminate(status, answer)`

## Browser Use

Browser Use 适配器（`brain/browser_use_adapter.py`）封装了 `browser-use` 库用于网页自动化：

- 导航至 URL
- 提取页面内容
- 填写表单和点击元素
- 截取页面截图

关键特性：
- **蓝色呼吸光效**：通过 CDP 注入的视觉指示器，表示浏览器正被智能体控制
- **会话复用**：单个 `session_id` 可运行多个顺序任务
- **超时**：`asyncio.wait_for`，默认 300 秒超时
- **保持活跃**：浏览器窗口保持可见（`keep_alive=True`）

## 用户插件

用户插件适配器触发安装在插件系统中的本地插件：

- 评估返回匹配插件的 `plugin_id` + `entry_id`
- 执行发送 `POST /plugin/trigger`，带插件标识符和参数
- 插件执行超时：10 秒（可在 `plugin/settings.py` 中配置）

## Brain 模块

| 模块 | 文件 | 用途 |
|------|------|------|
| DirectTaskExecutor | `brain/task_executor.py` | 统一并行评估 + 优先级执行 |
| McpRouterClient | `brain/mcp_client.py` | 通过 HTTP+SSE 的 MCP 工具路由 |
| ComputerUseAdapter | `brain/computer_use.py` | 基于视觉的桌面自动化 |
| BrowserUseAdapter | `brain/browser_use_adapter.py` | 网页浏览自动化 |
| TaskPlanner | `brain/planner.py` | 旧版两步式任务规划（参考） |
| ConversationAnalyzer | `brain/analyzer.py` | 多轮对话分析、任务识别 |
| Processor | `brain/processor.py` | 数据预处理/后处理 |
| TaskDeduper | `brain/deduper.py` | 基于 LLM 的重复检测 |
| MainBridge | `brain/main_bridge.py` | 服务间事件发布桥 |
| AgentSessionManager | `brain/agent_session.py` | 会话生命周期和任务追踪 |
| CUA | `brain/cua/` | Computer Understanding Agent —— 闭环视觉自动化子系统 |

## API 端点

完整的端点参考请参阅[智能体 REST API](/zh-CN/api/rest/agent)。
