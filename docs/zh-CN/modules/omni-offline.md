# Offline 客户端

**文件：** `main_logic/omni_offline_client.py`

`OmniOfflineClient` 提供基于文本的 LLM 对话，作为 Realtime API 不可用时的备用方案。

## 使用场景

- 所选提供商不支持 Realtime API 时
- 使用本地 LLM 部署（Ollama 等）时
- 禁用语音输入且偏好纯文本模式时

## 功能

- 文本输入、文本输出的对话
- 兼容任何 OpenAI 兼容的 API 端点
- 使用 LangChain（`langchain_openai.ChatOpenAI`）进行 LLM 集成
- 支持对话历史和系统提示词
- 独立的视觉模型配置（`vision_model`、`vision_base_url`、`vision_api_key`）
- 通过 `stream_proactive(instruction)` 生成主动消息
- 重复检测和响应丢弃处理

## 关键方法

| 方法 | 用途 |
|------|------|
| `connect()` | 初始化聊天模型 |
| `stream_audio(data)` | 接受音频输入（需要单独的 STT） |
| `stream_image(b64)` | 接受图像输入用于视觉模型 |
| `create_response()` | 生成 LLM 响应 |
| `stream_proactive(instruction)` | 生成主动（角色发起的）消息 |
| `switch_model(new_model, use_vision_config)` | 热切换到不同模型 |
| `has_pending_images()` | 检查是否有未处理的图像 |

## 与 Realtime 客户端的区别

| 功能 | Realtime 客户端 | Offline 客户端 |
|------|-----------------|----------------|
| 音频 I/O | 原生支持 | 需要单独的 STT/TTS |
| 流式传输 | WebSocket 双向 | HTTP 流式 |
| 多模态 | 原生（音频 + 图像） | 视觉模型（独立配置） |
| 延迟 | 较低（持久连接） | 较高（按请求） |
| 提供商支持 | 有限（需要 Realtime API） | 任何 OpenAI 兼容端点 |
| 主动消息 | `stream_proactive()` | `stream_proactive()` |
