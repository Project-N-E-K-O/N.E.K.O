# 架构概览

Project N.E.K.O. 由三个主要 Python 服务进程组成：主服务器拥有 UI 与实时会话；记忆服务器拥有持久记忆；Agent 服务器评估并执行可选后台工作。HTTP/WebSocket 承载服务和浏览器流量，ZeroMQ 承载 Agent 事件桥。

## 系统架构图

![架构图](/framework.svg)

## 主要服务

| 服务 | 默认端口 | 独立启动入口 | 职责 |
|---|---:|---|---|
| **主服务器** | 48911 | `app/main_server/__main__.py` | Web UI/静态资源、REST API、浏览器 WebSocket、逐角色会话、外部 TTS |
| **记忆服务器** | 48912 | `app/memory_server/__main__.py` | 对话接收、近期上下文、事实/反思/persona、启动上下文渲染与召回 |
| **Agent / Tool Server** | 48915 | `app/agent_server/__main__.py` | 能力状态、任务评估、通道分派、取消与任务结果 |

每个服务的 FastAPI app 与实现都位于对应 package 中。特别是 Agent 实现位于 `app/agent_server/`。

Agent 进程还会在独立线程中，于 `127.0.0.1:48916` 托管内嵌的 user-plugin FastAPI 服务。它是第二个 HTTP listener，不是第四个主要进程。可选 Monitor Server 位于 `:48913`，接收逐角色 mirror stream，也不属于三个核心服务的控制路径。

## 通信图

```text
浏览器
  │ HTTP + WebSocket
  ▼
主服务器 :48911
  ├── HTTP ───────────────> 记忆服务器 :48912
  ├── HTTP control ───────> Agent Tool Server :48915
  ├── HTTP proxy/call ────> 内嵌 User-Plugin :48916
  ├── WebSocket mirror ───> Monitor Server :48913（可选）
  └── ZeroMQ bridge
       PUB  :48961 ───────> Agent SUB       session/lifecycle 事件
       PUSH :48963 ───────> Agent PULL      可靠 analyze request
       PULL :48962 <─────── Agent PUSH      ACK、任务更新、结果
```

三个 ZeroMQ socket 都由主进程 bind；Agent 进程连接对应的镜像 socket。Agent → Main 结果没有 HTTP fallback。

## 关键运行时模式

### 逐角色 ownership

`app/main_server/character_runtime.py` 为每个 `lanlan_name` 拥有一个 role-state slot：`LLMSessionManager`、异步 WebSocket lock、sync-message queue 和 cross-server connector asyncio task。非活动 manager 可在停止长期任务后替换；活动中或启动中的 manager 会被保留。

### 显式会话模式与条件热切换

文本输入使用 `OmniOfflineClient`，音频输入使用 `OmniRealtimeClient`；manager 不会在两者间静默 failover。Pending-session 准备由回合/token 阈值、renew 状态或排队上下文触发，并非每次启动会话都无条件执行。

### Async、线程与进程边界

- FastAPI lifecycle、WebSocket I/O、模型回调、cross-server connector 和主服务器侧 Agent bridge 协调运行在 asyncio 上。
- 外部 TTS provider worker 运行在逐角色线程中，通过请求/响应队列通信。
- Agent ZeroMQ bridge 使用后台接收线程包装同步 socket，以兼容 Windows Proactor loop。
- 内嵌 user-plugin HTTP server 在自己的线程运行，但与 Agent 共用一个进程。
- Memory 与 Agent 状态都是进程本地状态；其他进程通过公开 HTTP/ZeroMQ 契约访问，而不是 import 对方运行时对象。

## 下一步

- [三服务器设计](/zh-CN/architecture/three-servers) —— 服务 ownership 与启动边界
- [数据流](/zh-CN/architecture/data-flow) —— 浏览器输入、模型输出与持久化路径
- [会话管理](/zh-CN/architecture/session-management) —— 模式选择与热切换生命周期
- [记忆系统](/zh-CN/architecture/memory-system) —— 持久化、自动渲染与按需召回
- [Agent 系统](/zh-CN/architecture/agent-system) —— 评估、通道、事件投递与任务状态
