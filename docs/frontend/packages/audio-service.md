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

- ✅ **已同步**：该包已通过 `sync-neko-packages.js` 同步到 N.E.K.O.-RN
- **目标路径**：`N.E.K.O.-RN/packages/project-neko-audio-service/`
- **Metro 配置**：已在 `metro.config.js` 的 `extraNodeModules` 中配置路径映射
- **依赖声明**：`package.json` 中已显式声明 `vite` devDependency（修复日期：2026-01-10）
- RN 侧 `react-native-pcm-stream` 属于本仓库独立原生模块，不应被上游覆盖。

---

#### Error Handling（错误处理规范）

##### 状态机与错误状态

- `AudioServiceState` 包含 `error` 状态，用于标识服务异常。
- `startVoiceSession()` 在会话启动或设备初始化失败时，会：
  1. 设置 `state = "error"`（触发 `state` 事件）
  2. 抛出原始异常（包含详细错误信息）

##### 常见失败场景

- **Web 端**：
  - 麦克风权限拒绝（`NotAllowedError`）
  - 设备不可用（`NotFoundError`）
  - AudioWorklet 加载失败
  - getUserMedia 其他错误
- **Native 端**：
  - 录音权限拒绝
  - PCMStream 模块未就绪
  - 原生模块调用失败
- **通用**：
  - 会话启动超时（默认 10s）
  - WebSocket 连接断开

##### 处理建议

1. **调用方职责**：捕获异常并根据 `error.message` 或 `error.name` 向用户展示友好提示。
2. **状态监听**：监听 `state` 事件可实时更新 UI（例如显示"启动中" → "录音中" → "错误"）。
3. **资源清理**：进入 `error` 状态后，建议调用 `detach()` 清理资源，或重新调用 `attach()` + `startVoiceSession()` 重试。

##### 示例代码

```typescript
const audioService = createWebAudioService({ client: realtimeClient });

// 监听状态变化
audioService.on("state", ({ state }) => {
  console.log("[audio] state:", state);
  if (state === "error") {
    // UI 显示错误提示
  }
});

try {
  await audioService.startVoiceSession({ timeoutMs: 10_000 });
  console.log("[audio] 语音会话已启动");
} catch (e: any) {
  console.error("[audio] startVoiceSession failed:", e);
  
  // 根据错误类型给出友好提示
  let message = "启动语音失败";
  if (e?.name === "NotAllowedError") {
    message = "麦克风权限被拒绝，请在浏览器设置中允许访问麦克风";
  } else if (e?.name === "NotFoundError") {
    message = "未检测到麦克风设备";
  } else if (e?.message?.includes("timeout")) {
    message = "会话启动超时，请检查网络连接";
  } else if (e?.message) {
    message = `启动失败：${e.message}`;
  }
  
  // 显示 Toast 或 Alert
  showToast(message, 3500);
  
  // 清理资源（可选，视业务逻辑决定是否重试）
  audioService.detach();
}
```

