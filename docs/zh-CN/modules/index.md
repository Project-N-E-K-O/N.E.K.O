# 核心模块

本节介绍组成对话会话的 Python 运行时模块。当前源码采用 package 结构：公开导入通常由各 package 的 `__init__.py` 提供，具体实现则拆分到职责单一的 mixin 与 provider worker 中。

## 运行时模块图

| 模块 | 当前源码 | 职责 |
|---|---|---|
| [LLMSessionManager](/zh-CN/modules/core) | `main_logic/core/` | 管理会话生命周期，协调输入、模型、记忆、工具与语音输出 |
| [Realtime Client](/zh-CN/modules/omni-realtime) | `main_logic/omni_realtime_client/` | 通过 provider 专用传输运行原生音频/实时会话 |
| [Offline Client](/zh-CN/modules/omni-offline) | `main_logic/omni_offline_client/` | 通过聊天补全 API 运行流式文本与视觉回合 |
| [TTS Client](/zh-CN/modules/tts-client) | `main_logic/tts_client/` | 解析外部 TTS worker，并定义 worker 队列契约 |
| [Config Manager](/zh-CN/modules/config-manager) | `utils/config_manager/` | 解析运行时存储、迁移、角色数据、API profile 与持久化设置 |

## 组合方式

`LLMSessionManager` 通过 `ConfigManager` 读取规范化设置，再根据输入模式选择一个对话客户端：

- 文本输入创建 `OmniOfflineClient`；
- 音频输入创建 `OmniRealtimeClient`；
- 外部语音输出还会启动 `get_tts_worker()` 返回的 worker；
- 原生音频 realtime provider 自己返回音频，不需要外部 TTS 路径。

因此，Offline Client 不是 Realtime Client 的自动故障降级。二者切换是会话管理器根据模式作出的决定。

## 执行边界

- 配置和大多数持久化方法是同步文件系统操作。异步调用方应优先使用已有的 `a*` 包装，或显式卸载到线程。
- 两个对话客户端都暴露异步方法。Realtime 维持持久传输和后台接收任务；Offline 每轮发起一次流式请求。
- 外部 TTS 在专用 worker 线程中运行。会话管理器用请求/响应队列连接异步模型输出与该线程。

建议先阅读 [LLMSessionManager](/zh-CN/modules/core) 了解主调用链，再按需查看客户端与 TTS 页面中的 provider 细节。
