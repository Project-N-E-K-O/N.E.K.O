# TTS Client

**Package：** `main_logic/tts_client/`

TTS package 根据当前核心与音色配置解析外部语音 provider，并以统一队列契约提供 worker 函数。`LLMSessionManager` 拥有队列、启动 daemon 线程，并把生成的 PCM 转发给客户端。

原生音频 realtime 模型不使用这条外部 TTS 路径。

## 工厂契约

```python
from main_logic.tts_client import get_tts_worker

worker_fn, api_key_override, provider_key = get_tts_worker(
    core_api_type="qwen",
    has_custom_voice=False,
    voice_id="",
)
```

`get_tts_worker()` 不会创建或启动 worker 对象，而是返回：

1. 在会话管理器线程中运行的 worker 函数；
2. 该路由需要的可选 API key override；
3. 用于 key 解析与诊断的规范 `provider_key`。

不支持或不可用的选择会解析到 dummy worker，让队列报告受控错误，而不是伪装合成成功。

## Provider 选择

注册的特殊路由会按优先级先于原生/core provider fallback 求值：

1. GPT-SoVITS
2. vLLM-Omni
3. MiniMax 克隆音色
4. ElevenLabs 克隆音色
5. CosyVoice 克隆音色
6. MiMo
7. 豆包 TTS

其余选择跟随当前 core 路由，包括 Qwen/Qwen International、免费路由的 Step 或 Gemini 行为、Step、GLM/CogTTS、Gemini、OpenAI 和 Grok。Voice metadata 与当前 provider 共同决定哪条克隆路径有效；音色克隆并非 DashScope 专属。

Provider 实现位于 `main_logic/tts_client/workers/`。它们会把输出规范化为应用所需 PCM 格式，并在需要时执行 provider 专用解码与 48 kHz 重采样。

## 队列协议

管理器向 worker 线程发送以下请求项：

| 请求 | 含义 |
|---|---|
| `(speech_id, text)` | 合成一个文本片段 |
| `(None, None)` | 结束当前 utterance |
| `("__interrupt__", None)` | 丢弃/静音当前 utterance 并重置 worker 状态 |
| `("__shutdown__", None)` | 停止 worker |

Worker 返回原始 PCM chunk 或带标签的 `("__audio__", speech_id, payload)` 消息，以及 ready、error 等控制消息。`speech_id` 让管理器能够拒绝来自已中断或被替换回复的迟到音频。

## 线程与异步边界

模型文本在 asyncio 一侧产生。会话管理器分段文本并排入 TTS 请求；选中的同步 worker 在专用线程消费请求。管理器的响应任务以不阻塞事件循环的方式读取响应队列，并把获准音频转发给 WebSocket 客户端。

因此，TTS package 本身不拥有会话生命周期、WebSocket 投递或 worker 线程。

## 打断与错误

发生打断时，管理器会清理排队输出、发送 interrupt sentinel、在支持时等待 worker 确认、清理迟到 chunk，并移除待处理 speech ID。各 provider worker 能力不同：有的可取消上游请求，有的只能停止输出或把最终结果静音。该契约不保证每个远端 API 调用都能被物理取消。

启动和合成失败会通过控制/error 消息返回。管理器决定重试、展示状态、重启 worker 或禁用语音路径。用于注册音色的 HTTP 路由和克隆 `voice_id` 的持久化不属于运行时 TTS 队列。
