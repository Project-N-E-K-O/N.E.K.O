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

## 热切换机制

热切换系统确保会话过渡零停机：

1. **准备**：当前会话处理用户输入的同时，后台使用最新的角色配置创建新的 `OmniRealtimeClient` 会话。

2. **缓存**：调用 `end_session()` 时，所有传输中的音频输出被存储在 `hot_swap_audio_cache` 中。

3. **切换**：`_perform_final_swap_sequence()` 原子性地将旧会话替换为新会话。

4. **刷新**：缓存的音频被发送到客户端，提供无缝的过渡体验。

这意味着角色可以在对话轮次之间更新人设、声音或模型设置，而用户不会感受到任何延迟。

## 音频处理

音频经过重采样流水线处理：

```
LLM output (24kHz PCM) ──> soxr resampler ──> 48kHz PCM ──> base64 ──> WebSocket
```

重采样器使用 `soxr`（高质量采样率转换）将 LLM 原生的 24kHz 输出转换为浏览器期望的 48kHz 播放采样率。

## 智能体集成

会话管理器通过回调与智能体系统协作：

1. 智能体结果通过 ZeroMQ 到达 `MainServerAgentBridge`
2. 结果通过 `pending_agent_callbacks` 分发到对应的 `LLMSessionManager`
3. `trigger_agent_callbacks()` 将智能体结果注入下一轮 LLM 对话
4. LLM 随后可以在回复用户时引用智能体的发现

## 翻译支持

`translate_if_needed()` 在用户语言与角色配置语言不同时提供自动翻译。该功能使用 `TranslationService`，依次回退到 googletrans -> translatepy -> 基于 LLM 的翻译。
