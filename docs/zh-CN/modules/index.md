# 核心模块

本节为需要了解或修改内部逻辑的开发者提供 N.E.K.O. 核心 Python 模块的深入介绍。

## 模块列表

| 模块 | 文件 | 用途 |
|------|------|------|
| [LLMSessionManager](./core) | `main_logic/core.py` | 中央会话协调器 |
| [Realtime 客户端](./omni-realtime) | `main_logic/omni_realtime_client.py` | Realtime API 的 WebSocket 客户端 |
| [Offline 客户端](./omni-offline) | `main_logic/omni_offline_client.py` | 基于文本的 LLM 客户端（备用） |
| [TTS 客户端](./tts-client) | `main_logic/tts_client.py` | 文本转语音合成 |
| [配置管理器](./config-manager) | `utils/config_manager.py` | 配置加载与持久化 |

## 辅助模块

| 模块 | 文件 | 用途 |
|------|------|------|
| Agent 事件总线 | `main_logic/agent_event_bus.py` | 主服务器与智能体服务器之间的 ZeroMQ 双向桥接 |
| Agent 桥接 | `main_logic/agent_bridge.py` | 轻量级 TCP 即发即忘消息发送器 |
| 跨服务器转发 | `main_logic/cross_server.py` | 向监控、记忆和弹幕服务器的单向消息转发 |
