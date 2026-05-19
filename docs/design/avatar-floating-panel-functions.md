# 模型旁边悬浮窗 4 日新手教程设计

本文档说明如何在现有首页 Yui 新手教程之后，把首页模型旁边的悬浮按钮、弹窗和侧边面板拆成 4 次新手教程。第 1 天继续使用已经落地的首页 Yui 新手教程；第 2 到第 4 天按本地自然日介绍剩余功能。前三天注重软件功能引导，第四天注重用户和猫娘之间的互动体验。

本文当前目标是为后续实现提供可执行流程稿。现在还没有预录语音，因此每段台词先预留 `voiceKey`；文本先给一版临时文案，后续可以只替换文案和语音资源，不改教程流程。模型表情与动作本阶段不实现，但每个小节都预留 `emotion`、`motion`、`lookAt` 和 `performanceCue` 字段，便于之后接入 `AvatarPerformance`。

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
| `TutorialAvatarReloadController` | 每轮开始前复用现有临时教程模型切换和聊天头像覆盖流程；每轮完成、跳过、pagehide、异常时恢复用户原模型。 |

推荐新增一个首页悬浮窗教程 Director，例如 `HomeAvatarFloatingGuideDirector`。它只负责：

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

建议 Day 2-4 的台词先集中放到类似 `static/yui-guide-steps.js` 的结构中，或新增 `static/avatar-floating-guide-steps.js`。文本 key 必须稳定，便于后续只替换文案和音频。

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
| `temporaryText` | 临时中文文案，可直接进入聊天窗口。 | 替换为正式多语言 i18n 文案。 |
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

第 1 天即现有首页 Yui 新手教程，现有主线来自 `HOME_SCENE_ORDER`：

```text
intro_basic
takeover_capture_cursor
takeover_plugin_preview
takeover_settings_peek
takeover_return_control
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

建议持久化字段：

| 字段 | 说明 |
| --- | --- |
| `avatarFloatingGuide.firstSeenDate` | 首次进入首页的本地日期，作为 Day 2 到 Day 4 的节奏锚点。 |
| `avatarFloatingGuide.completedRounds` | 已完成轮次数组。Day 1 由现有教程完成/跳过后写入。 |
| `avatarFloatingGuide.skippedRounds` | 用户主动跳过的轮次数组。 |
| `avatarFloatingGuide.lastAutoShownDate` | 最近一次自动展示新增悬浮窗教程的本地日期。 |
| `avatarFloatingGuide.currentRound` | 当前运行中的轮次，用于异常恢复和跨页面 handoff。 |

排期建议：

| 本地自然日 | 自动触发内容 | 重点层级 |
| --- | --- | --- |
| Day 1 | 现有首页 Yui 新手教程 | 入口级概览，已实现。 |
| Day 2 | 屏幕分享、语音与通话上下文 | 第一个功能介绍屏幕分享按钮，再说明语音联动、来源选择和麦克风弹窗。 |
| Day 3 | Agent、插件与管理入口 | Agent/任务 HUD、插件/OpenClaw、角色/API/记忆等管理入口。 |
| Day 4 | 猫娘互动体验 | 对话设置、主动搭话、隐私边界、动画表现、锁定、离开/回来。 |

## 通用流程骨架

Day 2-4 每轮都建议采用同一个运行骨架：

```text
prepareRound(round)
├─ Manager 判断日期、完成态、跳过态
├─ TutorialAvatarReloadController.beginOverride()
├─ TutorialSkipController.show()
├─ Director.create()
│  ├─ create TutorialInteractionTakeover
│  ├─ create TutorialHighlightController
│  ├─ create TutorialInterruptController
│  └─ resolve current prefix: live2d / vrm / mmd
├─ Director.playRound()
│  ├─ waitFloatingButtonsReady()
│  ├─ setTutorialTakingOver(true)
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

## Day 2：屏幕分享、语音与通话上下文

目标：Day 2 的第一个功能必须是“屏幕分享”按钮。先让用户知道屏幕分享入口在哪里，再解释它为什么依赖语音/音视频通话，最后补充来源选择和麦克风弹窗。教程只演示入口和弹窗，不默认开始真实录音、不默认选择真实屏幕来源。

### Day 2 场景顺序

```text
day2_intro_context
day2_screen_entry
day2_screen_requires_voice
day2_screen_source_popup
day2_screen_source_states
day2_mic_recap
day2_mic_popup_audio_quality
day2_mic_devices
day2_wrap
```

### Day 2 临时台词与占位

| scene | textKey | voiceKey | 临时文案 | 表情/动作占位 |
| --- | --- | --- | --- | --- |
| `day2_intro_context` | `tutorial.avatarFloating.day2.intro` | `avatar_floating_day2_intro` | “今天先教你一个很实用的按钮：如果你想让我看屏幕，入口就在我旁边。” | `emotion: curious`，`motion: null`，`lookAt: floating-buttons` |
| `day2_screen_entry` | `tutorial.avatarFloating.day2.screenEntry` | `avatar_floating_day2_screen_entry` | “这个是屏幕分享。点它之前，要先和我进入语音或音视频通话。” | `emotion: neutral`，`lookAt: #${prefix}-btn-screen` |
| `day2_screen_requires_voice` | `tutorial.avatarFloating.day2.screenRequiresVoice` | `avatar_floating_day2_screen_requires_voice` | “如果还没开始通话，它会提醒你：屏幕分享只在通话里使用。” | `emotion: explaining`，`lookAt: #${prefix}-btn-screen` |
| `day2_screen_source_popup` | `tutorial.avatarFloating.day2.screenSourcePopup` | `avatar_floating_day2_screen_source_popup` | “旁边的小三角可以打开来源列表，你可以选整个屏幕，也可以只选某个窗口。” | `emotion: proud`，`lookAt: .${prefix}-trigger-icon-screen` |
| `day2_screen_source_states` | `tutorial.avatarFloating.day2.screenSourceStates` | `avatar_floating_day2_screen_source_states` | “如果系统暂时拿不到窗口列表，这里也会告诉你原因，不会偷偷共享任何东西。” | `emotion: reassuring`，`lookAt: #${prefix}-popup-screen` |
| `day2_mic_recap` | `tutorial.avatarFloating.day2.micRecap` | `avatar_floating_day2_mic_recap` | “再回到语音按钮。你和我说话时，屏幕分享才有上下文。” | `emotion: soft`，`lookAt: #${prefix}-btn-mic` |
| `day2_mic_popup_audio_quality` | `tutorial.avatarFloating.day2.micAudioQuality` | `avatar_floating_day2_mic_audio_quality` | “麦克风设置里可以调播放音量、空间音频、降噪、增益，还能看实时音量。” | `emotion: explaining`，`lookAt: #${prefix}-popup-mic` |
| `day2_mic_devices` | `tutorial.avatarFloating.day2.micDevices` | `avatar_floating_day2_mic_devices` | “右边是设备列表。一般用系统默认就好，遇到多个麦克风时再手动选择。” | `emotion: neutral`，`lookAt: .mic-option` |
| `day2_wrap` | `tutorial.avatarFloating.day2.wrap` | `avatar_floating_day2_wrap` | “今天就到这里。记住：先通话，再决定要不要让我看屏幕。” | `emotion: happy`，`motion: null` |

### Day 2 高亮与 ghost cursor 流程

#### 1. `day2_intro_context`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.intro`。
2. 使用 `voiceKey = avatar_floating_day2_intro` 占位。

高亮流程：

1. 等待 `#${prefix}-floating-buttons` 可见。
2. persistent spotlight 放到悬浮按钮组，使用合并区域或按钮组容器。
3. 不打开弹窗，不点击真实 UI。

ghost cursor 流程：

1. cursor 从聊天窗口或上一次位置出现。
2. 移动到悬浮按钮组附近，轻微 wobble。

清理：

1. 保留 persistent spotlight 到下一小节。

#### 2. `day2_screen_entry`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.screenEntry`。
2. `voiceKey = avatar_floating_day2_screen_entry`。

高亮流程：

1. persistent spotlight 仍放到悬浮按钮组。
2. action spotlight 放到 `#${prefix}-btn-screen`，圆形高亮。

ghost cursor 流程：

1. cursor 移动到 `#${prefix}-btn-screen`。
2. 不点击主按钮，避免开启真实屏幕共享。
3. 只做 hover / wobble 展示。

真实 UI 操作：

1. 不调用 `live2d-screen-toggle`。

清理：

1. 保留 action spotlight 到下一小节，用于解释限制。

#### 3. `day2_screen_requires_voice`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.screenRequiresVoice`。
2. `voiceKey = avatar_floating_day2_screen_requires_voice`。

高亮流程：

1. action spotlight 继续放到 `#${prefix}-btn-screen`。
2. 如当前未录音，可短暂高亮 toast 区域或使用虚拟 spotlight 展示限制文案位置。

ghost cursor 流程：

1. cursor 在屏幕分享按钮上显示一次可见 click。
2. Director 不直接改 `isRecording`。
3. 如果当前 `window.isRecording === false`，可允许真实 click 触发 toast；否则改为文字说明，避免关闭用户已有共享。

真实 UI 操作：

1. 非录音状态可触发按钮 click，让现有逻辑显示 `app.screenShareRequiresVoice`。
2. 录音状态不点击主按钮，避免改变屏幕共享状态。

清理：

1. 清掉限制文案虚拟 spotlight。

#### 4. `day2_screen_source_popup`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.screenSourcePopup`。
2. `voiceKey = avatar_floating_day2_screen_source_popup`。

高亮流程：

1. action spotlight 放到 `.${prefix}-trigger-icon-screen` 或 trigger button shell。
2. cursor click 后等待 `#${prefix}-popup-screen` 显示。
3. popup 显示后，把 persistent spotlight 从悬浮按钮组切到屏幕来源弹窗。
4. 对屏幕分组和窗口分组分别使用 scene extra spotlight；如果没有来源则跳到降级状态说明。

ghost cursor 流程：

1. cursor 移动到小三角。
2. visible click。
3. 调用真实 trigger click 或 `manager.showPopup('screen', popup)`。
4. 等待 `renderScreenSourceList(popup)` 完成或出现 loading/失败状态。
5. cursor 移动到第一个 screen source，再移动到第一个 window source；不点击来源。

真实 UI 操作：

1. 打开 `#${prefix}-popup-screen`。
2. 允许渲染来源列表。
3. 不调用 `selectScreenSource(...)`。

清理：

1. 保持弹窗打开给下一小节说明状态。

#### 5. `day2_screen_source_states`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.screenSourceStates`。
2. `voiceKey = avatar_floating_day2_screen_source_states`。

高亮流程：

1. 如果存在 `.screen-source-option.selected`，action spotlight 放到选中项。
2. 如果存在 loading、notAvailable、noSources 或 loadFailed 文案，使用 virtual spotlight 包住该状态。
3. 如果存在 screen/window 分组标题，使用 scene extra spotlight 标记两个分组标题。

ghost cursor 流程：

1. cursor 在弹窗内短距离移动，指向缩略图、名称和选中态。
2. 不点击任何来源。

真实 UI 操作：

1. 无。

清理：

1. 关闭屏幕来源弹窗。
2. 清理 screen popup 相关 action / scene extra / virtual spotlight。

#### 6. `day2_mic_recap`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.micRecap`。
2. `voiceKey = avatar_floating_day2_mic_recap`。

高亮流程：

1. persistent spotlight 回到悬浮按钮组。
2. action spotlight 放到 `#${prefix}-btn-mic`。
3. 如果 `#${prefix}-btn-mic-mute` 当前可见，增加 retained extra spotlight；不可见时不强行创建。

ghost cursor 流程：

1. cursor 移动到 `#${prefix}-btn-mic`。
2. 不点击主语音按钮，避免开始或停止录音。
3. 如果静音按钮可见，cursor 移动到 `#${prefix}-btn-mic-mute` 并 wobble，不点击。

真实 UI 操作：

1. 无。

清理：

1. 清理静音按钮 retained extra spotlight。

#### 7. `day2_mic_popup_audio_quality`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.micAudioQuality`。
2. `voiceKey = avatar_floating_day2_mic_audio_quality`。

高亮流程：

1. action spotlight 放到 `.${prefix}-trigger-icon-mic`。
2. cursor click 后打开 `#${prefix}-popup-mic`。
3. persistent spotlight 切到麦克风弹窗。
4. 依次用 virtual spotlight 标记：
   - 扬声器音量区域；
   - 空间音频区域；
   - 降噪区域；
   - `#mic-gain-slider`；
   - `#mic-volume-bar-bg` / `#mic-volume-status`。

ghost cursor 流程：

1. cursor 点击 mic 小三角。
2. 等待 `renderFloatingMicList()` 完成。
3. cursor 在左栏从上到下移动，每个区域停顿。
4. 不拖动滑条，不切换开关。

真实 UI 操作：

1. 打开 `#${prefix}-popup-mic`。
2. 不改变音量、空间音频、降噪、增益。

清理：

1. 保持麦克风弹窗打开给下一小节。

#### 8. `day2_mic_devices`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.micDevices`。
2. `voiceKey = avatar_floating_day2_mic_devices`。

高亮流程：

1. action spotlight 放到右栏设备标题或第一个 `.mic-option`。
2. 如果存在 `.mic-option.selected`，额外高亮选中项。
3. 如果无设备或权限失败，高亮对应错误文案。

ghost cursor 流程：

1. cursor 移动到系统默认麦克风。
2. cursor 移动到一个具体设备项。
3. 不点击设备，避免改用户设置。

真实 UI 操作：

1. 无。

清理：

1. 关闭麦克风弹窗。
2. 清理 mic popup 相关 spotlight。

#### 9. `day2_wrap`

文本输出：

1. 输出 `tutorial.avatarFloating.day2.wrap`。
2. `voiceKey = avatar_floating_day2_wrap`。

高亮流程：

1. 清掉 action spotlight。
2. persistent spotlight 短暂回到聊天窗口。

ghost cursor 流程：

1. cursor 移动回聊天窗口或视口中心。
2. wobble 后隐藏。

完成：

1. `setTutorialTakingOver(false)`。
2. 标记 Day 2 done。

### Day 2 清理要求

- 关闭屏幕来源弹窗和麦克风弹窗。
- 不改变用户选择的屏幕来源、麦克风、增益、降噪、空间音频和扬声器音量。
- 如果教程临时打开过麦克风权限请求，失败时不阻塞 Day 2 后续小节。
- 不默认开始录音，不默认开始屏幕共享，不默认选择真实窗口。

## Day 3：Agent、插件与管理入口

目标：把 Day 1 只做概览的 Agent、插件和管理入口讲清楚。Day 3 仍以功能引导为主，重点是自动化能力中心、任务状态、插件入口、OpenClaw、角色/API/记忆等管理页面。

### Day 3 场景顺序

```text
day3_intro_agent
day3_agent_status_master
day3_agent_capabilities
day3_agent_task_hud
day3_plugin_side_panel
day3_openclaw_side_panel
day3_settings_management_entries
day3_wrap
```

### Day 3 临时台词与占位

| scene | textKey | voiceKey | 临时文案 | 表情/动作占位 |
| --- | --- | --- | --- | --- |
| `day3_intro_agent` | `tutorial.avatarFloating.day3.intro` | `avatar_floating_day3_intro` | “今天看看我的小帮手能力。这里不是普通设置，是我帮你操作电脑时会用到的工具箱。” | `emotion: proud`，`lookAt: #${prefix}-btn-agent` |
| `day3_agent_status_master` | `tutorial.avatarFloating.day3.statusMaster` | `avatar_floating_day3_status_master` | “最上面会显示 Agent 状态。总开关没准备好时，下面的能力也不会乱动。” | `emotion: explaining` |
| `day3_agent_capabilities` | `tutorial.avatarFloating.day3.capabilities` | `avatar_floating_day3_capabilities` | “键鼠控制、浏览器控制、专属桌面、插件和 OpenClaw，都是不同层级的帮忙方式。” | `emotion: neutral` |
| `day3_agent_task_hud` | `tutorial.avatarFloating.day3.taskHud` | `avatar_floating_day3_task_hud` | “如果我真的开始执行任务，旁边会出现任务面板。你能看到进度，也能随时终止。” | `emotion: reassuring` |
| `day3_plugin_side_panel` | `tutorial.avatarFloating.day3.pluginSidePanel` | `avatar_floating_day3_plugin_side_panel` | “用户插件这里还有一个侧边入口，能打开插件管理面板。” | `emotion: curious` |
| `day3_openclaw_side_panel` | `tutorial.avatarFloating.day3.openclawSidePanel` | `avatar_floating_day3_openclaw_side_panel` | “OpenClaw 需要外部服务配合。如果不可用，我会把原因告诉你。” | `emotion: explaining` |
| `day3_settings_management_entries` | `tutorial.avatarFloating.day3.managementEntries` | `avatar_floating_day3_management_entries` | “角色、模型、声音、API 和记忆这些长期配置，都放在设置里的管理入口。” | `emotion: neutral` |
| `day3_wrap` | `tutorial.avatarFloating.day3.wrap` | `avatar_floating_day3_wrap` | “今天你只需要记住：让我做事之前，先看看 Agent 状态和权限。” | `emotion: happy` |

### Day 3 高亮与 ghost cursor 流程

#### 1. `day3_intro_agent`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.intro`。
2. `voiceKey = avatar_floating_day3_intro`。

高亮流程：

1. persistent spotlight 放到悬浮按钮组。
2. action spotlight 放到 `#${prefix}-btn-agent`。

ghost cursor 流程：

1. cursor 移动到 Agent 按钮。
2. visible click。
3. 调用真实按钮 click 或 `manager.showPopup('agent', popup)`。
4. 等待 `#${prefix}-popup-agent` 显示。

真实 UI 操作：

1. 打开 Agent 弹窗。

清理：

1. persistent spotlight 切到 Agent 弹窗。

#### 2. `day3_agent_status_master`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.statusMaster`。
2. `voiceKey = avatar_floating_day3_status_master`。

高亮流程：

1. persistent spotlight 放到 `#${prefix}-popup-agent`。
2. action spotlight 放到状态栏。当前代码中状态栏 id 多处使用 `live2d-agent-status`，实现时应通过 `[id$="-agent-status"], #live2d-agent-status` 或现有 Agent UI 查询方法兼容。
3. virtual spotlight 放到 `#${prefix}-agent-master`，扩大开关行高亮。

ghost cursor 流程：

1. cursor 移动到状态栏，停顿。
2. cursor 移动到 Agent 总开关。
3. 不点击总开关，避免改用户权限。

真实 UI 操作：

1. 无。

清理：

1. 清理状态栏 action spotlight，保留 Agent 弹窗 persistent。

#### 3. `day3_agent_capabilities`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.capabilities`。
2. `voiceKey = avatar_floating_day3_capabilities`。

高亮流程：

1. 依次高亮：
   - `#${prefix}-agent-keyboard`
   - `#${prefix}-agent-browser`
   - `#${prefix}-agent-openfang`
   - `#${prefix}-agent-user-plugin`
   - `#${prefix}-agent-openclaw`
2. 使用 virtual spotlight 包住整行，避免只高亮隐藏 checkbox。

ghost cursor 流程：

1. cursor 逐项移动。
2. 每项短暂停顿，不点击。

真实 UI 操作：

1. 无。

清理：

1. 清理每项 action spotlight。

#### 4. `day3_agent_task_hud`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.taskHud`。
2. `voiceKey = avatar_floating_day3_task_hud`。

高亮流程：

1. 如果 `#agent-task-hud` 已存在且可见，persistent spotlight 暂时放到 HUD。
2. 如果不可见，教程可以调用 `AgentHUD.createAgentTaskHUD()` 创建但不显示真实任务，再用临时展示模式显示空 HUD。
3. action spotlight 依次放到：
   - `#agent-task-hud-stats`
   - `#agent-task-hud-minimize`
   - `#agent-task-hud-cancel`
   - `#agent-task-list`

ghost cursor 流程：

1. cursor 从 Agent 弹窗移动到 HUD。
2. cursor 指向运行/排队计数。
3. cursor 指向折叠按钮，不点击或只在临时 HUD 中点击并恢复。
4. cursor 指向终止全部按钮，不点击，避免弹确认框。

真实 UI 操作：

1. 可临时展示 HUD。
2. 不创建真实 Agent 任务。
3. 不调用终止接口。

清理：

1. 如果 HUD 是教程临时显示的，恢复原显示、折叠和拖拽位置。
2. persistent spotlight 回到 Agent 弹窗。

#### 5. `day3_plugin_side_panel`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.pluginSidePanel`。
2. `voiceKey = avatar_floating_day3_plugin_side_panel`。

高亮流程：

1. action spotlight 放到 `#${prefix}-agent-user-plugin`。
2. hover 或调用侧边面板 `_expand()` 展开 `#agent-user-plugin-actions`。
3. retained extra spotlight 保留用户插件开关。
4. virtual spotlight 放到 `#neko-sidepanel-action-agent-user-plugin-management-panel`。

ghost cursor 流程：

1. cursor 移动到用户插件行。
2. 不点击开关。
3. cursor 移动到侧边面板“管理面板”入口。
4. 不点击跨窗口入口，除非用户主动确认。

真实 UI 操作：

1. 展开侧边面板。
2. 不打开 `/api/agent/user_plugin/dashboard`。

清理：

1. 收起用户插件侧边面板或保留给下一小节前先调用 `collapseOtherSidePanels()`。

#### 6. `day3_openclaw_side_panel`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.openclawSidePanel`。
2. `voiceKey = avatar_floating_day3_openclaw_side_panel`。

高亮流程：

1. action spotlight 放到 `#${prefix}-agent-openclaw`。
2. 展开 `#agent-openclaw-actions`。
3. virtual spotlight 放到 `#neko-sidepanel-action-agent-openclaw-openclaw-guide`。
4. 如果 OpenClaw disabled 或 unavailable，额外高亮该行 title / disabled 状态说明。

ghost cursor 流程：

1. cursor 移动到 OpenClaw 行。
2. cursor 移动到“OpenClaw 接入教程”入口。
3. 不点击跨窗口入口。

真实 UI 操作：

1. 展开侧边面板。
2. 不调用开关变更，不打开 guide 页面。

清理：

1. 收起 OpenClaw 侧边面板。
2. 清理 Agent 侧边面板 retained / virtual spotlight。

#### 7. `day3_settings_management_entries`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.managementEntries`。
2. `voiceKey = avatar_floating_day3_management_entries`。

高亮流程：

1. 关闭 Agent 弹窗。
2. action spotlight 放到 `#${prefix}-btn-settings`。
3. cursor click 打开 `#${prefix}-popup-settings`。
4. persistent spotlight 切到设置弹窗。
5. 展开 `data-neko-sidepanel-type="character-settings"`。
6. scene extra spotlight 包含：
   - 角色设置入口；
   - character side panel；
   - `#${prefix}-menu-api-keys`；
   - `#${prefix}-menu-memory`。

ghost cursor 流程：

1. cursor 点击设置按钮。
2. cursor 移动到角色设置入口。
3. cursor 移动到角色侧边面板中的通用设置、模型管理、声音克隆入口。
4. cursor 移动到 API 密钥和记忆浏览入口。
5. 不点击跨页入口。

真实 UI 操作：

1. 打开设置弹窗。
2. 展开角色设置侧边面板。
3. 不打开跨页面管理页。

清理：

1. 关闭角色设置侧边面板和设置弹窗。

#### 8. `day3_wrap`

文本输出：

1. 输出 `tutorial.avatarFloating.day3.wrap`。
2. `voiceKey = avatar_floating_day3_wrap`。

高亮流程：

1. persistent spotlight 回到聊天窗口。
2. 清理所有 Agent / settings extra spotlight。

ghost cursor 流程：

1. cursor 回到聊天窗口或视口中心。
2. wobble 后隐藏。

完成：

1. `setTutorialTakingOver(false)`。
2. 标记 Day 3 done。

### Day 3 清理要求

- 关闭 Agent 弹窗、设置弹窗和所有侧边面板。
- 如果教程临时展示 HUD，结束后恢复原显示状态、折叠状态和拖拽位置。
- 不强制打开 Agent 总开关或任何子能力。
- 用户插件、OpenClaw、API、记忆、角色、媒体凭证等跨窗口入口默认不自动打开。

## Day 4：猫娘互动体验

目标：前三天已经完成主要功能引导，Day 4 专门讲“怎么和猫娘相处”。这一轮可以更强调台词、表情、视线、动作和用户选择，让用户理解聊天节奏、主动搭话、隐私边界、模型表现和离开/回来。

### Day 4 场景顺序

```text
day4_intro_companion
day4_chat_settings
day4_proactive_chat
day4_privacy_mode
day4_animation_tracking
day4_lock_interaction
day4_goodbye_return
day4_wrap
```

### Day 4 临时台词与占位

| scene | textKey | voiceKey | 临时文案 | 表情/动作占位 |
| --- | --- | --- | --- | --- |
| `day4_intro_companion` | `tutorial.avatarFloating.day4.intro` | `avatar_floating_day4_intro` | “最后一天，我们不只看按钮。今天讲讲怎么让我更适合陪在你旁边。” | `emotion: soft`，`motion: null` |
| `day4_chat_settings` | `tutorial.avatarFloating.day4.chatSettings` | `avatar_floating_day4_chat_settings` | “如果你想让我少刷屏、可以被打断、或者多一点表情反馈，就看这里。” | `emotion: explaining` |
| `day4_proactive_chat` | `tutorial.avatarFloating.day4.proactiveChat` | `avatar_floating_day4_proactive_chat` | “主动搭话决定我要不要偶尔找你说话，也能选择我从哪里找话题。” | `emotion: curious` |
| `day4_privacy_mode` | `tutorial.avatarFloating.day4.privacyMode` | `avatar_floating_day4_privacy_mode` | “隐私模式打开时，我不会主动看屏幕。你可以把边界设得很清楚。” | `emotion: reassuring` |
| `day4_animation_tracking` | `tutorial.avatarFloating.day4.animationTracking` | `avatar_floating_day4_animation_tracking` | “动画设置会影响我的画质、帧率，还有我会不会跟着你的鼠标看。” | `emotion: proud` |
| `day4_lock_interaction` | `tutorial.avatarFloating.day4.lockInteraction` | `avatar_floating_day4_lock_interaction` | “这个小锁能锁住我的交互。想拖动窗口或避免误点时，它会很有用。” | `emotion: neutral` |
| `day4_goodbye_return` | `tutorial.avatarFloating.day4.goodbyeReturn` | `avatar_floating_day4_goodbye_return` | “如果你想一个人安静一会儿，可以让我先离开；想我回来，再点这个按钮就好。” | `emotion: soft` |
| `day4_wrap` | `tutorial.avatarFloating.day4.wrap` | `avatar_floating_day4_wrap` | “四天的小教程到这里就结束啦。之后这些按钮都在我旁边，想用的时候叫我就好。” | `emotion: happy`，`motion: wave_placeholder` |

### Day 4 高亮与 ghost cursor 流程

#### 1. `day4_intro_companion`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.intro`。
2. `voiceKey = avatar_floating_day4_intro`。

高亮流程：

1. persistent spotlight 放到聊天窗口。
2. 不打开弹窗。

ghost cursor 流程：

1. cursor 在聊天窗口附近出现。
2. 轻微 wobble。

真实 UI 操作：

1. 无。

清理：

1. 保留 persistent spotlight。

#### 2. `day4_chat_settings`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.chatSettings`。
2. `voiceKey = avatar_floating_day4_chat_settings`。

高亮流程：

1. action spotlight 放到 `#${prefix}-btn-settings`。
2. cursor click 打开设置弹窗。
3. persistent spotlight 切到 `#${prefix}-popup-settings`。
4. action spotlight 放到“对话设置”菜单项。
5. 展开 `data-neko-sidepanel-type="chat-settings"`。
6. virtual spotlight 依次标记：
   - `#${prefix}-toggle-merge-messages`
   - `#${prefix}-toggle-focus-mode`
   - `#${prefix}-toggle-avatar-reaction-bubble`
   - 回复 token 上限滑条区域。

ghost cursor 流程：

1. cursor 点击设置按钮。
2. cursor 移动到对话设置入口并 hover / 展开侧边面板。
3. cursor 逐项移动，不点击开关，不拖动滑条。

真实 UI 操作：

1. 打开设置弹窗。
2. 展开对话设置侧边面板。
3. 不修改设置。

清理：

1. 收起对话设置侧边面板或调用 `collapseOtherSidePanels()`。

#### 3. `day4_proactive_chat`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.proactiveChat`。
2. `voiceKey = avatar_floating_day4_proactive_chat`。

高亮流程：

1. action spotlight 放到 `#${prefix}-toggle-proactive-chat`。
2. 展开 `data-neko-sidepanel-type="interval-proactive-chat"`。
3. virtual spotlight 依次标记：
   - `#${prefix}-proactive-chat-interval`
   - 媒体凭证入口；
   - `#${prefix}-proactive-vision-chat`
   - `#${prefix}-proactive-news-chat`
   - `#${prefix}-proactive-video-chat`
   - `#${prefix}-proactive-personal-chat`
   - `#${prefix}-proactive-music-chat`
   - `#${prefix}-proactive-meme-chat`
   - `#${prefix}-proactive-mini_game-chat`。

ghost cursor 流程：

1. cursor 移动到主动搭话主开关。
2. 不点击主开关。
3. cursor 移动到最低间隔滑条，不拖动。
4. cursor 移动到媒体凭证入口，不点击跨页入口。
5. cursor 逐项扫过搭话方式。

真实 UI 操作：

1. 展开主动搭话侧边面板。
2. 不修改开关和滑条。

清理：

1. 收起主动搭话侧边面板。

#### 4. `day4_privacy_mode`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.privacyMode`。
2. `voiceKey = avatar_floating_day4_privacy_mode`。

高亮流程：

1. action spotlight 放到 `#${prefix}-toggle-proactive-vision`。
2. 展开 `data-neko-sidepanel-type="interval-proactive-vision"`。
3. virtual spotlight 放到 `#${prefix}-proactive-vision-interval`。
4. 如果有 tooltip 或 title，允许高亮说明区域。

ghost cursor 流程：

1. cursor 移动到隐私模式开关。
2. 不点击。
3. cursor 移动到感知间隔滑条，不拖动。

真实 UI 操作：

1. 展开隐私模式侧边面板。
2. 不改变 `proactiveVisionEnabled`。

清理：

1. 收起隐私模式侧边面板。

#### 5. `day4_animation_tracking`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.animationTracking`。
2. `voiceKey = avatar_floating_day4_animation_tracking`。

高亮流程：

1. action spotlight 放到“动画设置”菜单项。
2. 展开 `data-neko-sidepanel-type="animation-settings"`。
3. virtual spotlight 依次标记：
   - 画质滑条；
   - 帧率滑条；
   - `#${prefix}-mouse-tracking-toggle`；
   - Live2D 全屏跟踪或 VRM/MMD 局部跟踪行；
   - 锁定悬停淡化行。

ghost cursor 流程：

1. cursor 移动到动画设置入口。
2. cursor 逐项扫过滑条和开关。
3. 不点击，不拖动。
4. 如果后续实现互动点，可让用户真实移动鼠标观察模型，但本阶段只预留。

真实 UI 操作：

1. 展开动画设置侧边面板。
2. 不修改画质、帧率、跟踪或悬停淡化。

清理：

1. 收起动画设置侧边面板。
2. 关闭设置弹窗。

#### 6. `day4_lock_interaction`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.lockInteraction`。
2. `voiceKey = avatar_floating_day4_lock_interaction`。

高亮流程：

1. action spotlight 放到 `#${prefix}-lock-icon`。
2. 如果锁图标因当前状态隐藏，教程期间应按现有逻辑允许显示，或退化为文字说明。

ghost cursor 流程：

1. cursor 移动到锁图标。
2. 可见 click。
3. 如果演示真实点击，必须记录原锁定状态，并在小节结束恢复。

真实 UI 操作：

1. 可调用真实 lock click 一次，再恢复原状态。
2. 如果恢复风险高，则只 hover，不点击。

清理：

1. 恢复原锁定状态。
2. 清理锁图标 action spotlight。

#### 7. `day4_goodbye_return`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.goodbyeReturn`。
2. `voiceKey = avatar_floating_day4_goodbye_return`。

高亮流程：

1. action spotlight 放到 `#${prefix}-btn-goodbye`。
2. cursor click 后等待模型隐藏和 `#${prefix}-btn-return` 出现。
3. action spotlight 切到 `#${prefix}-btn-return`。
4. 可用 virtual spotlight 标记返回按钮可拖拽区域。

ghost cursor 流程：

1. cursor 移动到“请她离开”按钮。
2. visible click，调用真实 `live2d-goodbye-click` 或当前模型对应事件。
3. 等待返回按钮出现。
4. cursor 移动到返回按钮。
5. 可演示很短的拖拽轨迹，但本阶段建议只说明可拖拽，不实际改变位置。
6. cursor click 返回按钮，恢复模型。

真实 UI 操作：

1. 触发离开。
2. 触发回来。

清理：

1. 确保模型、悬浮按钮、锁图标恢复。
2. 如果返回按钮位置被临时拖动，恢复原位置。

#### 8. `day4_wrap`

文本输出：

1. 输出 `tutorial.avatarFloating.day4.wrap`。
2. `voiceKey = avatar_floating_day4_wrap`。

高亮流程：

1. persistent spotlight 放到聊天窗口或当前模型容器。
2. 清掉所有 action / virtual / retained / scene extra spotlight。

ghost cursor 流程：

1. cursor 移动到视口中心或聊天窗口。
2. wobble 后隐藏。

完成：

1. `setTutorialTakingOver(false)`。
2. 标记 Day 4 done。
3. 标记 4 日教程全部完成，不再自动弹出。

### Day 4 清理要求

- 关闭设置弹窗和所有侧边面板。
- 恢复临时演示过的设置开关、滑条、锁定状态和模型显示状态。
- 不自动打开媒体凭证页面。
- 确保模型容器、悬浮按钮、锁图标、返回按钮状态正确。
- 标记 4 轮全部完成，不再自动弹出。

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

- `static/yui-guide-steps.js`：现有 Day 1 场景顺序、台词 key、默认 cursor target。
- `static/yui-guide-director.js`：首页教程场景编排、spotlight、ghost cursor 和真实 UI 操作。
- `static/universal-tutorial-manager.js`：教程启动、完成、跳过和页面级调度。
- `static/tutorial-interaction-takeover.js`：教程接管生命周期。
- `static/tutorial-highlight-controller.js`：教程高亮生命周期。
- `static/tutorial-interrupt-controller.js`：轻微打断和生气退出生命周期。
- `static/tutorial-skip-controller.js`：跳过按钮生命周期。
- `static/tutorial-avatar-reload-controller.js`：教程模型临时切换和恢复。
- `static/avatar-performance-stage.js`、`static/yui-guide-avatar-stage.js`：模型演出运行时和首页适配层。
- `static/avatar-ui-buttons.js`：通用按钮定义、麦克风静音按钮、返回按钮、按钮状态同步。
- `static/avatar-ui-popup.js`：设置弹窗、Agent 弹窗、侧边面板、麦克风列表、屏幕来源列表。
- `static/avatar-ui-popup-config.js`：Live2D、VRM、MMD 的角色设置入口配置。
- `static/avatar-popup-common.js`：弹窗和侧边面板定位、边界避让、侧边面板互斥。
- `static/live2d-ui-buttons.js`、`static/vrm-ui-buttons.js`、`static/mmd-ui-buttons.js`：不同模型类型的悬浮按钮定位、锁图标和返回状态。
- `static/common-ui-hud.js`：Agent 弹窗内容和 Agent 任务 HUD。
- `static/app-agent.js`、`static/js/agent_ui_v2.js`：Agent 状态机、能力检查和开关联动。
- `static/app-ui.js`：语音、屏幕共享、请她离开/回来等全局事件处理。
- `static/app-audio-capture.js`：麦克风权限、设备、增益、降噪、音量可视化、静音状态。
- `static/app-screen.js`：屏幕来源选择、屏幕共享流、截图和视频帧发送。
- `static/avatar-ui-drag.js`：主动搭话方式开关、弹窗兼容逻辑、拖拽期间 UI 屏蔽。
- `static/app-proactive.js`：主动搭话调度、模式选择、视觉感知和隐私边界。
- `static/avatar-reaction-bubble.js`：表情气泡定位和显示逻辑。

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
20. `AvatarPerformance` session 在完成、跳过、失败时都 release。
21. reduced motion 下教程能完成，且不播放大幅转场。
