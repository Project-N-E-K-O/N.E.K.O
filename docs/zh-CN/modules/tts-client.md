# TTS 客户端

**文件：** `main_logic/tts_client.py`（约 2300 行）

TTS 客户端通过统一的基于队列的接口处理多个提供商的文本转语音合成。

## 工厂函数

```python
from main_logic.tts_client import get_tts_worker

worker = get_tts_worker(config)
```

创建一个根据当前提供商和语音设置配置的 TTS 工作器。

## 支持的提供商

| 提供商 | 模块 | 特性 |
|--------|------|------|
| DashScope CosyVoice | 云端 | 高质量、语音克隆、流式传输 |
| DashScope TTS V2 | 云端 | 低延迟变体 |
| GPT-SoVITS | 本地 | 完全离线、可定制 |
| Custom | HTTP | 任何 OpenAI 兼容的 TTS 端点 |

## 队列架构

TTS 客户端使用生产者-消费者模式：

1. **请求队列**：会话管理器将文本句子入队
2. **工作线程**：从队列中取出文本，调用 TTS API，生成音频块
3. **响应队列**：准备好进行重采样和 WebSocket 传输的音频块

## 语音克隆流程

1. 用户通过 `/api/characters/voice_clone` 上传音频样本
2. 音频被发送到 DashScope 的语音注册 API
3. 返回一个 `voice_id` 并存储在角色配置中
4. 后续的 TTS 调用会包含该 `voice_id` 以实现个性化合成

## 打断处理

当用户打断时：

1. 两个队列都会被清空
2. 任何进行中的 TTS API 调用会被取消
3. 工作器立即准备好接受新输入
