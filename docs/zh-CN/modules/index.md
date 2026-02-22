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
