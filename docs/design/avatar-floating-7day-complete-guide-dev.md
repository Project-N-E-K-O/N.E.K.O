# 7 日新手教程完整开发文档

本文是 7 日新手教程的工程落地规格，补足现有分日文档中没有逐句写清的高光、Ghost Cursor、情绪动作、跳过按钮和通用生命周期约束。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-day1-home-guide-dev.md`
- `docs/design/avatar-floating-day2-screen-voice-guide-dev.md`
- `docs/design/avatar-floating-day3-agent-guide-dev.md`
- `docs/design/avatar-floating-day4-companion-guide-dev.md`
- `docs/design/avatar-floating-day5-personalization-guide-dev.md`
- `docs/design/avatar-floating-day6-agent-guide-dev.md`
- `docs/design/avatar-floating-day7-graduation-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/design/tutorial-framework-refactoring.md`
- `docs/design/avatar-floating-guide-runtime-feature-overview.md`

## 2026-06-18 回归复盘：重置 Day 1 后模型不可见与跳过按钮穿透

### 复现流程

PC 端流程为：启动 N.E.K.O.，进入 Day 1 新手教程，点击跳过并正常退出；随后重置第一天新手教程，再重新加载首页。异常状态下，教程浮动按钮或模型旁边按钮可能仍在正确位置，但用户模型和 YUI 教程模型视觉上都不可见。早期调试阶段还出现过跳过按钮点击穿透，或按钮不穿透但点击后不触发教程销毁。

这个问题的关键判断是：模型旁边按钮位置正确不等于 Live2D 本体已渲染。按钮位置可以来自当前或上一次模型 bounds，但 PIXI stage、currentModel 和 ticker 可能没有恢复到可见/运行状态。

### 根因

1. Day 1 拆分后 scene id 改为 `day1_intro_activation` 等命名；如果启动入口仍试图按迁移前的前奏/step 顺序过滤场景，会得到空流程，表现为流程看似启动但没有可靠进入 Day 1 avatar floating guide round。
2. 教程临时模型通过 `window.handleModelReload(..., { temporaryConfig, skipIdleRestore, skipPersistentExpressions })` 切到 `yui-origin` 后，原先只恢复 DOM 容器和调用 `resumeRendering()`。普通角色切换路径在 `static/app-character.js` 会额外启动 PIXI ticker 并 `ticker.update()`；教程热切换路径缺少这一步时，就会出现容器、canvas、浮动按钮存在，但 Live2D 本体没有重新绘制。
3. 旧的教程 Live2D fade 准备态会向容器/canvas 写入 `opacity`、`visibility`、`transition`、`pointer-events` 等 inline style。reset、teardown 或异常结束时如果残留，会让后续模型加载成功但视觉仍不可见。
4. PC 端跳过按钮穿透属于 Electron Pet 输入层问题。`src/preload-pet.js` 的若干路径先判断 `point.overChatWindow === true`，再判断教程控件；外置聊天窗/教程 overlay 状态下，skip 按钮所在区域可能被归类为 overChatWindow，于是 Pet 窗口进入穿透，按钮收不到点击。
5. 2026-06-18 后续复现发现，问题不只发生在“重置 Day 1 后重启教程”：多次普通 reload、没有重新启动新手教程时，用户 Live2D 模型也可能视觉上消失。代码证据是首页初始加载链路 `static/live2d-init.js::initLive2DModel()` 在 `loadModel()` 成功后只更新 `window.LanLan1.live2dModel/currentModel` 并注册 unload cleanup，没有显式走 `showLive2d()`；而 `static/app-ui.js::showLive2d()` 原本只在 ticker stopped 时 `ticker.start()`，没有像热切换修复那样强制 currentModel/stage 可见、`ticker.update()` 和 `renderer.render(stage)`。因此普通 reload 不经过 `static/app-interpage.js::handleModelReload()` 时，吃不到 Live2D render activation，表现为按钮/位置状态可能存在，但 canvas 没有稳定绘制出模型。

### 修复契约

1. 首页 Day 1 必须优先走 avatar floating guide round。`UniversalTutorialManager.startTutorialWhenI18nReady()` 和 `startTutorial()` 在 `shouldStartHomeAvatarFloatingGuideRound()` 为真时直接调用 `startAvatarFloatingGuideRound(1, { source })`。旧 Driver.js 页面教程和迁移前 step 播放器都已删除；非首页没有每日 round 时必须返回 `false`。
2. Day 2-7 旧图片替身演出已删除，但 Live2D 模型探身保留。首页/API 设置页/记忆页仍加载 `tutorial/avatar/yui-standin.js` 和 `tutorial/avatar/standin-controller.js`，它们只保存 scene/位置/时长和调度逻辑，不再引用图片资源；`main_routers/pages_router.py` 的 static asset version 输入必须继续跟踪 `yui-standin.js`、`standin-controller.js`、`yui-stage.js` 等教程 runtime，避免浏览器继续使用旧探身逻辑。
3. 教程临时 Live2D 模型和 interpage Live2D reload 完成后都必须执行渲染激活：清除 `live2d-container` / `live2d-canvas` 的隐藏 class 和 `opacity` / `visibility` / `pointer-events` 残留；调用 `showLive2d()` 与 `live2dManager.resumeRendering()`；强制 currentModel 与 PIXI stage `visible = true`、`alpha = 1`、`renderable = true`；如果 `pixi_app.ticker` 存在，必须 `ticker.start()` 并立即 `ticker.update()`；renderer 可用时执行一次 `renderer.render(stage)`。
4. Live2D 渲染激活必须有延迟二次保险。`app-interpage.js` 的 interpage temporary reload 成功路径负责执行三次 `activateTutorialLive2dDisplay()`：立即、80ms、300ms；`UniversalTutorialManager.ensureTutorialLive2dRenderActive(reason)` 也负责同样的三次 `runTutorialLive2dRenderActivation()`，每次调用先递增 `_tutorialLive2dRenderActivationToken`，延迟回调只在 token 仍匹配时执行，新的激活周期会让旧的 80ms/300ms 回调 no-op。伪流程是：`reloadModel()` 成功 -> 立即 `showLive2d()`/`resumeRendering()`/ticker start+update/renderer render -> schedule 80ms -> schedule 300ms；Manager 侧 `ensureTutorialLive2dRenderActive()` 同样用 token 包住立即、80ms、300ms，避免 interpage reload 与 Manager 复查交错时旧回调覆盖新状态。
5. `ensureTutorialYuiLive2dVisible()` 不能只在“当前不是 YUI 模型”时补救。检测到当前已经是 YUI 模型时，也要先执行 `ensureTutorialLive2dRenderActive()` 再做 viewport placement。这个分支对应“状态是 YUI，但视觉没出来”的重启复现。
6. `TutorialAvatarReloadController.beginOverride()` 在 `reloadModel(... temporary: true)` 前后都要调用 `setPreparing(true)`。第二次看似重复，但用于挡住异步 reload 完成后到身份覆盖前的短暂竞态，不能作为“无用重复代码”删除。
7. 不要 reintroduce 旧 fade 接口：`fadeOutBeforeRestore()`、`fadeOutTutorialLive2dBeforeRestore()`、`TUTORIAL_LIVE2D_FADE_IN_MS`、`TUTORIAL_LIVE2D_FADE_OUT_MS` 都属于弃用路径。教程 reset、teardown、destroy 只做准备态 class 和 inline style 清理，不再做隐藏式淡入淡出。
8. 常规模型 reload 不要增加跨类型 UI 大兜底；但教程 temporary override 必须保留一条窄 fallback：`handleModelReload()` 失败时只在 `useTemporaryConfig === true` 的分支里调用 `loadTemporaryTutorialLive2dModel(payload)`。这条 direct Live2D fallback 是历史半成功状态的修复：旧版 `round-prelude-controller.js` 曾经 catch `beginAvatarOverride()` / `ensureVisible()` 的错误后继续教程，导致流程继续但模型没有加载。当前 prelude 已改为失败即中止教程，但 fallback 仍用于处理 `handleModelReload()` 的临时热切换失败；非 temporary reload 仍然必须 `throw error`。
9. 当前 PC 端教程输入接管以 skip 按钮坐标命中为硬保证：`N.E.K.O.-PC/src/preload-pet.js::isPointWithinVisibleTutorialSkipButton()` 命中 `#neko-tutorial-skip-btn` 时，`syncMouseThroughStateWithCursor()`、`updateDesktopAvatarToolCursorFromPoint()` 和 `startMousePoller()` 都必须在 `overChatWindow` 分支前先 `setIgnoreState(false, true)` 并返回。`handleMousePosition()` 侧只通过 `elementFromPoint()` 处理 teardown bypass 期间的 `.yui-guide-overlay` / `.yui-guide-stage` 残留，使其重新穿透，不作为通用教程输入白名单。当前代码没有独立的 `isPointWithinTutorialInputSurface()` 函数，也没有把 `[data-yui-skip-control]` / `[data-yui-emergency-exit]` 接入 Pet 主窗口坐标命中；插件页本地 skip 仍由业务页自身拦截 pointer/mouse/touch/click。PC 项目仍保留 `.driver-overlay` / `.driver-popover` 作为残留清理兼容选择器，主项目教程源码不再恢复旧 Driver.js 输入面。
10. 不要用 MutationObserver 或教程开始事件 watcher 持续强制 PC 输入接管。当前保留的方案是：skip 按钮走局部坐标命中，teardown 后残留 `.yui-guide-overlay` / `.yui-guide-stage` 只进入短期 bypass 和可见性复查；不要把全屏教程 overlay 重新变成长期不穿透区域。
11. 普通首页初始 Live2D 加载也必须接入同一套显示稳定化契约。`static/live2d-init.js` 在 `loadModel()` 成功并更新 `LanLan1` 引用后要调用 `revealInitialLive2DModelWhenUiReady('initial-live2d-load')`，等待 `app-ui.js` 注册 `window.showLive2d()` 后补触发显示；`static/app-ui.js::showLive2d()` 的快速路径和淡入路径都要执行 `scheduleLive2DDisplayActivation()`：立即、80ms、300ms 三次确认 currentModel/stage `visible/alpha/renderable`，启动 ticker，执行 `ticker.update()`，并在 renderer 可用时 `renderer.render(stage)`。这条契约保护的是“普通 reload 用户模型不稳定”，不能只在教程 temporary reload 或 interpage reload 里兜底。
12. 2026-06-18 后续不稳定复现的明确代码证据是：`Live2DManager._configureLoadedModel()` 会先创建 `live2d-floating-buttons`，后面才把 `_modelLoadState` 置为 `ready` 并释放 `loadModel()` 的 `_isLoadingModel` 锁；而 `UniversalTutorialManager.waitForFloatingButtons()` 原本只要按钮 DOM 存在就放行。结果是 Day 1 教程可能在用户模型仍处于 `preparing/applying/settling` 时调用 temporary YUI reload，触发 `Model is already loading`，再被 round prelude catch 后继续教程，形成“流程继续但用户模型/教程模型都不显示”的随机状态。修复契约是：`waitForFloatingButtons()` 必须在按钮存在后继续等待 `waitForLive2dModelLoadIdle()`；`reloadTutorialModel()` 进入 `handleModelReload()` 或 direct `loadTemporaryTutorialLive2dModel()` 前必须 `waitForLive2dModelLoadIdleOrThrow()`；`Live2DManager` 在 `_modelLoadState = 'ready'` 后派发 `neko-live2d-model-ready` 事件辅助等待。
13. 悬浮窗教程 prelude 不允许吞掉 YUI 临时切模失败。`static/tutorial/core/round-prelude-controller.js` 的 `beginAvatarOverride()` 和 `ensureVisible(sceneId)` 任一失败，都必须先 `revealPrepared()` 清理准备态，再把错误抛回 `startAvatarFloatingGuideRound()`，由 `requestTutorialDestroy('destroy')` 统一收尾。日志文案也必须是“中止教程”，不能再写“继续教程”，否则会再次出现跳过按钮、教程 overlay、模型状态彼此脱节的半启动状态。
14. 2026-06-18 追加复现：重启新手教程后点击跳过，再 reload，用户模型稳定不可见；但重启 N.E.K.O. 前后端后只 reload 正常。明确代码证据是旧版 `TutorialTerminationRouter.requestTermination()` 在 `requestAvatarFloatingGuideCooperativeEnd(finalReason)` 返回 truthy 时直接 `return`，而旧版 `UniversalTutorialManager.requestAvatarFloatingGuideCooperativeEnd()` 只设置 end reason / 清 PC overlay / invalidate interaction，然后 `return true`，没有启动 `_teardownTutorialUI()` 和 `restoreTutorialAvatarOverride()`。因此 skip 按钮的 `onSkip` Promise 会先完成，实际用户模型恢复要等 round 自己检测 `terminationRequested` 后返回；如果此时 reload，就会进入“教程已标记跳过，但用户模型恢复链路未稳定收尾”的窗口。当前修复契约是：`requestTutorialEnd(reason)` 是 Manager 统一结束入口；`requestTutorialDestroy(reason)` 只作为兼容别名；`requestAvatarFloatingGuideCooperativeEnd(reason)` 必须委托 `requestTutorialEnd(reason)`；`TutorialTerminationRouter` 必须优先调用并返回 `director.tutorialManager.requestTutorialEnd(finalReason)`，不得再消费 cooperative half-path。

15. 2026-06-18 追加复现：skip 后教程状态已结束，但桌面仍无法点击 N.E.K.O. 背后的窗口，表现为 PC 全局透明 overlay 没有销毁或仍拦截鼠标。明确代码证据是 `UniversalTutorialManager.clearPcTutorialGlobalOverlay()` 原本把 `yuiGuidePcOverlayRunId` 读取、`window.nekoTutorialOverlay.relayToChat({ action: 'yui_guide_tutorial_lifecycle_ended' })` 和 `localStorage.removeItem('yuiGuidePcOverlayRunId')` 都放在 `window.nekoTutorialOverlay.clear` 存在的分支内；如果当前页面桥接对象短暂不可用，或 `clear()` 抛错，PC overlay 结束消息和本地 runId 清理都会被跳过。修复契约是：`requestTutorialEnd(reason)` 拥有教程收口，内部必须进入 `_teardownTutorialUI()` 并调用 `clearPcTutorialGlobalOverlay(reason)`；外部调用方不要再绕过 Manager 单独半结束。`clearPcTutorialGlobalOverlay()` 必须先读取 runId，再尝试 `nekoTutorialOverlay.clear({ tutorialRunId, reason })`，无论 clear 成功、返回 stale/ok false，还是 Promise reject，都要继续广播 lifecycle-ended：`relayToChat(message)`、`relayToPet(message)`、`nekoBroadcastChannel.postMessage(message)`；最后只要 localStorage 中仍是同一个 runId，就无条件 remove。PC/外置聊天窗收到重复 `yui_guide_tutorial_lifecycle_ended` 必须幂等：标记 ended run、清 spotlight/cursor、本地 runId 和 storage，重复消息只重复清空，不创建新 run。
16. 2026-06-18 追加复现：skip 后教程结束，但鼠标仍点不到 N.E.K.O. 后面的窗口。进一步排查后不能只归因于“全局透明 overlay 未销毁”：`N.E.K.O.-PC/src/tutorial-global-overlay-service.js` 创建 overlay 时会 `overlay.setIgnoreMouseEvents(true)`，理论上透明 overlay 残留也应点击穿透；真正控制 Pet 主窗口是否吃鼠标的是 `N.E.K.O.-PC/src/preload-pet.js::setIgnoreState()`。当前代码证据是 `isPointWithinVisibleTutorialSkipButton()` 在当前鼠标命中 `#neko-tutorial-skip-btn` 时立即 `setIgnoreState(false, true)`，而教程结束后的残留 `.yui-guide-overlay` / `.yui-guide-stage` 由 `_yuiGuideTutorialInputBypassActive`、`hasVisibleYuiGuideResidualOverlaySurface()` 和 `scheduleYuiGuideTutorialInputBypassExpiry()` 让 Pet 主窗口继续穿透直到残留消失。如果 skip 后这些新教程 DOM 有残留，或 Pet 端没有收到教程结束消息，下一轮 mouse poller 可能把 Pet 主窗口改回不穿透，表现为桌面后方无法点击。修复契约是：主项目 `clearPcTutorialGlobalOverlay()` 的 lifecycle ended 消息必须同时 `relayToPet()`；PC 端 `preload-pet.js` 必须监听 `neko:tutorial-overlay-relay` / `postMessage.__nekoTutorialOverlayRelay` / `neko:yui-guide:tutorial-lifecycle-ended`，收到 `yui_guide_tutorial_lifecycle_ended` 后立即 `setIgnoreState(true, true)` 并开启教程输入面 bypass，直到下一次 `neko:tutorial-started` 或 `neko:avatar-floating-guide-started` 再恢复教程输入面接管。
17. 同一类问题还会表现为：skip 后胶囊聊天框不能点击、模型不能拖动/滚轮缩放，但点击一次锁定按钮再解锁后恢复。它们不是三个独立 bug，而是 Pet 输入区域没有从教程态刷新回普通态：锁定按钮之所以能“修好”，是因为锁按钮路径会触发模型交互状态和输入区域重新计算。修复契约是：PC 端收到教程结束后不能只持续 `setIgnoreState(true)`，否则会临时压住模型区域；必须同时 `resetModelDraggingState('yui-guide-ended')`、清理 Avatar Tool press/range/native cursor 状态、`refreshPetInputRegionsSoon()`，再延迟调用 `syncMouseThroughStateWithCursor()`，让当前鼠标所在位置重新决定是穿透到聊天窗，还是在模型/锁按钮区域接收拖动和缩放。
18. 2026-06-18 追加契约：Day 1 点击跳过按钮就是硬结束新手教程，不播放自然结束专用的模型动作、挥手、模型淡出/回归或花瓣转场。明确代码证据是 Day 1 最后一幕 `day1_takeover_return_control` 的自然完成路径会在 70% cue 触发 `PetalTransitionController.playAtCue()` / `playReturn()`，而旧版 `playReturn()` 一旦开始会继续等待 `transition.done()` 和自然结束时长；如果此时用户点击 skip，就会看到“教程已结束但还在播放收尾动作”。修复契约是：`requestTutorialEnd('skip')` 使 Director 进入 `isStopping()` 后，`PetalTransitionController` 必须立即取消等待，只调用 `transition.finish()` 清理层，不再等待 `transition.done()`；自然完成 `complete` 才允许播放 Day 1/Day 2-7 的正常收尾花瓣和模型动作。
19. 2026-06-19 追加复现：处理 review 时为了保留用户进入教程前的模型锁定状态，`restoreAvatarFloatingModelInteractionState()` 被改成“没有 `_avatarFloatingModelLockSnapshot` 就不恢复”，避免 teardown 无条件 `setLocked(false)` 把用户主动锁定的模型解锁。这个方向是正确的，但如果只在 `startAvatarFloatingGuideRound()` 抓快照，普通 `startTutorial()` 路径进入教程后再 skip / teardown / reload，就会没有可恢复的模型交互状态，重新加载后再次出现“用户模型视觉上加载不出来、按钮状态却还在”的老问题。明确代码证据是：teardown 所有结束路径都会调用 `restoreAvatarFloatingModelInteractionState()`，而快照原本只由 `snapshotAvatarFloatingModelInteractionState('avatar-floating-guide-start')` 在悬浮窗 round 内捕获。修复契约是：`startTutorial()` 在 `this.isTutorialRunning = true` 后必须统一调用 `snapshotAvatarFloatingModelInteractionState('tutorial-start')`；`restoreAvatarFloatingModelInteractionState()` 仍然保持无快照 no-op，不能退回默认空对象或默认解锁。以后凡是修改教程启动、skip、destroy、teardown、用户锁定状态恢复时，都要同时检查“是否所有启动入口都有快照”和“是否所有结束入口只恢复快照值”，否则这个模型加载/交互状态 bug 会反复回潮。
20. 2026-06-19 追加复现：PC 端点击 Day 1 跳过后，桌面只有短时间能点击穿透，随后又点不到 N.E.K.O. 背后的窗口，胶囊输入框也无法点击。Win32 hit-test 监控日志确认：可穿透阶段点击命中 `Codex` / `Code` 等后方窗口；约数秒后点击重新命中 `rootTitle="Project N.E.K.O." | proc="electron" | rect=2,2,2559,1439 | topmost=True`，不是 `N.E.K.O. Toast` 或教程 global overlay 透明窗口。因此根因不只是 PC 全局教程 overlay 残留，而是 `N.E.K.O.-PC/src/preload-pet.js` 在 `resetYuiGuidePetInputState()` 后固定 5 秒调用 `restoreYuiGuidePetInputTakeover()`，如果 `.yui-guide-overlay` / `.yui-guide-stage` 或 `body.yui-taking-over` 残留还可见，Pet 主窗口会退出教程 teardown bypass 并恢复全屏命中，表现为“短暂可穿透后又被 Pet 主窗口挡住”。修复契约是：PC 端教程结束 bypass 不能按固定 5 秒硬退出；到期时必须先检测教程残留输入面，残留仍可见时继续 `setIgnoreState(true, true)` 并按短周期复查，只有残留消失后才清 `_yuiGuideTutorialInputBypassActive`，并在恢复时 `lastIgnoreState = null` 后调用 `syncMouseThroughStateWithCursor()`，让当前鼠标位置重新决定穿透/接管。与此同时，`tutorial-global-overlay-service.clear()` 仍必须销毁全局透明 BrowserWindow，并把已关闭 runId 的 delayed `begin()` / `update()` 视为 stale，避免另一路旧 overlay 复活。
21. 2026-06-19 追加复现：Ghost Cursor 和圆角矩形高亮都位于胶囊输入框时，只要移动真实鼠标触发轻微对抗，胶囊输入框高亮就闪烁；其它流程触发对抗不闪。诊断日志确认不是胶囊 DOM rect 抖动，而是 PC 全局 overlay renderer 在同一阶段反复收到 `spotlightCount: 1` 和 `spotlightCount: 0`：外置聊天窗以 `yui-guide-chat-*` runId 发送胶囊输入框 `spotlights`，首页/主教程以 `yui-guide-*` runId 发送 cursor-only 对抗 patch，PC 主进程 `tutorial-global-overlay-service` 只有一个 `activeRunId`，于是两边轮流抢占透明窗口状态，高亮被 `spotlights: []` 覆盖后又重建。修复契约是：同一教程视觉阶段必须共用一个 canonical `tutorialRunId`；`normalizeYuiGuideBridgeMessage()` 只能按 `message.tutorialRunId -> getExistingYuiGuidePcOverlayRunId() -> message.pcOverlayRunId` 的顺序补齐已有 run，并把结果同时写回 `tutorialRunId` 与 `pcOverlayRunId`，不得调用会创建新 run 的 `getYuiGuidePcOverlayRunId()`；已有 storage/override run 必须优先于外置窗本地 `pcOverlayRunId`，避免旧 chat-owned run 覆盖 canonical run。外置聊天窗收到 guide-scoped relay 后要通过 `rememberYuiGuidePcOverlayRunId(message.tutorialRunId)` 复用 canonical run，并把该 runId 继续传给 spotlight、cursor、drag、arc、保留 spotlight 和 retry patch。`handleYuiGuidePcOverlayStaleResult()` 必须先用清晰条件判断：`isStaleResponse`、`attemptedCurrentRun`、`attemptedChatOwnedRun`、`storedCanonicalRunId`、`attemptedCanonicalRun`；只有 attempted run 是当前 chat-owned run 且 storage 没有匹配 canonical run 时，才允许 `resetYuiGuidePcOverlayRunForRetry()` 旋转，canonical `yui-guide-*` stale 永远不能旋成新的 `yui-guide-chat-*` 抢占主教程 run。该类修复要同步覆盖 `static/app-interpage.js` 的 relay/BroadcastChannel 双路径和 `static/yui-guide-common.test.cjs` 的静态契约。
22. 2026-06-22 变更：Day 2-7 旧图片替身及其静态图片资源已删除，Live2D 模型探身保留。不要恢复旧 `static/tutorial/yui-guide/avatar-standin.js` 或 `static/assets/tutorial/avatar-standins/`；`static/tutorial/avatar/yui-standin.js` 只作为位置 cue 表存在，`static/tutorial/avatar/standin-controller.js` 只负责调度 `YuiGuideAvatarStage.startAvatarCornerPeek()`。
23. 2026-06-19 追加复现：Day 6 播放“有了它们，我不光能看 B 站弹幕……”时，插件管理界面已打开，但台词结束后仍停留很久才回到首页。明确代码证据是 `runDay6PluginDashboardHandoffFlow()` 原本裸等 `waitForPluginDashboardPerformance()`；这个 handoff 的 ready timeout 为 15 秒，ready 后 execution timeout 为“语音时长 + 12 秒”，而插件面板 runtime 自己在收不到 `plugin-dashboard:narration-finished` 时还有 `budget + 30000ms` 兜底。因此只要插件窗口 `DONE_EVENT` 没及时回到主页，就会在台词结束后被长超时拖住。修复契约是：Day 6 插件面板演出必须以主页语音时长作为硬边界；主页语音时长来自 `getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())` 的录音配置时长，不读插件窗口 audio currentTime，也不按文本估算。若 voice duration 取错，会过早触发 grace 导致插件窗口演出被截断，或过晚触发导致台词结束后继续停留。`waitForPluginDashboardPerformanceUntilNarrationBoundary()` 用 `remainingNarrationMs = narrationDurationMs - elapsedNarrationMs` 到点主动发送 `narration-finished`，只给插件窗口 `DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS = 900ms` 回 `DONE_EVENT`；超时后调用 `finishPluginDashboardHandoff('plugin_dashboard_done_grace_timeout')` 并继续关闭插件窗口、收起 Agent 侧栏、恢复首页。
24. 2026-06-20 追加复现：Day 7 自然播放完成后，不刷新页面直接重置/重放 Day 4，新手教程悬浮按钮可能出现，但 YUI 模型本体不显示。它不是 Day 4/Day 7 配置问题，也不是花瓣转场单独残留；所有每日 round 都共用 `TutorialRoundPreludeController.play()` 的 `beginAvatarOverride()` + `ensureTutorialYuiLive2dVisible()` 前奏。真正缺陷是教程临时 YUI 复用了主页全局 `window.live2dManager`：Day 7 结束恢复用户模型时，如果用户原模型是 VRM/MMD/PNGTuber，`handleModelReload()` 会隐藏 Live2D 容器、暂停 ticker、清 canvas，但不会像 `Live2DManager.removeModel()` 那样清掉 YUI 的 `currentModel`、`_lastLoadedModelPath`、`modelRootPath`、`modelName`。下一轮 Day 4 启动时，`isTutorialYuiLive2dActive()` 看到旧路径仍是 `yui-origin`，`hasTutorialYuiLive2dRenderableModel()` 又只检查 `currentModel + stage + renderer`，没有排除 `model.destroyed`、已从 `stage.children` 移除、`internalModel.coreModel` 缺失、canvas/renderer view 已脱离 DOM 等不可渲染旧引用，于是误判“YUI 已可用”，跳过真正的临时模型 reload，流程继续但画布没有可显示内容。

    修复契约：`isTutorialYuiLive2dActive()` 仍只表达路径/名称是否指向 YUI，不能混入视觉判断；视觉判断必须由 `hasTutorialYuiLive2dRenderableModel()` 承担，并升级为真实可画条件：manager 存在、`currentModel` 存在且未 destroyed、`internalModel.coreModel` 存在、`pixi_app.stage/renderer` 存在且未 destroyed、model 仍挂在当前 stage 上、renderer/canvas 仍可映射到页面上的 `#live2d-canvas`。`applyTutorialLive2dViewportPlacement()` 必须先复用同一可渲染判断，再读取 bounds；不能让旧 bounds 把不可渲染对象判成 placement 成功。`ensureTutorialYuiLive2dVisible()` 只有在 activeByPath、renderable、placement 三者都成功时才走快速恢复；任一失败都必须重新 `loadTemporaryTutorialLive2dModel()`，并在直接 load 后仍不可渲染时抛 `tutorial_yui_live2d_model_missing_after_load`。教程启动入口还要在 `startAvatarFloatingGuideRound()` 开头等待上一轮 `_teardownPromise` 完成，避免自然结束事件刚派发、模型恢复还在异步执行时立即启动下一轮。

    结束清理契约：教程结束恢复用户快照后，必须清理“只属于教程 YUI 的 Live2D runtime 残留”，但不能误删用户自己的 Live2D 模型。`_teardownTutorialUI()` 在 `restoreTutorialAvatarOverride()` 完成后调用 `clearTutorialYuiLive2dRuntimeResidue()`：只有当前业务模型类型已经不是 Live2D，且 `live2dManager` 的路径/名称仍指向 `yui-origin` 时，才调用 `live2dManager.removeModel({ skipCloseWindows: true })` 或兜底销毁旧模型，并清空 `_lastLoadedModelPath/modelRootPath/modelName/currentModel`、暂停/清空 Live2D renderer、隐藏 Live2D container/canvas、移除 Live2D 悬浮按钮/锁图标/回来按钮、清理 `LanLan1.live2dModel/currentModel` 中指向旧 YUI 的引用。若用户快照本来就是 Live2D，则只恢复显示层 CSS 和 renderer，不执行 runtime 清理，避免把用户真实 Live2D 删掉。`showLive2d()` 快速路径、`clearTutorialLive2dPreparingStyles()`、`restoreTutorialLive2dDisplayState()` 仍必须清理 `yui-guide-live2d-preparing`、`yui-guide-return-petal-fade`、`--yui-guide-return-avatar-opacity`，并保持 container 不写死 `opacity: 1 !important`，canvas 可用 inline `!important` 拉回可见。
25. 2026-06-20 测试与验收方案：静态单测必须覆盖 `hasTutorialYuiLive2dRenderableModel()` 不再信任旧 `currentModel` 引用，断言它检查 `model.destroyed`、`internalModel.coreModel`、stage 挂载关系和 renderer/canvas DOM；`applyTutorialLive2dViewportPlacement()` 必须先调用该判断；`ensureTutorialYuiLive2dVisible()` 在 activeByPath 但 renderable/placement 失败时会重新加载。teardown 单测必须覆盖 `restoreTutorialAvatarOverride()` 之后调用 `clearTutorialYuiLive2dRuntimeResidue()`，且该清理函数只在非 Live2D 用户模型 + YUI 残留路径时运行，并使用 `removeModel({ skipCloseWindows: true })` / 元数据清空 / DOM 隐藏兜底。启动单测必须覆盖普通 `startAvatarFloatingGuideRound()` 也会等待 `_teardownPromise`，不能只有 `restartCurrentTutorial()` 等待。人工验收至少跑四条链路：用户原模型为 VRM 时 Day 7 完播后不刷新直接重置并播放 Day 4，YUI 必须重新加载并显示；用户原模型为 Live2D 时 Day 7 完播后播放 Day 4，不能误删用户 Live2D 且 YUI 能显示；Day 2 重启场景中按钮出现时 canvas 必须可见；skip/angry exit/自然结束后再重放任意 Day 3-7，不得出现按钮存在但模型透明、暂停或空舞台。

26. 2026-06-23 追加复现：Day 2 第一句文案可能总是走“嘿嘿，昨天听到你的声音之后...”分支，即使用户在上一天教程期间没有使用语音控制；同时音频分支和文字分支容易被历史 usage 状态污染。明确代码证据是 `static/tutorial/yui-guide/director.js` 原本只用 `hasAvatarFloatingGuideUsage('voiceUsed')` 的布尔值判断 Day 2 的 text / voiceKey / emotion，而 `neko_avatar_floating_guide_usage_v1.voiceUsed` 可能来自更早的历史使用，不一定发生在本轮 Day 1 教程启动之后。修复契约是：每日 round 启动时必须在同一个 `neko_avatar_floating_guide_usage_v1` 中记录 `dayNStartedAt`、`currentRound` 和 `currentRoundStartedAt`；语音控制使用时继续写入 `voiceUsedAt`，并在存在 active round 时写入 `voiceUsedRound`。Day 2 的 `day2_intro_context` 文字、音频和 emotion 必须统一调用同一个判断：只有 `voiceUsed === true` 且 `voiceUsedAt >= day1StartedAt` 时，才使用 `tutorial.avatarFloating.day2.introVoiceUsed`、`avatar_floating_day2_intro_voice_used` 和 `happy`；否则必须使用默认 intro 文案、默认 `avatar_floating_day2_intro` 和 `sad`。因为 Day 2 开场是 `timelinePlayback` 场景，`static/tutorial/core/scene-orchestrator.js` 的 timeline audio runtime 和 `static/tutorial/core/visual-runtime.js` 的 chat / emotion 命令也必须调用 Director 的 `resolveAvatarFloatingSceneText()`、`resolveAvatarFloatingSceneVoiceKey()`、`resolveAvatarFloatingSceneEmotion()`，不能直接读取 normalizer 带出的默认 `scene.voiceKey` / `scene.emotion`；否则会出现“台词显示正确，但音频仍播放昨天...”的分叉。不能再直接用 `voiceUsed` 布尔值决定 Day 2 分支；静态契约测试见 `tests/unit/test_avatar_floating_i18n_contracts.py::test_day2_voice_used_intro_ignores_voice_usage_before_day1_start`，timeline 回归测试见 `static/yui-guide-scene-orchestrator.test.cjs::SceneOrchestrator timeline audio uses director-resolved narration` 与 `static/tutorial-visual-runtime.test.cjs::VisualRuntime resolves timeline chat voice key and emotion through director hooks`。

### 相关代码与测试

- 主项目：`static/tutorial/core/universal-manager.js`、`static/tutorial/avatar/reload-controller.js`、`static/tutorial/core/skip-controller.js`、`static/app-interpage.js`、`static/app-ui.js`、`static/live2d-init.js`、`templates/index.html`、`main_routers/pages_router.py`、`static/yui-guide-common.test.cjs`、`tests/test_agent_rewrite_regression.py::test_home_yui_guide_avatar_override_does_not_persist_tutorial_model`、`tests/unit/test_avatar_floating_day1_round_contracts.py`、`tests/unit/test_universal_tutorial_manager_static.py`
- PC 项目：`N.E.K.O.-PC/src/preload-pet.js`、`N.E.K.O.-PC/src/tutorial-global-overlay-service.js`、`N.E.K.O.-PC/src/preload-tutorial-global-overlay.js`、`N.E.K.O.-PC/test/main-composition-contract.test.js`、`N.E.K.O.-PC/test/tutorial-overlay-z-order-contract.test.js`

## 代码入口

当前 7 日教程由以下入口共同实现：

| 能力 | 文件 |
| --- | --- |
| Day 1 首页主线 | `static/tutorial/yui-guide/days/day1-home-guide.js` |
| Day 2-7 每日 round 配置 | `static/tutorial/yui-guide/days/day2-screen-voice-guide.js` 至 `static/tutorial/yui-guide/days/day7-graduation-guide.js` |
| Round 编排、单 scene 外壳 / 旁白准备、目标解析、时序、真实点击 | `static/tutorial/core/scene-orchestrator.js`、`static/tutorial/yui-guide/director.js` |
| Timeline/Command 渐进重构基础 | `static/tutorial/core/timeline-engine.js`、`static/tutorial/core/command-registry.js`、`static/tutorial/core/script-normalizer.js`、`static/tutorial/core/visual-runtime.js` |
| 设置巡游 flow / Day4 面板巡游 schema 试点 | `static/tutorial/core/settings-tour-flow.js` |
| 操作注册表 / cursor anchor / 通用 operation 分发 | `static/tutorial/core/operation-registry.js`、`static/tutorial/yui-guide/director.js` |
| Ghost Cursor / 高光 / 花瓣视觉控制器 | `static/tutorial/visual/ghost-cursor-controller.js`、`static/tutorial/visual/spotlight-controller.js`、`static/tutorial/visual/petal-transition-controller.js`、`static/tutorial/visual/highlight-controller.js`、`static/tutorial/visual/controllers.js` |
| PC 全局视觉 overlay / 浏览器兜底 renderer | `static/tutorial/yui-guide/overlay.js`、`static/tutorial/visual/overlay-renderer.js` |
| 跨窗口 command bus / target registry / chat adapter | `static/tutorial/core/bridge-command-bus.js`、`static/tutorial/core/target-geometry-registry.js`、`static/tutorial/core/chat-window-adapter.js`、`static/app-interpage.js` |
| scoped resources / guide helper 聚合入口 | `static/tutorial/core/scoped-resources.js`、`static/tutorial/core/guide-helpers.js`、`static/tutorial/yui-guide/common.js` |
| 对抗、暂停、终止路由 | `static/tutorial/visual/resistance-controllers.js` |
| 接管、外置聊天窗同步 | `static/tutorial/core/interaction-takeover.js`、`static/app-interpage.js`、`templates/chat.html` |
| 跳过按钮 | `static/tutorial/core/skip-controller.js`、`static/tutorial/core/universal-manager.js` |
| 每日启动准备 / 临时切换教程模型并恢复 | `static/tutorial/core/round-prelude-controller.js`、`static/tutorial/avatar/reload-controller.js`、`static/tutorial/core/universal-manager.js` |
| React 聊天窗真实工具按钮 | `frontend/react-neko-chat/src/App.tsx`、`static/app-react-chat-window.js` |
| PC 全局透明教程 overlay（PC 端唯一视觉层） | `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`、`N.E.K.O.-PC/src/tutorial-global-overlay-service.js`、`N.E.K.O.-PC/src/preload-tutorial-global-overlay.js`、`N.E.K.O.-PC/src/preload-common.js`、`N.E.K.O.-PC/src/main.js` |

所有选择器里的 `${p}` 都由 `YuiGuideDirector.resolveElement()` 按当前悬浮 UI 前缀展开。

## 重构后框架状态（2026-06-15）

七日新手教程框架已经从“Director/Overlay 中集中写大量命令式流程”收敛为“每日配置 + SceneOrchestrator 编排 + 专用 controller/flow/registry 承接重复能力”的结构。当前主线仍保持原有 `round.scenes`、台词、真实点击和完成态语义，不要求一次性把七天教程改成纯声明式 schema。

2026-06-15 起，Timeline/Command 重构基础层已进入代码：`TutorialCommandRegistry` 负责 command 派发，`TutorialScriptNormalizer` 负责把旧 scene 映射成 `TimelineScene.timeline[]`，`TutorialTimelineEngine` 负责时间点、blocking command、pause/resume、afterAudioEnd 和 run token 调度，`TutorialVisualRuntime` 负责把视觉/operation command 转发到现有 Director/controller 边界。`SceneOrchestrator` 已支持显式 `scene.timelinePlayback === true` 的播放路径；当前 Day 1-7 正式 `round.scenes` 大多进入 timeline playback，Day 1 的 `day1_intro_greeting` 例外：PC 端不需要音频激活，首句问候必须走普通 scene 分支以复用外置聊天窗 target/cursor/spotlight 逻辑，只把 hug/gift 演出挂到 `day1-intro-greeting-performance` operation。已迁移收尾继续复用现有花瓣 cue 清理路径，`settingsTour.play` 已作为 SettingsTourFlow 的 timeline runtime 入口，`settingsPanel.close` 负责只关闭设置面板/侧栏、不隐藏 Ghost Cursor，Day 1 activation/猫爪和 Day 6 插件等复杂 scene 通过 operation command 回调现有专用 flow。设置巡游 scene 必须显式写 `timeline[]` 调用 `settingsTour.play`，避免 normalizer 自动旁白和 SettingsTourFlow 旁白重复播放。Timeline/Command 是一套共享框架，不按每天复制 engine；Day 1-7 正式新手教程的时序、cue、跨窗口 handoff 和旁白结束等待只能使用当前 `voiceKey` 的真实音频播放快照或 `GUIDE_AUDIO_DURATIONS_BY_KEY` 实测时长，不得再按台词文本长度估算旁白时长。

重构后的主要边界如下：

| 边界 | 当前职责 | 优化效果 |
| --- | --- | --- |
| `SceneOrchestrator` | 统一 round setup、scene loop、scene prelude、通用 scene core、generic narration/cursor/operation/finalize。 | Director 不再直接拥有所有 scene 外壳，新增普通 scene 时只需要沿用统一编排。 |
| `SettingsTourFlow` | 统一 Day 2/4/5 设置巡游的 narration、scene guard、panel ellipse、finalize；Day4 chat/model 已完成最小 schema 试点。 | 设置类 scene 不再散落重复 guard、面板巡游和收尾逻辑，Day4 面板巡游可通过 schema 描述。 |
| `OperationRegistry` | 通过 exact / prefix / predicate 注册 Day 1/3/4/6、settings、compact、avatar tool 等 operation。 | operation 分发从长 if 链变为注册表，新增或调整单个 operation 更可控。 |
| 视觉 controller / renderer | Ghost Cursor、Spotlight、PetalTransition、Highlight primitive、PC overlay complete-state store 和浏览器兜底 spotlight 渲染分层。 | cursor/spotlight/petal 在 PC overlay 中按独立通道更新，skip/destroy/pagehide 统一清理同一个 run。 |
| bridge / registry / adapter | 外置聊天窗 command bus、目标几何 registry、本地/外置聊天窗 adapter、非教程广播 sender。 | 跨窗口 spotlight/cursor/身份/头像消息不再各处手写，`cursorAction: move/click` 都能等待 settled anchor。 |
| scoped resources / lifecycle | listener、timeout、interval、raf、pagehide 临时清理和生命周期 token/end reason 收口。 | 重复启动、快速 skip、跨页 handoff 后更不容易遗留旧监听或旧 timer。 |

已完成的重构阶段以「新手教程框架重构方案」为准：Phase 10-14 完成视觉层/公共模块文件级拆分，Phase 15 完成 `SettingsTourFlow` 实体迁移，Phase 16 完成设置巡游内部 helper 收敛，Phase 17 完成 Day4 chat/model 面板巡游 schema 试点。

当前不建议继续做的大改：

1. 不建议把 7 天全部 scene 一次性改成纯声明式 schema；差异行为仍需要手写 handler escape hatch。
2. 不建议把教程业务流程搬进 PC overlay；PC overlay 只负责 cursor、spotlight、petal 渲染和透明窗口生命周期。Day 2-7 旧图片替身已删除，不应再引入本地替身 DOM 或 PC overlay 图片层；Live2D 模型探身必须回到首页 Live2D runtime。
3. 不建议删除 `window.TutorialHighlightController.createController()` 兼容入口；正式每日教程仍通过统一视觉 controller 使用该入口，`tutorial/avatar/floating-guide-reset.js` 入口脚本仍存在，但其中的 reset 专用步骤表 / player 已废弃并删除。

发布前还需要补真实长链路验收记录，见本文末尾“验收清单”和「新手教程各功能与框架能力说明」。

## PC 全局透明 Overlay 要求

七日主线在 PC 端的可见 Ghost Cursor 和教程高光必须由 N.E.K.O.-PC 全局透明教程 overlay 渲染。业务窗口只保留目标解析、真实 UI 点击、台词、音频播放、emotion、operation 和完成态写入。网页端或 PC 全局 overlay 不可用时，`YuiGuideOverlay` 只允许作为首页内的浏览器调试兜底 spotlight/petal renderer，不得恢复本地 Ghost Cursor、本地聊天窗高光或本地替身 DOM。每日收尾花瓣是例外：首页在发送 PC overlay `petal` patch 的同时保留 DOM fallback，避免 PC 端能力声明或渲染异常导致 Day 7 毕业花瓣转场完全消失。

导演约束：

1. PC 端全局 overlay 是 cursor 和高光的唯一教程视觉来源；Pet 页面、聊天窗、Agent HUD 和插件页不再各自叠加教程 cursor、高光或模型替身 DOM，花瓣只允许首页保留收尾兜底。旧图片替身已删除；Live2D 模型探身只由首页 Live2D runtime 渲染。
2. 所有目标矩形统一转换为 screen 坐标后发送给 PC overlay；overlay 再按 display bounds 渲染到对应透明窗口。
3. overlay 始终点击穿透，skip、真实按钮点击和教程接管白名单仍由原页面和 Manager 处理。
4. 如果 PC bridge 不可用、IPC 超时或运行在网页端，只能回退到首页 `YuiGuideOverlay` 的浏览器 spotlight/petal 兜底，不阻塞教程；聊天窗和插件页仍不得创建本地 cursor、高光或替身层。
5. Day 1-7 每日收尾都复用同一套收尾 cue：收尾台词期间重新高亮分日指定的收尾目标（当前 Day 1-7 收尾均回胶囊输入框或独立聊天窗输入区），约 70% 同步隐藏 Ghost Cursor、清理高光并播放花瓣。
6. Ghost Cursor 在每日教程开始后保持可见，直到收尾语音播放完再消失；所有位置变化都必须走平滑移动动画，只允许记录和复用可见锚点。
7. Day 1-7 每日教程进入第一幕前必须先启动模型脸部/目光对 Ghost Cursor 的持续跟踪；整个 round 内不因 scene 切换停止，直到正常收尾、skip、angry exit 或 destroy 清理。教程期 look-at 直接通过首页 Director 读取 `YuiGuideOverlay.getCursorPosition()`，等同于教程期间把“跟踪真实鼠标”替换成“跟踪 Ghost Cursor”。PC 全局 overlay 接管时，首页 `YuiGuideOverlay.getCursorPosition()` 也必须随远端 Ghost Cursor 移动动画逐帧更新，并使用与 PC overlay 可见 cursor 一致的 `cubic-bezier(.22,1,.36,1)` 进度，不能只在动画结束后跳到终点，也不能用线性镜像导致模型脸部和屏幕 cursor 缓动错位。
8. PC 全局 overlay 的每次 update 都必须携带当前完整可见状态；spotlight refresh 不能漏掉已可见的 cursor，cursor move/click 也不能漏掉已存在的 spotlight 或 petal，否则远端渲染层可能在 Day 2 设置按钮、主动搭话开关等同屏指认场景中交替清空图层并闪烁。
9. 普通 scene 切换、外置聊天窗收口、临时面板关闭和插件页/首页 handoff 只允许清理对应 spotlight、panel 或业务窗口本地状态，不得清空 PC 全局 overlay 的 Ghost Cursor。`clearExternalizedChatGuideTarget({ clearCursor: true })`、`setExternalizedChatCursor('')`、`cursor.hide()` 或等价 PC overlay clear/hide 只允许用于 skip、生气退出、destroy/stop 和收尾花瓣 cue。
10. 外置聊天窗目标的 `cursorAction: 'move'` 和 `cursorAction: 'click'` 都必须等待外置窗口回传的 screen anchor；不能只在 click 场景等待 movement promise。否则跨窗口 move 会被主流程提前跳过，Ghost Cursor 会停在上一句按钮位置，典型表现是 Day 1 最后一句停在【键鼠控制】开关。
11. Day 1-7 任意一天触发 skip、angry exit、destroy、pagehide 或自然结束，都必须进入 `UniversalTutorialManager.requestTutorialEnd(reason)`，并复用 `clearPcTutorialGlobalOverlay()` / `clearAllTutorialLifecycles()` 这条公共路径销毁 PC 全局透明 BrowserWindow；每日 scene 不允许各自手写 overlay clear。`requestTutorialDestroy(reason)` 只作为旧入口兼容别名，最终也必须委托到 `requestTutorialEnd(reason)`，避免点击 skip 或 reload/pagehide 后透明窗口、模型覆盖和输入状态半清理。
13. PC 主进程 `tutorial-global-overlay-service` 在 `clear()` 后必须记录已关闭的 `tutorialRunId`，并拒绝同一 runId 后续 delayed `begin()` / `update()`，返回 stale。否则 skip 后残留异步 cursor/spotlight patch 会把透明 BrowserWindow 重新创建出来，表现为桌面短暂恢复可点后又被全局透明 overlay 挡住。
14. 首页、外置聊天窗和插件页向 PC 全局 overlay 写入同一个教程视觉阶段时，必须共享同一个 canonical `tutorialRunId` 和同一个 sequence 生成器；不能让 spotlight 用 `yui-guide-chat-*`、cursor-only 对抗用 `yui-guide-*`。PC overlay 的 spotlight 与 Ghost Cursor 渲染函数虽然分开，但它们共享同一个透明 BrowserWindow、`activeRunId` 和完整状态 payload；任一窗口使用不同 runId 抢占 active run，都会让另一通道的状态被旧缓存或空数组覆盖，典型表现是胶囊输入框圆角高亮在真实鼠标对抗期间闪烁。

PC overlay 的回归验收必须覆盖：聊天窗输入区到模型旁按钮的跨窗口平滑移动、圆形按钮高光、聊天窗圆角矩形高光、每日收尾花瓣 cue、对抗暂停恢复，以及 skip / angry exit / 自然结束后的透明窗口销毁。

## 通用生命周期硬要求

每一天教程启动后，都必须完整接入 `home-yui-guide-lifecycle-modularization.md` 抽出的通用模块，不允许在每日 scene 里复制这些生命周期逻辑。

Day 1-7 的跳过、生气退出、自然结束、destroy/stop 和 pagehide 共享同一个 PC 全局透明 overlay 清理口：`UniversalTutorialManager.requestTutorialEnd(reason)` 先设置结束原因，再调用 `clearAllTutorialLifecycles(reason)` 与 `clearPcTutorialGlobalOverlay(reason)`，随后等待 driver destroy / `onTutorialEnd()` 的完整生命周期。这个方法会读取 `yuiGuidePcOverlayRunId` 并调用 `window.nekoTutorialOverlay.clear({ reason, tutorialRunId })`；PC 端随后销毁全局透明 BrowserWindow、停止置顶重申、恢复 Pet 窗口点击穿透，并把同一 runId 的后续 delayed begin/update 当作 stale 拒绝。任何每日配置、scene handler、外置聊天窗或插件 handoff 都不得绕过这条通用生命周期。

每日 round 的导演入口不得为了聊天窗 surface ready 阻塞启动。需要聊天窗作为锚点的 scene 在自己的播放流程里按需 `ensureChatVisible()`，并通过 `NekoHomeTutorialFeatureController.enforce('avatar-floating-dayN-surface-ready')` 重新压一次教程期间的功能禁用状态，随后再建立对应 spotlight。这样可以避免模型切换完成后、首个 scene 开始前出现额外空档。

教程接管期间必须严格禁用主动搭话、主动视觉、主动音乐/表情包/小游戏邀请等 proactive 功能，以及 Galgame 模式。禁用不是 UI 文案约定，而是运行时约束：`begin` 负责快照并关闭，`enforce` 负责在聊天窗打开后再次关闭，`end` 只在教程正常结束、skip、angry exit 或 destroy 清理时恢复快照。

每日启动/久别重逢 greeting 不再通过 WebSocket `home_tutorial_state` 或后端 TTL guard 压制。启动时 `app-websocket.js` 只把 `greeting_check` 标记为 pending，不立刻发送；`UniversalTutorialManager.dispatchStartupGreetingRelease(reason)` 负责派发 `neko:startup-greeting-release` 作为唯一放行信号。教程正常结束、skip、angry exit、destroy/pagehide 等生命周期出口必须先走统一清理，再发 `tutorial-completed`、`tutorial-skipped` 或对应 destroy reason 放行；如果 7 天教程已经结束、当前页面不是首页、没有 pending round、缺少按钮锚点或 round 未注册，Manager 也必须立即发 no-tutorial release，让 `greeting_check` 回到原来的久别重逢判断链路。旧的 `home_tutorial_state`、`blocking_greeting` 和后端教程 greeting guard 已废弃，后续不要重新引入。

| 模块 | 每日教程期间必须生效的行为 |
| --- | --- |
| `TutorialInteractionTakeover` | 教程接管期调用 `setTutorialTakingOver(true)`；只放行 skip、当前演示目标、系统弹窗和必要的真实 UI；外置聊天窗同步禁用/恢复按钮、同步 spotlight/cursor。 |
| `TutorialHighlightController` | 所有圆形、矩形、union、extra、virtual、precise 高光都经由 Director 包装方法调用；对抗暂停期间 mutation 保持上一帧，不销毁、不重建；scene 切换、skip、destroy、angry exit 时统一清理。 |
| `ResistanceController` | 接管期 Ghost Cursor 始终监听真实鼠标移动；真实鼠标有有效移动时先做轻微反方向对抗位移。首页 Director 与 Day 6 插件管理窗口的台词打断统一沿用猫形态眩晕的持续摇晃算法，但不要求按住鼠标主键，并使用更高阈值：1.1 秒窗口内至少 8 次方向反转、反转首尾跨度至少 600ms、反转段持续速度至少 1100px/s，并忽略短于 50px 的移动段；满足后触发一次 `interrupt_resist_light`。轻微对抗通过 `PauseCoordinator` 暂停 cursor/spotlight，抵抗动画结束后恢复原目标移动；第 3 次升级为 `interrupt_angry_exit`。angry exit 触发瞬间清理高光和 cursor，生气台词/演出音频结束后再走 skip 语义，不写完成态。 |
| `TutorialSkipController` | `#neko-tutorial-skip-btn` 在教程全程可见且可点；点击后立刻进入 `handleTutorialSkipRequest()`，再由 Manager 直接调用 `requestTutorialEnd('skip')`。Director、插件页、外置聊天窗和 pagehide 只能回到这个统一结束入口，不能再各自走半套 skip/destroy。 |
| `TutorialAvatarReloadController` | 教程开始临时切到 `yui-origin`；正常完成、skip、angry exit、destroy、pagehide、handoff 失败都必须恢复用户原模型和聊天头像身份。切模完成后不得生成教程聊天头像截图，不得先播放常驻 idle/sway 模型动作，也不得先套用 `yui-origin` 的 `常驻/swz` 表情，必须直接进入每日 round 的台词、look-at 和 scene 时序。 |
| `ReactChatWindow` | Day 1-7 教程接管期间必须禁止用户点击胶囊输入框进入输入态，并禁用聊天窗内各功能按钮（发送、导入图、截图、翻译、点歌、Galgame、历史小蓝条、工具轮按钮等）；教程输入锁或按钮锁生效时，已展开的胶囊输入态要收回默认态，教程演示仍可通过 Director 高亮、移动 Ghost Cursor 和 host API 指认或打开目标。 |

切模并确认 `yui-origin` 可见后，Day 1-7 必须显示等待 1500ms，再进入每日 round 台词播放；除此之外不得再插入其他固定等待。教程期间不再抓取聊天头像截图，且胶囊输入框和聊天窗各功能按钮都禁止用户点击。Day 1-7 都不得在每日 round 启动前预热或等待聊天窗 surface ready；需要显示聊天窗的场景由各自 scene 内部打开。Ghost Cursor look-at 等准备链路必须尽量与第一句启动并行。

所有可见 Ghost Cursor 动画原则上都必须进入 N.E.K.O.-PC 全局透明教程 overlay。正式教程由首页 Director 通过 `YuiGuidePcOverlayBridge` 驱动；【记忆浏览】重置入口只负责清理对应 day 状态、标记 pending/manual reset 和清首次提示标记，不得立即启动教程，也不再自行播放 `tutorial/avatar/floating-guide-reset.js` 的 reset 专用步骤表。用户刷新 Neko 后，正式 7 日教程流程统一读取 pending day 并启动对应 day。外置聊天窗只把聊天窗内目标解析成 screen cursor / spotlight patch 发送到 PC 全局 overlay，并继续向首页回传 `yui_guide_chat_cursor_anchor`；不得渲染任何本地 Ghost Cursor 或本地高光。插件页等子窗口长期目标也应采用同样的全局 overlay / 锚点回传策略，避免同一时刻出现两套教程视觉层；但当前插件管理页没有接入跨窗口 overlay bridge，Day 6 插件 dashboard handoff 临时由插件页 runtime 自绘 pointer，并要求首页/PC 全局 cursor 在插件页接管期间先隐藏、插件页关闭后恢复。花瓣转场不是 Ghost Cursor，本地 DOM fallback 可与 PC `petal` patch 同时存在，用于保证最终收尾视觉必达。
连续型 Ghost Cursor 动画也遵守同一规则：`runEllipseAnimation()`、抵抗回弹、轻微对抗和类似逐帧动画长期目标必须直接发送到全局透明 overlay；业务窗口不得创建本地 cursor shell、拖尾、点击星星或图片 cursor 作为 fallback。当前 Day 6 插件 dashboard 是临时例外：插件页 runtime 只允许创建简化 pointer，不得恢复旧图片 cursor、拖尾或点击星星效果。

普通清理流程必须是 cursor-preserving：`prepareAvatarFloatingScene()`、`cleanupBefore`、外置聊天窗 spotlight 清理、插件页 handoff 前的首页收口和设置/Agent 面板关闭，都只能让旧目标失焦，不能让 Ghost Cursor 视觉层消失或把当前位置重置成默认点。跨窗口接力时，源窗口只清自己的业务状态或目标注册，PC 全局 overlay 必须继续保留上一帧可见位置，目标窗口解析出新 screen 坐标后再从该位置平滑移动过去。

PC 全局透明教程 overlay 的 BrowserWindow 必须在 active run 期间保持 `screen-saver` 级别并轻量 `moveTop()` reassert，重申间隔不得高于 160ms，确保 Ghost Cursor、高亮框和花瓣在长距离移动期间也始终压过 Pet 主窗口里的模型按钮。教程 clear、skip、angry exit、destroy 或 stop 后必须停止 reassert 并销毁透明 BrowserWindow；`preload-common` 收到 `neko:yui-guide:tutorial-lifecycle-ended` 时也要兜底调用 bridge clear，PC 主进程 clear 后通过 `onClear` 恢复 Pet 窗口点击穿透并通知 Pet preload 清理教程状态和必要的兜底 DOM。业务窗口不再提供本地 Ghost Cursor fallback。

### 跳过按钮始终有效

1. `#neko-tutorial-skip-btn` 必须保持 `position: fixed`、最高层级、`pointer-events: auto`，不能被 overlay、花瓣层、插件 handoff 蒙层遮住。
2. `isAllowedTutorialInteractionTarget()` 必须始终把 skip 按钮列为白名单；外置聊天窗或插件页需要转发 skip 时，必须回到首页 Manager 的统一入口。
3. 首次点击后可以禁用按钮防重复提交，但禁用只能发生在 skip 请求已经进入 `handleTutorialSkipRequest()` 之后；不能出现“按钮可见但点击无效”的窗口。
4. skip 期间必须立即停止后续 scene 进展、停止 Ghost Cursor 动画、清理当前高光；不得等待当前台词自然播放完才响应。
5. 插件页本地 skip 控件即使已经触发过 skip，也必须继续拦截 pointer/mouse/touch/click，避免点击穿透到底层页面。

## 高光不重叠原则

1. 每个时刻最多保留一套 primary spotlight；需要 persistent/secondary 时，必须和 primary 框选的 DOM 不相交。
2. 同一目标不能同时出现 action spotlight、extra spotlight、virtual spotlight 或 CSS precise highlight。
3. 大区域和小按钮不能同时框住同一层级。例如聊天窗 composer 区和 composer 内的 Avatar 工具按钮不能同时高亮；必须先清理 composer 区，再切到按钮。
4. 设置弹窗内不允许同时高亮整个弹窗和侧边栏按钮；需要说明弹窗时只高亮侧边栏容器，需要说明开关时只高亮开关本体。
5. Day 3 Avatar 工具阶段只高亮 Avatar 工具按钮，不高亮工具菜单前三个 `.composer-icon-button[data-avatar-tool-id]`；小游戏三个选项如真实出现，只允许圆形高光，不能使用猫耳、猫爪或第二层外框。
6. 收尾 scene 是唯一允许重新回到聊天窗大区域或分日指定收尾目标的阶段；进入收尾前必须先清掉当天临时菜单、按钮和侧边栏高光。

## 情绪与动作有效性

教程期间使用 `yui-origin` 模型。每句台词必须声明 emotion，并且只从有效动作池中取动作：

| emotion | 有效用途 | 动作要求 |
| --- | --- | --- |
| `happy` | 欢迎、邀请、撒娇、收尾、鼓励尝试 | 从 happy motion 池随机；不得覆盖 cursor lookAt、真实点击演出、花瓣收尾。 |
| `neutral` | 规则说明、安全边界、隐私、存储 | 从 neutral motion 池随机；动作幅度应小，不抢设置或 HUD 巡游注意力。 |
| `surprised` | 发现入口、冒险感、慌乱前奏 | 从 surprised motion 池随机；Day 5 慌乱 scene 由 `settings-peek-panic` 自定义动作优先。 |
| `sad` | 轻微委屈、未听过声音的承接 | 从 sad motion 池随机；不能升级成 angry exit。 |
| `angry` | 傲娇、强打断、生气退出 | 普通台词可用 angry 池；`interrupt_angry_exit` 必须使用自定义 angry exit 演出，并覆盖正在播放的教程动作 session。 |
| `Idle` | 等待用户选择、低强度停顿 | 只用于无强演出的等待态。 |

如果 motion 资源不存在或当前模型动作锁被自定义演出占用，运行时必须降级为表情或 Idle；不能因为动作缺失阻塞台词、高光、cursor 或 skip。

## 待实现方案：每日第一句通用挥手放大演出

Day 1 第一句台词 `day1_intro_greeting`（“微风、阳光，还有刚刚好出现的你...”）当前使用的模型演出不是分日 scene 内联逻辑，而是首页 Director 通过 `day1-intro-greeting-performance` 调到 `YuiGuideAvatarStage.playIntroGreetingHug()`。该演出实际完成三件事：播放 YUI 的欢迎表情/挥手参数，按 Live2D frame scale / frameY 让模型向用户靠近放大，并在结束时提交最终靠近位置；后续不要复制这段姿态和 frame 算法。

本节是每日首句动作抽象的历史过渡方案。实现“首句动作与小剧场共用 Live2D Motion Core”后，`daily-intro-greeting-performance` 只作为兼容入口保留；正式首句配置应使用 `introAvatarPerformance` 固定 preset，并通过 `daily-intro-avatar-performance` / `YuiGuideAvatarStage.playAvatarMotion()` 播放。不要再把 Day 2-7 新 scene 接回单一“挥手放大” operation。

目标是把这段“第一句欢迎挥手放大”抽象为 Day 1-7 通用能力，挂到每天真正播放的第一句台词上：

| Day | 第一句 scene | 是否与现有 Live2D 探身 cue 冲突 |
| --- | --- | --- |
| 1 | `day1_intro_greeting` | 否；`day1_intro_activation` 是前置激活，不算每日第一句台词。 |
| 2 | `day2_intro_context` | 是；当前 cue 表有 `day2_intro_context` 探身，实现时必须删除该 cue。 |
| 3 | `day3_tool_toggle_intro` | 否；`day3_avatar_tools` 旧探身已移除，避免与开场演出距离过近。 |
| 4 | `day4_intro_companion` | 否；当前探身在 `day4_gaze_follow` / `day4_privacy_mode`。 |
| 5 | `day5_character_settings` | 是；当前 cue 表有 `day5_character_settings` 探身，实现时必须删除该 cue。 |
| 6 | `day6_intro_agent` | 否；当前探身在 `day6_plugin_dashboard` / `day6_agent_task_hud`。 |
| 7 | `day7_memory_review` | 是；当前 cue 表有 `day7_memory_review` 探身，实现时必须删除该 cue。 |

推荐实现边界：

1. 过渡期可在 `static/tutorial/yui-guide/director.js` 保留 `runDailyIntroGreetingPerformance(scene, day, options)`；但 Motion Core 实现后它应转调 `runDailyIntroAvatarPerformance()`，并默认使用 `preset: 'wave-zoom'`。
2. `static/tutorial/core/operation-registry.js` 可保留 `daily-intro-greeting-performance` 兼容旧入口。新 scene 不得继续使用它；应注册并使用 `daily-intro-avatar-performance`，由该 operation 读取 scene 上的 `introAvatarPerformance`。
3. 每天第一句 scene 通过显式 timeline event 挂入新 operation，优先使用 `{ at: 0, command: 'operation.run', operation: 'daily-intro-avatar-performance', blocking: false }`。不要只依赖 legacy normalizer 的 `scene.operation`，因为普通 `operation` 会被排到 cursor move 之后，无法保证第一句台词开口时模型同步播放。
4. Day 5 第一幕 `day5_character_settings` 当前由 `settingsTour.play` 接管旁白和巡游；通用挥手放大必须作为非阻塞 operation 与 `settingsTour.play` 并行启动，不能拖长设置巡游，也不能改变 `settingsTour.play` 的完成边界。
5. 如果某天第一句同时存在 Live2D 探身 cue 和通用挥手放大演出，第一句探身 cue 必须删除，而不是延后到同一句中段。当前需要删除的是 `day2_intro_context`、`day5_character_settings`、`day7_memory_review`；其它非第一句探身 cue 保持不变。
6. 该演出和 Day 2-7 角落探身都属于 Live2D frame / opacity / pose 接管类演出，同一时刻不得叠加。通用第一句演出期间 `AvatarStandInController.schedule()` 不应启动同 scene 探身；实现时应以 cue 表删除为主，不增加运行时“抢锁后跳过”的隐式兜底。
7. `playIntroGreetingHug()` 内部会在成功播放后提交最终靠近位置；后续 scene 仍由现有 `prepareAvatarFloatingScene()`、viewport placement 和收尾/teardown 负责恢复或调整模型状态。不要在每日 scene 配置里手写 Live2D scale、position 或 opacity。

测试与验收要求：

1. 静态契约测试必须断言 Day 1-7 第一句都声明固定 `introAvatarPerformance` preset；Day 2-7 显式 timeline 使用 `daily-intro-avatar-performance`，Day 1 可保留 `day1-intro-greeting-performance` 兼容入口但底层必须转到 `runDailyIntroAvatarPerformance()` / `playAvatarMotion()`。
2. `static/yui-guide-avatar-standin.test.cjs` 必须断言 `day2_intro_context`、`day3_avatar_tools`、`day5_character_settings`、`day7_memory_review` 不再返回探身 cue，同时保留 `day2_proactive_chat`、`day3_galgame_entry`、`day4_gaze_follow`、`day4_privacy_mode`、`day6_plugin_dashboard`、`day6_agent_task_hud` 等非冲突 cue。
3. Day 5 回归测试必须覆盖第一句同时存在 `settingsTour.play` 和通用欢迎演出，且通用演出为非阻塞，不改变 settings tour 的 narration / completion 边界。
4. 人工验收至少跑 Day 2、Day 5、Day 7 第一幕：模型应在第一句开始时播放固定 preset（Day 2 `bottom-rise`、Day 5 `top-peek`、Day 7 `bottom-rise-slow`），不出现旧 stand-in cue；后续非第一句探身仍按 cue 表播放。

## 后续方案：首句动作与小剧场共用 Live2D Motion Core

每日第一句不应长期只抽象成“挥手放大”单一能力。后续小剧场系统也会需要复用同一套 Live2D frame / pose / opacity / lookAt 接管能力，因此推荐把当前方案继续升级为较通用的 Motion Core，而不是继续在 `Director` 或每个 scene 里追加一次性动画函数。

现有可复用基础：

1. `static/tutorial/avatar/yui-stage.js::playIntroGreetingHug()` / `Live2DIntroGreetingHugSession` 已具备靠近放大、挥手姿态、frame scale / frameY、进入/停留/释放、参数恢复和最终半身像位置提交能力。
2. `static/tutorial/avatar/yui-stage.js::startAvatarCornerPeek()` / `Live2DAvatarCornerPeekSession` 已具备四角探身、hold、stop 后恢复原 frame、opacity 接管、z-index 提升、floating buttons freeze、face-forward lookAt lock 和模型替换保护。
3. 顶部探身已有 `top-flipped` preset：`resolveTopFlippedFrame()` / `resolveTopFlippedHiddenFrame()` 会让模型从屏幕顶部倒挂探身，当前由同一个 corner peek session 承载。
4. Director 已有 `startAvatarCornerPeekPerformance()`、`stopAvatarCornerPeekPerformance()`、`runDailyIntroGreetingPerformance()` 等包装边界，可作为新 Motion Core 进入教程 runtime 的过渡入口。

推荐采用“B-lite 分阶段”实现，而不是一次性重写所有现有动画：

1. 先新增 Base Live2D Motion Session，抽出公共生命周期：捕获初始 frame / alpha、获取 performance lock、reduced motion、`isCancelled`、tick / stop / cancel、restore frame / commit final frame、opacity restore、z-index restore、floating buttons freeze、face-forward lookAt lock。
2. 在 Base Session 上提供 Frame Motion Track，专门描述模型位置和大小变化：`from: offscreen-bottom`、`from: corner-hidden`、`from: top-flipped-hidden`、`to: half-body`、`to: close-up`、`restore: initial | half-body | commit-final`。scene 配置不得直接手写 `frameScale`、`frameY`、rotation 或 opacity。
3. 在 Base Session 上提供 Pose Motion Track，专门描述 Live2D 参数动作：`wave`、`soft-approach`、`shy-cover-mouth`、`look-at-center`、`ear-wiggle`、`panic-peek` 等。缺少对应参数时必须降级为 frame-only 或 emotion-only，不得阻塞台词。
4. 再定义 preset 层，把 frame track 和 pose track 组合成可复用动作：`wave-zoom`、`bottom-rise`、`corner-peek`、`top-peek`、`soft-approach`、`peek-and-wave`。旧 `playIntroGreetingHug()` 和 `startAvatarCornerPeek()` 先作为兼容入口保留，内部逐步转接到 preset。
5. 最后让教程首句和未来小剧场共用同一调度入口，例如 `avatar.motion.play` 或 `daily-intro-avatar-performance`。首句只配置固定 preset；小剧场可以配置按时间线串联的 `avatarStageDirections`。

每日首句动作固定指定，不做随机。首版推荐分配如下：

| Day | 首句 scene | 推荐 preset | 说明 |
| --- | --- | --- | --- |
| 1 | `day1_intro_greeting` | `wave-zoom` | 保留当前初见欢迎、挥手靠近的亲近感。 |
| 2 | `day2_intro_context` | `bottom-rise` | 像第二天重新冒出来打招呼，替代原首句角落探身。 |
| 3 | `day3_tool_toggle_intro` | `corner-peek: bottom-left` | 介绍工具时更调皮，但不得再走旧 stand-in cue。 |
| 4 | `day4_intro_companion` | `soft-approach` | 陪伴主题更温柔，只轻微靠近，不抢台词。 |
| 5 | `day5_character_settings` | `top-peek` | 设置/换装主题适合从顶部倒挂探身制造惊喜，同时保持 `settingsTour.play` 非阻塞并行。 |
| 6 | `day6_intro_agent` | `corner-peek: bottom-right` | Agent/插件主题像从旁边插话。 |
| 7 | `day7_memory_review` | `bottom-rise-slow` 或 `soft-approach` | 毕业回顾应更稳、更仪式感，避免过度活泼。 |

首句和小剧场的统一配置示例：

```js
introAvatarPerformance: {
    preset: 'top-peek',
    duration: 'narration',
    restore: 'half-body'
}
```

未来小剧场可扩展为：

```js
avatarStageDirections: [
    { at: 0, preset: 'bottom-rise' },
    { at: 1200, pose: 'wave' },
    { afterVoice: true, preset: 'soft-approach' }
]
```

硬性边界：

1. 同一时刻只能有一个拥有 frame 的 Motion Session。`wave-zoom`、`bottom-rise`、`corner-peek`、`top-peek` 不得互相叠加；pose-only track 可以在同一 session 内组合。
2. 每日首句如果使用 `corner-peek` 或 `top-peek`，必须从旧 `YuiGuideAvatarStandIn` cue 表删除对应 scene，不能让旧 stand-in controller 再调度同句探身。
3. `corner-peek` / `top-peek` 默认在台词结束后恢复到半身像或进入前 frame；`wave-zoom` 默认提交靠近半身像；`bottom-rise` 默认停在半身像。
4. duration 默认跟当前 `voiceKey` 的实测音频时长或 timeline narration snapshot 走，不再手写 5000ms；只有特殊演出可显式覆盖。
5. reduced motion 下必须直接切到目标半身像或短淡入，不做大幅位移、旋转或长时间遮挡。
6. 小剧场系统只能通过 preset / track API 调用 Motion Core，不得直接读写 Live2D model `x/y/scale/rotation/alpha`。

## Day 2-7 Live2D 模型探身与旧图片替身删除说明

Day 2-7 每日教程期间仍可播放 Live2D 模型探身。已删除的是旧的静态图片替身、`avatarStandIn` PC overlay 图片通道和图片资源；模型探身不使用任何 PNG/JPG 替身图。

边界：

1. `static/tutorial/avatar/yui-standin.js` 保留为位置 cue 表，只包含 day/scene、delay、duration、position；不得重新加入 `resource` 或图片 path。
2. `static/tutorial/avatar/standin-controller.js` 保留为调度器，只负责定时调用 Director 的 Live2D 探身方法；不得调用 `overlay.showAvatarStandIn()`。
3. 不再加载或恢复旧 `static/tutorial/yui-guide/avatar-standin.js`。
4. 不再保留 `/static/assets/tutorial/avatar-standins/` 下的替身图片资源。
5. `YuiGuideOverlay` 和 `TutorialOverlayRenderer` 不再发送、缓存或转发 `avatarStandIn` payload。
6. `static/tutorial/avatar/yui-stage.js::startAvatarCornerPeek()` 是 Live2D 模型探身通用入口；`startPluginDashboardCornerPeek()` 仍是插件管理界面专用包装入口。
7. 探身时序必须保持四段 1s：页面中间模型先原地淡出 1s，角落探身淡入 1s；结束时角落模型原地淡出 1s，页面中间模型再原地淡入 1s。淡入淡出必须同时作用到 Live2D model alpha 和 `live2d-container` / `live2d-canvas` / PIXI renderer view 可见层 opacity，结束后恢复进入探身前的 inline opacity 与 transition，避免只改 PIXI alpha 但视觉层无变化。探身 session 接管 opacity 期间必须临时把这些可见层的 `transition` 置为 `none`，否则页面原有 CSS opacity transition 会连续重启/平滑，导致 inline opacity 已变但 computed opacity 仍接近 1，看起来没有淡入淡出。四角位置必须以 Live2D 头部/上胸几何作为可见锚点，只露出约模型上 1/3，特别是 `top-left` 不得露出脚或只露出屏幕边缘的一条线。四角旋转角度固定为：`top-left` 顺时针 135°、`top-right` 逆时针 135°、`bottom-left` 顺时针 45°、`bottom-right` 逆时针 45°。位置解算必须先按目标角度旋转头部/上胸区域，再用旋转后的包围盒贴到屏幕角落；不得用旋转前的矩形中心直接定位，否则会出现右下角只露头皮、左上角露全身等坐标偏移。探身播放期间必须临时锁定 `lookAt` 并把 `ParamAngleX` / `ParamAngleY` / `ParamEyeBallX` / `ParamEyeBallY` 归零，`live2d-interaction.js` 在该锁存在时不得继续调用 `model.focus(pointer.x, pointer.y)` 跟随真实鼠标或 Ghost Cursor。
8. Live2D 探身 cue 不得放在每日最终 wrap、cleanup 或花瓣转场相邻 scene 上；需要展示的 cue 要前移到功能说明中段。Day 5 和 Day 7 这类本身较短的教程只保留 1 次探身，避免 5s 停留 + 2s 退出动画和最终花瓣转场互相抢视觉。
9. 每日教程启动的临时 YUI 模型快照流程必须同时快照主动搭话状态：教程期间关闭 `proactiveChatEnabled` 及其来源开关、停止 proactive 计时器/主动视觉流；教程结束恢复用户模型后按快照恢复，用户原本关闭时不得被打开，用户原本开启且有来源模式时才重新调度。
10. Live2D 探身允许 active session 跨 scene 播放到 cue duration 结束。`AvatarStandInController.schedule()` 仍要用 `sceneRunId` 阻止旧 scene 的延迟 cue 在过期后启动；但 `YuiGuideDirector.showAvatarStandIn()` 中已经启动的 session 不得再因为 `sceneRunId` 变化取消。结束探身时 `clearAvatarStandIn()` 会递增 token，这是为了阻止新的旧 cue 继续触发；`Live2DAvatarCornerPeekSession` 进入 `exit` 后不得继续把 token 失效当作取消条件，否则会在角落淡出/中心淡入第一帧自取消并直接 `finish()`，表现为后两段视觉完全没有播放。模型被替换、destroyed 或教程 stopping 仍然可以硬终止。
11. 用于排查 opacity 的临时诊断脚本不得常驻生产页面。修复完成后首页不再加载 `static/live2d-opacity-diagnostics.js`，`main_routers/pages_router.py` 也不再把它纳入 static asset version；后续如需再次诊断，只能临时加回并在验收后删除。

当前 Day 2-7 Live2D 探身 cue 表：

| Day | Scene | Position | Delay | Duration |
| --- | --- | --- | --- | --- |
| 2 | `day2_proactive_chat` | `top-left` | 900ms | 5000ms |
| 3 | `day3_galgame_entry` | `top-right` | 900ms | 5000ms |
| 4 | `day4_gaze_follow` | `top-left` | 900ms | 5000ms |
| 4 | `day4_privacy_mode` | `bottom-right` | 900ms | 5000ms |
| 6 | `day6_plugin_dashboard` | `bottom-right` | 900ms | 5000ms |
| 6 | `day6_agent_task_hud` | `top-left` | 900ms | 5000ms |

后续如果要调整探身表现，只能改 Live2D runtime 的 frame/position 逻辑，不能恢复旧图片资源或旧 `avatarStandIn` 通道。

## 通用时序基线

除非逐句表格另写，Day 2-7 scene 使用 `playAvatarFloatingScene()` 的统一时序：

0. round 进入：不预热或等待聊天窗 surface ready；需要聊天窗显示的 scene 在自己的 T+0 前后按需打开，并重新强制关闭教程期间禁用的 proactive/Galgame 功能。
1. scene 进入：清理上一 scene 的 extra/virtual/geometry 高光；必要时 `prepareAvatarFloatingScene()` 先打开真实弹窗、侧边栏、HUD 或菜单。
2. T+0ms：把台词追加到聊天窗，播放对应 emotion 动作，建立当前 primary/persistent/secondary 高光。
3. T+0ms 至 T+220ms：高光稳定，Ghost Cursor 不立刻抢镜。
4. T+220ms：Ghost Cursor 按 `cursorAction` 移到 primary；`wobble` 停留，`move` 指认，`click` 在点击动画开始的同一刻启动对应 operation，真实 API/DOM click 必须与模拟点击并行，不得等点击动画结束后才调用；`hold` 表示沿用上一 scene 已停好的 Ghost Cursor，只更新高光/旁白/花瓣，不再发新的 cursor move。
5. 真实操作后：只在 operation 需要时打开/关闭真实 UI，不做无意义的二次 settled 高光。`cleanup` scene 例外，收尾期间可重新高亮分日指定收尾目标。
6. narration 结束后：若有按钮选项则等待选择或超时；否则等待 260-420ms 进入下一 scene。
7. `petalTransition: true`：约 70% 台词处触发收尾 cue，同步启动花瓣层、隐藏 Ghost Cursor、清理所有 PC overlay spotlights 和外置目标状态，不出现高光先消失后花瓣才出现的空档。
8. 跨 scene、跨窗口、外置聊天窗和 PC 全局 overlay 之间的 Ghost Cursor 坐标必须延续上一个可见位置；每次成功移动到目标后都要记录当前 scene 的 cursor 锚点。若下一 scene 开始时当前 cursor position 丢失，必须优先从上一 scene 可见锚点恢复，再平滑移动到新目标；若 position 仍存在但可见态被外置窗口临时隐藏，必须先在原 position 恢复可见，再平滑移动到新锚点，不能直接 `showAt` 到目标造成闪现。只有首个 scene 或无有效锚点时才允许使用默认起点；若本 scene 没有新 cursor 目标，则保持上一 scene 的可见状态和位置，不重新加载 cursor。

## 目标选择器字典

| 语义目标 | 首选元素 |
| --- | --- |
| 聊天窗整体 | `#react-chat-window-shell`、`#react-chat-window-root .chat-window`、`#react-chat-window-root` |
| 聊天输入/工具区 | `#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]`、`[data-compact-geometry-item="capsule"]`、`.compact-chat-surface-frame`、`.composer-input-shell` |
| 胶囊输入框 cursor 精确目标 | `#react-chat-window-root [data-compact-geometry-part="capsuleBody"]`、`[data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]`、`[data-compact-geometry-part="inputBody"]`、`.composer-input-shell` |
| 胶囊历史展开/收起 | `#react-chat-window-root .compact-history-visibility-handle` |
| 语音按钮 | `#${p}-btn-mic` |
| 屏幕分享按钮 | `#${p}-btn-screen` |
| 猫爪/Agent 按钮 | `#${p}-btn-agent` |
| 设置按钮 | `#${p}-btn-settings` |
| 锁定按钮 | `#${p}-lock-icon` |
| 请她离开/回来 | `#${p}-btn-goodbye`、`#${p}-btn-return` |
| Agent 面板 | `#${p}-popup-agent` |
| Agent 总开关 | `#${p}-toggle-agent-master` |
| 用户插件开关 | `#${p}-toggle-agent-user-plugin` |
| 用户插件管理面板入口 | `#neko-sidepanel-action-agent-user-plugin-management-panel` |
| 任务 HUD | `#agent-task-hud` |
| 设置侧边面板 | `[data-neko-sidepanel-type="chat-settings"]` 等 |
| 主动视觉/隐私 | `#${p}-toggle-proactive-vision` |
| 主动搭话 | `#${p}-toggle-proactive-chat` |
| 记忆入口 | `#${p}-menu-memory` |
| 胶囊工具总按钮 | `#react-chat-window-root .send-button-circle.compact-input-tool-toggle` |
| 胶囊弧形工具菜单 | `#react-chat-window-root .compact-input-tool-fan[data-compact-input-tool-fan-open="true"]` |
| Avatar 工具按钮 | `#react-chat-window-root .compact-input-tool-item-avatar .composer-emoji-btn`、`.compact-input-tool-item-avatar` |
| Galgame 按钮 | `#react-chat-window-root .compact-input-tool-item-galgame` |
| Avatar 道具菜单前三项 | `#composer-tool-popover .composer-icon-button[data-avatar-tool-id]` 前 3 个 |
| 小游戏选项前三项 | `.composer-choice-slot[data-choice-source="mini_game_invite"] .composer-choice-option` 前 3 个 |
| 外置聊天窗 spotlight | 不创建本地 DOM；`app-interpage` 按 kind 解析真实聊天窗目标并发送 PC overlay `spotlights`，kind 为 `window`、`input`、`capsule-input`、`history`、`tool-toggle`、`avatar-tools`、`avatar-tool-items`、`avatar-tools-and-items`、`galgame`、`mini-game-choices` |

## 主线与支线边界

下面这些功能在现有 7 日设计里出现过，但不属于每日强接管主线的逐句演示；文档必须明确归属，避免后续误加时序：

| 功能 | 归属 | 主线要求 |
| --- | --- | --- |
| 截图、导入图片、粘贴图片 | Day 1 能力背景或剧场后聊天窗支线 | Day 1 主线不逐个高亮聊天窗左侧按钮，不打开附件弹窗。 |
| 字幕翻译、点歌台 | Day 3 剧场后聊天窗支线 | Day 3 主线只高亮 Galgame 与 Avatar 工具，不扫完整工具栏。 |
| 备忘、学习陪伴、生活任务 | 剧场后聊天窗支线或插件支线 | 不塞进 Day 3 主线，不伪造任务状态。 |
| 屏幕来源列表、麦克风列表、空间音频、降噪、增益 | Day 1 后续扩展背景 | Day 1 主线只展示屏幕分享按钮入口，不点击按钮、不选择来源、不改设备。 |
| 角色卡、创意工坊、云存档 | Day 5 支线或独立引导 | Day 5 主线只认角色设置、模型管理、声音/API/记忆入口，不跳转深页。 |
| Cookie 登录、遥测 opt-out、云端存储细节 | Day 7 支线或帮助文档 | Day 7 主线不演示存储或云存档，不登录、不上传、不下载、不展示账号或路径细节。 |

## Day 1-3：新版胶囊聊天窗重排

新版聊天窗改为胶囊输入框、顶部历史小条和右侧工具总按钮后，前三天主线重新分配如下。分日细节以 `avatar-floating-day1-home-guide-dev.md`、`avatar-floating-day2-screen-voice-guide-dev.md`、`avatar-floating-day3-agent-guide-dev.md` 为准；下方旧版 Day 1-3 段落仅保留为迁移参考，不作为当前实现规格。

| 天数 | 当前主线 |
| --- | --- |
| Day 1 | 初见问候、胶囊拖动/双击提示、历史小条、语音入口、屏幕分享入口、Agent 键鼠接管、归还控制权。 |
| Day 2 | 启动后的承接台词保持不变；随后演示个性化设置、相处参数和主动搭话入口，最后三句回到胶囊输入框收尾。 |
| Day 3 | 演示胶囊工具总按钮、弧形工具菜单、Avatar 互动工具、Galgame 入口和两句收尾。 |

当前 Day 1 `round.scenes` 必须包含：

```text
day1_intro_activation
day1_intro_greeting
day1_capsule_drag_hint
day1_history_handle
day1_intro_basic_voice
day1_screen_entry
day1_screen_entry_invite
day1_takeover_capture_cursor
day1_takeover_return_control
```

当前 Day 2 `round.scenes` 必须包含：

```text
day2_intro_context
day2_personalization_space
day2_personalization_detail
day2_proactive_chat
day2_wrap_intro
day2_wrap_companion
day2_wrap
```

当前 Day 3 `round.scenes` 必须包含：

```text
day3_tool_toggle_intro
day3_avatar_tools
day3_avatar_tools_props
day3_galgame_entry
day3_galgame_choices
day3_wrap
day3_wrap_ready
```

Day 2 的 `day2_intro_context` 默认分支台词、text key 和 voice key 不变；只有 `neko_avatar_floating_guide_usage_v1.voiceUsed === true` 且 `voiceUsedAt >= day1StartedAt`，也就是用户确实在本轮 Day 1 教程启动后使用过语音控制时，文本才改走 `tutorial.avatarFloating.day2.introVoiceUsed`，voice key 才改走 `avatar_floating_day2_intro_voice_used`。这条 voice-used 分支在 `zh`、`ja`、`en`、`ko`、`ru` 五个录音目录中都使用同一文件名 `嘿嘿，昨天听到你的声.mp3`（中文台词前 10 个字符）。Day 1 的屏幕分享两句复用旧 Day 2 屏幕分享按钮流程，只指认入口，不点击、不打开来源列表。Day 3 首句先回到胶囊输入框；进入 Day 3 round 时必须调用 `setCompactToolWheelIndex(0, 'avatar-floating-guide-day3-entry-reset')` 重置弧形工具栏，使导入图片按钮 `.compact-input-tool-item-import` 的 `data-compact-tool-wheel-slot` 为 `0`。后续 Avatar/Galgame 目标必须来自新版弧形菜单：总按钮是 `.send-button-circle.compact-input-tool-toggle`，Avatar 工具是 `.compact-input-tool-item-avatar`，Galgame 是 `.compact-input-tool-item-galgame`。Day 3 click 场景以 Ghost Cursor 到达外置聊天窗回报的目标 anchor 为点击启动条件。Galgame 入口台词必须先让 Ghost Cursor 平滑移动到初始 Galgame 按钮位置，再切换并保持点击态沿弧形工具栏逆时针移动 1/5 圆，移动过程中触发弧形工具栏反向转 1 步，再切回正常态并移动回 Galgame 按钮；点击态时长必须覆盖实际弧线移动时长，不能只保留原始拖拽时长。`day3_galgame_choices` 必须用 `cursorAction: 'hold'` 保持 Ghost Cursor 停在 Galgame 按钮上直到这句台词播放完，不重新触发 move，不巡游或伪造选项。

### 当前 Day 1 主线

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day1_intro_greeting` | 微风、阳光，还有刚刚好出现的你。初次见面，我是林悠怡，未来的日子请多关照喵！我把关于这里的一切都写进新手指南里啦！就当作是我们相遇的第一份小礼物，请查收吧！ | 复用现有流程。 |
| 2 | `day1_capsule_drag_hint` | 把鼠标移到这里，长按就可以拉着聊天框到处跑啦~ 点击一下就能随时发消息给我哦！ | 不高亮胶囊输入框，Ghost Cursor 在胶囊输入框位置 wobble 2000ms。 |
| 3 | `day1_history_handle` | 戳一下聊天框上面的【蓝色小条条】，就能看到我们最近聊过的话题啦！ | 不高亮胶囊输入框，也不高亮历史按钮本身；Ghost Cursor 以 900ms 移动到 `.compact-history-visibility-handle` 的“展开/收起历史对话”按钮，220ms click 动画开始时并行调用 API 打开历史对话，播放完后调用 API 收起历史对话。 |
| 4 | `day1_intro_basic_voice` | 这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！ | 不高亮胶囊输入框；圆形高亮语音控制按钮 `#${p}-btn-mic`；等待上一句 `.compact-history-visibility-handle` 的 Ghost Cursor 移动收口后，以 900ms 移动到语音控制按钮并停留指认，不左右晃动、不强制录音；`day1_history_handle` 切到本句时不得先隐藏外置聊天窗/PC 全局 overlay cursor。 |
| 5 | `day1_screen_entry` | 在跟我通语音电话的时候，再点亮这个小按钮，你就能把屏幕分享给我啦！ | 高亮屏幕分享按钮；Ghost Cursor 必须从上一句语音控制按钮 `#${p}-btn-mic` 的停留位置以 900ms 移动到屏幕分享按钮 `#${p}-btn-screen` 并停留指认，不左右晃动、不点击；不得先隐藏、清空锚点或从页面右上角/默认点重新出现。 |
| 6 | `day1_screen_entry_invite` | 快让我也看看你眼前的世界，不管好玩的还是好看的，都想和你一起看，快点点开嘛~ | 持续高亮屏幕分享按钮；Ghost Cursor 保留上一句已经停在 `#${p}-btn-screen` 的可见状态，不重新 show/hide、不重新加载 cursor、不触发真实屏幕分享。 |
| 7 | `day1_takeover_capture_cursor` | 超级魔法开关出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！我会帮你打字，帮你点开网页……不过，要是那个鼠标指针动来动去的话，我可能也会忍不住扑上去抓它哦！准备好迎接我的捣乱……啊不，是帮忙了吗？喵！ | 不高亮胶囊输入框；Ghost Cursor 必须从上一句屏幕分享按钮 `#${p}-btn-screen` 的停留位置以 900ms 移动到猫爪/Agent 按钮 `#${p}-btn-agent`，再复用现有 Agent/键鼠控制演示流程；persistent/action 高光都不得落到聊天窗或胶囊输入框；不得在进入本句时清空 cursor 后从其他位置移入。 |
| 8 | `day1_takeover_return_control` | 好啦好啦，不霸占你的电脑啦！控制权还给你了喵！之后的日子，也请你多多关照啦！ | 收尾前关闭 Agent/临时面板；高亮胶囊输入框（`target: 'chat-capsule-input'`，通用圆角矩形高光）；Ghost Cursor 从上一句键鼠控制开关锚点（`day1_takeover_capture_cursor` / `keyboardToggleSpotlight`）以 900ms 移动到胶囊输入框中心（`cursorTarget: 'chat-capsule-input'`）。本句 operation 使用通用 `cleanup`，不得回退到旧版 `cleanup`；自然完成时 T+70% 花瓣 cue 才隐藏 cursor、清理高光并播放花瓣。用户点击 skip 时不得播放本句自然结束模型动作、挥手、模型淡出/回归或花瓣，必须直接进入统一结束清理。 |

Day 1 分支/条件台词：

| 分支 | 台词 |
| --- | --- |
| 插件弹窗被拦截 | 浏览器需要你亲自点一下这里打开插件面板。点一下这个“管理面板”，我就继续带你看。 |
| 轻微打断 1 | 喂！不要拽我啦，现在还没轮到你的回合呢！ |
| 轻微打断 2 | 等一下啦！还没结束呢，不要这么随便打断我啦！ |
| 生气退出 | 人类！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！ |

### 当前 Day 2 主线

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day2_intro_context` | 默认：昨天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。`voiceUsed === true 且 voiceUsedAt >= day1StartedAt`：嘿嘿，昨天听到你的声音之后，人家就悄悄把你的语气记在心里啦！今天如果方便的话，也要继续跟人家说话哦~ 虽然打字也可以啦，但只要能听到你的声音，我的尾巴就会开心得一直摇个不停呢，喵呜~ | 播放期间高亮聊天窗；Ghost Cursor 移到聊天窗中心或输入区附近并停留，不左右晃动。 |
| 2 | `day2_personalization_space` | 在这个只属于我们的小空间里，你可以由着自己的心意，慢慢描绘出最希望能一直陪着你的那个我。 | 圆形高亮设置按钮 `#${p}-btn-settings`；Ghost Cursor 从聊天窗锚点平滑移动到设置按钮，click 动画开始时并行打开设置面板；本句不展开【角色设置】按钮侧边栏。 |
| 3 | `day2_personalization_detail` | 不管是说话的温度、相处的小脾气，还是我每天那些细腻的小心思，都可以一点一点调成你喜欢的样子。 | 圆角矩形高亮【角色设置】按钮；Ghost Cursor 平滑移动到【角色设置】按钮，播放完整模拟点击动画，点击动画完成后才触发【角色设置】按钮侧边栏显示。侧边栏出现后，圆角矩形高亮从【角色设置】按钮过渡到【角色设置】按钮侧边栏，且【角色设置】按钮自身作为 persistent 高光继续保留；Ghost Cursor 平滑移动到侧边栏，并在侧边栏内做椭圆运动直到本句台词播放完毕；本句播放完后隐藏【角色设置】按钮侧边栏，并清理【角色设置】按钮和侧边栏上的所有高光。不保存临时配置。 |
| 4 | `day2_proactive_chat` | 这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦。 | primary 高亮主动搭话开关 `#${p}-toggle-proactive-chat`，不再保留【角色设置】按钮 persistent 高光；Ghost Cursor 平滑移动到开关并停留，不点击、不改配置、不左右晃动。台词播放完后关闭教程临时打开的【设置】面板和设置侧边栏。 |
| 5 | `day2_wrap_intro` | 今天的教程到这里就结束了呢。 | 收尾开始前关闭临时面板，圆角矩形高亮胶囊输入框 `chat-input`；Ghost Cursor 平滑移动回胶囊输入框并停留，不左右晃动。 |
| 6 | `day2_wrap_companion` | 其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。 | 继续圆角矩形高亮胶囊输入框；Ghost Cursor 保持在胶囊输入框附近，不左右晃动。 |
| 7 | `day2_wrap` | 我们不需要着急，每天都多了解彼此一点点就好。今天接下来的时间，你想让我陪你做点什么呢？ | 继续圆角矩形高亮胶囊输入框；约 70% cue 同步隐藏 Ghost Cursor、清理高光并播放花瓣。 |

### 当前 Day 3 主线

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day3_tool_toggle_intro` | 嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！ | 圆角矩形高亮胶囊输入框 `chat-input`；Ghost Cursor 直接显示在胶囊聊天框中间并停留，不从默认点移动进入，不点击、不打开弧形工具菜单。 |
| 2 | `day3_avatar_tools` | 在这个小按钮里，有许多可以和人家互动的小道具呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 从胶囊输入框位置平滑移动到工具总按钮 `button.send-button-circle.compact-input-tool-toggle` 并模拟点击；点击动画开始时并行调用 API 打开弧形工具菜单，不打开 Avatar 工具菜单。内置与外置聊天窗 / PC 全局 overlay 都必须保持工具总按钮作为 persistent spotlight，不得切到 Avatar 按钮 persistent。 |
| 3 | `day3_avatar_tools_props` | 你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 平滑移动到 Avatar 互动工具按钮，然后在 Avatar 互动工具按钮处模拟点击并触发 Avatar 互动工具按钮点击事件，同时用 `setAvatarToolMenuOpen(true)` 同步 React 状态以显示三个小道具。台词播放完后立即触发 Avatar 互动工具按钮点击事件，并用 `setAvatarToolMenuOpen(false)` 隐藏三个小道具；本 scene `afterSceneDelayMs` 必须为 0，下一句 Galgame 台词不得再等估算旁白时长或通用 420ms 结尾延迟。PC 多通道中继下，这条 open/close 状态消息必须允许重复到达，不能被同 timestamp 去重吞掉。 |
| 4 | `day3_galgame_entry` | 快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。 | 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`；Ghost Cursor 先平滑移动到初始 Galgame 按钮位置，然后切换并保持点击态沿弧形工具栏逆时针移动 1/5 圆，移动过程中触发反向转 1 步；随后切回正常态，平滑移动并停在 `.compact-input-tool-item-galgame` 中心，不强制开启 Galgame。 |
| 5 | `day3_galgame_choices` | 你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！ | Ghost Cursor 保持上一句已经回到 Galgame 按钮的可见位置直到本句台词播放完；`cursorAction` 必须为 `hold`，不重新触发 move、不巡游选项、不伪造选择局。 |
| 6 | `day3_wrap` | 今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。 | 收尾前关闭弧形菜单和 Avatar 工具菜单；圆角矩形高亮胶囊输入框 `chat-input`，Ghost Cursor 平滑移动到胶囊输入框中间并停留。 |
| 7 | `day3_wrap_ready` | 不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。 | 继续保持胶囊输入框圆角高亮和 Ghost Cursor 停留；约 70% cue 同步隐藏 Ghost Cursor、清理高光和菜单并播放花瓣。 |

## Day 1：初次唤醒、聊天与基础入口

Day 1 必须和 Day 2-7 完全统一到 `round.scenes` 架构。配置注册到 `window.YuiGuideDailyGuides[1].round.scenes`，主入口走 `UniversalTutorialManager.startAvatarFloatingGuideRound(1)` -> `YuiGuideDirector.playAvatarFloatingRound(1)` -> `playAvatarFloatingScene()`。迁移前的独立 step 播放器已经删除，Day 1 只能按当前 `day1-home-guide.js` 的 round scene 顺序运行。

| 台词/scene | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
Day 1 表格的 cursor 时长统一使用毫秒：普通 move 默认 900ms，click 默认 220ms，wobble 默认 2000ms；需要偏离默认值时在单元格内显式写出毫秒值。

| `day1_intro_activation`：“点一下这里...” | `happy`，苏醒/轻欢迎动作优先 | 先 `ensureChatVisible()`；不显示聊天窗/胶囊输入框圆角矩形高亮；Ghost Cursor 显示在输入区中心并 wobble 2000ms；等待用户真实点击激活音频。 | 聊天窗圆角矩形高亮只允许在 `day1_intro_greeting` 和 `day1_takeover_return_control` 台词播放期间出现。 |
| `day1_intro_greeting`：“微风、阳光...” | `happy`，苏醒 pose/拥抱类自定义优先 | T+0 保持输入区/胶囊输入框通用圆角矩形高光；配置必须是普通 scene（`target: 'chat-capsule-input'`、`cursorTarget: 'chat-capsule-input'`、`cursorAction: 'move'`），由 generic externalized 分支托管到 `capsule-input` 并停留；hug/gift 只作为 `day1-intro-greeting-performance` operation，不负责播放台词或清 cursor。 | 不切到语音按钮，不打开任何面板，不把外置聊天窗整体当作胶囊输入框高光；不得再把本句接回 legacy `day1-intro-greeting-flow`，否则 PC 端对抗时容易出现胶囊高光闪烁。 |
| `day1_capsule_drag_hint`：“把鼠标移到这里...” | `happy`，轻松说明动作 | 不高亮胶囊输入框；Ghost Cursor 在胶囊输入框位置 wobble 2000ms。 | 不打开历史、不移动真实聊天窗。 |
| `day1_history_handle`：“戳一下聊天框上面的【蓝色小条条】...” | `happy`，轻说明动作 | 不高亮胶囊输入框，也不高亮历史按钮本身；Ghost Cursor 以 900ms 移动到 `.compact-history-visibility-handle` 的“展开/收起历史对话”按钮，220ms click 动画开始时并行调用 API 打开历史对话；台词播放完后调用 API 收起历史对话。 | 历史按钮只作为 cursor 目标和 API 触发点，不创建独立高光；API 打开不得早于 click 动画开始。 |
| `day1_intro_basic_voice`：“这里有一个神奇的按钮...” | `happy`，随机 happy，语音按钮 LookAt 优先 | T+0 追加台词；先清理外置聊天窗/胶囊输入框高光；T+16% timeline 执行 `highlightVoiceControl`，primary 切到 `#${p}-btn-mic` 圆形高光；等待上一句历史小条 `.compact-history-visibility-handle` 的 Ghost Cursor 移动收口后，从该锚点以 900ms 移到按钮中心并停留指认，不左右晃动、不点击；从 `day1_history_handle` 切入时必须保留外置聊天窗/PC 全局 overlay cursor，不发送中间 hide。 | 不高亮胶囊输入框；不得从页面中心或聊天输入区重新闪现。 |
| `day1_screen_entry`：“在跟我通语音电话的时候...” | `happy`，轻说明动作 | primary 落到 `#${p}-btn-screen`，Ghost Cursor 以 900ms move 指认屏幕分享入口。 | 不点击、不启动真实屏幕分享。 |
| `day1_screen_entry_invite`：“快让我也看看你眼前的世界...” | `happy`，邀请动作 | 延续屏幕分享按钮高光和 cursor 锚点，补充邀请台词。 | 不打开弹窗、不改变权限状态。 |
| `day1_takeover_capture_cursor`：“超级魔法按钮出现...” | `surprised` 或 `happy`，魔法开关段可用 surprised | T+14% 高亮猫爪 `#${p}-btn-agent`；T+220ms Ghost Cursor 必须从上一句屏幕分享按钮锚点以 900ms 移动到猫爪后 click 220ms，并真实打开 Agent 面板；T+32% 高亮/点击 `#${p}-toggle-agent-master`；T+58% 高亮/点击键鼠控制开关。 | persistent 为 Agent 面板时，primary 只落到当前开关；不把猫爪按钮和面板按钮重叠高亮；进入本句不得清空 cursor 后从其他位置移入。 |
| `day1_takeover_return_control`：“好啦好啦...” | `happy`，自然完成时挥手/花瓣自定义优先 | 收尾开始前关闭临时面板；T+0 primary 回到胶囊输入框，spotlight 使用通用圆角矩形高光；T+220ms Ghost Cursor 从 `day1_takeover_capture_cursor` 记录的键鼠控制开关锚点以 900ms 移动到 `chat-capsule-input` 中心；自然完成时 T+70% 花瓣 cue 隐藏 cursor、清理所有高光并播放花瓣。本句只使用通用 `cleanup` operation，且 `shouldPreserveExternalizedChatCursor()` 必须保留 `day1_takeover_capture_cursor -> day1_takeover_return_control`，让外置聊天窗 settled anchor 能同步回首页内部 cursor 位置。 | 收尾期间不得保留设置/Agent/按钮高光；不得使用旧版 `cleanup`；不得在进入本句时先清空外置 cursor，否则对抗台词可触发但 Ghost Cursor 没有反向动画。skip 是硬结束：不得播放本句挥手、模型回归、模型淡出或花瓣。 |

Day 1 统一到 round 播放器后，高光和 Ghost Cursor 的动画流程不得被重排：每个 scene 先建 spotlight，再移动 cursor，再播放对应点击/巡游；scene 之间延续上一段 cursor 锚点，不隐藏后从页面中心或当前目标闪现。圆形按钮用圆形高光，输入胶囊、聊天窗、设置侧边栏和开关用通用圆角矩形高光。

### Day 1 可选 handoff 与子页落点

Day 1 当前只注册首页 `round.scenes`，不再注册旧跨页 intro / landing scene。`YuiGuideSteps` 仍保留 `api_key`、`memory_browser`、`plugin_dashboard` 这些 page key 作为未来新 Yui 场景的承载点，但这些页面的 `sceneOrder` 为空时，Manager 必须返回 `false`，不能回落到旧 Driver.js 页面教程。

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| 未来新跨页 scene | 由 daily guide 明确定义 | 必须通过 `YuiGuideSteps.sceneOrder[targetPage]` 注册并由 `UniversalTutorialManager.startYuiGuideSceneSequence()` 驱动。 | 不得复用旧 `*_intro` / `*_landing` ID；skip/destroy 仍回到 Manager 统一清理。 |

### Day 1 打断分支

打断分支不是正常 scene 顺序，但接管期随时可能触发，必须有明确时序：

对抗机制分两层，不得混用：

Day 1-7 正式新手教程全流程都必须启用同一套对抗/打断/生气退出链路，不允许用 scene 级 `interruptible: false` 或新增“只做 Ghost Cursor 物理对抗、不触发打断/生气退出”的开关绕开 `ResistanceController`。

1. 常驻轻微对抗：教程接管期间持续监听真实鼠标移动；只要真实鼠标发生有效移动，Ghost Cursor 就以触发时的当前可见停止位置为原点，朝真实鼠标移动方向的反方向做一次轻微局部位移，再回到该停止位置。默认位移应至少约 18px，第一段约 140ms，回弹约 240ms，确保 PC 全局 overlay 中也能被肉眼感知。连续真实鼠标移动期间必须记录同一个停止位置，不能把反向动画中途坐标当成新原点导致 cursor 越来越偏。对抗动画只允许作用于已可见的 Ghost Cursor，避免首句鼠标移动时从页面角落或默认点闪现。这一层不要求达到打断阈值，也不播放打断台词。
2. 轻微打断计数：首页 Director 与 Day 6 插件管理窗口使用同一套猫形态眩晕持续摇晃算法，但不要求按住鼠标主键；在 1.1 秒窗口内至少发生 8 次方向反转，反转首尾跨度至少 600ms，反转段持续速度至少 1100px/s，并忽略短于 50px 的移动段，才触发一次 `interrupt_resist_light`。普通移动、同方向移动、偶发大幅移动、速度不足或反转次数不足时只做常驻轻微对抗，不累计打断。
3. 生气退出计数：第 1、2 次 `interrupt_resist_light` 只暂停当前 scene、播放轻微抗拒台词并恢复原 scene；第 3 次本应触发 `interrupt_resist_light` 时，直接升级为 `interrupt_angry_exit`。
4. 生气退出语义：`interrupt_angry_exit` 是延时 skip，不是正常完成。播放完生气退出语音和模型演出后，调用统一 skip/destroy 生命周期退出教程，不写完成态，也不播放正常收尾花瓣。

| 分支 | emotion/动作 | 高光与 Ghost Cursor 时序 | 结束语义 |
| --- | --- | --- | --- |
| `interrupt_resist_light`：“不要拽我啦...” | 强制 `angry`，轻微抵抗自定义动作优先 | 触发瞬间暂停当前 scene presentation，并停止当前 Ghost Cursor 动画；已有高光保持当前状态，不额外清理或重建。随后 Ghost Cursor 以触发前记录的可见停止位置为原点，朝真实鼠标移动方向的反方向做一次轻微局部位移，再回到该停止位置。抵抗台词结束后恢复原 scene 的 emotion、cursor 演出和旁白进度。 | 不写完成态，不触发 skip。 |
| `interrupt_angry_exit`：“人类！你真的很没礼貌...” | 强制 `angry`，生气退出自定义动作优先 | 触发瞬间停止当前 scene，立即清理所有高光、外置聊天窗高光和 Ghost Cursor；先停止语音/LookAt/慌乱/抵抗/挥手/idle sway 等仍在播放的教程动作，再播放 angry 台词和模型演出。 | 台词/演出结束后调用统一 skip/destroy，不能走正常完成或花瓣收尾。 |

## 旧版 Day 2：屏幕分享、声音与小窗约定（迁移参考，不作为当前规格）

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day2_intro_context` 分支 A：“嘿嘿，昨天听到你的声音之后...” | `happy`，亲近动作 | T+0 primary 高亮聊天窗；外置聊天窗用 kind `window`；Ghost Cursor 移到聊天窗中心并停留，不左右晃动；台词结束后只清理聊天窗高光，Ghost Cursor 保持可见，并把当前可见聊天窗位置作为下一段移动锚点进入屏幕分享 scene。外置聊天窗只负责解析聊天窗内目标并回传 `yui_guide_chat_cursor_anchor`；PC 端 Ghost Cursor 视觉表演仍由全局透明 overlay 承载。首页 Director 必须把外置聊天窗写入/回传的可见 screen cursor 点换算回 scene 锚点，不允许退到页面右下角代理坐标，也不得在 `day2_intro_context` 到 `day2_screen_entry` 之间清空 cursor 导致先消失再显示。 | 使用 `tutorial.avatarFloating.day2.introVoiceUsed` 和 `avatar_floating_day2_intro_voice_used`；不显示“现在说一句/继续打字”选项，不补高亮语音按钮。 |
| `day2_intro_context` 分支 B：“昨天你一直在噼里啪啦打字...” | `sad`，轻委屈动作 | 同分支 A；台词结束后直接进入下一 scene，不等待选择或超时。 | 不把 sad 当 angry，不触发退出。 |
| `day2_screen_entry`：“在跟我通语音电话的时候...” | `happy`，撒娇邀请动作 | T+0 primary 切到 `#${p}-btn-screen` 圆形高光；T+220ms Ghost Cursor 从聊天窗起点平滑移动到按钮并停留，不播放模拟点击动画，不调用真实 click，不能先隐藏再显示，也不能闪现到页面中心；360ms 后继续。 | 不触发真实限制提示，不打开来源列表，不同时高亮语音按钮。 |
| `day2_screen_entry_invite`：“快让我也看看你眼前的世界...” | `happy`，撒娇邀请动作 | 继续高亮 `#${p}-btn-screen`；Ghost Cursor 在按钮附近停留，不左右晃动、不 click。 | 不触发屏幕分享限制提示。 |
| `day2_wrap_intro`：“今天的教程到这里就结束了呢。” | `happy`，温柔收尾动作 | 收尾开始前关闭临时提示/弹窗；T+0 primary 回到胶囊输入框 `chat-input`；T+220ms cursor 从上一句主动搭话开关锚点平滑移动回胶囊输入框中间并停留；外置聊天窗回传 `input` 锚点时，首页 Director 如果发现 cursor 只是隐藏但仍有上一段按钮 position，必须先在该 position 恢复可见再 move 到胶囊输入框，不能直接闪现到胶囊输入框。 | 不触发花瓣，给下一句收尾留出完整转场。 |
| `day2_wrap_companion`：“其实只要能这样陪着你...” | `happy`，温柔收尾动作 | 继续圆角矩形高亮胶囊输入框；Ghost Cursor 在胶囊输入框附近停留，不左右晃动。 | 不触发花瓣，给最终句留出完整转场。 |
| `day2_wrap`：“我们不需要着急...” | `happy`，温柔收尾动作 | 继续圆角矩形高亮胶囊输入框；T+70% 花瓣 cue 同步启动花瓣层、清理所有高光和 cursor；完成 Day 2。 | 不保留屏幕按钮、设置按钮、角色设置或主动搭话高光，不出现高亮先消失后花瓣才启动的空档。 |

## 旧版 Day 3：互动、娱乐与摸得到的陪伴（迁移参考，不作为当前规格）

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day3_chat_tools`：“来啦来啦...” | `happy`，兴奋邀请动作 | 首个 scene T+0 primary 高亮 composer 区：`.composer-panel` 优先，其次 `.composer-input-shell`；外置聊天窗 kind `input`；Ghost Cursor 直接显示/保持在工具区中心；台词结束后清理该区域。 | 不高亮整聊天窗，不同时高亮具体工具按钮。 |
| `day3_avatar_tools` / `day3_avatar_tools_props`：“在这个小按钮里...”两句 | `happy`，互动玩耍动作 | 准备阶段关闭旧工具菜单；第一句 T+0 持续圆形高亮 `button.send-button-circle.compact-input-tool-toggle`，cursor 平滑移动到工具总按钮并模拟点击，只打开弧形工具菜单；第二句持续高亮工具总按钮，cursor 平滑移动到 Avatar 互动工具按钮并模拟点击，同时触发 Avatar 互动工具按钮点击事件显示三个小道具；台词播放完后再次触发 Avatar 互动工具按钮点击事件隐藏三个小道具。 | 工具总按钮保持 persistent 圆形高光；道具入口只展示，不出现高光或 cursor tour；不触发真实道具消耗。 |
| `day3_galgame_entry` / `day3_galgame_choices`：“快点开这个【Galgame模式】...”两句 | `surprised`，冒险期待动作 | 先收起 Avatar 道具菜单并保持工具总按钮 persistent 圆形高亮；第一句 cursor 先平滑移动到初始 Galgame 按钮位置，再切换并保持 click 态沿弧形工具栏逆时针移动 1/5 圆，移动过程中触发 `compactToolWheelRotateRequest(direction=-1, stepCount=1)`，最后切回正常态并重新移动到 Galgame 按钮中心；不强制点击 Galgame、不改 Galgame 设置。第二句保持 Ghost Cursor 停在 Galgame 按钮上直到台词播放完，使用 `cursorAction: 'hold'`，不再重新 move、不巡游选项。 | Galgame 按钮和小游戏选项不能同时与 composer 大区域重叠；不得伪造小游戏局。 |
| `day3_wrap` / `day3_wrap_ready`：“今天带你认识的这些功能...”两句 | `happy`，鼓励尝试动作 | 收尾第一句关闭工具菜单/更多菜单并清理按钮高光；T+0 primary 回胶囊输入框 `chat-input`，cursor 平滑移动到胶囊输入框中间并停留；第二句继续保持胶囊输入框圆角高亮和 cursor 停留；第二句 T+70% 花瓣 cue 清理 PC overlay spotlights、外置目标状态、工具区目标状态和 cursor；完成 Day 3。 | 不保留 Galgame 或道具菜单高光。 |

## Day 4：相处距离、主动陪伴与模型行为

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day4_intro_companion`：“今天，就让我悄悄跟上...” | `happy`，温柔靠近动作 | T+0 primary 高亮聊天窗；外置聊天窗 kind `window`；cursor move 并停留；台词结束清理聊天窗高光。 | 不提前打开设置。 |
| `day4_chat_settings`：“在这里可以决定...” | `neutral`，规则说明动作 | T+0 圆形 primary 高亮设置按钮，T+220ms cursor 移到设置按钮并模拟点击，同时调用打开设置 API；设置按钮作为 persistent 保持到 `day4_privacy_mode` 播完。设置弹窗打开后，primary 切到“对话设置”按钮，cursor 移过去并调用 `show-settings-sidepanel:chat-settings`；侧边栏出现后取消按钮主高亮，primary 切到 `[data-neko-sidepanel-type="chat-settings"]`，cursor 在侧边栏内椭圆运动到本句结束。 | 不高亮整张设置弹窗，不创建入口加面板的 union 范围。 |
| `day4_model_behavior`：“如果你想要看到...” | `happy`，活泼说明动作 | 先收起对话设置侧边栏；设置按钮 persistent 继续保留；T+0 primary 从对话设置侧边栏平滑过渡到“动画设置”按钮；T+220ms cursor 移到该按钮后调用 `show-settings-sidepanel:animation-settings`；侧边栏出现后 primary 切到 `[data-neko-sidepanel-type="animation-settings"]`，cursor 在侧边栏内椭圆运动到本句结束。 | 不在本段切锁定/离开按钮，不高亮整张设置弹窗。 |
| `day4_gaze_follow`：“开启这个功能后...” | `happy`，活泼说明动作 | 继续使用动画设置面板；设置按钮 persistent 继续保留；T+0 primary 从动画设置侧边栏平滑移动到“跟踪鼠标”按钮外层开关行；T+220ms cursor 移到该按钮。 | 不点击、不改值。 |
| `day4_privacy_mode`：“这个是控制人家能不能看屏幕...” | `neutral`，安全边界动作 | 清理前段动画设置侧边栏，不展开 `interval-proactive-vision`，不显示隐私模式旁边侧边框；设置按钮 persistent 保持到本句结束；primary 高亮隐私模式按钮 / `#${p}-toggle-proactive-vision` 外层开关行；T+220ms cursor move 到隐私模式按钮；本句播完后调用 `closeSettingsPanel()` 收起设置弹窗。 | 不同时高亮主动搭话或聊天设置，不改变锁定或隐私状态。 |
| `day4_model_lock`：“总是不小心触碰到...” | `happy`，活泼说明动作 | `cleanupBefore` 确保前段与设置按钮 persistent 已清理；primary 圆形高亮 `#${p}-lock-icon`，cursor 平滑移动到锁定按钮并停留。 | 只展示按钮，不真的锁定。 |
| `day4_return_home`：“如果你现在需要专注...” | `happy`，活泼说明动作 | primary `#${p}-btn-goodbye`，可 secondary 框 `#${p}-btn-return`，cursor move 并停留。 | 只展示按钮，不真的让 Yui 离开。 |
| `day4_wrap`：“真正舒服的陪伴才不是...” | `happy`，温柔收束动作 | 收尾前关闭设置弹窗和侧边栏，恢复用户配置；T+0 primary 回胶囊输入框，Ghost Cursor 停在输入框中心；T+70% 花瓣 cue 清理高光/cursor；完成 Day 4。 | 不保留隐私/动画面板高光。 |

## Day 5：个性化与长期配置

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day5_character_settings`：“从今天起...” | `happy`，专属感动作 | T+0 primary 高亮聊天窗；约 1 秒后聊天窗高光消失，cursor 从聊天窗平滑移动到设置按钮，同时圆形高亮设置按钮；设置弹窗打开后 primary 切到“角色设置”按钮，cursor 移到按钮并展开 `[data-neko-sidepanel-type="character-settings"]`；随后 primary 平滑过渡到角色设置侧边栏，cursor 在侧边栏内椭圆运动到本句结束。 | 不跳转子页面，不同时高亮整个设置弹窗。 |
| `day5_character_panic`：“咦，这里居然还能把我换掉吗...” | `surprised`，`settings-peek-panic` 自定义慌乱优先 | 播放期间继续 primary 高亮 `[data-neko-sidepanel-type="character-settings"]`；operation 按本句时长运行慌乱演出；本句播完后清除高光并收起角色设置侧边栏。 | 不切到模型管理或声音克隆入口，不阻止后续真实操作。 |
| `day5_memory_entry`：“如果你不小心忘记了...” | `angry`，傲娇动作，非 angry exit | operation `show-settings-menu:memory` 打开设置菜单 memory；T+0 primary `#${p}-menu-memory`；T+220ms cursor move 并停留；不打开 `/memory_browser`。 | 不把记忆入口和角色设置面板同时高亮。 |
| `day5_wrap`：“好啦好啦...” | `happy`，期待定制动作 | 收尾前关闭设置和侧边栏；T+0 primary 回胶囊输入框，Ghost Cursor 停在输入框中心；音频 `好啦好啦，快去试试这.mp3`；T+70% 花瓣 cue 清理所有高光/cursor；完成 Day 5。 | 不保留记忆入口高光。 |

## Day 6：Agent、任务 HUD 与能力节奏

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day6_intro_agent`：“噔噔噔噔...” | `happy`，兴奋介绍动作 | T+0 primary 圆形高亮 `#${p}-btn-agent`；T+220ms cursor click；operation `open-agent` 真实打开 `#${p}-popup-agent`；弹窗出现后 persistent 可框 Agent 面板。 | 按钮点击后清理按钮高光，面板和当前控件分层不重叠。 |
| `day6_agent_status_master`：“快跟我老实交代...” | `neutral`，等待动作 | 显式 timeline 在旁白启动后立即执行 `day6-plugin-open-agent-panel-flow`；operation 内先确保猫爪悬浮按钮可见，再圆形高亮猫爪入口；Ghost Cursor 平滑移动到猫爪按钮，随后播放模拟点击；点击动画开始时必须同步触发猫爪按钮点击事件，真实展开猫爪/Agent 侧边栏并记录预览状态。move 基准 2800ms（按本句音频缩放到 2100-5200ms），click 基准 620ms（缩放到 480-1200ms）。 | 本句只负责“高亮猫爪 -> cursor 移到猫爪 -> 模拟点击并触发真实点击 -> 展开猫爪侧边栏”；按钮点击后清理按钮高光，面板和当前控件分层不重叠。 |
| `day6_plugin_side_panel`：“除了之前介绍的功能...” | `happy`，自信炫耀动作 | 显式 timeline 在旁白启动后立即执行 `day6-plugin-open-management-panel-flow`；operation 内先圆形高亮用户插件按钮 `#${p}-toggle-agent-user-plugin`，Ghost Cursor 平滑移动到用户插件按钮；移动/点击用户插件按钮期间同步展开用户插件按钮旁边的管理面板按钮。随后取消用户插件按钮高亮，改为高亮管理面板按钮 `#neko-sidepanel-action-agent-user-plugin-management-panel`；管理入口使用无 padding、左右拉长且上下各扩 10px 的虚拟圆角高光；Ghost Cursor 再平滑移动到管理面板按钮并模拟点击，插件 dashboard 窗口等待 900ms 以稳定进入下一幕 handoff；成功后保留插件 dashboard preview state 供下一幕 handoff 使用。两段 cursor move 基准 1120ms（按本句音频缩放到 840-2100ms）且必须传 `exactDuration: true`，click 基准 480ms（缩放到 360-900ms）。 | 用户插件按钮和管理入口必须分阶段高亮：高亮用户插件时不同时高亮管理入口，切到管理入口前先取消用户插件高亮；不能再框整个侧边栏。 |
| `day6_plugin_dashboard`：“有了它们...” | `happy`，插件页接力演出 | 显式 timeline 在旁白启动后立即执行 `day6-plugin-dashboard-handoff-flow`；operation 内隐藏首页/PC 全局 cursor、把旁白时间戳与音频 URL 交给插件 dashboard runtime；插件页 runtime 当前负责自绘可见 pointer、指认侧边栏插件入口并巡视 `plugin-main`，完成后关闭教程创建的插件窗口、折叠用户插件侧栏、关闭 Agent 面板并恢复首页 cursor。 | 插件页不得自行写完成态；首页 Manager 仍统一处理 skip、interrupt、done 收口。 |
| `day6_agent_task_hud`：“看这里看这里...” | `happy`，打工热情动作 | `cleanupBefore` 清理 Agent 面板；operation `show-task-hud` 调 `AgentHUD.showAgentTaskHUD()`；T+0 primary `#agent-task-hud`；T+220ms cursor 只移动到 HUD 并停留，不 tour HUD 内控件。 | 不创建假任务；不与 Agent 面板高光并存。 |
| `day6_agent_task_hud_control`：“你要是计划有变...” | `happy`，打工热情动作 | 继续 primary `#agent-task-hud`，复用上一句 `day6_agent_task_hud` 高光 key；Ghost Cursor 再次确保移动到 HUD 并停留，不巡游内部按钮，也不做椭圆运动。 | 不创建假任务；不与 Agent 面板高光并存；与上一句切换时高光不换 key，避免闪烁。 |
| `day6_wrap_cleanup`：“呼...” | `happy`，安心收束动作 | 关闭 Agent 面板、侧边栏和教程临时 HUD，恢复进入前 HUD 状态；primary 回胶囊输入框，Ghost Cursor 保持当前位置，不再主动移回输入框，避免与轻微对抗插播移动抢占；外置聊天窗 cleanup 必须保留 input spotlight/cursor target，不得先清空后重发。 | 不保留 HUD 高光；之后不再追加 cursor move。 |
| `day6_wrap`：“你可以放心地继续...” | `happy`，安心收束动作 | primary 继续保持胶囊输入框；Ghost Cursor 保持在输入框中心，不再移动；中文音频 11.34s，T+70%（约 7.94s）必须启动花瓣转场、模型渐隐并清理所有高光/cursor，不得等台词结束后才启动；完成 Day 6。 | 不保留 HUD 高光。 |

## Day 7：毕业、进阶入口与共生约定

| scene/台词 | emotion/动作 | 高光与 Ghost Cursor 时序 | 不重叠要求 |
| --- | --- | --- | --- |
| `day7_memory_review`：“七天前...” | `neutral`，仪式感回顾动作 | 只播放台词和情绪动作；不创建 primary、不移动 Ghost Cursor、不打开设置菜单或敏感记忆页。 | 不展示或朗读具体记忆内容。 |
| `day7_memory_control`：“这些小脚印...” | `happy`，温柔积极动作 | operation 打开设置菜单 memory；T+0 primary `#${p}-menu-memory`；T+220ms cursor move 到入口；只说明可整理/可放走，不点击保存、整理或清理。 | 不额外框存储或云存档入口。 |
| `day7_graduation_wrap`：“微风还在窗边...” | `happy`，毕业收束/花瓣优先 | 收尾前清理所有临时状态；T+0 primary 回胶囊输入框；T+220ms cursor move 到输入框中心并停留；T+70% 最终花瓣 cue 隐藏 cursor、清理所有高光并写入 Day 7 完成态。 | 不保留任何跨页入口高光。 |

## 外置聊天窗等价规则

外置聊天窗不直接使用首页 DOM 高光，也不创建聊天窗本地 spotlight DOM，必须用 `TutorialInteractionTakeover` 发送 kind。PC 全局 overlay 启用时，外置聊天窗的 cursor kind 用于解析目标、回传 `yui_guide_chat_cursor_anchor`，并可把 show/move/click/wobble/hide 的 cursor patch 和 `spotlights` patch 发送到 PC 全局 overlay；外置聊天窗自身不得渲染本地 `#yui-guide-chat-cursor` 或 `#yui-guide-chat-spotlight`。首页 Director 收到外置聊天窗锚点时必须保持 cursor 轨迹连续：可见 cursor 直接 move；隐藏但有 position 的 cursor 先在旧 position 恢复可见，再 move 到新锚点；没有可用 position 时才允许直接 show 到新锚点。

外置聊天窗的 spotlight 更新必须是“显式清理才清空”：`applyYuiGuideChatSpotlight('')` 可以发送 `spotlights: []`，但同一个 kind 的目标短暂不可见、React 重排或窗口尺寸刷新时，只能复用上一帧 PC rect，不能发空数组再重建，否则胶囊输入框这类目标会在对抗暂停或聊天窗重排时闪烁。

首页和外置聊天窗同时向 PC 全局 overlay 写入同一个 `tutorialRunId` 时，必须共用 `localStorage.yuiGuidePcOverlaySequence` 生成单调递增 `sequence`。真实鼠标移动触发对抗时，首页会高频发送 cursor-only 更新；如果首页和聊天窗各自维护本地 sequence，PC 主进程 `tutorial-global-overlay-service.acceptEnvelope()` 会把稍后到达但 sequence 落后的外置聊天窗 spotlight 更新判为 stale，胶囊输入框高亮就会在对抗动画期间闪烁。当前修复点是 `static/tutorial/yui-guide/overlay.js` 与 `static/app-interpage.js` 都通过共享 storage sequence 发包。

除了共享 sequence，外置聊天窗还必须共享首页传入的 `tutorialRunId`。`TutorialInteractionTakeover.postExternalChatCommand()` 发出外置聊天命令前只能从 `localStorage.yuiGuidePcOverlayRunId` 读取已有 canonical run，并写入 `tutorialRunId` / `pcOverlayRunId`；`ChatWindowAdapter.beforeExternalizedSpotlight(kind, meta)` 的 `meta.tutorialRunId` / `meta.pcOverlayRunId` 也只能来自同一个已有 run，不能在 hook 内创建新 run。`normalizeYuiGuideBridgeMessage()` 只能用已有 runId 补齐消息，优先级是 `message.tutorialRunId`、已有 storage/override run、最后才是 `message.pcOverlayRunId`；它不能调用会创建新 run 的 helper。外置聊天窗收到 guide-scoped relay 后要通过 `rememberYuiGuidePcOverlayRunId(message.tutorialRunId)` 复用 canonical run，并把该 runId 继续传给 spotlight、cursor、drag、arc、保留 spotlight 和 retry patch。真实鼠标对抗期间首页发送 cursor-only patch 时，外置聊天窗仍可继续发送胶囊输入框 spotlight patch，但两者必须落在同一个 active run 上；否则 PC renderer 会在 `spotlightCount: 1` 与 `spotlightCount: 0` 之间来回切换。

外置聊天窗 scene 中触发真实鼠标对抗时，首页仍负责计算 Ghost Cursor 的反向/回弹动画，但 `GhostCursorController.reactToUserMotion()` / `resistTo()` 必须带 `forcePcOverlay: true`，由首页 `YuiGuideOverlay.moveCursorTo()` 调用 PC bridge 的 `moveCursorOnlyTo()`。这类 patch 只发送 `{ cursor }`，不能经过 `completeStateStore.applyPatch()` 拼完整状态，否则会把外置聊天窗已经维护的 spotlight 状态覆盖掉；也不能只更新首页内部位置不发 PC patch，否则会出现“对抗台词播放了，但屏幕上 Ghost Cursor 没有反向动画”。`forcePcOverlay` 判断只能检查 PC bridge 是否暴露 `moveCursorOnlyTo()`，不能再依赖 `isPcOverlayActive()` 或 `shouldForwardCursorToPcOverlay()`，因为外置聊天窗接管期间首页正常 cursor 输出会被 suppression 主动关闭。对抗入口还必须在 `cursor.hasVisiblePosition()` 为 false 时，从首页当前 cursor position 或最近的外置 `yui_guide_chat_cursor_anchor` 恢复内部可见状态；否则 Day 1 首句前半句可对抗，后半句因首页内部 cursor visible 状态脱节而直接 return。对应护栏在 `tests/unit/test_avatar_floating_day1_round_contracts.py`、`static/yui-guide-visual-controllers.test.cjs` 和 `N.E.K.O.-PC/test/tutorial-overlay-z-order-contract.test.js`。

Day 1 首句问候还有一条独立闪烁风险：如果它被接回 timeline `day1-intro-greeting-flow` legacy operation，旧 operation 可能绕过 generic externalized scene 分支，自己手写 `hideHomeCursorForExternalizedChat()`、`setExternalizedChatSpotlight('input')`、`setExternalizedChatCursor('')` 或取消 suppression。当前正确实现是普通 scene + `day1-intro-greeting-performance`，台词、高光和 cursor 都由 SceneOrchestrator 通用路径处理；否则即使共享 sequence 正确，真实鼠标对抗时也会因为首页 cursor 状态和外置聊天窗胶囊 spotlight/cursor 被不同路径反复接管而闪烁。

外置聊天窗的通用 `cursorAction: click` 可以由外置窗口解析目标后发送一次 `cursor:*:click` patch 到 PC 全局 overlay，点击图切换时长沿用统一 `DEFAULT_CURSOR_CLICK_VISIBLE_MS=420`；外置聊天窗回传 anchor 时 `effect` 必须为空，避免锚点同步触发第二次点击。
Day 3 的外置工具 click 是例外，必须采用和 Day 2 首页 click 相同的 movement-driven 架构：外置窗口只解析目标并回传 `yui_guide_chat_cursor_anchor`，不得发送 `cursor:*:click` patch；cursor movement helper 先等待目标 anchor 到达，再由首页 Director 调用 `clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS)` 启动模拟点击，并通过 `onClickStart` 并行触发真实 operation；主流程不得为 Day 3 单独维护一条 click scene 定时分支。PC overlay 移动结束后回传的 anchor 必须带 `settled: true`；首页收到 settled anchor 时只同步内部 cursor 坐标，不再向 PC overlay 发送第二次可见 move。若首页开始等待时 anchor 尚未回传，必须等待未来 anchor 或超时，不能因当前没有 move promise 就提前启动点击。

`cursorAction: move` 的外置聊天窗场景同样必须等待 `waitForExternalizedChatCursorMove()`：外置窗口解析目标并发送 move patch，PC overlay 移动完成后回传 settled anchor，首页 Director 再继续后续 operation、cue 或 scene 收口。Day 1 最后一句 `day1_takeover_return_control` 是该规则的回归样例；如果它没有等待外置 move anchor，Ghost Cursor 会一直停留在上一句的键鼠控制开关。如果 `capture_cursor -> return_control` 没有保留外置 cursor 目标，真实鼠标对抗会只进入打断/台词链路，却没有可见 Ghost Cursor 位置用于播放轻微反向对抗动画。

| 首页语义 | 外置 kind | 要求 |
| --- | --- | --- |
| 聊天窗整体 | `window` | 高光整个独立聊天窗，cursor 在窗口中心停留。 |
| 输入/工具区 | `input` | 只高光 composer 区，不高光整窗。 |
| 胶囊输入框 cursor 精确目标 | `capsule-input` | 只解析胶囊输入框/胶囊主体的 screen anchor，用于收尾或需要 cursor 精确落到输入框中心的 scene；不得被 `shouldSuppressYuiGuideChatLocalFx()` 当成本地效果吞掉。 |
| Avatar 工具按钮 | `avatar-tools` | 只高光真实 Avatar 工具按钮。 |
| Avatar 道具项 | `avatar-tool-items` | 当前 Day 3 主线不使用；如后续单独演示道具项，只高光真实道具按钮，不加外层第二圈。 |
| Avatar 工具按钮加道具项 | `avatar-tools-and-items` | 当前 Day 3 主线不使用；如后续需要同时展示工具按钮和道具项，最多包含工具按钮加真实道具，不能再叠加第二个外框。 |
| Galgame | `galgame` | 只高光 Galgame 按钮，不自动改设置。 |
| 小游戏选项 | `mini-game-choices` | 只高亮真实 `mini_game_invite` 选项，不伪造选择局。 |

外置聊天窗同样必须保证 skip 有效。跨窗口 skip 分两类：坐标命中首页 `#neko-tutorial-skip-btn` 时转发坐标并由首页真实按钮 click；明确 skip 源如插件页按钮或 angry exit 直接调用 Manager 统一 skip 入口。

## Bug 处理与回归规范

处理新手教程期间的 Ghost Cursor、高光、外置聊天窗或 PC 全局 overlay 问题时，先按链路排查，不要只改 scene 文案或 target：

1. 先确认上一句是否记录了可复用 cursor anchor；跨 scene 问题通常要检查 `rememberAvatarFloatingSceneCursorAnchor()` 和下一句的 `resolveAvatarFloatingCursorStartPoint()`。
2. 再确认当前 scene 是否进入外置聊天窗分支；只要存在 `externalizedSceneTargetKind`，首页 Director 的 `moveAvatarFloatingCursor()` 路径就可能让外置窗口负责解析和发送 PC overlay patch，必须检查外置窗口是否解析目标、发送 patch、回传 `yui_guide_chat_cursor_anchor`。
3. `cursorAction: 'move'` 和 `cursorAction: 'click'` 都要检查等待逻辑。click 需要真实 operation 与模拟点击并行启动；move 也需要等待外置 move anchor settled 后再继续后续 operation 或 cue。
4. 检查 kind 映射和 suppress 规则：例如 `chat-capsule-input` 必须映射到 `capsule-input`，且 `input` / `capsule-input` 不能被 `shouldSuppressYuiGuideChatLocalFx()` 吞掉。
5. 检查清理时机：普通 scene 切换只能清 spotlight/panel，不能 hide/clear cursor；只有 skip、angry exit、destroy/stop 和收尾花瓣 cue 可以清空 cursor。
6. 检查 PC overlay 通道是否独立：`spotlights`、`cursor`、`petal` 任一通道更新时都必须经 complete-state store 带上当前可见状态，不能因为更新高光顺手清掉 cursor 或花瓣。
7. 检查对抗暂停是否只是冻结视觉：`PauseCoordinator.pauseForResistance()` 只能暂停 cursor/spotlight mutation，高光应保持上一帧，Ghost Cursor 在当前位置做对抗动画后继续原目标；不能销毁后重建 spotlight。若 scene 处于外置聊天窗接管状态，对抗动画必须走首页 bridge 的 `moveCursorOnlyTo()` cursor-only patch，并保留外置聊天窗 spotlight。
8. 检查旧版 operation 遗留：Day 1 统一到 `round.scenes` 后，历史 `day1-managed-scene:*` 只能作为语义参考，不能覆盖新版 scene 的 `cursorTarget`、spotlight variant、外置 move 等待和通用收口。
9. Bug 修复必须同步补源码契约测试；Ghost Cursor/高光结构类问题优先放进 `static/yui-guide-day1-round-structure.test.cjs` 或相邻的分日结构测试，PC overlay z-order/relay 问题同步跑 N.E.K.O.-PC 的 overlay contract 测试。
10. Day 3 Galgame choices cursor 上移这类问题要同时看两条写入源：外置聊天窗解析目标并发送 PC overlay cursor，首页 overlay 也可能因旧 scene 动画继续转发 cursor。外置聊天窗接管 cursor 后，首页只能更新内部 cursor 位置并保持 DOM suppression，不能继续向 PC overlay 发送 cursor patch；Galgame freezePoint hold 只能用一次 `cursorHoldSettleMs` 补采样，不能加多 timer 轮询。
11. 只在真实鼠标移动触发对抗时出现的外置聊天窗高亮闪烁，要优先检查 `sequence` 而不是只查 DOM rect：看 `static/tutorial/yui-guide/overlay.js` 的首页 bridge、`static/app-interpage.js` 的外置聊天窗 bridge 是否都调用共享 sequence helper；再对照 PC 主进程 `acceptEnvelope()` 的 stale 逻辑，确认外置 spotlight 更新没有被首页 cursor flood 压成旧包。
12. 如果 sequence 正确但胶囊输入框高亮仍只在对抗期间闪烁，继续检查 `tutorialRunId` 是否分裂：PC 日志里若出现 `yui-guide-chat-*` 的 `spotlightCount: 1` 与 `yui-guide-*` 的 `spotlightCount: 0` 交替，就说明外置聊天窗和首页在抢同一个 `activeRunId`。修复方向不是改高亮动画，而是让 `static/app-interpage.js` 的 relay、BroadcastChannel、spotlight retry 和 cursor patch 全部复用消息里的 canonical runId，并禁止 stale 处理把 canonical run 旋转成新的 chat-owned run。
13. 看到“模型旁悬浮按钮已显示，但模型本体不可见”时，优先查 Live2D 视觉层而不是模型路径：确认 `live2dManager.getCurrentModel()`、`pixi_app.stage/renderer`、`#live2d-container/#live2d-canvas` 的 inline/computed `opacity/display/visibility`，以及 `body.yui-guide-live2d-preparing`、`body.yui-guide-return-petal-fade` 和 `--yui-guide-return-avatar-opacity` 是否残留。按钮存在只说明 `setupFloatingButtons()` 走过，不代表 WebGL canvas 已揭示。修复必须同时覆盖 `app-ui` 快速显示路径和教程 `ensureTutorialLive2dRenderActive()`，不能只改模型加载或每日 scene 配置。

## 验收清单

1. 每句台词都能在本文表格中找到 emotion、动作规则、高光目标和 Ghost Cursor 时序。
2. 所有高光目标都能映射到真实 DOM 或外置 kind；不可见时只允许降级到同组容器，不允许伪造元素。
3. 同一时刻没有重叠 spotlight；尤其是设置弹窗/侧边栏、聊天 composer/工具按钮、Agent 面板/开关、HUD/Agent 面板不能重叠。
4. Day 3 Avatar 工具阶段持续圆形高亮胶囊工具总按钮；`day3_avatar_tools` 只让 Ghost Cursor 慢移到工具总按钮并点击打开弧形菜单，`day3_avatar_tools_props` 才允许 Ghost Cursor 平滑移动到 Avatar 互动工具并点击打开/收起三个小道具；不高亮三个道具项，也不让 cursor 移动到三个道具项；小游戏三选项如真实出现，只出现纯圆形高光，不出现猫耳、猫爪或第二层边框。
5. 所有真实点击只发生在文档明确允许的位置：Day 1 历史小条 API 打开/收起、Day 3 工具总按钮与 Avatar 工具按钮打开/收起、Day 6 Agent/插件入口等；其余按各日文档指定的非点击动作处理。Day 1 屏幕分享按钮只展示入口，不播放 Ghost Cursor 模拟点击，也不调用真实按钮 click。允许真实点击且 `cursorAction: 'click'` 的 scene，真实 API/DOM click 必须和 Ghost Cursor 点击动画并行启动。
6. Day 3 工具总按钮必须触发真实按钮 `click()` 打开弧形菜单；Avatar 工具阶段必须播放 Avatar 按钮 click 动画并发送按钮 click 请求，同时用 `setAvatarToolMenuOpen()` 主机 API 打开/关闭道具菜单，避免教程锁定期间 disabled 按钮吞掉 DOM click 后三个道具不显示。React 三个道具只在 `toolMenuOpen && compactInputToolFanOpen` 同时成立时渲染，因此 Avatar 菜单 open request 必须同步撑开/保留弧形菜单，教程锁定不能把承载道具菜单的 fan 状态关成 false。PC 端 `neko:tutorial-overlay-relay`、`postMessage.__nekoTutorialOverlayRelay` 和 BroadcastChannel 可能投递相同 timestamp 的同一状态消息；`yui_guide_set_avatar_tool_menu_open` 必须像 `yui_guide_set_compact_tool_fan_open` 一样绕过去重，`yui_guide_click_avatar_tool_button` 则保持去重以防双击反向收起。
7. 每句台词 emotion 都在 `yui-origin` 有效动作池内；自定义演出优先，motion 缺失能降级。
8. `#neko-tutorial-skip-btn` 在教程期间可见、可点、白名单放行；点击后立刻进入统一 skip。
9. skip、destroy、pagehide、angry exit 都会清理高光、cursor 和 PC 全局透明 BrowserWindow，并恢复用户模型；angry exit 必须等生气音频/演出结束后再进入统一 skip/destroy。
10. Day 1-7 收尾花瓣转场只属于自然完成路径：70% cue 清理 cursor/highlight，正常完成才播放。skip、angry exit、destroy、pagehide 都不播放正常收尾花瓣，也不播放 Day 1 归还控制权的挥手/模型回归动作；如果花瓣转场已经开始，`isStopping()` 后必须立即 `finish()` 清层，不等待自然结束。
11. Day 1 历史小条、Day 1 handoff 子页、Day 3 弧形菜单和 Day 6 空台词过渡 scene 都有明确高光/cursor/skip 时序。
12. 独立或外置页面只能做目标解析、真实操作和 skip 等价适配；教程 spotlight 和 Ghost Cursor 必须回到 N.E.K.O.-PC 全局透明教程 overlay，结果必须回到首页 Manager 的统一生命周期。
13. 外置聊天窗的 `cursorAction: 'move'` 场景必须和 click 一样等待目标 anchor；Day 1 最后一句必须能从键鼠控制开关平滑移动到胶囊输入框中心，不能停在上一句按钮位置。
