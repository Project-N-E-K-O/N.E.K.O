# 🏗️ 猫娘 Claudian 架构文档

> 深入了解猫娘 Claudian 的内部架构和设计原理

---

## 目录

1. [整体架构](#1-整体架构)
2. [核心模块](#2-核心模块)
3. [通信协议](#3-通信协议)
4. [数据流](#4-数据流)
5. [前端架构](#5-前端架构)
6. [后端架构](#6-后端架构)
7. [扩展机制](#7-扩展机制)

---

## 1. 整体架构

### 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      浏览器 (前端)                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐      │
│  │  HTML   │  │   CSS   │  │   JS    │  │  i18n   │      │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘      │
└─────────────────────────────────────────────────────────────┘
                           │
                      SSE / HTTP
                           │
┌─────────────────────────────────────────────────────────────┐
│                    N.E.K.O (后端)                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                NekoClaudianPlugin                    │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐            │  │
│  │  │  HTTP   │  │  SSE    │  │  API    │            │  │
│  │  │ Server  │  │ Stream  │  │ Routes  │            │  │
│  │  └─────────┘  └─────────┘  └─────────┘            │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                   核心模块                           │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐            │  │
│  │  │  Tab    │  │  Chat   │  │ Claude  │            │  │
│  │  │ Manager │  │  State  │  │ Runtime │            │  │
│  │  └─────────┘  └─────────┘  └─────────┘            │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                 Claude CLI 子进程                    │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 层次结构

| 层次 | 说明 | 技术 |
|------|------|------|
| 表示层 | 用户界面 | HTML, CSS, JS |
| 通信层 | 前后端通信 | HTTP, SSE |
| 业务层 | 核心逻辑 | Python |
| 数据层 | 持久化 | JSON 文件 |
| 外部层 | Claude CLI | 子进程 |

---

## 2. 核心模块

### 2.1 Runtime 模块

```
core/providers/claude/runtime/
├── chat_runtime.py      # 持久查询管理
├── cold_start.py        # 冷启动查询
├── spawn.py             # 子进程管理
├── cli_resolver.py      # CLI 路径解析
├── session_manager.py   # 会话管理
├── message_channel.py   # 消息通道
├── types.py             # 类型定义
└── ...
```

**职责**：
- 管理 Claude CLI 子进程
- 处理持久查询和冷启动
- 管理消息流和会话状态

### 2.2 Controller 模块

```
core/controllers/
├── input_controller.py      # 输入控制
├── stream_controller.py     # 流控制
├── conversation_controller.py # 对话控制
├── selection_controller.py  # 选择控制
└── navigation_controller.py # 导航控制
```

**职责**：
- 处理用户输入
- 管理流式输出
- 控制对话生命周期

### 2.3 State 模块

```
core/state/
└── chat_state.py  # 聊天状态管理
```

**职责**：
- 管理消息列表
- 跟踪流式状态
- 管理工具调用状态

### 2.4 Tab 模块

```
core/tabs/
├── tab.py              # 单个 Tab
├── tab_manager.py      # Tab 管理器
├── provider_resolution.py # Provider 解析
└── types.py            # 类型定义
```

**职责**：
- 管理多个对话会话
- 处理 Tab 切换
- 管理 Provider 绑定

---

## 3. 通信协议

### 3.1 HTTP REST API

```
基础路径: /neko_claudian/api

GET  /health           — 健康检查
GET  /status           — 状态信息
GET  /tabs             — Tab 列表
POST /tab/new          — 创建 Tab
POST /tab/close        — 关闭 Tab
POST /tab/switch       — 切换 Tab
POST /send             — 发送消息
GET  /settings         — 获取设置
POST /settings         — 更新设置
```

### 3.2 SSE (Server-Sent Events)

```
GET /neko_claudian/api/stream/*        — 全局事件流
GET /neko_claudian/api/stream/{tab_id} — Tab 事件流
```

**事件格式**：
```
data: {"type": "text", "content": "Hello"}

data: {"type": "tool_use", "id": "123", "name": "Bash", "input": {...}}

data: {"type": "tool_result", "id": "123", "content": "...", "isError": false}
```

**心跳**：
```
: ping
```

### 3.3 消息类型

| 类型 | 说明 |
|------|------|
| `text` | 文本内容 |
| `thinking` | 思考内容 |
| `tool_use` | 工具调用 |
| `tool_result` | 工具结果 |
| `error` | 错误信息 |
| `usage` | 使用统计 |
| `done` | 完成信号 |
| `session_info` | 会话信息 |

---

## 4. 数据流

### 4.1 发送消息流程

```
用户输入
    ↓
InputController.sendMessage()
    ↓
创建用户消息
    ↓
添加到 ChatState
    ↓
调用 ClaudeChatRuntime.query()
    ↓
Claude CLI 子进程
    ↓
流式响应
    ↓
StreamController.handleStreamChunk()
    ↓
更新 UI
```

### 4.2 接收消息流程

```
Claude CLI 输出
    ↓
MessageChannel 接收
    ↓
transformClaudeMessage() 转换
    ↓
StreamController 处理
    ↓
更新 ChatState
    ↓
SSE 广播到前端
    ↓
前端更新 UI
```

---

## 5. 前端架构

### 5.1 目录结构

```
static/
├── index.html           # 主页面
├── js/
│   ├── main.js          # 主入口
│   ├── api.js           # API 客户端
│   ├── sse.js           # SSE 客户端
│   ├── state.js         # 状态管理
│   ├── i18n.js          # 国际化
│   └── chat/
│       ├── rendering/   # 渲染器
│       ├── controllers/ # 控制器
│       ├── state/       # 状态
│       └── tabs/        # Tab 管理
├── css/
│   ├── base/            # 基础样式
│   ├── components/      # 组件样式
│   ├── features/        # 功能样式
│   └── modals/          # 弹窗样式
└── i18n/
    ├── zh-CN.json       # 中文
    ├── en.json          # 英文
    └── ...              # 其他语言
```

### 5.2 组件架构

```
App
├── Header
├── TabBar
│   └── Tab[]
├── Main
│   ├── Messages
│   │   └── Message[]
│   │       ├── UserMessage
│   │       └── AssistantMessage
│   │           ├── TextBlock
│   │           ├── ThinkingBlock
│   │           ├── ToolCallBlock
│   │           └── SubagentBlock
│   ├── StatusPanel
│   └── InputContainer
│       ├── QueueIndicator
│       └── InputWrapper
│           ├── TextArea
│           └── SendButton
└── Modals
    ├── SettingsModal
    └── ConfirmModal
```

---

## 6. 后端架构

### 6.1 插件生命周期

```
__init__()
    ↓
startup()
    ├── 初始化数据目录
    ├── 加载设置
    ├── 初始化组件
    ├── 启动 HTTP Server
    └── 注册静态 UI
    ↓
ready (运行中)
    ↓
shutdown()
    ├── 关闭 HTTP Server
    ├── 关闭 Claude Runtime
    ├── 关闭 MCP Servers
    └── 持久化数据
    ↓
stopped
```

### 6.2 核心组件

| 组件 | 职责 |
|------|------|
| `NekoClaudianPlugin` | 插件主类 |
| `_HttpServer` | HTTP 服务器 |
| `ClaudeChatRuntime` | Claude 运行时 |
| `TabManager` | Tab 管理 |
| `ChatState` | 状态管理 |
| `InputController` | 输入控制 |
| `StreamController` | 流控制 |

---

## 7. 扩展机制

### 7.1 MCP 扩展

通过 MCP 协议添加外部工具：

```json
{
    "mcpServers": {
        "my-tool": {
            "command": "node",
            "args": ["tool-server.js"]
        }
    }
}
```

### 7.2 Agent 扩展

通过 Agent 定义添加自定义代理：

```json
{
    "agents": [
        {
            "id": "my-agent",
            "name": "My Agent",
            "prompt": "You are a specialized agent...",
            "tools": ["Read", "Write"]
        }
    ]
}
```

### 7.3 Slash Command 扩展

通过 Slash Command 添加自定义命令：

```json
{
    "slashCommands": [
        {
            "name": "review",
            "description": "Code review",
            "content": "Please review the following code..."
        }
    ]
}
```

---

## 📚 相关文档

- [README.md](../README.md) — 项目简介
- [TUTORIAL.md](../TUTORIAL.md) — 使用教程
- [CLAUDIAN_PORT_NOTES.md](CLAUDIAN_PORT_NOTES.md) — 移植笔记

---

Made with 🐱 by Neko Team
