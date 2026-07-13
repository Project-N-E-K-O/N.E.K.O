# 模型旁边悬浮窗 4 日新手教程设计

本文档说明如何在现有首页 Yui 新手教程之后，把首页模型旁边的悬浮按钮、弹窗和侧边面板拆成 4 次新手教程。第 1 天继续使用已经落地的首页 Yui 新手教程；第 2 到第 4 天按本地自然日介绍剩余功能。前三天注重软件功能引导，第四天注重用户和猫娘之间的互动体验。

本文当前状态是 Day 2-4 已按 Day 1 架构接入正式教程流程。现在还没有预录语音，因此每段台词先预留 `voiceKey`；文本先给一版临时文案，后续可以只替换文案和语音资源，不改教程流程。模型表情与动作本阶段不实现，但每个小节保留 `emotion`、`motion`、`lookAt` 和 `performanceCue` 的设计位，便于之后接入 `AvatarPerformance`。

## 当前落地状态

- Day 1 仍由现有首页 Yui 新手教程负责，不新增第二套 Day 1。
- Day 2-4 由 `UniversalTutorialManager.startAvatarFloatingGuideRound(day, options)` 启动，进入 `YuiGuideDirector.playAvatarFloatingRound(round, options)` 播放。
- Day 2-4 的 scene 配置目前集中在 `static/tutorial/yui-guide/director.js` 的 `AVATAR_FLOATING_GUIDE_ROUNDS` 中，保留稳定 `textKey` 和 `voiceKey`。
- 首页重置按钮由 `static/tutorial/avatar/floating-guide-reset.js` 绑定。Day 2-4 重启优先走 Manager/Director 正式流程，旧的临时播放器只作为兜底。
- 状态持久化使用 `localStorage` 的 `neko_avatar_floating_guide_v1`，字段包括 `firstSeenDate`、`completedRounds`、`skippedRounds`、`currentRound`、`pendingRound`、`manualResetRound`、`lastAutoShownRound`、`lastAutoShownDate`。
- `main_routers/pages_router.py` 必须把 `static/tutorial/core/universal-manager.js`、`static/tutorial/yui-guide/director.js`、`static/tutorial/avatar/floating-guide-reset.js` 纳入 `static_asset_version` 计算，否则刷新首页可能继续加载旧脚本。

参考约束：

- `docs/design/home-yui-guide-text-highlight-cursor-flow.md`：Day 1 现有首页教程的文本、高亮、ghost cursor、真实 UI 点击和流程交接方式。Day 2-4 应沿用它的“文本先行、spotlight 明确、cursor 点击真实 UI、场景结束必清理”的写法。
- `docs/design/home-yui-guide-lifecycle-modularization.md`：每个新增新手教程都必须接入五个通用生命周期模块，不能复制通用生命周期逻辑。
- `docs/design/avatar-performance-module-maintenance.md`：模型演出只通过 `AvatarPerformance` / `YuiGuideAvatarStage` 接管需要的能力，并在完成、跳过、异常时 release / destroy。

## 核心原则

1. 第 1 天使用现有首页 Yui 新手教程，不复制、不重写、不并行维护另一套 Day 1。
2. 新增 4 日排期只负责“何时播放哪一轮”，不改变现有首页教程内部顺序。
3. Day 2 的第一个功能必须是“屏幕分享”按钮，因为屏幕分享和语音控制联动，而 Day 1 已经介绍过语音控制入口。
4. Day 1 到 Day 3 以功能引导为主：入口、限制、状态、弹窗、跨页入口和清理规则要讲清楚。
5. Day 4 以猫娘互动为主：聊天节奏、打断、表情反馈、主动搭话、隐私边界、模型跟随和离开/回来要围绕“怎么和她相处”来讲。
6. 每天最多自动播放 1 轮；完成或跳过后，当天不再自动弹出。
7. 用户错过多天后，下一次启动只播放尚未完成的最早一轮，不连续补播多轮。
8. 教程演示默认不保存用户配置变更；如果必须触发真实 UI 状态，需要记录原值并在小节结束时恢复。
9. 移动端隐藏 Agent 和“请她离开”时，相关小节降级为文字说明或等桌面端再触发。

## 每轮必须接入的通用模块

Day 2、Day 3、Day 4 都必须接入 `home-yui-guide-lifecycle-modularization.md` 中列出的五个通用模块。新增 Director 只能持有页面业务知识，不能复制这些模块的生命周期实现。

| 模块 | 每轮使用方式 |
| --- | --- |
| `TutorialInteractionTakeover` | 每轮开始时创建 controller。需要自动点击、打开弹窗、禁止用户误操作时调用 `setActive(true)`；用户可自由观察或需要手动确认时短暂放行白名单目标；轮次结束、跳过、异常时 `destroy()`。 |
| `TutorialHighlightController` | 每个小节用它创建 persistent/action/virtual/extra/precise spotlight。所有按钮、弹窗、侧边面板高亮都通过它或 Director 的薄包装完成；小节和轮次结束必须清理对应 spotlight。 |
| `TutorialInterruptController` | 每轮进入 takeover 后启用轻微打断和生气退出语义。触发生气退出时立即清理当前高亮、侧边面板和 ghost cursor，语音/文本结束后走统一 skip，不标记 done。 |
| `TutorialSkipController` | 每轮开始显示跳过按钮。点击后走 Manager 的统一 skip 入口，再由 Director 清理本轮弹窗、侧边面板、cursor 和临时状态。 |
| `TutorialAvatarReloadController` | 每轮开始前复用现有临时教程模型切换和聊天头像覆盖流程；完成切模后必须解除 `yui-guide-live2d-preparing`，并校验当前 Live2D 是否为 `yui-origin`。若热切换静默失败，必须直接加载 `/static/yui-origin/yui-origin.model3.json` 兜底；每轮完成、跳过、pagehide、异常时恢复用户原模型。 |

Day 2-4 当前不新增独立 `HomeAvatarFloatingGuideDirector` 类，而是在现有 `YuiGuideDirector` 中增加悬浮窗教程适配层。它只负责：

1. Day 2-4 的 scene 顺序。
2. 目标 DOM 解析和 fallback。
3. ghost cursor 路径、点击节奏和真实 UI 操作。
4. 每段临时文案、`voiceKey`、演出占位字段。
5. 弹窗、侧边面板、HUD 的专属清理。

它不负责：

1. 全局接管监听。
2. 高亮 DOM 属性生命周期。
3. 跳过按钮创建和销毁。
4. 临时模型切换和恢复。
5. 生气退出的通用语义。

## 临时文本与资源占位格式

Day 2-4 的台词目前集中在 `static/tutorial/yui-guide/director.js` 的 `AVATAR_FLOATING_GUIDE_ROUNDS` 中。文本 key 必须稳定，便于后续只替换文案和音频；如果后续多语言文案膨胀，再拆到类似 `static/tutorial/yui-guide/steps.js` 或新增 `static/avatar/avatar-floating-guide-steps.js`。

示例结构：

```js
{
  id: 'day2_screen_entry',
  textKey: 'tutorial.avatarFloating.day2.screenEntry.text',
  voiceKey: 'avatar_floating_day2_screen_entry',
  temporaryText: '这个按钮是屏幕分享。你想让我看哪里的时候，就会从这里开始。',
  emotion: null,
  motion: null,
  lookAt: '#${prefix}-btn-screen',
  performanceCue: null,
  highlight: {
    persistent: 'chat-window',
    action: '#${prefix}-btn-screen'
  },
  cursor: {
    target: '#${prefix}-btn-screen',
    click: false
  }
}
```

占位约定：

| 字段 | 当前阶段 | 后续替换 |
| --- | --- | --- |
| `text` / `temporaryText` | 临时中文文案，可直接进入聊天窗口。当前代码字段为 `text`，文档示例里的 `temporaryText` 只表达语义。 | 替换为正式多语言 i18n 文案。 |
| `voiceKey` | 只占位，不要求音频存在。 | 对接预录语音文件或 TTS 缓存。 |
| `emotion` | 先为 `null`。 | 后续填 `curious`、`proud`、`shy`、`panic` 等。 |
| `motion` | 先为 `null`。 | 后续填挥手、点头、靠近等动作 cue。 |
| `lookAt` | 可先填目标 selector。 | 后续接入 `AvatarPerformance` 的 lookAt session。 |
| `performanceCue` | 先为 `null` 或字符串占位。 | 后续驱动表情、动作、转场。 |

## 代码核对结果

悬浮按钮入口来自 `AvatarButtonMixin.getDefaultButtonConfigs()`，Live2D、VRM、MMD 共用同一套按钮语义：

| 入口 | DOM 形式 | 点击后内容 |
| --- | --- | --- |
| `mic` | `#${prefix}-btn-mic`，带独立小三角 `.${prefix}-trigger-icon-mic` | 语音开关；小三角打开麦克风弹窗。录音中会显示 `#${prefix}-btn-mic-mute` 静音按钮。 |
| `screen` | `#${prefix}-btn-screen`，带独立小三角 `.${prefix}-trigger-icon-screen` | 屏幕共享开关；小三角打开屏幕/窗口来源列表。未处于语音通话时主按钮会提示限制。 |
| `agent` | `#${prefix}-btn-agent` | Agent 弹窗，含状态栏、总开关、子能力开关和部分侧边快捷面板。 |
| `settings` | `#${prefix}-btn-settings` | 设置弹窗，含对话设置、动画设置、主动搭话、隐私模式、角色设置、API、记忆入口。 |
| `goodbye` | `#${prefix}-btn-goodbye` | 隐藏模型并显示“请她回来”按钮。移动端隐藏。 |
| lock icon | `#${prefix}-lock-icon` | 锁定/解锁模型交互。 |
| return | `#${prefix}-btn-return` | “请她回来”，可拖拽，点击恢复模型和按钮组。 |

点击后继续细分的弹窗/侧边面板如下：

| 类型 | 代码入口 | 细分功能 |
| --- | --- | --- |
| 麦克风弹窗 | `renderFloatingMicList()` / `#${prefix}-popup-mic` | 扬声器音量、空间音频、降噪、麦克风增益、实时音量条、默认/指定麦克风设备列表、权限失败和无设备状态。 |
| 屏幕来源弹窗 | `renderScreenSourceList()` / `#${prefix}-popup-screen` | Electron source 列表，按“屏幕”和“窗口”分组，缩略图、选中态、loading、不可用、无来源和加载失败状态。 |
| Agent 弹窗 | `AgentHUD._createAgentPopupContent()` / `#${prefix}-popup-agent` | 状态栏、Agent 总开关、键鼠控制、Browser Control、专属桌面、用户插件、OpenClaw。 |
| Agent 用户插件侧边面板 | `data-neko-sidepanel-type="agent-user-plugin-actions"` | “管理面板”快捷入口，打开 `/api/agent/user_plugin/dashboard`。 |
| Agent OpenClaw 侧边面板 | `data-neko-sidepanel-type="agent-openclaw-actions"` | “OpenClaw 接入教程”快捷入口，打开 `/api/agent/openclaw/guide`。 |
| Agent 任务 HUD | `AgentHUD.createAgentTaskHUD()` / `#agent-task-hud` | 运行/排队计数、任务列表、空状态、折叠/展开、终止全部任务、单任务终止、拖拽位置保存。 |
| 设置弹窗 | `createSettingsPopupContent()` / `#${prefix}-popup-settings` | 对话设置、动画设置、主动搭话、隐私模式、角色设置、API 密钥、记忆浏览。 |
| 对话设置侧边面板 | `data-neko-sidepanel-type="chat-settings"` | 合并消息、允许打断、表情气泡、回复 token 上限滑条。 |
| 动画设置侧边面板 | `data-neko-sidepanel-type="animation-settings"` | 画质、帧率、跟踪鼠标、全屏/局部跟踪、锁定悬停淡化。 |
| 主动搭话侧边面板 | `data-neko-sidepanel-type="interval-proactive-chat"` | 最低间隔、媒体凭证、屏幕分享、新闻网站、视频网站、个人动态、音乐推荐、表情包分享、小游戏邀请。 |
| 隐私模式侧边面板 | `data-neko-sidepanel-type="interval-proactive-vision"` | 感知间隔；主开关是反向语义，UI 勾选“隐私模式”表示关闭主动视觉感知。 |
| 角色设置侧边面板 | `data-neko-sidepanel-type="character-settings"` | 通用设置、模型管理、声音克隆，按当前模型类型注入对应入口。 |

侧边面板统一由 `createSidePanelContainer()` 创建，使用 `data-neko-sidepanel` 注册，并通过 `AvatarPopupUI.collapseOtherSidePanels()` 保证同一时刻只展开一个主要侧边面板。

## 第 1 天复用现有教程

第 1 天即现有首页 Yui 新手教程，现有主线来自 `static/tutorial/yui-guide/days/day1-home-guide.js` 的 round scenes：

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

Day 1 的流程、文本、高亮、ghost cursor 和真实 UI 点击以 `home-yui-guide-text-highlight-cursor-flow.md` 为准。Day 2-4 的新增教程必须沿用 Day 1 的这些约定：

1. 文本输出先进入聊天窗口或教程气泡，再执行对应 UI 展示。
2. 每段最多一个主 persistent spotlight。
3. 当前要点击的目标使用 action spotlight。
4. 多个并列 UI 使用 retained extra / scene extra / virtual spotlight。
5. ghost cursor 不能只移动，必须和真实 UI 操作对应。
6. 生气退出不能走 done，必须走 skip。

Day 1 完成或跳过后，把 `avatarFloatingGuide.completedRounds` 标记为包含 `1`，后续自然日从 Day 2 开始。

## 排期与状态

当前持久化 key 为 `neko_avatar_floating_guide_v1`。字段如下：

| 字段 | 说明 |
| --- | --- |
| `avatarFloatingGuide.firstSeenDate` | 首次进入首页的本地日期，作为 Day 2 到 Day 4 的节奏锚点。 |
| `avatarFloatingGuide.completedRounds` | 已完成轮次数组。Day 1 由现有教程完成/跳过后写入。 |
| `avatarFloatingGuide.skippedRounds` | 用户主动跳过的轮次数组。 |
| `avatarFloatingGuide.lastAutoShownDate` | 最近一次自动展示新增悬浮窗教程的本地日期。 |
| `avatarFloatingGuide.currentRound` | 当前运行中的轮次，用于异常恢复和跨页面 handoff。 |
| `avatarFloatingGuide.pendingRound` | 刚被重置或准备启动的轮次。 |
| `avatarFloatingGuide.manualResetRound` | 用户通过首页重置按钮手动重启的轮次。 |
| `avatarFloatingGuide.lastAutoShownRound` | 最近一次自动展示的轮次。 |
| `avatarFloatingGuide.resetHistory` | 最近 20 次手动重置记录，便于调试重启链路。 |

排期建议：

| 本地自然日 | 自动触发内容 | 重点层级 |
| --- | --- | --- |
| Day 1 | 现有首页 Yui 新手教程 | 入口级概览，已实现。 |
| Day 2 | 屏幕分享、语音与通话上下文 | 第一个功能介绍屏幕分享按钮，再说明语音联动、来源选择和麦克风弹窗。 |
| Day 3 | Agent、插件与管理入口 | Agent/任务 HUD、插件/OpenClaw、角色/API/记忆等管理入口。 |
| Day 4 | 猫娘互动体验 | 对话设置、主动搭话、隐私边界、动画表现、锁定、离开/回来。 |

## 通用流程骨架

Day 2-4 当前采用同一个运行骨架：

```text
prepareRound(round)
├─ Manager 判断日期、完成态、跳过态
├─ TutorialAvatarReloadController.beginOverride()
├─ Manager.ensureTutorialYuiLive2dVisible()
├─ TutorialSkipController.show()
├─ Manager.ensureYuiGuideDirector()
├─ Director.playAvatarFloatingRound()
│  ├─ waitFloatingButtonsReady()
│  ├─ setTutorialTakingOver(true)
│  ├─ enableInterrupts()
│  ├─ play scenes in order
│  └─ close all temporary panels
└─ finishRound(done / skip / angry_exit / error)
   ├─ hide skip button
   ├─ destroy interrupt controller
   ├─ destroy highlight controller
   ├─ destroy interaction takeover
   ├─ restore avatar override
   ├─ close popups / side panels / HUD temporary state
   └─ mark completed or skipped
```

注意：2-4 天启动前必须先走 `waitForFloatingButtons()`，但切模后仍要以 `live2d` 前缀解析教程 DOM。`ensureTutorialYuiLive2dVisible()` 负责在热切换失败或准备态残留时兜底加载 `yui-origin` 并解除隐藏状态。

每个 scene 的标准结构：

```text
scene
├─ text: 临时文本 + textKey
├─ voice: voiceKey 占位
├─ performance: emotion / motion / lookAt / performanceCue 占位
├─ highlight before speech
├─ ghost cursor move
├─ visible click or hover
├─ call real UI API
├─ verify expected UI state
├─ optional retained / virtual / extra spotlight
└─ cleanup scene-local highlight and timers
```

## 高亮与光标规则

- 每轮最多保留一个 persistent spotlight，用于说明当前上下文，例如聊天窗口、悬浮按钮组、Agent 弹窗或设置弹窗。
- 当前要点击或讲解的按钮使用 action spotlight。
- 侧边面板使用 retained extra spotlight 或 virtual spotlight，避免打开面板时重置整个弹窗高亮。
- ghost cursor 必须遵循“先高亮、再移动、再可见 click、再调用真实 UI API、再等待 UI 状态”的顺序。
- 如果目标 DOM 不存在，当前小节安全跳过或退化为文字介绍，不阻塞整轮教程。
- 小三角弹窗和 hover 侧边面板需要先确认 `popup.style.display === 'flex'`，再定位侧边面板目标。
- 任何跨窗口入口默认只讲用途，不自动打开；如确需演示，必须按 handoff 规则隐藏首页 cursor，并在返回首页后恢复。
- 生气退出触发时立即清理当前 action、virtual、retained extra、scene extra spotlight 和 ghost cursor，不等待语音或文本播放结束。

## 分日流程文档

Day 2-4 的逐 scene 文本、高亮、ghost cursor、真实 UI 操作和清理要求已经按天拆到独立文档。总文档只保留架构、排期、公共约束和验收清单，避免同一套流程长期双写。

| 轮次 | 文档 | 介绍内容 |
| --- | --- | --- |
| Day 1 | `docs/design/avatar-floating-day1-home-yui-guide-flow.md` | 初次见面、语音入口、Agent/键鼠控制、插件预览、设置一瞥、归还控制权和打断分支。 |
| Day 2 | `docs/design/avatar-floating-day2-screen-voice-guide-flow.md` | 屏幕分享入口、通话限制、屏幕来源弹窗、语音上下文、麦克风弹窗和设备列表。 |
| Day 3 | `docs/design/avatar-floating-day3-agent-management-guide-flow.md` | Agent 状态与能力、任务 HUD、用户插件、OpenClaw、角色/API/记忆等管理入口。 |
| Day 4 | `docs/design/avatar-floating-day4-companion-guide-flow.md` | 对话设置、主动搭话、隐私模式、动画表现、锁定、请她离开和回来。 |

功能树总览见 `docs/design/avatar-floating-guide-feature-tree.md`。

## 功能总表

| 功能组 | 包含功能 | 轮次 |
| --- | --- | --- |
| 现有首页教程 | 初次见面、语音入口概览、Agent/键鼠控制、插件预览、设置概览、归还控制权 | Day 1 |
| 屏幕分享与通话上下文 | 屏幕分享按钮、语音联动限制、屏幕/窗口来源、缩略图、选中态、不可用/失败状态、语音按钮复习、录音中静音、麦克风弹窗 | Day 2 |
| Agent 与任务 | 状态栏、总开关、键鼠控制、Browser Control、专属桌面、任务 HUD、任务状态、单任务/全部终止 | Day 3 |
| 插件与管理入口 | 用户插件、插件管理面板、OpenClaw、OpenClaw 接入教程、角色设置、模型管理、声音克隆、API 密钥、记忆浏览 | Day 3 |
| 猫娘对话互动 | 合并消息、允许打断、表情气泡、回复 token 上限、主动搭话、媒体凭证、隐私模式 | Day 4 |
| 猫娘表现与陪伴边界 | 画质、帧率、鼠标跟踪、全屏/局部跟踪、锁定悬停淡化、锁定、离开、回来 | Day 4 |

## 相关源码

- `static/tutorial/yui-guide/steps.js`：现有 Day 1 场景顺序、台词 key、默认 cursor target。
- `static/tutorial/yui-guide/director.js`：首页教程场景编排、spotlight、ghost cursor 和真实 UI 操作；Day 2-4 的 `AVATAR_FLOATING_GUIDE_ROUNDS` 和 `playAvatarFloatingRound()` 也在这里。
- `static/tutorial/core/universal-manager.js`：教程启动、完成、跳过和页面级调度；负责 Day 2-4 状态持久化、自动排期、手动重启、临时切模校验和 `yui-origin` 兜底。
- `static/tutorial/avatar/floating-guide-reset.js`：首页 Day 1-4 重置按钮绑定。Day 2-4 优先委托 `UniversalTutorialManager.startAvatarFloatingGuideRound()`；兜底播放器在 N.E.K.O.-PC 环境必须把 Ghost Cursor show/move/click/hide 发送到全局透明教程 overlay，并隐藏本地 fallback cursor。
- `main_routers/pages_router.py`：`static_asset_version` 计算。新增或修改教程 runtime 脚本时必须纳入该列表，避免桌面端或浏览器加载旧脚本。
- `static/tutorial/core/interaction-takeover.js`：教程接管生命周期。
- `static/tutorial/visual/highlight-controller.js`：教程高亮生命周期。
- `static/tutorial-interrupt-controller.js`：轻微打断和生气退出生命周期。
- `static/tutorial/core/skip-controller.js`：跳过按钮生命周期。
- `static/tutorial/avatar/reload-controller.js`：教程模型临时切换和恢复。
- `static/avatar/avatar-performance-stage.js`、`static/tutorial/avatar/yui-stage.js`：模型演出运行时和首页适配层。
- `static/avatar/avatar-ui-buttons/*.js`：通用按钮定义、麦克风静音按钮、返回按钮、按钮状态同步。
- `static/avatar/avatar-ui-popup.js`：设置弹窗、Agent 弹窗、侧边面板、麦克风列表、屏幕来源列表。
- `static/avatar/avatar-ui-popup-config.js`：Live2D、VRM、MMD 的角色设置入口配置。
- `static/avatar/avatar-popup-common.js`：弹窗和侧边面板定位、边界避让、侧边面板互斥。
- `static/live2d/live2d-ui-buttons.js`、`static/vrm/vrm-ui-buttons.js`、`static/mmd/mmd-ui-buttons.js`：不同模型类型的悬浮按钮定位、锁图标和返回状态。
- `static/common-ui-hud.js`：Agent 弹窗内容和 Agent 任务 HUD。
- `static/app/app-agent.js`、`static/js/agent_ui_v2.js`：Agent 状态机、能力检查和开关联动。
- `static/app/app-ui`：语音、屏幕共享、请她离开/回来等全局事件处理。
- `static/app/app-audio-capture.js`：麦克风权限、设备、增益、降噪、音量可视化、静音状态。
- `static/app/app-screen.js`：屏幕来源选择、屏幕共享流、截图和视频帧发送。
- `static/avatar/avatar-ui-drag.js`：主动搭话方式开关、弹窗兼容逻辑、拖拽期间 UI 屏蔽。
- `static/app/app-proactive.js`：主动搭话调度、模式选择、视觉感知和隐私边界。
- `static/avatar/avatar-reaction-bubble.js`：表情气泡定位和显示逻辑。

## 验证清单

实现时至少检查：

1. Day 1 仍走现有首页 Yui 新手教程，不出现第二套 Day 1。
2. 现有首页教程完成或跳过后，新增排期能从 Day 2 开始。
3. Day 2 的第一个高亮和第一段功能说明必须是屏幕分享按钮，而不是语音按钮。
4. Day 2、Day 3、Day 4 每轮都接入五个通用模块：接管、高亮、打断、跳过、临时切模。
5. 每轮都有稳定 `textKey` 和 `voiceKey`，即使语音资源暂时不存在也不影响文字教程播放。
6. 每轮都预留 `emotion`、`motion`、`lookAt`、`performanceCue`，但当前实现可以先不执行模型表情与动作。
7. 每天只自动展示一轮，完成/跳过后同日不重复展示。
8. 错过多天后不会连续播放多轮。
9. Day 2、Day 3 和 Day 4 的 skip 都会落到统一销毁路径。
10. 生气退出触发时立即清理高亮和 ghost cursor，语音或文本结束后走 skip，不走 done。
11. 弹窗和侧边面板在完成、跳过、异常、页面隐藏、模型切换时全部关闭。
12. 每个 ghost cursor click 都对应真实 UI 状态变化；不存在只移动光标不执行操作的演示。
13. 目标 DOM 缺失时能安全跳过当前小节。
14. 移动端不尝试展示隐藏的 Agent 和“请她离开”按钮。
15. Agent 用户插件和 OpenClaw 侧边面板互斥展开，不残留 hover timer。
16. Agent 任务 HUD 临时展示后恢复原显示、折叠和拖拽位置。
17. 设置类临时演示会恢复原值，不悄悄改用户偏好。
18. 麦克风、屏幕、跨窗口入口遇到权限失败或不可用时，不阻塞整轮教程。
19. Day 4 的猫娘互动小节需要同时包含功能目标和互动反馈，不能退化成单纯设置说明。
20. Day 2-4 手动重启时必须切到 `yui-origin`。如果 `window.handleModelReload()` 静默失败，Manager 必须通过 `ensureTutorialYuiLive2dVisible()` 直接加载 `/static/yui-origin/yui-origin.model3.json` 兜底。
21. Day 2-4 切模结束后必须移除 `body.yui-guide-live2d-preparing`，否则模型可能已经切换但仍被 CSS 隐藏。
22. `main_routers/pages_router.py` 的 `_YUI_GUIDE_ASSET_VERSION_PATHS` 必须包含 `static/tutorial/core/universal-manager.js`、`static/tutorial/yui-guide/director.js`、`static/tutorial/avatar/floating-guide-reset.js`。修改这些文件后需要重启后端/应用进程，让新的 `static_asset_version` 生效。
23. `AvatarPerformance` session 在完成、跳过、失败时都 release。
24. reduced motion 下教程能完成，且不播放大幅转场。
