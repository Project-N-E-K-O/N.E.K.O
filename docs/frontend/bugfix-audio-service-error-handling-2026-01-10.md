# Audio Service 错误处理修复报告

**日期**: 2026-01-10  
**修复人**: Noah Wang  
**影响范围**: `@project_neko/audio-service` (Web + Native)

---

## 一、问题描述

### 1.1 现象

在 `audio-service` 的 `startVoiceSession()` 方法中，当会话启动或麦克风初始化失败时：

- ❌ **缺少错误处理**：`Promise.all()` 可能抛出异常，但没有被捕获
- ❌ **状态未更新**：失败时状态停留在 `"starting"`，而不是转为 `"error"`
- ❌ **异常未传播**：调用方无法感知失败原因，难以给用户友好提示

### 1.2 受影响的代码位置

- `frontend/packages/audio-service/src/web/audioServiceWeb.ts` (L192-216)
- `frontend/packages/audio-service/src/native/audioServiceNative.ts` (L178-204)

---

## 二、问题溯源

### 2.1 状态机设计

`AudioServiceState` 定义了完整的状态机（`src/types.ts:3-10`）：

```typescript
export type AudioServiceState =
  | "idle"       // 未初始化
  | "starting"   // 启动中
  | "ready"      // 已就绪
  | "recording"  // 录音中
  | "playing"    // 播放中
  | "stopping"   // 停止中
  | "error";     // 错误状态 ← 已定义但未使用
```

**关键发现**：`error` 状态已在类型定义中存在，但在实际实现中被遗漏。

### 2.2 下游组件已有错误事件机制

在 `src/web/mic.ts:155` 中，`WebMicStreamer` 的 worklet 错误会触发 `error` 状态：

```typescript
// worklet 侧可能回传结构化错误（{ type: "error", ... }）
if (maybe && maybe.type === "error") {
  console.error("[audio-service][worklet] process error", {
    name: maybe.name,
    message: maybe.message,
    stack: maybe.stack,
  });
  // 进入 error 状态由上层决定如何提示/恢复
  this.emitter.emit("state", { state: "error" });
}
```

**结论**：底层模块（如 `mic`）已预留错误状态机制，但上层（`audioServiceWeb`/`audioServiceNative`）在启动流程中未实现对应处理。

### 2.3 对比其他方法

在 `stopVoiceSession()` 中，状态转换是完整的：

```typescript
const stopVoiceSession: AudioService["stopVoiceSession"] = async () => {
  setState("stopping");
  await mic.stop();
  // ... 业务逻辑 ...
  setState("ready");  // ✅ 正常路径有明确的状态转换
};
```

但 `startVoiceSession()` 中缺少异常路径的状态管理：

```typescript
// ❌ 旧代码（缺陷）
await Promise.all([sessionP, mic.start(...)]);
setState("recording");  // 只有成功路径
```

---

## 三、修复方案

### 3.1 核心原则

1. **捕获异常**：用 `try-catch` 包裹可能失败的异步操作。
2. **状态同步**：失败时立即设置 `state = "error"`，触发 `state` 事件通知监听者。
3. **异常传播**：重新抛出原始异常（保留错误信息），供调用方处理。

### 3.2 修复后的代码模式

```typescript
const startVoiceSession: AudioService["startVoiceSession"] = async (opts) => {
  attach();
  setState("starting");

  const timeoutMs = opts?.timeoutMs ?? 10_000;
  const targetSampleRate = opts?.targetSampleRate ?? (args.isMobile ? 16000 : 48000);

  const sessionP = waitSessionStarted(timeoutMs);
  try {
    args.client.sendJson({ action: "start_session", input_type: "audio" });
  } catch (_e) {
    // ignore（sendJson 本身的异常属于预期内）
  }

  try {
    // ✅ 关键修复：捕获并行操作的异常
    await Promise.all([
      sessionP,
      mic.start({
        microphoneDeviceId: opts?.microphoneDeviceId ?? null,
        targetSampleRate,
      }),
    ]);
    setState("recording");  // ✅ 成功路径
  } catch (e) {
    setState("error");      // ✅ 失败路径：更新状态
    throw e;                // ✅ 传播异常给调用方
  }
};
```

### 3.3 修复的文件清单

| 文件 | 行数 | 变更 |
|------|------|------|
| `frontend/packages/audio-service/src/web/audioServiceWeb.ts` | 192-216 | 添加 try-catch + setState("error") |
| `frontend/packages/audio-service/src/native/audioServiceNative.ts` | 178-204 | 添加 try-catch + setState("error") |
| `docs/frontend/packages/audio-service.md` | 末尾 | 新增"错误处理规范"章节 + 示例代码 |

---

## 四、常见失败场景分析

### 4.1 Web 端常见错误

| 错误类型 | `error.name` | 触发场景 |
|---------|-------------|---------|
| 麦克风权限拒绝 | `NotAllowedError` | 用户点击"禁止"或浏览器策略阻止 |
| 设备不可用 | `NotFoundError` | 系统未检测到麦克风硬件 |
| AudioWorklet 加载失败 | - | Worklet 脚本 URL 错误或 CSP 策略限制 |
| 会话启动超时 | - | `waitSessionStarted()` 超过 10s 未收到 `session_started` |

### 4.2 Native 端常见错误

| 错误类型 | 触发场景 |
|---------|---------|
| 录音权限拒绝 | 用户未授予麦克风权限或系统设置禁止 |
| PCMStream 模块未就绪 | 原生模块未正确链接或初始化失败 |
| 会话启动超时 | WebSocket 连接断开或服务端未响应 |

### 4.3 通用场景

- **WebSocket 连接断开**：`waitSessionStarted()` 中的 `close` 事件监听会 resolve resolver，但后续 `mic.start()` 可能仍会失败。
- **并发调用冲突**：虽然 `waitSessionStarted()` 有复用机制，但多次快速调用 `startVoiceSession()` 仍可能导致状态混乱（建议上层防抖）。

---

## 五、调用方处理建议

### 5.1 基本错误处理模式

```typescript
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
  }
  
  showToast(message, 3500);
  audioService.detach();  // 清理资源
}
```

### 5.2 状态监听与 UI 同步

```typescript
audioService.on("state", ({ state }) => {
  switch (state) {
    case "starting":
      showLoading("启动语音中...");
      break;
    case "recording":
      hideLoading();
      showRecordingIndicator();
      break;
    case "error":
      hideLoading();
      // 可选：通过全局 Toast 提示（如果未在 catch 中处理）
      break;
    case "idle":
      hideRecordingIndicator();
      break;
  }
});
```

### 5.3 资源清理与重试策略

- **立即清理**：进入 `error` 状态后调用 `detach()`，确保麦克风资源释放。
- **重试逻辑**：可在 3s 后重新调用 `attach()` + `startVoiceSession()`（需限制重试次数，避免无限循环）。
- **权限引导**：针对 `NotAllowedError`，可引导用户手动在浏览器地址栏点击权限图标。

---

## 六、验证清单

- [x] Web 端 `startVoiceSession` 添加 try-catch
- [x] Native 端 `startVoiceSession` 添加 try-catch
- [x] 失败时设置 `state = "error"`
- [x] 异常正确传播到调用方
- [x] 文档更新（`audio-service.md`）
- [ ] 集成测试：模拟麦克风权限拒绝（需手动测试）
- [ ] 集成测试：模拟会话启动超时（需 mock WebSocket）
- [ ] 上层调用方（如 `Demo.tsx`）已有 try-catch，无需修改

---

## 七、影响评估

### 7.1 破坏性变更

**无破坏性变更**：

- API 签名未变（`startVoiceSession` 仍返回 `Promise<void>`）
- 调用方原本就应该捕获异常（这是 Promise 的标准用法）
- 新增的 `error` 状态是类型定义中早已存在的合法状态

### 7.2 向后兼容性

- ✅ **已有 try-catch 的调用方**：行为不变（现在能正确捕获异常）
- ✅ **监听 `state` 事件的调用方**：新增 `error` 状态，原有分支不受影响
- ⚠️ **未捕获异常的调用方**：现在会正确抛出异常（这是修复，不是退步）

### 7.3 受益方

- **前端 Demo 页面**（`frontend/src/web/Demo.tsx`）：已有完整的 try-catch，现在能收到准确的错误信息
- **React Native 应用**（`N.E.K.O.-RN`）：若使用了 `audio-service`，同样受益于错误状态通知

---

## 八、后续优化建议

### 8.1 短期（本次修复已覆盖）

- [x] 修复 `startVoiceSession` 的错误处理逻辑
- [x] 更新文档说明错误处理规范

### 8.2 中期（可选）

- [ ] 在 `stopVoiceSession` 中也添加 try-catch（目前假设 `mic.stop()` 不会抛出异常）
- [ ] 增强 `waitSessionStarted()` 的超时错误信息（区分"超时"和"连接断开"）
- [ ] 为 `AudioServiceEvents` 新增 `error` 事件（携带 Error 对象），与 `state:error` 互补

### 8.3 长期（架构改进）

- [ ] 引入重试机制（在 audio-service 内部自动重试，避免上层重复实现）
- [ ] 区分"可恢复错误"和"不可恢复错误"（例如权限拒绝是不可恢复的，超时是可重试的）
- [ ] 提供 `getLastError()` 方法，让上层在需要时获取详细错误信息

---

## 九、相关资源

- **修改的文件**：
  - `frontend/packages/audio-service/src/web/audioServiceWeb.ts`
  - `frontend/packages/audio-service/src/native/audioServiceNative.ts`
  - `docs/frontend/packages/audio-service.md`
- **参考文档**：
  - [MDN - getUserMedia Errors](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia#exceptions)
  - [WebAudio API 错误处理最佳实践](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Best_practices)
- **测试页面**：
  - Web: `http://localhost:5173` (运行 `cd frontend && npm run dev:web`)
  - 模拟权限拒绝：浏览器开发者工具 → Settings → Privacy → Block microphone

---

**修复完成时间**: 2026-01-10 (本地时间)  
**构建命令**: `cd frontend && npm run build` (重新构建 audio-service UMD)  
**部署建议**: 该修复属于底层基础能力，建议尽快合并并部署到测试环境验证。
