# Offline Client

**Package：** `main_logic/omni_offline_client/`

`OmniOfflineClient` 是文本输入会话使用的流式聊天补全客户端。`LLMSessionManager` 在 `input_mode == "text"` 时明确选择它；Realtime 连接失败后并不会自动降级到该客户端。

公开类由 lifecycle、streaming、media、tool 和 Gemini-support mixin 组成，`main_logic.omni_offline_client` 是稳定导入路径。

## 请求模型

`connect(instructions)` 初始化内存中的 system message 与客户端状态，不会建立持久模型 socket。每次 `stream_text()` 都发起一次异步流式请求、触发回调，然后更新对话历史。

主要生命周期方法如下：

| 方法 | 契约 |
|---|---|
| `connect(instructions, native_audio=False)` | 初始化 system prompt 与历史 |
| `stream_text(text)` | 发送用户回合、流式输出可见文本并持久化已完成回合 |
| `stream_image(image_b64)` | 暂存给下一文本回合使用的图片 |
| `switch_model(model, use_vision_config=False)` | 在异步模型切换锁下替换聊天客户端 |
| `prime_context(text, skipped=False)` | 把启动/热切换上下文追加到 system message |
| `create_response(instructions, skipped=False)` | 追加持久用户消息；作为 realtime 兼容接口保留 |
| `prompt_ephemeral(...)` | 执行临时指令，但不持久化该指令 |
| `cancel_response()` / `handle_interruption()` | 停止接收活动回复的后续 chunk |
| `close()` | 关闭 HTTP/SDK 客户端并清空历史与暂存媒体 |

`stream_audio()` 和 `send_event()` 是兼容 no-op。文本模式不会在该客户端内部执行 STT。

## Provider 与多模态输入

普通路径使用项目的 chat-LLM adapter 访问已配置的 OpenAI/Anthropic 兼容 provider；符合条件的原生 Gemini 配置改走 Google GenAI SDK。实际兼容性取决于 adapter 和 endpoint，不能假定任意 OpenAI 外形的服务端都可用。

该客户端并非纯文本。`stream_image()` 会把 base64 图片加入队列，下一次 `stream_text()` 构造多模态用户消息。带图时可选择独立配置的视觉模型与 endpoint；历史中的旧图片 payload 会被淘汰以限制增长。

外部语音合成由 `LLMSessionManager` 和 [TTS Client](/zh-CN/modules/tts-client) 管理，不属于 `OmniOfflineClient`。

## 工具调用

`set_tools()`、`set_tool_call_handler()` 和 `has_tools()` 管理当前工具契约。Tool-aware streaming 支持兼容聊天格式和原生 Gemini SDK 格式；它会验证工具调用、调用管理器回调，并把结果送回模型。`max_tool_iterations` 限制连续模型/工具循环，默认值为 3。

## 异步与取消边界

- Streaming 与生命周期方法均为异步；模型替换由 `_model_switch_lock` 串行化。
- 对话历史属于单个客户端实例，不支持用并行用户回合同时修改它。
- `cancel_response()` 改变响应状态，使后续 chunk 被丢弃；它不保证所有 provider 已发出的请求都能在传输层取消。
- `close()` 还会把同步的 Gemini SDK close 操作卸载到线程，避免阻塞事件循环。

## 重试与失败行为

普通文本流对可重试的模型/网络错误最多尝试三次，并使用短退避；关闭或打断会停止后续尝试。账户、API key、配额、安全拦截、空完成、重复回复和长度限制分别通过状态/丢弃回调交给管理器呈现或恢复。

如果构造、模型切换或所有重试均失败，错误会通过回调报告或抛给会话层。客户端不会自行切换到 `OmniRealtimeClient` 或另一个 provider。
