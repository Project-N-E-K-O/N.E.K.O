# 会话管理

`main_logic/core.py` 中的 `LLMSessionManager` 类是每个角色对话会话的中央协调器。每个角色拥有自己的管理器实例。

## 会话生命周期

```
new connection ──> start_session() ──> stream_data() ──> end_session()
                        │                                      │
                        │                               热切换到
                        │                               预热的会话
                        │
                   创建 OmniRealtimeClient
                   启动 TTS 工作线程
                   后台准备下一个会话
```

## 关键属性

| 属性 | 类型 | 用途 |
|------|------|------|
| `websocket` | WebSocket | 当前客户端连接 |
| `lanlan_name` | str | 角色标识符 |
| `session` | OmniRealtimeClient | 当前 LLM 会话 |
| `is_active` | bool | 会话是否正在运行 |
| `input_mode` | str | `"audio"` 或 `"text"` |
| `voice_id` | str | 角色的 TTS 声音 ID |
| `tts_request_queue` | Queue | 出站 TTS 请求 |
| `tts_response_queue` | Queue | 入站 TTS 音频 |
| `agent_flags` | dict | 智能体能力标志 |
| `hot_swap_audio_cache` | list | 切换期间缓存的音频 |
| `is_hot_swap_imminent` | bool | 最终切换正在进行中 |
| `is_preparing_new_session` | bool | 后台准备进行中 |
| `message_cache_for_new_session` | list | 准备期间积累的对话 |
| `pending_session_warmed_up_event` | Event | 后台准备完成信号 |
| `background_preparation_task` | Task | 后台准备的 asyncio 任务 |
| `final_swap_task` | Task | 原子切换的 asyncio 任务 |

## 热切换机制

热切换系统通过约 40 秒的周期确保会话过渡零停机：

```
0-40s:  活跃会话处理轮次
  40s:  到达运行阈值 -> 触发记忆归档
  50s:  摘要完成 + 10s 延迟 -> 后台准备开始
        • 准备新客户端（OmniRealtimeClient 或 OmniOfflineClient）
        • 如需则预热 TTS 工作线程
        • 设置 pending_session_warmed_up_event
  50s+: 当前轮次结束 + 新会话就绪 -> 最终切换
        • 关闭旧会话
        • 激活新会话
        • 以限速方式刷新热切换音频缓存（5 倍块乘数）
        • 无缝恢复对话
```

1. **准备**：当前会话处理用户输入的同时，后台使用最新的角色配置创建新的 `OmniRealtimeClient` 会话。

2. **缓存**：调用 `end_session()` 时，所有传输中的音频输出被存储在 `hot_swap_audio_cache` 中。

3. **切换**：`_perform_final_swap_sequence()` 原子性地将旧会话替换为新会话。

4. **刷新**：缓存的音频被发送到客户端，提供无缝的过渡体验。

这意味着角色可以在对话轮次之间更新人设、声音或模型设置，而用户不会感受到任何延迟。

## 回调处理器

`LLMSessionManager` 为会话事件提供了丰富的回调处理器：

| 处理器 | 用途 |
|--------|------|
| `handle_new_message()` | 清空 TTS 队列，重置重采样器状态，生成新的 `speech_id` |
| `handle_text_data(text, is_first_chunk)` | 发送文本到前端 + 入队 TTS 请求 |
| `handle_audio_data(audio_bytes)` | 重采样（24->48kHz）并流式传输到前端 |
| `handle_input_transcript(transcript)` | 用户活动追踪 + 消息缓存更新 |
| `handle_output_transcript(text, is_first_chunk)` | 显示文本 + TTS 入队 |
| `handle_response_complete()` | TTS 信号、轮次结束、热切换触发评估 |
| `handle_response_discarded()` | 清空 TTS 流水线，通知前端 |
| `handle_silence_timeout()` | 90 秒静默自动关闭（仅 GLM/Free API） |

## 音频处理

音频经过状态化重采样流水线处理：

```
LLM output (24kHz PCM) ──> soxr ResampleStream ──> 48kHz PCM ──> base64 ──> WebSocket
```

重采样器使用 `soxr` `ResampleStream`，在单条消息内的音频块之间维护内部状态。每条新消息时（`handle_new_message()`）重置重采样器状态，以避免跨消息伪影。

## 智能体集成

会话管理器通过回调与智能体系统协作：

1. 智能体结果通过 ZeroMQ 到达 `MainServerAgentBridge`
2. 结果通过 `pending_agent_callbacks` 分发到对应的 `LLMSessionManager`
3. `trigger_agent_callbacks()` 将智能体结果注入下一轮 LLM 对话
4. LLM 随后可以在回复用户时引用智能体的发现

## 主动消息批处理

连续的未同步助手消息在记忆同步前被合并。系统区分用户驱动和主动（智能体发起）的响应，确保在记忆存储中正确归因。

## 翻译支持

`translate_if_needed()` 在用户语言与角色配置语言不同时提供自动翻译。该功能使用 `TranslationService`，依次回退到 googletrans -> translatepy -> 基于 LLM 的翻译。
