# TTS 流水线

N.E.K.O. 支持多种 TTS（文本转语音）提供商，采用统一的基于队列的架构，实现流式音频输出和实时中断。

## 架构

```
LLM 文本输出
      │
      ▼
TTS 请求队列 ──> TTS 工作线程
                           │
                  ┌────────┼──────────────┐
                  │        │              │
                  ▼        ▼              ▼
             CosyVoice  GPT-SoVITS   StepFun RT
            (DashScope)  (本地)      (WebSocket)
                  │        │              │
                  └────────┼──────────────┘
                           │
                  TTS 响应队列
                           │
                  soxr ResampleStream（24→48 kHz）
                      （状态化，按消息）
                           │
                  WebSocket ──> 浏览器
```

## 支持的提供商

| 提供商 | 类型 | 特性 |
|--------|------|------|
| **DashScope CosyVoice** | 云端 API | 高质量、语音克隆、多种音色 |
| **DashScope TTS V2** | 云端 API | 更快速、更低延迟 |
| **StepFun 实时** | WebSocket (`wss://api.stepfun.com/v1/realtime/audio`) | 实时流式、低延迟 |
| **通义千问 CosyVoice 3** | 云端 API（阿里云） | 自定义说话人声音合成、Flash 模型 |
| **GPT-SoVITS** | 本地服务 | 完全离线、可自定义、v3 API 支持 |
| **自定义端点** | 用户自定义 | 任何 OpenAI 兼容的 TTS API |

## 基于队列的流式传输

TTS 流水线使用生产者-消费者模式：

1. **生产者**（主线程）：随着 LLM 流式输出文本，完整的句子被加入 `tts_request_queue`。
2. **消费者**（TTS 工作线程）：从队列取出文本，合成音频，将 PCM 数据块加入 `tts_response_queue`。
3. **发送者**（主线程）：从队列取出音频数据块，从 24kHz 重采样到 48kHz，通过 WebSocket 发送。

重采样器使用 `soxr` `ResampleStream`，在单条消息内的音频块之间维护内部状态。每条新消息边界时重置状态，以避免跨消息伪影。

## 中断处理

当用户在角色仍在说话时开始发言：

1. LLM 提供商触发 `on_interrupt` 事件
2. 两个 TTS 队列立即被清空
3. 待处理的音频被丢弃
4. 系统准备好接收新的用户输入

## 语音克隆

用户可以通过上传约 15 秒的干净音频样本来创建自定义声音：

1. 通过 `/api/characters/voice_clone` 上传音频（multipart 表单）
2. 系统优先尝试本地 TTS（`/v1/speakers/register`）如果可用
3. 回退到阿里云 CosyVoice 云 API 进行语音克隆
4. 返回唯一的 `voice_id` 并存储在角色配置中
5. 该角色后续所有的 TTS 请求都使用克隆的声音

## 音频格式

| 参数 | 值 |
|------|------|
| LLM 输出采样率 | 24,000 Hz |
| 浏览器播放采样率 | 48,000 Hz |
| 格式 | PCM 16 位有符号小端序 |
| 声道 | 单声道 |
| 重采样器 | soxr `ResampleStream`（状态化，高质量） |

## 免费声音

N.E.K.O. 内置了无需自定义 API 密钥的音色：

| 名称 | Voice ID |
|------|----------|
| 俏皮女孩 (Playful Girl) | `voice-tone-OdVwaw2Az2` |
| 可爱女孩 (Cute Girl) | `voice-tone-OdVwrbG3No` |
| 可爱少女 (Cute Maiden) | `voice-tone-OdVx7X482K` |
| 温柔少女 (Gentle Maiden) | `voice-tone-OdVyxjm0lk` |
| 清冷御姐 (Cool Elder Sister) | `voice-tone-OdVyPmim9I` |
| 以及更多 5 种... | |
