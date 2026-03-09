# TTS 客户端

**文件：** `main_logic/tts_client.py`

TTS 客户端通过统一的基于队列的接口处理多个提供商的文本转语音合成。

## 工厂函数

```python
from main_logic.tts_client import get_tts_worker

worker = get_tts_worker(core_api_type='qwen', has_custom_voice=False)
```

创建一个根据当前提供商和语音设置配置的 TTS 工作器。

## 提供商解析

工厂函数按以下优先级选择：

1. 如果配置了 `tts_custom`：`gptsovits_tts_worker`（HTTP）或 `local_cosyvoice_worker`（WebSocket）
2. 如果 `has_custom_voice=True`：`cosyvoice_vc_tts_worker`（DashScope 语音克隆）
3. 按 `core_api_type`：路由到匹配的工作器
4. 回退：`dummy_tts_worker`

## 支持的提供商

| 提供商 | 工作器函数 | 协议 | 特性 |
|--------|-----------|------|------|
| Qwen CosyVoice | `qwen_realtime_tts_worker` | WebSocket | DashScope Realtime TTS |
| CosyVoice VC | `cosyvoice_vc_tts_worker` | DashScope SDK | 自定义语音（克隆） |
| StepFun | `step_realtime_tts_worker` | WebSocket | step-tts-2，免费模式通过 `wss://lanlan.tech/tts` |
| GLM CogTTS | `cogtts_tts_worker` | WebSocket | 智谱 CogTTS |
| Gemini | `gemini_tts_worker` | Google GenAI SDK | Google Gemini TTS |
| OpenAI | `openai_tts_worker` | HTTP | OpenAI TTS API |
| GPT-SoVITS v3 | `gptsovits_tts_worker` | HTTP→WebSocket | 本地自定义 TTS |
| Local CosyVoice | `local_cosyvoice_worker` | WebSocket | 直连本地 CosyVoice 服务器 |
| Free | `step_realtime_tts_worker(free_mode=True)` | WebSocket | 免费层通过 lanlan.tech |
| (无) | `dummy_tts_worker` | — | 空操作回退 |

## 队列架构

TTS 客户端使用生产者-消费者模式：

1. **请求队列**：会话管理器将文本句子入队
2. **工作线程**：从队列中取出文本，调用 TTS API，生成音频块
3. **响应队列**：准备好进行重采样和 WebSocket 传输的音频块

## 语音克隆流程

1. 用户通过角色 API 上传音频样本
2. 音频被发送到 DashScope 的语音注册 API
3. 返回一个 `voice_id` 并存储在角色配置中
4. 后续的 TTS 调用使用 `cosyvoice_vc_tts_worker` 和该 `voice_id`
5. 系统优先尝试本地 TTS，再回退到云端

## 打断处理

当用户打断时：

1. 两个队列都会被清空
2. 任何进行中的 TTS API 调用会被取消
3. 工作器立即准备好接受新输入
