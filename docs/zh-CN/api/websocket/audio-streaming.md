# 音频流

应用 WebSocket 的音频传输不对称：麦克风输入是 JSON 样本数组，服务端语音输出则是 JSON 头加二进制音频。

## Wire 格式

| 方向 | 传输 | 当前第一方格式 |
|---|---|---|
| 客户端 → 服务端 | JSON 文本帧 | `stream_data.data` 为带符号 PCM16 样本整数数组；单声道，由 Python 打包为小端。 |
| 服务端 → 客户端 | JSON 头 + 二进制帧 | 通常为 48,000 Hz、单声道、带符号 PCM16；第一方播放器也会识别 Ogg Opus 兼容 chunk。 |

应用路由**不接受** base64 麦克风音频，也不会为客户端输入调用 `receive_bytes()`。

## 先启动语音会话

```json
{ "action": "start_session", "input_type": "audio" }
```

收到下列事件后再发送样本：

```json
{ "type": "session_started", "input_mode": "audio" }
```

`session_preparing` 不表示就绪。启动失败时要同时处理机器可读的 `status` 和 `session_failed`。

## 麦克风输入

第一方 AudioWorklet 将 Float32 转为带符号 PCM16，桌面端重采样至 48 kHz，移动端至 16 kHz，每个 JSON 帧约 10 ms：

```json
{ "action": "stream_data", "input_type": "audio", "data": [12, -41, 203, 98] }
```

后端用小端 16 位 `struct.pack` 打包整数列表。超出有符号 16 位范围或非整数会导致打包失败并丢弃该 chunk。

不要从数组长度推断支持任意采样率。已实现的第一方路径为：

- 桌面端 48 kHz、每 10 ms 480 个样本；
- 移动端 16 kHz、每 10 ms 160 个样本。

之后 provider transport 还可按所选实时 API 的原生采样率再次重采样。

### 顺序与背压

Router 会 await `audio`、`avatar_drop_image` 和 `user_image` 以保持顺序；其他媒体可异步调度。JSON 表示便于浏览器/Electron bridge，但带宽开销高；这是本地 UI 协议，不是高效 WAN 音频传输。

游戏路由激活时，音频也会进入其实时 STT 路径。

## 降噪

降噪为可选项，由对话偏好 `noiseReductionEnabled` 控制。当前后端通过 480 样本 chunk 识别专用 48 kHz 预处理路径。启用且处理器可用时，它会缓冲、降噪、下采样后再上传 provider；预处理返回空表示缓冲不足，该帧暂不转发。

不能声称 `pyrnnoise` 始终加载，也不能声称任意 chunk 大小都会得到相同预处理。

## 语音边界检测

实时 provider 通常在收到音频流后负责语音/VAD 边界。应用还有 manager 层静音超时，可发送：

```json
{
  "type": "auto_close_mic",
  "reason_code": "silence_timeout",
  "api_type": "...",
  "message": "..."
}
```

随后结束语音会话。VAD 行为和阈值取决于所选 provider/配置；WebSocket 没有独立 VAD 配置消息。

## 服务端语音输出

每个 chunk 连续写两个帧：

1. 文本头：

   ```json
   { "type": "audio_chunk", "speech_id": "speech-id" }
   ```

2. 一个包含该 chunk 音频字节的二进制帧。

第一方解码器按到达顺序把二进制帧与头队列关联。客户端也应如此；不能把无配对二进制帧当成新语音。一个 speech 的所有 chunk 使用相同 `speech_id`，供客户端丢弃被打断轮次的迟到音频。

多数 TTS worker 用流式 `soxr` 将 provider 的 24 kHz 或其他采样率归一化为 48 kHz PCM16。这是 worker 实现细节，不代表所有 provider 原生都是 24 kHz。兼容 provider 可能返回 Ogg（`OggS`）；否则按 48 kHz 小端 PCM16 解码。

协议没有单独的 `audio_end` 帧。文本/轮次生命周期和前端队列清空共同决定完成。第一方客户端会把真实可听边界回报服务端：

```json
{ "action": "voice_play_start", "turnId": "speech-id", "source": "audio_playback" }
```

```json
{ "action": "voice_play_end", "turnId": "speech-id", "source": "audio_playback" }
```

这样主动消息不会打断已经生成但尚未播放完的音频。

## 打断

Provider 用户活动事件与客户端 `speech_id` 跟踪共同实现插话。打断时 manager 清理待处理 TTS，前端丢弃被打断 speech 的队列/迟到 chunk。已经在途的帧无法撤回，因此客户端必须继续按 `speech_id` 过滤，不能假设下一个二进制帧一定可播放。

`pause_session` 和 `end_session` 是显式停止控制；两者都会拆除当前 provider session，同时保留应用 socket。

## 语音模式中的图片

`screen`、`camera` 是实时媒体输入类型，以图片 data URL 放在 `stream_data.data` 中。`avatar_position` 可选且与新鲜图片配对。截图频率、空闲节流和来源选择是前端策略，不是固定 WebSocket 协议保证。
