# 会话管理

`LLMSessionManager` 是每个角色的聊天传输、LLM 客户端、TTS、热切换、工具回调和主动投递协调器。类在 `main_logic/core/manager.py` 中组装；领域方法位于 `lifecycle.py`、`streaming.py`、`turn.py`、`tts_runtime.py` 和 `proactive.py` 等 mixin 中。

## 所有权模型

主服务器为每个已加载角色保留一个 manager。Manager 可以比单次浏览器连接活得更久，但只保存当前 WebSocket，且只有最新连接世代可以控制它。断连清理会校验预期 WebSocket，防止旧 socket 清掉替代它的新连接。

Manager 管理两种不同的 LLM 客户端：

| 输入模式 | 客户端 | 输入与输出 |
|---|---|---|
| `text` | `OmniOfflineClient` | 文本/图片输入、流式文本输出，项目 TTS 提供语音 |
| `audio` | `OmniRealtimeClient` | 实时 PCM 和转写；根据音色路由使用原生音频或项目 TTS |

文本与音频之间的切换是重建，不是原地切换模式。

## 启动状态机

```text
收到 start_session
        │
        ├─ 串行化并发启动 / 等待跨模式启动
        ├─ 重新加载模型和音色配置
        ├─ 设置 session_ready = false
        ├─ 获取记忆上下文并构造初始 prompt
        ├─ 在局部变量中构造对应模式的客户端
        ├─ 连接、绑定受保护回调、同步工具
        ├─ compare-and-set 到 self.session
        ├─ 按需启动或复用外部 TTS
        └─ 刷新待处理输入；发送 session_started
```

前端先收到 `session_preparing`。成功后收到 `session_started`；失败后收到 `session_failed`，半创建资源会被关闭。

记忆上下文是启动依赖，并不存在“失败后使用空上下文”的可选降级。Memory Server 请求失败会抛出连接错误，进入普通失败清理，并计入重试/熔断次数。

`_starting_session_count` 和 `_starting_input_mode` 保护异步启动窗口。另一模式的请求会等待当前启动结束，再串行拆除并重启。Manager 还使用 compare-and-set 提升，避免迟到的启动覆盖更新的获胜会话。

连续三次启动失败后会打开启动熔断。内部恢复不再重试，直到用户主动发送 `start_session` 清除熔断；短冷却也会阻止快速重试循环。

## 输入顺序与背压

上游客户端尚未完全就绪时也能接收输入：

- 有序文本和图片在 `input_cache_lock` 下保存在 `pending_input_data`，激活后再刷新。
- 音频经过最大 300 项的 `asyncio.Queue`。队列满时丢弃最旧条目，防止事件循环和麦克风采集无限增长内存。
- 音频处理器启用时，传入的 48 kHz 麦克风块经过降噪，并转换为实时上游要求的 16 kHz 格式。
- 每次 stream 操作都会快照 session 和 audio epoch；预处理过程中发生拆除或替换时丢弃该数据。

因此调用方应使用 `stream_data()`，而不是直接写 `self.session`。

## 会话续期与热切换

热切换用于续期长会话，同时不丢失上下文或麦克风输入：

1. Manager 在后台预热同模式的 pending 客户端，并带入下一份上下文快照。
2. 到续期边界时，用最终上下文和按预算选择的 Agent/事件回调 prime pending 客户端。
3. 提升即将发生时到达的音频，在预处理后存入 `hot_swap_audio_cache`。
4. 取消并等待旧 listener，然后把 pending 客户端原子提升为 `self.session`。
5. 把缓存的 16 kHz 输入音频按有界块刷新到新的实时客户端。

该缓存保存的是**切换期间的用户输入音频**，不是助手输出。`end_session()` 表示拆除，并不等于“切到预热会话”。提升失败或被取消时会关闭 pending 资源，并保留未安全消费的回调。

## TTS 所有权

项目 TTS 路径启用时，manager 拥有请求 `Queue`、响应 `Queue`、一个 daemon provider 线程和一个异步响应 handler。文本会话选择这条路径；音频会话也可能改为从实时 provider 请求原生音频。

TTS 就绪与待处理文本独立于 session 就绪。兼容且仍存活的 worker 可跨 session 启动复用；provider、音色、端点、模型或凭证变化会产生新的 runtime identity 和 worker。

项目 TTS 输出以 48 kHz 单声道 PCM 投递。重采样按 provider 的源采样率在各 worker 内完成，并不是 `LLMSessionManager` 中统一的最后步骤。

## Agent 与主动投递

Agent 结果经主服务器 ZeroMQ 桥到达，并放入对应角色的 `pending_agent_callbacks`。投递取决于模式和活动状态：

- 文本模式可在状态机空闲时启动受控的主动文本轮次。
- 语音模式优先尝试受支持的手动注入；否则回调留在队列，等待下一次热切换 prime。
- 使用前端真实的 `voice_play_start` / `voice_play_end` 作为主动语音闸门，避免把生成完成误当成播放完成。
- goodbye 模式和 takeover controller 可以延迟或抑制普通本地投递，同时保留队列。

投递有锁和 token 预算保护。放不下的条目留到后续轮次，WebSocket 重连也会再次尝试投递。

## 拆除

`end_session()` 和 `cleanup()` 会取消并等待 listener、启动/切换任务、TTS handler 和 pending 资源，再清空引用。拆除路径会快照资源身份，避免误关并发启动刚创建的新 worker 或 client。

WebSocket 断连调用 `cleanup(expected_websocket=...)`。服务器主动关闭会话时还会在适用情况下向前端发送 `session_ended_by_server`。

## 翻译是独立服务

字幕和角色资料翻译在 `utils/language_utils.py` 中实现，并通过 `/translate` 等路由暴露；它不是 `LLMSessionManager` 的阶段。

降级顺序取决于地区：

- 中国区：大陆可访问的 `translatepy` 服务，然后 LLM 翻译。
- 其他地区：Google Translate，然后 LLM 翻译。Google 被标记不可用后，后续调用会跳过它。
- 所有后端失败时返回原文。

## 实现映射

| 关注点 | 文件 |
|---|---|
| 属性所有权和 mixin 组装 | `main_logic/core/manager.py` |
| 启动、热切换、结束、清理 | `main_logic/core/lifecycle.py` |
| 输入缓存和音频流 | `main_logic/core/streaming.py` |
| 轮次完成和中断 | `main_logic/core/turn.py` |
| TTS worker 生命周期 | `main_logic/core/tts_runtime.py` |
| Agent/主动回调投递 | `main_logic/core/proactive.py` |
| 浏览器 WebSocket 分派 | `main_routers/websocket_router.py` |
