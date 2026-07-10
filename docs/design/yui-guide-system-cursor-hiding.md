# 新手教程真实鼠标隐藏排查与修改记录

## 范围

本文只记录新手教程期间“真实系统鼠标隐藏”的链路、排查结论和后续修改。涉及两个仓库：

- N.E.K.O：教程页面、Ghost Cursor、PC overlay relay 消息生产方。
- N.E.K.O.-PC：Electron preload/main、系统鼠标隐藏服务、消息消费方。

后续凡是修改新手教程期间真实鼠标隐藏、恢复、relay 协议、runId/generation 校验或系统鼠标 helper，都应在本文“修改记录”追加说明。

## 当前结论

新手教程期间真实鼠标隐藏已改为由“当前教程生命周期 + 全局透明教程覆盖层 active”共同持有。只要进入新手教程并创建了全局透明覆盖层，N.E.K.O.-PC 就隐藏真实系统鼠标；不再以 Ghost Cursor 是否存在作为隐藏前提。

## 可观察成功标准

- 新手教程进入 takeover 或教程启动后，且全局透明教程覆盖层 active 时，真实系统鼠标隐藏，Ghost Cursor 仍可显示和移动。
- 不能仅凭 NEKO-PC 日志或截图判断是否隐藏；macOS 诊断应从 Electron/Node 侧调用系统原生 API 采样真实 cursor visibility。
- 教程结束、跳过、页面卸载、强打断退出、应用退出时，真实系统鼠标恢复。
- 隐藏/恢复只作用于当前教程生命周期，不污染普通聊天、avatar 工具、独立窗口、拖拽、最小化和非教程鼠标行为。
- PC 侧拒收过期 runId/generation 的保护仍有效。

## 当前代码链路

### N.E.K.O 侧：发送隐藏/恢复请求

- `static/tutorial/yui-guide/common.js`
  - `syncPcSystemCursorHidden(hidden, reason, options)` 构造 `action: 'yui_guide_system_cursor_visibility'` 消息。
  - 消息字段包括 `hidden`、`tutorialRunId`、`reason`、`timestamp`。
  - `tutorialRunId` 从 `localStorage.yuiGuidePcOverlayRunId` 读取。
  - 通过 `window.nekoTutorialOverlay.relayToChat`、`relayToPet` 和 `appInterpage.nekoBroadcastChannel` 发送。

- `static/tutorial/core/universal-manager.js`
  - `emitTutorialStarted()` 调用 `syncPcSystemCursorHidden(true, 'tutorial-started')`。
  - 教程结束路径调用 `syncPcSystemCursorHidden(false, reason)` 恢复。

- `static/tutorial/yui-guide/director.js`
  - `setTutorialTakingOver(true)` 调用 `syncSystemCursorHidden(true, 'taking_over_started')`。
  - `syncSystemCursorHidden()` 转发到 `YuiGuideCommon.syncPcSystemCursorHidden()`。

- `static/tutorial/yui-guide/overlay.js`
  - PC overlay bridge 创建并保存 `yuiGuidePcOverlayRunId`。
  - overlay 清理时移除该 runId。

### N.E.K.O.-PC 侧：接收并执行系统隐藏

- `src/preload-common.js`
  - 暴露 `window.nekoTutorialOverlay`。
  - `relayToChat()` / `relayToPet()` 通过 Electron IPC 把教程 relay 消息送到 main 进程。

- `src/main.js`
  - `syncTutorialSystemCursorFromOverlayRelay(event, payload)` 处理教程 relay。
  - 先校验发送窗口来源必须可信。
  - 收到 lifecycle start action 时，记录当前 active tutorial cursor runId/generation。
  - 收到 `yui_guide_system_cursor_visibility` 且 `hidden=true` 时，只作为兼容的隐藏意图信号；真正执行隐藏还要求全局透明教程覆盖层已 active。
  - `tutorialGlobalOverlayService` 在覆盖层 begin/clear 时回调 main，main 用“当前教程生命周期仍 active + 当前全局透明覆盖层 active”决定是否调用 `systemCursorVisibilityService.setHidden(true)`。
  - 覆盖层 runId 可能与教程 lifecycle runId 不一致，例如 chat/pet 覆盖层重放或轮换。PC 侧不再要求二者相等才隐藏，只要求当前教程生命周期存在。
- 覆盖层替换不会恢复真实鼠标；只有覆盖层真正 clear、教程 lifecycle ended 或应用退出时才恢复。
- 收到 `yui_guide_system_cursor_temporary_reveal` 时，如果当前教程 lifecycle 和全局透明覆盖层仍 active，PC 侧会临时恢复真实鼠标；计时结束后重新检查覆盖层租约并恢复隐藏。

- `src/system-cursor-visibility-service.js`
  - macOS 使用 `osascript -l JavaScript` helper 进入 accessory 前台原生上下文后，再调用 CoreGraphics `CGDisplayHideCursor` / `CGDisplayShowCursor`。
  - macOS hide helper 会通过 `delay()` 长驻保持隐藏；恢复时 NEKO-PC kill hide helper，并另起 restore helper 多次调用 `CGDisplayShowCursor`，避免 JXA stdin 等待在退出时抛 TypeError。
  - macOS helper 输出需包含 `hidden=true;visible=false` 才能确认隐藏成功；只有 `hidden=true` 不再足够。
  - Windows 使用透明系统光标替换并在恢复时调用 `SPI_SETCURSORS`。
  - `setHidden(true)` 进入 hide，`setHidden(false)` 进入 show。

## 已确认事实

- N.E.K.O 当前会发送 `yui_guide_system_cursor_visibility` 消息。
- 该消息通常带 `tutorialRunId`。
- N.E.K.O.-PC 当前不会直接执行该消息，而是要求它属于当前 active tutorial cursor lifecycle。
- N.E.K.O.-PC 当前认可的 lifecycle start action 是：
  - `yui_guide_tutorial_lifecycle_started`
  - `yui_guide_tutorial_started`
  - `avatar_floating_guide_started`
- N.E.K.O 已在教程启动时先发送 `yui_guide_tutorial_lifecycle_started`，再发送 `yui_guide_system_cursor_visibility`。
- N.E.K.O 会在 lifecycle start / hide 前确保 `yuiGuidePcOverlayRunId` 已存在，用于给 PC 侧提供初始教程标识；后续覆盖层轮换可能产生不同 runId，PC 侧不再依赖二者一致性。
- N.E.K.O.-PC 侧最终隐藏条件不是 Ghost Cursor 存在，而是当前教程 lifecycle active 且全局透明教程覆盖层 active。
- 覆盖层 runId 与 lifecycle runId 可能不同；这种不一致不再阻止真实鼠标隐藏。
- 轻对抗机制触发台词 `interrupt_resist_light` 时，页面侧发送 `yui_guide_system_cursor_temporary_reveal`，duration 为 2000ms。PC 侧在这 2 秒内暂停重新隐藏，2 秒后若教程覆盖层仍 active 就恢复隐藏；macOS 原生采样已确认这 2 秒内 `CGCursorIsVisible() == true`，之后恢复为 `false`。
- macOS 下 `osascript -l JavaScript` 的 `console.log(...)` 在实测中可能进入 helper stderr；N.E.K.O.-PC 已将 stdout/stderr 都纳入隐藏确认解析。
- macOS 下后台 `osascript` 直接调用 `CGDisplayHideCursor` 会出现返回成功但 `CGCursorIsVisible()` 仍为 true 的情况。
- macOS helper 进入 accessory 前台原生上下文后再隐藏，实测教程启动后 5 秒内原生采样可持续看到 `CGCursorIsVisible() == false`。
- macOS helper 不应再使用 `NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile()` 维持生命周期；该 JXA 写法在 stdin 关闭时会抛 `TypeError: Object is not a function`。

## 当前怀疑点

已修复的问题：

- N.E.K.O 原先只发送系统鼠标隐藏请求，没有先发送 N.E.K.O.-PC 期望的 tutorial lifecycle start，PC 主进程可能无法建立 active runId。
- lifecycle start 过早发送时可能没有 runId，后续 overlay 生成 runId 后又与 PC active state 不一致。现在开始阶段会先确保 runId。
- macOS helper 的隐藏确认可能从 stderr 返回，旧解析只认 stdout，导致服务内部没有确认隐藏成功。
- macOS helper 在后台进程直接调用 CoreGraphics 时，`CGDisplayHideCursor` 返回成功但真实可见性未变；现在 helper 会先激活 accessory 原生上下文，并要求 `visible=false` 才确认隐藏。
- PC 侧原先要求系统鼠标隐藏 relay 的 runId 与 lifecycle runId 一致；但真实覆盖层 runId 会在 chat/pet 覆盖层之间轮换，导致隐藏意图被拒收。现在隐藏租约绑定到“教程 active + 全局透明覆盖层 active”，覆盖层 runId 不再必须等于 lifecycle runId。

## 待验证问题

- Windows / Linux 还需分别用平台原生方法补充真实可见性诊断；当前自动化真实验证闭环完成于 macOS。
- 当前仍会记录 rejected relay，用于排查旧窗口/聊天页残留消息；但诊断是否通过只看原生可见性采样。

## 建议验证点

- N.E.K.O 发送前记录：action、hidden、tutorialRunId、reason。
- N.E.K.O.-PC preload relay 记录：是否进入 `relayToChat` / `relayToPet`。
- N.E.K.O.-PC main 记录：
  - sender 是否可信。
  - action、runId、generation。
  - `isCurrentTutorialSystemCursorRelay()` 拒收原因。
  - 是否调用 `systemCursorVisibilityService.setHidden()`。
- 系统 helper 记录：macOS 是否输出 `hidden=true`，Windows 是否完成透明 cursor 替换。
- macOS 真实隐藏诊断：`scripts/diagnose-yui-guide-system-cursor.js` 在教程启动后 5 秒内采样 `CGCursorIsVisible()`，必须观察到至少一次 `false`。

## 修改记录

| 时间 | 仓库 | 修改 | 验证 |
| --- | --- | --- | --- |
| 2026-07-02 | N.E.K.O | 新增本文档，记录当前鼠标隐藏链路、怀疑点和后续修改记录位置；未修改实现代码。 | 文档新增，无运行时验证。 |
| 2026-07-02 | N.E.K.O | 新增 `syncPcTutorialLifecycleStarted()`，教程启动时先发送 `yui_guide_tutorial_lifecycle_started`，再隐藏系统鼠标；开始/隐藏前会确保 `yuiGuidePcOverlayRunId` 已创建并复用。 | `node --test-name-pattern "common helper relays PC tutorial lifecycle start before cursor visibility\|common helper creates a PC overlay run id before tutorial lifecycle start\|tutorial start activates PC lifecycle before hiding the system cursor" static/yui-guide-common.test.cjs` 通过。 |
| 2026-07-02 | N.E.K.O.-PC | 新增 `scripts/diagnose-yui-guide-system-cursor.js`，用于在教程启动后 5 秒内监控 lifecycle，并用 macOS 原生 `CGCursorIsVisible()` 判断真实鼠标是否隐藏；诊断通过不再依赖日志或截图。 | `node scripts/diagnose-yui-guide-system-cursor.js --help` 通过；初次原生诊断失败，5 秒内 `visible` 始终为 true。 |
| 2026-07-02 | N.E.K.O.-PC | 系统鼠标服务在 macOS 下同时解析 helper stdout/stderr，并将 helper 确认条件收紧为 `hidden=true` 且不能是 `visible=true`。 | `node --test test/system-cursor-visibility-service.test.js` 通过。 |
| 2026-07-02 | N.E.K.O.-PC | macOS helper 调用 `CGDisplayHideCursor` 前先进入 accessory 前台原生上下文，修复后台 helper 返回成功但真实鼠标仍可见的问题。 | 重启 NEKO-PC 后运行 `node scripts/diagnose-yui-guide-system-cursor.js --duration-ms=5000 --wait-start-ms=90000`，第一天教程启动后原生采样从约 100-200ms 起持续返回 `visible=false`，诊断 `ok: true`。 |
| 2026-07-02 | N.E.K.O.-PC | macOS hide helper 改为 `delay()` 长驻，恢复时 kill hide helper 并启动独立 restore helper 多次调用 `CGDisplayShowCursor`；移除会在 stdin 关闭时抛错的 Foundation stdin 等待写法。 | 单独 helper 启停无 JXA TypeError；最终真实诊断 `ok: true`，恢复日志为 `helper restore started` / `helper exited`，结束后原生查询 `visible=true`。 |
| 2026-07-02 | N.E.K.O.-PC | 将真实鼠标隐藏生命周期绑定到“当前教程 active + 全局透明教程覆盖层 active”；覆盖层 begin 持有隐藏租约，clear / lifecycle ended / app quit 恢复。覆盖层替换只切换 active run，不先恢复，避免教程中途闪回真实鼠标。 | 定向语法与合约测试通过；最终重启 NEKO-PC 后运行 `node scripts/diagnose-yui-guide-system-cursor.js --duration-ms=5000 --wait-start-ms=90000`，第一天教程启动后 `CGCursorIsVisible()` 采样观察到 `false`，诊断 `ok: true`；教程结束后原生查询 `visible=true`。 |
| 2026-07-02 | N.E.K.O / N.E.K.O.-PC | 轻对抗台词 `interrupt_resist_light` 触发时新增 2 秒真实鼠标临时显示：页面侧发送 `yui_guide_system_cursor_temporary_reveal`，PC 侧先恢复真实鼠标，2 秒后若教程覆盖层仍 active 则重新隐藏。 | `node --check` 覆盖相关文件；N.E.K.O 定向测试 5 项通过；N.E.K.O.-PC 主进程合约测试与系统鼠标服务测试通过。重启/重新启动第一天教程后，用独立 macOS 原生 `CGCursorIsVisible()` 采样确认：教程接管后先为 `false`；触发 `interrupt_resist_light` 后约 0-2 秒为 `true`；约 2.3 秒后恢复为 `false`。 |
