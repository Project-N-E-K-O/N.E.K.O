### `@project_neko/audio-service`（跨端音频：麦克风上行 + 语音下行 + 打断控制）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/audio-service`
- **职责**：提供跨端音频服务门面（AudioService）：
  - 麦克风采集 → 通过 Realtime 上行（`stream_data`）
  - 服务端音频下行播放（Web: WebAudio；RN: PCMStream 原生播放）
  - 输出/输入振幅事件（口型同步）
  - 精确打断控制（speech_id / interrupted_speech_id）
- **非目标**：不实现业务 UI；不内置 WebSocket 客户端（依赖注入 `RealtimeClientLike`）。

---

#### Public API

- `index.ts`：导出 types + `SpeechInterruptController`。
- `index.web.ts`：导出 `createWebAudioService()`、`createGlobalOggOpusDecoder()`。
- `index.native.ts`：导出 `createNativeAudioService()`。

---

#### Entry points & exports

- `package.json`：`exports["."]` 提供 `react-native` / `default`；`exports["./web"]` 提供 web 入口。
- **设计要点**：核心 types 在 `src/types.ts`，平台差异仅出现在 `src/web/*` 与 `src/native/*`。

---

#### Key modules

- `src/types.ts`
  - `AudioService`：attach/detach、startVoiceSession/stopVoiceSession、stopPlayback。
  - `RealtimeClientLike`：抽象 websocket client（send/sendJson/on(json|binary|open|close)）。
  - `NekoWsIncomingJson / NekoWsOutgoingJson`：音频相关的协议字段约定（轻量）。
- `src/protocol.ts`
  - `SpeechInterruptController`：复刻 legacy 的“精确打断”逻辑：
    - `user_activity(interrupted_speech_id)` 触发 pending reset
    - `audio_chunk(speech_id)` 决策 drop/allow/reset_decoder
- `src/web/audioServiceWeb.ts`
  - Web 端实现：
    - 通过 `WebMicStreamer` 采集并上行
    - 通过 `WebAudioChunkPlayer` 播放下行（支持 Blob/ArrayBuffer/TypedArray）
    - focus 模式：播放时可暂停上行，降低回声与误打断
    - OGG/OPUS 解码：默认尝试旧版全局 `window[\"ogg-opus-decoder\"]`
- `src/native/audioServiceNative.ts`
  - RN 端实现：
    - 依赖 `react-native-pcm-stream` 录音（native 重采样到 targetRate）
    - 下行优先假设 PCM16（ArrayBuffer/Uint8Array）并用 PCMStream 播放
    - 通过 PCMStream amplitude/stop 事件输出振幅

---

#### Platform Notes（常见坑）

- **Web 下行格式**：可以是 PCM16 或 OGG/OPUS（取决于服务端与 decoder 配置）；打断时“是否 reset decoder”由 `SpeechInterruptController` 的决策驱动。
- **RN 下行格式**：当前实现优先假设 PCM16；若服务端下发 OGG/OPUS，需额外适配（不建议在 core 做平台判断）。
- **计时器类型**：RN/DOM lib 差异通过 `types/timers.d.ts` 兜底。

---

#### Sync to N.E.K.O.-RN Notes

- 该包当前尚未纳入 `N.E.K.O.-RN/scripts/sync-neko-packages.js` 默认 mapping（需要时再扩展）。
- 若纳入同步，目标目录应视为生成物；RN 侧 `react-native-pcm-stream` 属于本仓库独立原生模块，不应被上游覆盖。

