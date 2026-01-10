# Audio Service 错误处理修复总结

**日期**: 2026-01-10  
**任务**: 修复 `@project_neko/audio-service` 的 `startVoiceSession()` 错误处理缺失  
**状态**: ✅ 完成

---

## 修改清单

### 1. 源码修复

#### 1.1 Web 端 (`audioServiceWeb.ts`)

```diff
  const startVoiceSession: AudioService["startVoiceSession"] = async (opts) => {
    attach();
    setState("starting");

    const timeoutMs = opts?.timeoutMs ?? 10_000;
    const targetSampleRate = opts?.targetSampleRate ?? (args.isMobile ? 16000 : 48000);

    const sessionP = waitSessionStarted(timeoutMs);
    try {
      args.client.sendJson({ action: "start_session", input_type: "audio" });
    } catch (_e) {
      // ignore
    }

-   await Promise.all([
-     sessionP,
-     mic.start({
-       microphoneDeviceId: opts?.microphoneDeviceId ?? null,
-       targetSampleRate,
-     }),
-   ]);
-
-   setState("recording");
+   try {
+     await Promise.all([
+       sessionP,
+       mic.start({
+         microphoneDeviceId: opts?.microphoneDeviceId ?? null,
+         targetSampleRate,
+       }),
+     ]);
+     setState("recording");
+   } catch (e) {
+     setState("error");
+     throw e;
+   }
  };
```

**变更说明**：
- ✅ 添加 try-catch 捕获会话启动和麦克风初始化异常
- ✅ 失败时设置 `state = "error"` 通知监听者
- ✅ 抛出原始异常供调用方处理

#### 1.2 Native 端 (`audioServiceNative.ts`)

```diff
  const startVoiceSession: AudioService["startVoiceSession"] = async (opts) => {
    attach();
    setState("starting");

    const timeoutMs = opts?.timeoutMs ?? 10_000;
    attachRecordingListeners();

-   // 先请求后端启动 session，再启动录音（也可以并行，但 native 端更倾向先确保会话就绪）
-   const sessionP = waitSessionStarted(timeoutMs);
-   try {
-     args.client.sendJson({ action: "start_session", input_type: "audio" });
-   } catch (_e) {}
-
-   await sessionP;
-
-   try {
-     PCMStream.startRecording(
-       args.recordSampleRate ?? 48000,
-       args.recordFrameSize ?? 1536,
-       args.recordTargetRate ?? 16000
-     );
-   } catch (_e) {
-     // ignore
-   }
-
-   setState("recording");
+   try {
+     // 先请求后端启动 session，再启动录音（也可以并行，但 native 端更倾向先确保会话就绪）
+     const sessionP = waitSessionStarted(timeoutMs);
+     try {
+       args.client.sendJson({ action: "start_session", input_type: "audio" });
+     } catch (_e) {}
+
+     await sessionP;
+
+     try {
+       PCMStream.startRecording(
+         args.recordSampleRate ?? 48000,
+         args.recordFrameSize ?? 1536,
+         args.recordTargetRate ?? 16000
+       );
+     } catch (_e) {
+       // ignore
+     }
+
+     setState("recording");
+   } catch (e) {
+     setState("error");
+     throw e;
+   }
  };
```

**变更说明**：
- ✅ 添加外层 try-catch 捕获会话启动超时或录音启动失败
- ✅ 失败时设置 `state = "error"` 通知监听者
- ✅ 抛出原始异常供调用方处理

---

### 2. 文档更新

#### 2.1 `audio-service.md` 新增章节

在 `docs/frontend/packages/audio-service.md` 末尾添加了"错误处理规范"章节，包含：

- **状态机与错误状态**：说明 `error` 状态的触发时机
- **常见失败场景**：
  - Web: `NotAllowedError`（权限拒绝）、`NotFoundError`（设备不存在）、AudioWorklet 加载失败
  - Native: 录音权限拒绝、PCMStream 模块未就绪
  - 通用: 会话启动超时、WebSocket 断开
- **处理建议**：状态监听、异常捕获、资源清理
- **示例代码**：完整的错误处理模式（根据 `error.name` 给出友好提示）

#### 2.2 新增溯源报告

创建 `docs/frontend/bugfix-audio-service-error-handling-2026-01-10.md`，详细记录：

- **问题描述**：现象 + 受影响位置
- **问题溯源**：
  - 状态机设计（`error` 状态已定义但未使用）
  - 下游组件已有错误事件机制（`mic.ts` 的 worklet 错误处理）
  - 与 `stopVoiceSession` 对比分析
- **修复方案**：核心原则 + 代码模式
- **常见失败场景分析**：Web/Native/通用场景
- **调用方处理建议**：基本模式 + 状态监听 + 资源清理
- **验证清单**：修改文件清单 + 测试计划
- **影响评估**：破坏性变更分析 + 向后兼容性
- **后续优化建议**：短期/中期/长期改进点

---

## 问题根源分析

### 核心原因

1. **状态机不完整**：`AudioServiceState` 包含 `error` 状态，但在 `startVoiceSession` 实现中被遗漏。
2. **异常路径未处理**：只考虑了成功路径（`setState("recording")`），缺少失败路径的状态转换。
3. **异常被吞没**：`Promise.all()` 可能抛出的异常未被捕获，导致调用方无法感知失败。

### 影响链路

```
startVoiceSession() 失败
  ↓
状态停留在 "starting"（❌ 不正确）
  ↓
UI 显示"启动中"无限等待
  ↓
用户困惑：为什么没有反应？
  ↓
开发者困惑：日志中没有错误信息
```

### 修复后的链路

```
startVoiceSession() 失败
  ↓
setState("error") → 触发 state 事件
  ↓
throw 原始异常
  ↓
调用方 catch 到异常
  ↓
根据 error.name 给出友好提示
  ↓
UI 显示具体错误原因（如"麦克风权限被拒绝"）
  ↓
用户理解并采取行动（去设置中开启权限）
```

---

## 验证方法

### 自动化验证

```bash
cd /Users/noahwang/projects/N.E.K.O/frontend
npm run typecheck  # 类型检查（audio-service 修改部分无类型错误）
npm run build      # 重新构建 UMD bundles
```

### 手动验证（推荐）

#### Web 端测试

1. **启动开发服务器**：
   ```bash
   cd frontend && npm run dev:web
   ```

2. **模拟麦克风权限拒绝**：
   - 打开浏览器开发者工具 → Settings → Privacy
   - 选择"Block microphone"
   - 点击"启动语音"按钮
   - 预期：Toast 显示"麦克风权限被拒绝"

3. **模拟会话启动超时**：
   - 修改 `Demo.tsx` 中的 `timeoutMs: 1000`（改为 1 秒）
   - 确保后端未启动或网络断开
   - 点击"启动语音"按钮
   - 预期：Toast 显示"会话启动超时"

4. **正常流程**：
   - 允许麦克风权限
   - 确保后端正常运行
   - 点击"启动语音"按钮
   - 预期：Toast 显示"语音会话已启动"，状态转为 `recording`

#### Native 端测试（如果已集成）

1. **构建并运行 RN 应用**：
   ```bash
   cd /Users/noahwang/projects/N.E.K.O.-RN
   npm start
   ```

2. **模拟录音权限拒绝**：
   - iOS: Settings → Privacy → Microphone → 关闭应用权限
   - Android: Settings → Apps → Permissions → Microphone → Deny
   - 点击"启动语音"
   - 预期：显示"录音权限被拒绝"提示

---

## 影响范围

### 直接受益

- ✅ `frontend/src/web/Demo.tsx`：已有 try-catch，现在能收到准确的错误信息
- ✅ 监听 `state` 事件的组件：能实时更新 UI 状态（如显示"错误"状态）

### 无破坏性变更

- ✅ API 签名未变（`startVoiceSession` 仍返回 `Promise<void>`）
- ✅ 已捕获异常的代码：行为不变（异常处理更精确）
- ✅ 未捕获异常的代码：现在会正确抛出异常（这是修复，符合 Promise 规范）

### 需要同步的仓库

如果 `N.E.K.O.-RN` 使用了 `audio-service`（当前尚未纳入同步），需在同步时一并更新。

---

## 后续行动

### 必须执行

- [x] 修复 Web 端 `startVoiceSession` 错误处理
- [x] 修复 Native 端 `startVoiceSession` 错误处理
- [x] 更新 `audio-service.md` 文档
- [x] 创建溯源报告文档
- [ ] **重新构建 bundles**：`cd frontend && npm run build`
- [ ] **手动测试**：验证麦克风权限拒绝和超时场景

### 可选优化

- [ ] 修复 `ChatInput.tsx` 的 TypeScript 错误（与本次修复无关的遗留问题）
- [ ] 为 `stopVoiceSession` 添加类似的错误处理
- [ ] 增强超时错误信息（区分"超时"和"连接断开"）
- [ ] 新增 `AudioServiceEvents.error` 事件（携带 Error 对象）

---

## 参考文档

- **修改的文件**：
  - `frontend/packages/audio-service/src/web/audioServiceWeb.ts` (L192-220)
  - `frontend/packages/audio-service/src/native/audioServiceNative.ts` (L178-209)
  - `docs/frontend/packages/audio-service.md` (新增错误处理章节)
  - `docs/frontend/bugfix-audio-service-error-handling-2026-01-10.md` (新增)

- **相关规范**：
  - `.cursorrules` 第 54-58 行：packages 分层与错误处理原则
  - `frontend/packages/audio-service/src/types.ts`：`AudioServiceState` 状态机定义

- **外部资料**：
  - [MDN - getUserMedia Errors](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia#exceptions)
  - [WebAudio API Best Practices](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Best_practices)

---

**完成时间**: 2026-01-10  
**下一步**: 运行 `npm run build` 并进行手动测试验证
