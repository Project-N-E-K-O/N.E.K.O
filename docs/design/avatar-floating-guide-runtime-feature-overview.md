# 新手教程各功能与框架能力说明

本文面向接手开发者，说明 7 日新手教程当前包含哪些功能、这些功能由哪些框架模块承接，以及重构后的优化效果。逐句台词和每日镜头以 [7 日新手教程完整开发文档](avatar-floating-7day-complete-guide-dev.md) 为准；重构阶段和实现细节以 [新手教程框架重构方案](tutorial-framework-refactoring.md) 为准。

## 功能总览

| 功能域 | 覆盖内容 | 主线天数 | 当前边界 |
| --- | --- | --- | --- |
| 初见与聊天入口 | 文本输入、语音入口、设置一瞥、主动搭话入口、插件管理预览 | Day 1 | 主线只演示入口和安全边界，不强改用户配置。 |
| 语音与屏幕分享 | 语音承接、屏幕分享入口、通话后分享屏幕的规则 | Day 2 | 主线只触发真实提示和入口指认，不选择屏幕、不强开通话。 |
| 娱乐与互动工具 | Avatar 工具、Galgame、小游戏/点歌/翻译支线 | Day 3 | 主线演示 Galgame 和 Avatar 工具，更多工具放到剧场后支线；Galgame 入口台词中 Ghost Cursor 先到 Galgame 按钮，点击态沿弧形工具栏逆时针移动 1/5 圆并触发反向转 1 步，再恢复正常态回到 Galgame 按钮；下一句用 `cursorAction: hold` 保持停在 Galgame 按钮直到台词播放完。 |
| 陪伴距离设置 | 对话设置、模型行为、视线跟随、隐私模式、锁定、离开/回来 | Day 4 | 设置巡游由 `SettingsTourFlow` 承接；chat/model 已走 schema 试点，其他差异场景保留手写实体。 |
| 个性化设置 | 角色设置、模型/声音/API 入口、记忆浏览入口 | Day 5 | 主线只做入口级展示，不替换模型、不克隆声音、不改写记忆。 |
| Agent 与插件 | Agent 状态、键鼠权限、插件管理、任务 HUD | Day 6 | operation 由 `OperationRegistry` 注册分发；插件页 handoff 回到首页统一生命周期。 |
| 记忆与毕业 | 记忆回顾、存储与长期陪伴提示、毕业收尾 | Day 7 | 主线不执行云存档上传/下载，不展示账号或路径细节。 |
| 跳过与对抗 | skip、真实鼠标轻微对抗、强打断、angry exit | 全程 | skip/termination/resistance 已统一收口，angry exit 不写完成态。 |
| 视觉演出 | Ghost Cursor、高光、花瓣、模型替身、PC overlay / DOM fallback | 全程 | 视觉 controller 和 renderer 分层，PC overlay update 携带完整可见状态；PC 端透明 overlay 负责实际渲染 cursor、spotlight、petal 和 avatarStandIn。 |
| 跨窗口同步 | 外置聊天窗、插件页、PC native relay、BroadcastChannel | Day 1/3/6 等 | command bus / target registry / chat adapter 承接消息和目标解析。 |

## 重构后的运行框架

```text
Daily guide config
  -> UniversalTutorialManager
  -> RoundPreludeController
  -> YuiGuideDirector
  -> SceneOrchestrator
       -> generic scene core
       -> OperationRegistry
       -> SettingsTourFlow
       -> visual controllers / overlay renderer
       -> bridge command bus / target registry / chat adapter
```

核心原则：

1. 每日 guide 文件只描述当天 round 和 scene 配置，不重复声明公共 helper。
2. `SceneOrchestrator` 负责 round/scene 编排，Director 更多作为能力入口和兼容层。
3. `SettingsTourFlow` 负责设置巡游类 scene，Day4 chat/model 已用 schema 描述稳定面板巡游。
4. `OperationRegistry` 负责真实 UI 操作，不再在 Director 中堆长 if 链。
5. 【记忆浏览】里的 7 日教程重置入口只做“清对应 day 状态 + 标记 pending/manual reset + 清首次提示标记”，不得立即启动教程，也不得再维护或播放一套 reset 专用步骤表；`tutorial/avatar/floating-guide-reset.js` 中的旧播放器已删除。用户刷新 Neko 后，正式 7 日教程流程统一读取 pending day 并启动对应 day。
6. 视觉层由 controller/renderer 分层承接，不让 scene handler 直接拥有 cursor、spotlight、petal、avatar stand-in 的底层渲染细节。
7. 跨窗口能力走 command bus、target registry 和 chat adapter，避免首页、外置聊天窗、PC native relay 各写一套消息协议。
8. listener、timer、interval、raf 和 pagehide 临时清理走 scoped resources，降低重复启动和快速 skip 后的残留风险。

PC 全局透明 overlay 的当前实现边界（按 2026-06-14 代码审计校正）：

1. 网页侧 `TutorialOverlayRenderer` 维护 spotlight、cursor、petal、avatarStandIn 的完整可见状态，并通过 `YuiGuidePcOverlayBridge` / `app-interpage` 发给 PC。
2. PC 主进程 `N.E.K.O.-PC/src/tutorial-global-overlay-service.js` 负责 runId/sequence 防旧包、按显示器拆分 spotlight/cursor/petal，并保留 `avatarStandIn` 给每个透明窗口。
3. PC 透明窗口 `N.E.K.O.-PC/src/preload-tutorial-global-overlay.js` 负责绘制 spotlight、Ghost Cursor、petal 和 `.avatar-stand-in`；替身位置规则与 DOM fallback 保持一致。
4. 网页侧 skip、angry exit、自然结束、destroy 或 stop 后会走 `UniversalTutorialManager.clearPcTutorialGlobalOverlay()` / `clearAllTutorialLifecycles()`。`clearPcTutorialGlobalOverlay()` 从 `localStorage.yuiGuidePcOverlayRunId` 读取当前 runId，调用 `window.nekoTutorialOverlay.clear({ reason, tutorialRunId })`，并向聊天窗 relay `yui_guide_tutorial_lifecycle_ended`；`app-interpage.js` 收到该 relay 后也会清理外置聊天窗侧的 spotlight/cursor 状态并再次调用 native clear。
5. PC overlay `clear()` 当前实现会把当前 `tutorialRunId` 记入 closed run 集合、清空 `activeRunId` / `activeState`、停止 z-order reassert timer，向透明窗口发送 inactive state，并销毁所有 overlay BrowserWindow；同一 runId 的 delayed `begin()` / `update()` 会被 stale 拒绝。
6. 首页 overlay bridge 与外置聊天窗 bridge 会同时向同一个 `tutorialRunId` 写入 PC overlay；两边必须共用 `localStorage.yuiGuidePcOverlaySequence` 分配全局单调 `sequence`。否则真实鼠标对抗期间首页 cursor-only 更新会把 PC 主进程 `activeSequence` 推高，外置聊天窗稍后发送的胶囊 spotlight 更新会被 stale 拒绝，表现为高亮闪烁。
7. 外置聊天窗接管 cursor/spotlight 时，真实鼠标对抗动画仍由首页 `GhostCursorController` 驱动，但必须通过 `forcePcOverlay` 发送 PC cursor-only patch；该 patch 只包含 `cursor`，不得走 complete-state store 重写 spotlight。`forcePcOverlay` 只能以 PC bridge 的 `moveCursorOnlyTo()` 能力为准，不能依赖首页正常 cursor 转发状态，因为 externalized scene 会 suppress 首页 cursor output。对抗入口还要在首页内部 cursor 不可见时从当前 position 或最近外置 anchor 恢复可见状态，否则会出现同一句台词前半段可对抗、后半段直接 return 的现象。
8. Day 1 首句问候不应再是 timeline `day1-intro-greeting-flow` legacy operation。PC 端不需要音频点击激活，首句应作为普通 scene 使用 `target: 'chat-input'`、`cursorTarget: 'chat-capsule-input'`、`cursorAction: 'move'`，只把 hug/gift 演出挂到 `day1-intro-greeting-performance`；否则首句播放期间触发真实鼠标对抗时，胶囊输入框高光会闪烁。
9. Day 1 最后一句 `day1_takeover_return_control` 必须保留上一句 `day1_takeover_capture_cursor` 的外置 cursor 链路并等待 `chat-capsule-input` settled anchor。否则对抗机制可计数和播放台词，但 Ghost Cursor 没有稳定可见位置，表现为无对抗动画。
10. `N.E.K.O.-PC/test/tutorial-overlay-z-order-contract.test.js` 已覆盖 clear 后销毁透明窗口、下一轮重新创建窗口以及 delayed update 不复活旧 runId。发布前仍建议人工复现 Day2-7 skip 后 Pet 下层点击穿透恢复。

## 已知问题：Day2-7 skip 后下层不可点击

现象：第 2-7 天新手教程期间点击跳过按钮后，用户不能点击 Neko 层级下面的内容；第 1 天同类操作正常。

当前代码链路：

1. 跳过按钮由 `TutorialSkipController` 创建，点击后调用 `UniversalTutorialManager.handleTutorialSkipRequest()`。
2. `handleTutorialSkipRequest()` 进入 `requestTutorialDestroy('skip')`，立即调用 `clearPcTutorialGlobalOverlay('skip')`；随后 driver fallback / `onTutorialEnd()` 会进入 `clearAllTutorialLifecycles()`，再次恢复 DOM 交互、移除 skip 按钮、解锁聊天输入、广播 `neko:yui-guide:tutorial-lifecycle-ended`。
3. Director 的 `destroy()` 会销毁 `TutorialInteractionTakeover`，移除 document 捕获监听，并执行 `performFullCleanup({ destroyInteractionTakeover: true, destroyOverlay: true })`；因此网页侧普通事件拦截残留的可能性低。
4. PC 侧已补齐 clear 语义：`clear()` 发送 inactive 后会 destroy 全屏透明 BrowserWindow 并清空 `overlayWindows`，避免 inactive 透明窗口继续占用输入区域。

Day1 与 Day2-7 的差异：

1. Day1 是首次主页教程，跳过 Day1 时 `markAvatarFloatingGuideRoundOutcome()` 会把 Day1 同时计入 `completedRounds`，用于开启后续 7 日自动轮次；Day2-7 跳过只写入 `skippedRounds`，不会写 completed。
2. Day1 有 intro activation / greeting / managed takeover 等 scene 形态；Day2-7 主要走 `SceneOrchestrator.playGenericScene()`、`SettingsTourFlow` 或 operation registry。Day1 intro activation 的真实职责是等待用户在网页端产生一次点击来解锁浏览器音频播放，不再承担 PC takeover 生命周期启动。
3. 两者结束时都会汇入同一个 Manager 清理入口。PC 全局透明 overlay clear 语义已补齐后，skip-only 残留继续复现，说明问题不只在 PC 窗口销毁。
4. 进一步对比自然结束与 skip：自然结束会先等待 `SceneOrchestrator.playRound()` 正常返回，round `finally` 完整执行后才 `requestTutorialDestroy('complete')`；旧 skip 是 `TutorialSkipController` 在 `pointerdown` 里立即触发 `requestTutorialDestroy('skip')`，绕过了 round 自己 unwind 的时机。
5. 旧实现因此有两类风险：一是 `playRound finally` 没有先按自然结束顺序执行，二是如果 skip 发生在 `await refreshAndValidateTutorialLayout()` 中，清理恢复 pointer-events 后，旧的 async layout 校验可能继续执行 `rollbackTutorialInteractionState()` / `disableAllTutorialInteractions()`，再次把 Neko 层级相关元素置为不可点击。

已落实的修复与仍需验收：

1. PC 端 `tutorial-global-overlay-service.clear()` 已在发送 inactive 后销毁所有 overlay BrowserWindow，并清空 `overlayWindows`；同时保留 closed runId stale 保护，避免 delayed begin/update 复活。
2. 已扩展 `N.E.K.O.-PC/test/tutorial-overlay-z-order-contract.test.js`，覆盖 clear 后 BrowserWindow destroy、下一轮重新创建窗口，以及同一 runId delayed update 不复活窗口。
3. skip / angry exit 已改为合作式终止：skip 按钮优先调用 `director.skip('skip', 'skip')`，termination router 只设置终止 reason / stopping 标志并取消当前旁白，随后让正在跑的 round 自己返回；`startAvatarFloatingGuideRound()` 在 round 返回后统一调用 `requestTutorialDestroy(endReason)`，复用自然结束生命周期。
4. `UniversalTutorialManager` 已增加 `_tutorialInteractionApplyToken`，在 skip/onTutorialEnd/teardown 时使正在进行的交互状态应用失效；`applyTutorialInteractionState()` 和 `refreshAndValidateTutorialLayout()` 在 await 后检查 token，失效后直接退出，不再 rollback 或重新关闭 pointer-events。
5. 仍需桌面人工验收 Day2-7 skip 后点击 Neko 层级下面内容恢复，确认 Electron/系统层 click-through 和 DOM pointer-events 都已恢复。

## 重构优化效果

| 优化方向 | 重构前 | 重构后 | 效果 |
| --- | --- | --- | --- |
| 导演层职责 | Director 承担大量 scene 外壳、operation、设置巡游、视觉细节和清理逻辑。 | Director 主要保留能力入口，scene 外壳、设置巡游、operation、视觉和资源清理由独立模块承接。 | 定位问题更快，改动范围更清楚。 |
| 设置巡游 | Day2/Day4/Day5 多处重复 narration、guard、panel ellipse、finalize。 | `SettingsTourFlow` 统一处理；Day4 chat/model 走 `getPanelTourSchema()` + `playPanelTourScene()`。 | 减少重复代码，后续增加设置面板巡游可优先写 schema。 |
| 视觉演出 | Cursor、高光、花瓣、替身与 PC overlay payload 容易交叉影响。 | GhostCursor、Spotlight、PetalTransition、AvatarStandIn、OverlayRenderer 分层，PC overlay service/preload 明确接收并绘制 avatarStandIn。 | skip/destroy/scene 切换时更不容易残留或闪烁，PC 端替身不会因状态清洗被丢掉。 |
| 跨窗口通信 | 首页、聊天窗、PC relay 分散处理消息和去重。 | command bus 统一 action、队列、dedup bypass、BroadcastChannel 和 native relay。 | 外置聊天窗 ready、cursor anchor、头像和教程身份同步更稳定。 |
| 目标解析 | semantic target、external kind、本地 selector 分散在各处。 | target geometry registry 统一 external kind、shape、本地 selectors。 | cursor/spotlight 目标更一致，外置窗口和首页使用同一套语义。 |
| 资源清理 | listener/timer/interval 清理散落。 | scoped resources 统一注册和销毁。 | 重复启动、跨页、快速 skip 后残留风险降低。 |
| 回归护栏 | 主要依赖少量静态测试和人工检查。 | 增加 static、frontend、Phase 11 视觉截图回归。 | 模块边界不容易退回旧写法。 |

## 当前完成状态

1. Phase 10-14：视觉层 facade、overlay renderer、ghost cursor、spotlight、petal、avatar stand-in、highlight primitive 和公共模块文件级拆分已完成。
2. Phase 15：Day2/Day4/Day5 设置巡游实体方法已迁入 `SettingsTourFlow`，Director 对应方法只保留薄 wrapper。
3. Phase 16：`SettingsTourFlow` 内部 scene guard、finalize 参数和 panel tour helper 已收敛。
4. Phase 17：Day4 chat/model 面板巡游完成最小 schema 化试点。
5. Phase 11：视觉截图回归已补自动测试，覆盖 DOM fallback 桌面/移动和 PC overlay payload。
6. PC overlay lifecycle：网页侧 skip、angry exit、自然结束、destroy/stop 均已要求复用统一 clear 生命周期；已关闭 runId 的 delayed begin/update 会被拒绝，避免透明 overlay 被旧消息复活；替身图片由 PC overlay service/preload 接力渲染。当前 PC service 的 clear 会发送 inactive、销毁透明 BrowserWindow 并清空 overlay window map，首页和外置聊天窗的 overlay update 使用共享 sequence 防止对抗 cursor 更新压掉聊天窗 spotlight 更新；外置聊天窗接管期间的对抗动画使用首页 bridge 的 cursor-only patch，保证 Ghost Cursor 可见反向动画不清空高光。后续重点是桌面人工验收 click-through 恢复。

## 仍需人工确认的发布前验收

这些项目是发布前验收记录，不代表还要继续拆代码：

| 验收项 | 操作 | 通过标准 |
| --- | --- | --- |
| 重复启动 | 连续启动教程，第一次中途退出后再次进入。 | 不重复播放台词，不叠加 skip listener，不残留旧高光/cursor。 |
| 快速 skip | 在 Day1、Day4 设置巡游、Day5 panic 中快速 skip。 | 台词停止、视觉清理、模型恢复，不误写完成态。 |
| 跨页设置/记忆 | 教程中打开设置页或记忆页，再回首页。 | termination/bridge 消息不丢，返回后不叠加旧监听。 |
| 外置聊天窗 | 关闭并重开外置聊天窗，重复 Day1/Day3 跨窗口 cursor 链路。 | settled anchor 可恢复，move/click 不提前执行。 |
| PC handoff | PC overlay 可用时执行 handoff、skip、destroy；重点复现 Day2-7 skip 与真实鼠标对抗。 | native relay/BroadcastChannel 不残留，PC overlay clear payload 完整；透明 BrowserWindow 被销毁，Pet 窗口点击穿透恢复；同一 runId 的 delayed begin/update 不会复活透明窗口；首页 cursor-only 更新不会压掉外置聊天窗 spotlight 更新。 |
| 视觉截图 | 复跑 Phase 11 自动视觉回归。 | 截图非空、移动端无横向溢出、spotlight/cursor/petal/avatarStandIn payload 完整；PC 端能实际绘制并清除 `.avatar-stand-in`。 |

## 后续不建议直接推进的范围

1. 不建议一次性把 7 天所有 scene 改成纯声明式 schema。
2. 不建议把 PC overlay 和 DOM fallback 合并成单一路径。
3. 不建议删除 `window.TutorialHighlightController.createController()` 兼容入口。
4. 不建议在主线中强制执行模型替换、声音克隆、记忆改写、云存档上传/下载等高风险操作。

## 后续可选的小步方向

如果确实还要继续做代码层面重构，只建议按下面顺序小步推进：

1. 选择一个差异最小的设置巡游 scene，补前端回归后再扩展 schema。
2. 每次只迁移一个 scene，不跨 Day 批量改。
3. 保留手写 handler escape hatch，复杂行为先不要强塞进 schema。
4. 每次迁移后复跑 `static/*.test.cjs`、相关 `tests/frontend/test_home_prompt_flow.py` 用例和 Phase 11 视觉回归。
