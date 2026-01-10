### `@project_neko/audio-service`（跨端音频：麦克风上行 + 语音下行 + 打断控制）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/audio-service`\n- **职责**：提供跨端音频服务门面（AudioService）：\n  - 麦克风采集 → 通过 Realtime 上行（`stream_data`）\n  - 服务端音频下行播放（Web: WebAudio；RN: PCMStream 原生播放）\n  - 输出/输入振幅事件（口型同步）\n  - 精确打断控制（speech_id / interrupted_speech_id）\n- **非目标**：不实现业务 UI；不内置 WebSocket 客户端（依赖注入 `RealtimeClientLike`）。\n
---

#### Public API

- `index.ts`：导出 types + `SpeechInterruptController`。\n- `index.web.ts`：导出 `createWebAudioService()`、`createGlobalOggOpusDecoder()`。\n- `index.native.ts`：导出 `createNativeAudioService()`。\n
---

#### Entry points & exports

- `package.json`：`exports["."]` 提供 `react-native` / `default`；`exports["./web"]` 提供 web 入口。\n- **设计要点**：核心 types 在 `src/types.ts`，平台差异仅出现在 `src/web/*` 与 `src/native/*`。\n
---

#### Key modules

- `src/types.ts`\n  - `AudioService`：attach/detach、startVoiceSession/stopVoiceSession、stopPlayback。\n  - `RealtimeClientLike`：抽象 websocket client（send/sendJson/on(json|binary|open|close)）。\n  - `NekoWsIncomingJson / NekoWsOutgoingJson`：音频相关的协议字段约定（轻量）。\n- `src/protocol.ts`\n  - `SpeechInterruptController`：复刻 legacy 的“精确打断”逻辑：\n    - `user_activity(interrupted_speech_id)` 触发 pending reset\n    - `audio_chunk(speech_id)` 决策 drop/allow/reset_decoder\n- `src/web/audioServiceWeb.ts`\n  - Web 端实现：\n    - 通过 `WebMicStreamer` 采集并上行\n    - 通过 `WebAudioChunkPlayer` 播放下行（支持 Blob/ArrayBuffer/TypedArray）\n    - focus 模式：播放时可暂停上行，降低回声与误打断\n    - OGG/OPUS 解码：默认尝试旧版全局 `window[\"ogg-opus-decoder\"]`\n- `src/native/audioServiceNative.ts`\n  - RN 端实现：\n    - 依赖 `react-native-pcm-stream` 录音（native 重采样到 targetRate）\n    - 下行优先假设 PCM16（ArrayBuffer/Uint8Array）并用 PCMStream 播放\n    - 通过 PCMStream amplitude/stop 事件输出振幅\n
---

#### Platform Notes（常见坑）

- **Web 下行格式**：可以是 PCM16 或 OGG/OPUS（取决于服务端与 decoder 配置）；打断时“是否 reset decoder”由 `SpeechInterruptController` 的决策驱动。\n- **RN 下行格式**：当前实现优先假设 PCM16；若服务端下发 OGG/OPUS，需额外适配（不建议在 core 做平台判断）。\n- **计时器类型**：RN/DOM lib 差异通过 `types/timers.d.ts` 兜底。\n
---

#### Sync to N.E.K.O.-RN Notes

- 该包当前尚未纳入 `N.E.K.O.-RN/scripts/sync-neko-packages.js` 默认 mapping（需要时再扩展）。\n- 若纳入同步，目标目录应视为生成物；RN 侧 `react-native-pcm-stream` 属于本仓库独立原生模块，不应被上游覆盖。\n
