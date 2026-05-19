# 模型旁边悬浮窗 4 日新手教程设计

本文档说明如何把首页模型旁边的悬浮按钮、弹窗和侧边面板拆成 4 次新手教程。第 1 天继续使用已经落地的首页 Yui 新手教程；第 2 到第 4 天按本地自然日介绍剩余功能。前三天注重软件功能引导，第四天注重用户和猫娘之间的互动体验。

参考约束：

- `docs/design/avatar-performance-module-maintenance.md`：模型演出只通过 `AvatarPerformance` / `YuiGuideAvatarStage` 接管需要的能力，并在完成、跳过、异常时 release / destroy。
- `docs/design/home-yui-guide-lifecycle-modularization.md`：接管、跳过、临时教程模型、恢复用户模型交给现有生命周期模块，教程业务层只编排场景。
- `docs/design/home-yui-guide-text-highlight-cursor-flow.md`：每个场景都要明确文本输出、spotlight、ghost cursor、真实 UI 操作和清理规则。

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

## 互动式引导要求

每一轮都不应该只是“念功能说明”。每个小节至少包含这三层：

1. 猫娘先用一句自然台词解释“这个功能能帮你做什么”。
2. ghost cursor 高亮目标并展示点击、hover、打开弹窗或状态变化。
3. 给用户一个轻量互动点，例如确认按钮位置、观察状态变化、选择是否手动打开跨页入口、或回答猫娘的提示。

实现时保持这些边界：

- 功能引导日可以真实打开弹窗和侧边面板，但不默认保存用户偏好。
- 跨窗口入口默认只讲用途，不自动打开；如确需演示，必须按 handoff 规则隐藏首页 cursor，并在返回首页后恢复。
- Day 4 可以更强调猫娘的反应、表情、视线、主动提示和陪伴感，但仍要保证每个 ghost cursor click 对应真实 UI 操作。

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

## 功能与互动补充

本次代码核对后，需要补进这些介绍点：

1. 屏幕分享和语音控制存在联动：Day 2 必须先讲 `#${prefix}-btn-screen`，再回扣“要先进入语音/音视频通话”的限制。
2. 麦克风弹窗不只是设备列表，还包含扬声器音量、空间音频、降噪、增益、实时音量、权限失败和无设备状态。
3. 屏幕来源弹窗需要介绍 loading、Electron 捕获不可用、无来源和加载失败状态。
4. Agent 任务 HUD 除了终止全部任务，还支持单任务终止、任务状态展示和拖拽位置保存。
5. Agent 子能力要保留 `agent-openfang` 对应的“专属桌面”，用户文案不直接暴露 openfang 名称。
6. 角色设置侧边面板的真实入口是“通用设置”“模型管理”“声音克隆”，并会按 `lanlan_name` 打开当前模型对应页面。
7. 设置弹窗里的 API 密钥、记忆浏览、媒体凭证都是跨页面入口，默认只讲用途，不自动打开。
8. 锁定、请她离开、请她回来属于猫娘互动闭环，应放进 Day 4，而不是混在功能管理日里讲完。

## 第 1 天复用现有教程

第 1 天即现有首页 Yui 新手教程，现有主线来自 `HOME_SCENE_ORDER`：

```text
intro_basic
takeover_capture_cursor
takeover_plugin_preview
takeover_settings_peek
takeover_return_control
```

Day 1 功能树：

```text
Day 1：现有首页 Yui 新手教程（已实现）
├─ 初次见面与语音入口
│  ├─ 激活首页输入框或外置聊天窗
│  ├─ 播放初次见面问候
│  ├─ 高亮 `#${prefix}-btn-mic`，说明语音入口
│  └─ 互动点：让用户知道“可以用声音和她说话”
├─ Agent / 猫爪入口概览
│  ├─ 高亮 `#${prefix}-btn-agent`
│  ├─ 打开 Agent 弹窗
│  ├─ 打开 Agent 总开关 `#${prefix}-agent-master`
│  ├─ 打开键鼠控制 `#${prefix}-agent-keyboard`
│  └─ 互动点：让猫娘演示“她可以帮忙操作电脑”
├─ 用户插件入口预览
│  ├─ 打开用户插件开关 `#${prefix}-agent-user-plugin`
│  ├─ 展示用户插件侧边面板
│  └─ 预览“管理面板”入口
├─ 设置入口概览
│  ├─ 高亮 `#${prefix}-btn-settings`
│  ├─ 展示设置弹窗
│  └─ 概览角色设置、模型管理、声音克隆、API、记忆入口
└─ 归还控制权
   ├─ 关闭教程临时面板
   ├─ 播放归还控制权台词和花瓣转场
   └─ 恢复用户原模型与页面交互
```

Day 1 实现要求：

- 沿用现有 `UniversalTutorialManager` 和 `YuiGuideDirector` 启动逻辑。
- 不新增 `avatarFloatingGuide` 的 Day 1 scene。
- 现有教程完成或跳过后，把 `avatarFloatingGuide.completedRounds` 标记为包含 `1`。
- 用户跳过现有首页教程，也视为 Day 1 已处理，后续自然日继续进入 Day 2。

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

## 生命周期接入

Day 2 到 Day 4 应复用新手教程通用生命周期模块，而不是新增一套通用运行时。新增首页悬浮窗教程 Director 只负责本轮专属编排和页面适配。

通用模块必须优先复用：

| 能力 | 归属 |
| --- | --- |
| 全局交互接管、正脸锁、外置聊天窗口按钮禁用 | `TutorialInteractionTakeover`。 |
| 跳过按钮 | `TutorialSkipController`。 |
| 教程期间临时切换 Yui 模型、恢复用户模型 | `TutorialAvatarReloadController`。 |
| spotlight / highlight | `TutorialHighlightController` + `YuiGuideOverlay`。 |
| 轻微打断 / 生气退出 | `TutorialInterruptController`。 |

专属适配层可以保留：

| 能力 | 归属 |
| --- | --- |
| 场景顺序、台词、目标元素、真实 UI 点击 | `YuiGuideDirector` 或新的首页悬浮窗教程 Director。 |
| ghost cursor 路径、点击节奏、真实 UI 操作顺序 | 首页 Director 或新增首页悬浮窗教程 Director。 |
| 模型动作、表情、LookAt、临时参数申请 | Director 编排，实际执行由 `AvatarPerformance` + `YuiGuideAvatarStage`。 |
| 弹窗和侧边面板定位 | `AvatarPopupUI.positionPopup()` / `positionSidePanel()`。 |
| 插件管理页内部 highlighter、ghost cursor、IPC bridge | `frontend/plugin-manager/src/yui-guide-runtime.ts`，只作为插件管理页专属适配层。 |

每轮启动时：

1. 等待当前模型类型的悬浮按钮就绪。
2. 如需强制引导点击，调用 `setTutorialTakingOver(true)`。
3. 显示跳过按钮，并让 skip 落到统一 `requestTutorialDestroy()` / director `skip()` 路径。
4. 需要模型看向按钮时只申请 `lookAt`；需要动作或表情时才申请 `motion` / `expression`。
5. 场景结束、跳过、页面隐藏、模型切换、异常时，都必须清理 spotlight、ghost cursor、弹窗、侧边面板、临时锁和演出 session。
6. 生气退出等同“语音后跳过”：触发时立即清高亮和 ghost cursor，语音播放完后走统一 skip 路径。

## 高亮与光标规则

- 每轮最多保留一个 persistent spotlight，用于说明当前上下文，例如悬浮按钮组、Agent 弹窗或设置弹窗。
- 当前要点击或讲解的按钮使用 action spotlight。
- 侧边面板使用 retained extra spotlight，避免打开面板时重置整个弹窗高亮。
- ghost cursor 必须遵循“先高亮、再移动、再可见 click、再调用真实 UI API”的顺序。
- 如果目标 DOM 不存在，当前小节安全跳过或退化为文字介绍，不阻塞整轮教程。
- 小三角弹窗和 hover 侧边面板需要先确认 `popup.style.display === 'flex'`，再定位侧边面板目标。
- 任何跨窗口入口默认只讲用途，不自动打开；如确需演示，必须按 handoff 规则隐藏首页 cursor，并在返回首页后恢复。

## Day 2：屏幕分享、语音与通话上下文

目标：Day 2 的第一个功能必须是“屏幕分享”按钮。先让用户知道屏幕分享入口在哪里，再解释它为什么依赖语音/音视频通话，最后补充来源选择和麦克风弹窗。教程只演示入口和弹窗，不默认开始真实录音、不默认选择真实屏幕来源。

Day 2 功能树：

```text
Day 2：屏幕分享、语音与通话上下文
├─ 屏幕分享主入口（本轮第一个功能）
│  ├─ `#${prefix}-btn-screen`
│  │  ├─ 说明开始/停止屏幕共享
│  │  ├─ 说明它和语音控制联动
│  │  └─ 未处于语音通话时说明 `app.screenShareRequiresVoice`
│  └─ 互动点：猫娘先问“想让我看哪里？”，再提示需要先进入语音通话
├─ 屏幕来源弹窗
│  ├─ `.${prefix}-trigger-icon-screen` -> `#${prefix}-popup-screen`
│  ├─ loading `app.screenSource.loading`
│  ├─ 屏幕分组 `.screen-source-option[data-source-id^="screen:"]`
│  ├─ 窗口分组 `.screen-source-option[data-source-id^="window:"]`
│  ├─ 缩略图、名称、title 和选中态 `.screen-source-option.selected`
│  └─ 降级状态
│     ├─ Electron 捕获不可用 `app.screenSource.notAvailable`
│     ├─ 无来源 `app.screenSource.noSources`
│     └─ 加载失败 `app.screenSource.loadFailed`
├─ 语音联动复习
│  ├─ `#${prefix}-btn-mic`
│  │  ├─ 复习开始/停止语音会话
│  │  └─ 说明屏幕分享通常服务于语音/音视频对话
│  └─ `#${prefix}-btn-mic-mute`
│     ├─ 说明录音中才显示
│     └─ 说明可临时静音/取消静音
└─ 麦克风弹窗
   ├─ `.${prefix}-trigger-icon-mic` -> `#${prefix}-popup-mic`
   ├─ 左栏：播放与输入质量
   │  ├─ 扬声器音量 `#speaker-volume-slider`
   │  ├─ 空间音频 `speaker.spatialAudioLabel`
   │  ├─ 降噪 `microphone.noiseReduction`
   │  ├─ 麦克风增益 `#mic-gain-slider`
   │  └─ 实时音量 `#mic-volume-bar-bg` / `#mic-volume-status`
   ├─ 右栏：设备选择
   │  ├─ 系统默认麦克风 `.mic-option`
   │  ├─ 指定麦克风 `.mic-option[data-device-id]`
   │  └─ 当前选中态 `.mic-option.selected`
   └─ 降级状态
      ├─ 无麦克风设备 `microphone.noDevices`
      └─ 权限或加载失败 `microphone.loadFailed`
```

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 2.1 屏幕分享按钮 | `#${prefix}-btn-screen` | 本轮第一个功能。说明入口、用途和语音联动限制。 |
| 2.2 屏幕来源列表 | `.${prefix}-trigger-icon-screen` -> `.screen-source-option` | 展示屏幕/窗口分组、缩略图、选中态和不可用状态。 |
| 2.3 语音联动复习 | `#${prefix}-btn-mic` | 回扣 Day 1 的语音控制，说明屏幕分享为什么依赖通话上下文。 |
| 2.4 录音中静音 | `#${prefix}-btn-mic-mute` | 仅在录音中出现；未录音时使用文字说明或临时占位高亮。 |
| 2.5 麦克风弹窗 | `.${prefix}-trigger-icon-mic` -> `#${prefix}-popup-mic` | 打开麦克风弹窗，介绍双栏布局。 |
| 2.6 播放与输入质量 | `#speaker-volume-slider`、空间音频、降噪、`#mic-gain-slider`、实时音量条 | 说明播放音量和麦克风输入质量分别影响什么。 |
| 2.7 设备列表 | `.mic-option` | 说明系统默认和指定设备的区别，以及选中态。 |

清理要求：

- 关闭屏幕来源弹窗和麦克风弹窗。
- 不改变用户选择的屏幕来源、麦克风、增益、降噪、空间音频和扬声器音量。
- 如果教程临时打开过麦克风权限请求，失败时不阻塞 Day 2 后续小节。
- 不默认开始录音，不默认开始屏幕共享，不默认选择真实窗口。

## Day 3：Agent、插件与管理入口

目标：把 Day 1 只做概览的 Agent、插件和管理入口讲清楚。Day 3 仍以功能引导为主，重点是自动化能力中心、任务状态、插件入口、OpenClaw、角色/API/记忆等管理页面。

Day 3 功能树：

```text
Day 3：Agent、插件与管理入口
├─ Agent 基础
│  ├─ `#${prefix}-btn-agent` -> `#${prefix}-popup-agent`
│  ├─ 状态栏 `#live2d-agent-status`
│  │  ├─ 查询中
│  │  ├─ 就绪/已开启
│  │  └─ 预检失败或能力不可用原因
│  ├─ Agent 总开关 `#${prefix}-agent-master`
│  └─ 子能力开关
│     ├─ 键鼠控制 `#${prefix}-agent-keyboard`
│     ├─ Browser Control `#${prefix}-agent-browser`
│     ├─ 专属桌面 `#${prefix}-agent-openfang`
│     ├─ 用户插件 `#${prefix}-agent-user-plugin`
│     └─ OpenClaw `#${prefix}-agent-openclaw`
├─ Agent 任务 HUD
│  ├─ `#agent-task-hud`
│  ├─ 运行/排队计数 `#agent-task-hud-stats`
│  ├─ 任务卡片
│  │  ├─ running / queued / completed / failed / cancelled
│  │  └─ 单任务终止 `.task-card-cancel`
│  ├─ 折叠/展开 `#agent-task-hud-minimize`
│  ├─ 终止全部任务 `#agent-task-hud-cancel`
│  └─ 拖拽位置保存 `agent-task-hud-position`
├─ Agent 扩展入口
│  ├─ 用户插件侧边面板 `data-neko-sidepanel-type="agent-user-plugin-actions"`
│  │  └─ 管理面板 `#neko-sidepanel-action-agent-user-plugin-management-panel`
│  └─ OpenClaw 侧边面板 `data-neko-sidepanel-type="agent-openclaw-actions"`
│     ├─ 可用性 `/api/agent/openclaw/availability`
│     └─ 接入教程 `#neko-sidepanel-action-agent-openclaw-openclaw-guide`
└─ 设置里的管理入口
   ├─ `#${prefix}-btn-settings` -> `#${prefix}-popup-settings`
   ├─ 角色设置 `data-neko-sidepanel-type="character-settings"`
   │  ├─ 通用设置 `/character_card_manager`
   │  ├─ 模型管理 `/model_manager?lanlan_name=...`
   │  └─ 声音克隆 `/voice_clone?lanlan_name=...`
   ├─ API 密钥 `#${prefix}-menu-api-keys`
   └─ 记忆浏览 `#${prefix}-menu-memory`
```

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 3.1 Agent 弹窗 | `#${prefix}-btn-agent` -> `#${prefix}-popup-agent` | 介绍 Agent 是自动化能力中心。 |
| 3.2 Agent 状态和总开关 | `#live2d-agent-status`、`#${prefix}-agent-master` | 说明状态栏、预检、总开关和子能力依赖。 |
| 3.3 Agent 子能力 | `#${prefix}-agent-keyboard`、`#${prefix}-agent-browser`、`#${prefix}-agent-openfang`、`#${prefix}-agent-user-plugin`、`#${prefix}-agent-openclaw` | 说明各能力用途，不强制打开。 |
| 3.4 任务 HUD | `#agent-task-hud` | 展示任务运行时的计数、任务卡片、折叠、终止和拖拽位置。 |
| 3.5 插件和 OpenClaw 侧边面板 | 用户插件 / OpenClaw side panel | 说明管理面板和接入教程入口，默认不自动打开跨窗口页面。 |
| 3.6 设置管理入口 | `#${prefix}-btn-settings`、`character-settings`、`#${prefix}-menu-api-keys`、`#${prefix}-menu-memory` | 说明角色、模型、声音克隆、API 和记忆入口，不深入跨页配置。 |

清理要求：

- 关闭 Agent 弹窗、设置弹窗和所有侧边面板。
- 如果教程临时展示 HUD，结束后恢复原显示状态、折叠状态和拖拽位置。
- 不强制打开 Agent 总开关或任何子能力。
- 用户插件、OpenClaw、API、记忆、角色、媒体凭证等跨窗口入口默认不自动打开。

## Day 4：猫娘互动体验

目标：前三天已经完成主要功能引导，Day 4 专门讲“怎么和猫娘相处”。这一轮可以更强调台词、表情、视线、动作和用户选择，让用户理解聊天节奏、主动搭话、隐私边界、模型表现和离开/回来。

Day 4 功能树：

```text
Day 4：猫娘互动体验
├─ 对话节奏与反馈
│  ├─ `#${prefix}-btn-settings` -> `#${prefix}-popup-settings`
│  ├─ 对话设置 `data-neko-sidepanel-type="chat-settings"`
│  │  ├─ 合并消息 `#${prefix}-merge-messages`
│  │  ├─ 允许打断 `#${prefix}-focus-mode`
│  │  ├─ 表情气泡 `#${prefix}-avatar-reaction-bubble`
│  │  └─ 回复 token 上限滑条
│  └─ 互动点：猫娘解释“你可以打断我，也可以让我说短一点”
├─ 主动搭话与隐私边界
│  ├─ 主动搭话 `data-neko-sidepanel-type="interval-proactive-chat"`
│  │  ├─ 最低间隔 `#${prefix}-proactive-chat-interval`
│  │  ├─ 媒体凭证 `/api/auth/page`
│  │  └─ 搭话方式
│  │     ├─ 屏幕分享
│  │     ├─ 新闻网站
│  │     ├─ 视频网站
│  │     ├─ 个人动态
│  │     ├─ 音乐推荐
│  │     ├─ 表情包分享
│  │     └─ 小游戏邀请
│  ├─ 隐私模式 `data-neko-sidepanel-type="interval-proactive-vision"`
│  │  ├─ UI 勾选表示关闭主动视觉感知
│  │  └─ 感知间隔 `#${prefix}-proactive-vision-interval`
│  └─ 互动点：猫娘询问“要不要让我偶尔主动找你说话”
├─ 模型表现与跟随
│  ├─ 动画设置 `data-neko-sidepanel-type="animation-settings"`
│  │  ├─ 画质 low / medium / high
│  │  ├─ 帧率 30fps / 45fps / 60fps / VSync
│  │  ├─ 跟踪鼠标 `#${prefix}-mouse-tracking-toggle`
│  │  ├─ Live2D 全屏跟踪或 VRM/MMD 局部跟踪
│  │  └─ 锁定悬停淡化
│  └─ 互动点：让用户移动鼠标，看猫娘视线或身体跟随
└─ 陪伴边界与收尾
   ├─ 锁图标 `#${prefix}-lock-icon`
   │  └─ 锁定/解锁模型交互
   ├─ 请她离开 `#${prefix}-btn-goodbye`
   │  └─ 隐藏模型、悬浮按钮和锁图标
   ├─ 请她回来 `#${prefix}-btn-return`
   │  ├─ 返回按钮可拖拽
   │  └─ 点击恢复模型和按钮组
   └─ 互动点：猫娘用轻量告别/回来台词完成 4 日教程
```

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 4.1 对话设置 | `chat-settings` | 介绍合并消息、允许打断、表情气泡和回复 token 上限，强调用户能调节相处节奏。 |
| 4.2 主动搭话 | `interval-proactive-chat` | 介绍最低间隔、媒体凭证和不同搭话方式。 |
| 4.3 隐私模式 | `interval-proactive-vision` | 说明勾选隐私模式表示关闭主动视觉感知，并可调感知间隔。 |
| 4.4 动画与跟随 | `animation-settings` | 介绍画质、帧率、鼠标跟踪、全屏/局部跟踪和锁定悬停淡化。 |
| 4.5 锁定交互 | `#${prefix}-lock-icon` | 说明锁定/解锁模型交互，演示后恢复原锁定状态。 |
| 4.6 请她离开 / 回来 | `#${prefix}-btn-goodbye`、`#${prefix}-btn-return` | 说明陪伴边界：可以让她暂时离开，也可以随时叫回来。 |

清理要求：

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
4. 每天只自动展示一轮，完成/跳过后同日不重复展示。
5. 错过多天后不会连续播放多轮。
6. Day 2、Day 3 和 Day 4 的 skip 都会落到统一销毁路径。
7. 生气退出触发时立即清理高亮和 ghost cursor，语音结束后走 skip，不走 done。
8. 插件管理页触发生气退出时，插件页本地 `main` spotlight 和 ghost cursor 也要由插件页 runtime 自行清理。
9. 弹窗和侧边面板在完成、跳过、异常、页面隐藏、模型切换时全部关闭。
10. 每个 ghost cursor click 都对应真实 UI 状态变化；不存在只移动光标不执行操作的演示。
11. 目标 DOM 缺失时能安全跳过当前小节。
12. 移动端不尝试展示隐藏的 Agent 和“请她离开”按钮。
13. Agent 用户插件和 OpenClaw 侧边面板互斥展开，不残留 hover timer。
14. Agent 任务 HUD 临时展示后恢复原显示、折叠和拖拽位置。
15. 设置类临时演示会恢复原值，不悄悄改用户偏好。
16. 麦克风、屏幕、跨窗口入口遇到权限失败或不可用时，不阻塞整轮教程。
17. Day 4 的猫娘互动小节需要同时包含功能目标和互动反馈，不能退化成单纯设置说明。
18. `AvatarPerformance` session 在完成、跳过、失败时都 release。
19. reduced motion 下教程能完成，且不播放大幅转场。
