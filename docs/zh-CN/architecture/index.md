# 架构概览

Project N.E.K.O. 采用**多进程微服务架构**构建，三个主要服务器通过 WebSocket、HTTP 和 ZeroMQ 消息进行协作。

## 系统架构图

![架构图](/framework.svg)

## 三服务器设计

| 服务器 | 端口 | 入口文件 | 职责 |
|--------|------|---------|------|
| **主服务器** | 48911 | `app/main_server.py` | Web UI、REST API、WebSocket 聊天、TTS |
| **记忆服务器** | 48912 | `app/memory_server.py` | 语义召回、时间索引历史、记忆压缩 |
| **智能体服务器** | 48915 | `app/agent_server.py` | 后台任务执行（Computer Use、Browser Use、OpenClaw 远程代理、OpenFang 独立智能体、用户插件） |

主服务器是面向用户的入口。它提供 Web UI 服务，处理所有 REST API 请求，并维护用于实时语音/文字聊天的 WebSocket 连接。记忆服务器和智能体服务器是内部服务，由主服务器与之通信。

## 通信模式

```
┌────────────────────────────────────────────────┐
│              Main Server (:48911)                │
│                                                  │
│  FastAPI ─── REST Routers                        │
│  WebSocket ─── LLMSessionManager                 │
│  HTTP Client (httpx) ───────────┐                │
│  ZeroMQ PUB  (:48961) ──┐       │                │
│  ZeroMQ PUSH (:48963) ──┼── MainServerAgentBridge│
│  ZeroMQ PULL (:48962) ──┘       │                │
└─────────┬───────────────────────┼────────────────┘
          │                       │
    ┌─────┴─────┐         ┌───────┴────────┐
    │           │         │                │
    ▼           ▼         ▼                ▼
  记忆服务器   监控服务器  智能体服务器     用户插件
  Memory     Monitor    (Tool Server)  服务器
  Server     Server     (:48915)       (:48916)
  (:48912)   (:48913)   HTTP REST +    HTTP REST
   HTTP      单向状态推送  ZeroMQ 事件
```

- **主服务器 <-> 记忆服务器**：通过 HTTP 请求存储/查询记忆（记忆服务器 `:48912`）
- **主服务器 <-> 智能体服务器**：两条通道协同工作 ——
  - **控制 / 派发**：通过 `httpx` 走 HTTP REST 调用 Tool Server（`:48915`），以及用户插件服务器（`:48916`）
  - **事件流式传输**：通过 ZeroMQ，由主进程绑定套接字 —— `PUB :48961`（会话 → 智能体）、`PUSH :48963`（分析队列 → 智能体）、`PULL :48962`（智能体 → 主进程结果）；`agent_server` 连接对应的镜像套接字
- **主服务器 <-> 监控服务器**：向监控服务器单向推送状态（`:48913`）

## 关键架构模式

### 会话热切换

`LLMSessionManager` 在当前会话仍然活跃时，于后台预先准备新的 LLM 会话。当用户结束一个对话轮次时，系统无缝切换到预热好的会话，实现零停机。音频在过渡期间被缓存，之后再行发送。

### 按角色隔离

每个角色（通过 `lanlan_name` 标识）拥有独立的：
- `LLMSessionManager` 实例
- 同步连接器线程
- WebSocket 锁
- 消息队列
- 关闭事件

### 异步/同步边界

FastAPI 处理器是异步的。TTS 合成在专用线程中运行，通过队列通信。音频处理使用执行器线程池。ZeroMQ 事件桥运行后台接收线程。

## 下一步

- [三服务器设计](./three-servers) —— 每个服务器的详细分析
- [数据流](./data-flow) —— 从前端到 LLM 再返回的请求生命周期
- [会话管理](./session-management) —— 热切换机制深入解析
- [Neko x QwenPaw 接入规范](./neko-qwenpaw-integration) —— 桌宠前端与能力后端的 REST 接入约定
