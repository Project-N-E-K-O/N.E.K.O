# 模型旁边悬浮窗 7 日新手教程设计

本文档说明如何把首页模型旁边的悬浮按钮、弹窗和侧边面板拆成 7 次新手教程。第 1 天继续使用现有首页 Yui 新手教程；第 2 到第 7 天按本地自然日逐步介绍更细的弹窗和侧边面板能力。

参考约束：

- `docs/design/avatar-performance-module-maintenance.md`：模型演出只通过 `AvatarPerformance` / `YuiGuideAvatarStage` 接管需要的能力，并在完成、跳过、异常时 release / destroy。
- `docs/design/home-yui-guide-lifecycle-modularization.md`：接管、跳过、临时教程模型、恢复用户模型交给现有生命周期模块，教程业务层只编排场景。
- `docs/design/home-yui-guide-text-highlight-cursor-flow.md`：每个场景都要明确文本输出、spotlight、ghost cursor、真实 UI 操作和清理规则。

## 核心原则

1. 第 1 天使用现有首页 Yui 新手教程，不复制、不重写、不并行维护另一套 Day 1。
2. 新增 7 日排期只负责“何时播放哪一轮”，不改变现有首页教程内部顺序。
3. 第 2 到第 7 天介绍 Day 1 未展开讲清楚的弹窗、侧边面板和子控件。
4. 每天最多自动播放 1 轮；完成或跳过后，当天不再自动弹出。
5. 用户错过多天后，下一次启动只播放尚未完成的最早一轮，不连续补播多轮。
6. 教程演示默认不保存用户配置变更；如果必须触发真实 UI 状态，需要记录原值并在小节结束时恢复。
7. 移动端隐藏 Agent 和“请她离开”时，相关轮次降级为文字说明或等桌面端再触发。

## 代码核对结果

悬浮按钮入口来自 `AvatarButtonMixin.getDefaultButtonConfigs()`，Live2D、VRM、MMD 共用同一套按钮语义：

| 入口 | DOM 形式 | 点击后内容 |
| --- | --- | --- |
| `mic` | `#${prefix}-btn-mic`，带独立小三角 | 语音开关；小三角打开麦克风弹窗。录音中会显示 `#${prefix}-btn-mic-mute` 静音按钮。 |
| `screen` | `#${prefix}-btn-screen`，带独立小三角 | 屏幕共享开关；小三角打开屏幕/窗口来源列表。 |
| `agent` | `#${prefix}-btn-agent` | Agent 弹窗，含状态栏、总开关、子能力开关和部分侧边快捷面板。 |
| `settings` | `#${prefix}-btn-settings` | 设置弹窗，含对话设置、动画设置、主动搭话、隐私模式、角色设置、API、记忆入口。 |
| `goodbye` | `#${prefix}-btn-goodbye` | 隐藏模型并显示“请她回来”按钮。 |
| lock icon | `#${prefix}-lock-icon` | 锁定/解锁模型交互。 |
| return | `#${prefix}-btn-return` | “请她回来”，可拖拽，点击恢复模型和按钮组。 |

点击后继续细分的弹窗/侧边面板如下：

| 类型 | 代码入口 | 细分功能 |
| --- | --- | --- |
| 麦克风弹窗 | `renderFloatingMicList()` / `#${prefix}-popup-mic` | 扬声器音量、空间音频、降噪、麦克风增益、实时音量条、默认/指定麦克风设备列表。 |
| 屏幕来源弹窗 | `renderScreenSourceList()` / `#${prefix}-popup-screen` | Electron source 列表，按“屏幕”和“窗口”分组，缩略图、选中态、`selectScreenSource(...)`。 |
| Agent 弹窗 | `AgentHUD._createAgentPopupContent()` / `#${prefix}-popup-agent` | 状态栏、Agent 总开关、键鼠控制、Browser Control、专属桌面、用户插件、OpenClaw。 |
| Agent 用户插件侧边面板 | `data-neko-sidepanel-type="agent-user-plugin-actions"` | “管理面板”快捷入口，打开 `/api/agent/user_plugin/dashboard`。 |
| Agent OpenClaw 侧边面板 | `data-neko-sidepanel-type="agent-openclaw-actions"` | “OpenClaw 接入教程”快捷入口，打开 `/api/agent/openclaw/guide`。 |
| Agent 任务 HUD | `AgentHUD.createAgentTaskHUD()` / `#agent-task-hud` | 运行/排队计数、任务列表、空状态、折叠/展开、终止全部任务、拖拽位置保存。 |
| 设置弹窗 | `createSettingsPopupContent()` / `#${prefix}-popup-settings` | 对话设置、动画设置、主动搭话、隐私模式、角色设置、API 密钥、记忆浏览。 |
| 对话设置侧边面板 | `data-neko-sidepanel-type="chat-settings"` | 合并消息、允许打断、表情气泡、回复字数限制滑条。 |
| 动画设置侧边面板 | `data-neko-sidepanel-type="animation-settings"` | 画质、帧率、跟踪鼠标、全屏/局部跟踪、锁定悬停淡化。 |
| 主动搭话侧边面板 | `data-neko-sidepanel-type="interval-proactive-chat"` | 最低间隔、媒体凭证、视觉、新闻、视频、个人动态、音乐、表情包、小游戏邀请。 |
| 隐私模式侧边面板 | `data-neko-sidepanel-type="interval-proactive-vision"` | 感知间隔；主开关是反向语义，UI 勾选“隐私模式”表示关闭主动视觉感知。 |
| 角色设置侧边面板 | `data-neko-sidepanel-type="character-settings"` | 当前模型管理、角色卡/角色设置、音色克隆等按模型类型注入的导航项。 |

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

这一轮已经覆盖：

| 现有场景 | 已介绍内容 |
| --- | --- |
| `intro_basic` | 初次见面、语音入口、悬浮麦克风按钮。 |
| `takeover_capture_cursor` | Agent / 猫爪按钮、Agent 总开关、键鼠控制。 |
| `takeover_plugin_preview` | 用户插件入口和插件管理面板预览。 |
| `takeover_settings_peek` | 设置按钮、角色设置、模型管理、声音克隆、API、记忆入口概览。 |
| `takeover_return_control` | 教程收尾、归还控制权、恢复用户模型。 |

Day 1 实现要求：

- 沿用现有 `UniversalTutorialManager` 和 `YuiGuideDirector` 启动逻辑。
- 不新增 `avatarFloatingGuide` 的 Day 1 scene。
- 现有教程完成或跳过后，把 `avatarFloatingGuide.completedRounds` 标记为包含 `1`。
- 用户跳过现有首页教程，也视为 Day 1 已处理，后续自然日继续进入 Day 2。

## 排期与状态

建议持久化字段：

| 字段 | 说明 |
| --- | --- |
| `avatarFloatingGuide.firstSeenDate` | 首次进入首页的本地日期，作为 Day 2 到 Day 7 的节奏锚点。 |
| `avatarFloatingGuide.completedRounds` | 已完成轮次数组。Day 1 由现有教程完成/跳过后写入。 |
| `avatarFloatingGuide.skippedRounds` | 用户主动跳过的轮次数组。 |
| `avatarFloatingGuide.lastAutoShownDate` | 最近一次自动展示新增悬浮窗教程的本地日期。 |
| `avatarFloatingGuide.currentRound` | 当前运行中的轮次，用于异常恢复和跨页面 handoff。 |

排期建议：

| 本地自然日 | 自动触发内容 | 重点层级 |
| --- | --- | --- |
| Day 1 | 现有首页 Yui 新手教程 | 入口级概览。 |
| Day 2 | 语音、麦克风与音量 | `mic` 按钮、麦克风弹窗、录音中静音按钮。 |
| Day 3 | 屏幕共享与来源选择 | `screen` 按钮、屏幕/窗口来源弹窗。 |
| Day 4 | Agent 基础与任务 HUD | `agent` 弹窗、状态栏、能力开关、任务 HUD。 |
| Day 5 | Agent 扩展侧边面板 | 用户插件侧边面板、OpenClaw 侧边面板、跨窗口 handoff。 |
| Day 6 | 设置弹窗的对话与主动能力 | 对话设置、主动搭话、隐私模式、角色设置。 |
| Day 7 | 动画、模型控制与收尾 | 动画设置、锁定、请她离开、请她回来。 |

## 生命周期接入

第 2 到第 7 天应复用现有首页教程链路，而不是新增独立运行时。

| 能力 | 归属 |
| --- | --- |
| 场景顺序、台词、目标元素、真实 UI 点击 | `YuiGuideDirector` 或新的首页悬浮窗教程 Director。 |
| 全局交互接管、正脸锁、外置聊天窗口按钮禁用 | `TutorialInteractionTakeover`。 |
| 跳过按钮 | `TutorialSkipController`。 |
| 教程期间临时切换 Yui 模型、恢复用户模型 | `TutorialAvatarReloadController`。 |
| 模型动作、表情、LookAt、临时参数 | `AvatarPerformance` + `YuiGuideAvatarStage`。 |
| spotlight / highlight / ghost cursor | 现有 overlay / director 体系。 |
| 弹窗和侧边面板定位 | `AvatarPopupUI.positionPopup()` / `positionSidePanel()`。 |

每轮启动时：

1. 等待当前模型类型的悬浮按钮就绪。
2. 如需强制引导点击，调用 `setTutorialTakingOver(true)`。
3. 显示跳过按钮，并让 skip 落到统一 `requestTutorialDestroy()` / director `skip()` 路径。
4. 需要模型看向按钮时只申请 `lookAt`；需要动作或表情时才申请 `motion` / `expression`。
5. 场景结束、跳过、页面隐藏、模型切换、异常时，都必须清理 spotlight、ghost cursor、弹窗、侧边面板、临时锁和演出 session。

## 高亮与光标规则

- 每轮最多保留一个 persistent spotlight，用于说明当前上下文，例如悬浮按钮组、Agent 弹窗或设置弹窗。
- 当前要点击或讲解的按钮使用 action spotlight。
- 侧边面板使用 retained extra spotlight，避免打开面板时重置整个弹窗高亮。
- ghost cursor 必须遵循“先高亮、再移动、再可见 click、再调用真实 UI API”的顺序。
- 如果目标 DOM 不存在，当前小节安全跳过或退化为文字介绍，不阻塞整轮教程。
- 小三角弹窗和 hover 侧边面板需要先确认 `popup.style.display === 'flex'`，再定位侧边面板目标。
- 任何跨窗口入口默认只讲用途，不自动打开；如确需演示，必须按 handoff 规则隐藏首页 cursor，并在返回首页后恢复。

## Day 2：语音、麦克风与音量

目标：把 Day 1 只点到为止的语音入口展开讲清楚，让用户知道麦克风弹窗不只是设备列表。

讲解功能：

- `mic` 主按钮用于开始/停止语音会话。
- `mic` 小三角打开 `#${prefix}-popup-mic`。
- 录音中会出现 `#${prefix}-btn-mic-mute`，用于临时静音或取消静音。
- 麦克风弹窗左侧包含扬声器音量、空间音频、降噪、麦克风增益、实时麦克风音量。
- 麦克风弹窗右侧包含系统默认麦克风和具体输入设备列表。
- 麦克风权限或设备不可用时，弹窗显示错误/空状态。

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 2.1 语音入口 | `#${prefix}-btn-mic` | 说明这是语音会话开关。教程只演示入口，不默认开始真实录音。 |
| 2.2 设备与音频设置 | `.${prefix}-trigger-icon-mic` -> `#${prefix}-popup-mic` | 打开麦克风弹窗，介绍左右两栏布局。 |
| 2.3 扬声器与空间音频 | `speaker.volumeLabel`、`speaker.spatialAudioLabel` 对应区域 | 说明这些影响 AI 语音播放，不影响麦克风输入。 |
| 2.4 降噪与增益 | `microphone.noiseReduction`、`#mic-gain-slider` | 说明噪声环境可开降噪，声音太小再调增益。 |
| 2.5 实时音量 | `#mic-volume-bar-bg`、`#mic-volume-status` | 说明录音后可用音量条判断过低、正常、过载。 |
| 2.6 设备列表 | `.mic-option` | 说明系统默认和指定设备的区别。 |
| 2.7 录音中静音 | `#${prefix}-btn-mic-mute` | 仅在录音中出现；未录音时使用文字说明或临时展示占位高亮。 |

清理要求：

- 关闭麦克风弹窗。
- 不改变用户选择的麦克风、增益、降噪、空间音频和扬声器音量。
- 如果教程临时打开过麦克风权限请求，失败时不阻塞后续轮次。

## Day 3：屏幕共享与来源选择

目标：说明屏幕共享只服务于语音/音视频对话，并让用户认识屏幕/窗口来源列表。

讲解功能：

- `screen` 主按钮用于开始/停止屏幕共享。
- 不处于语音通话时，直接开启会提示“屏幕分享仅用于音视频通话”。
- `screen` 小三角打开来源弹窗。
- 来源弹窗按“屏幕”和“窗口”分组，显示缩略图、名称、选中态。
- 点击来源调用 `selectScreenSource(source.id, source.name, displayName)` 保存共享目标。

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 3.1 主按钮限制 | `#${prefix}-btn-screen` | 说明为什么需要先进入语音会话。 |
| 3.2 来源列表入口 | `.${prefix}-trigger-icon-screen` | 打开 `#${prefix}-popup-screen`。 |
| 3.3 屏幕分组 | `.screen-source-option[data-source-id^="screen:"]` | 展示整屏来源。 |
| 3.4 窗口分组 | `.screen-source-option[data-source-id^="window:"]` | 展示单窗口来源。 |
| 3.5 选中态 | `.screen-source-option.selected` | 说明蓝色边框/背景表示当前共享目标。 |
| 3.6 降级状态 | `app.screenSource.notAvailable` / `app.screenSource.noSources` | Electron `desktopCapturer` 不可用或无来源时展示文字。 |

清理要求：

- 关闭屏幕来源弹窗。
- 不默认选择真实窗口。
- 不改变用户原有屏幕共享状态。

## Day 4：Agent 基础与任务 HUD

目标：在 Day 1 介绍过 Agent 入口的基础上，补充 Agent 弹窗里的状态、开关依赖和任务 HUD。

讲解功能：

- Agent 弹窗状态栏显示连接、预检和可用性状态。
- Agent 总开关是键鼠控制、Browser Control、专属桌面、用户插件、OpenClaw 的前置条件。
- 各子能力可能因 Agent 未启用、预检失败、OpenClaw 不可用等原因被禁用。
- 任务 HUD 显示运行/排队计数、任务列表、空状态、折叠/展开、终止全部任务和拖拽位置。

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 4.1 打开 Agent 弹窗 | `#${prefix}-btn-agent` -> `#${prefix}-popup-agent` | 介绍这里是自动化能力中心。 |
| 4.2 状态栏 | `#live2d-agent-status` / 同名多前缀元素 | 说明“查询中、就绪、已开启、预检失败”等状态。 |
| 4.3 Agent 总开关 | `#${prefix}-agent-master` | 说明子能力依赖总开关。 |
| 4.4 子能力 | `#${prefix}-agent-keyboard`、`#${prefix}-agent-browser`、`#${prefix}-agent-openfang` | 说明键鼠、浏览器、专属桌面各自用途。 |
| 4.5 任务 HUD | `#agent-task-hud` | 展示任务运行时会出现，不创建真实任务。 |
| 4.6 HUD 操作 | `#agent-task-hud-minimize`、`#agent-task-hud-cancel` | 说明折叠、展开、终止全部任务需要确认。 |

清理要求：

- 关闭 Agent 弹窗。
- 如果教程临时展示 HUD，结束后恢复原显示状态和折叠状态。
- 不强制打开 Agent 总开关或任何子能力。

## Day 5：Agent 扩展侧边面板

目标：专门介绍 Agent 弹窗里会继续展开的用户插件和 OpenClaw 侧边面板。

讲解功能：

- 用户插件开关允许角色调用用户安装的插件能力。
- 用户插件侧边面板有“管理面板”入口，打开 `/api/agent/user_plugin/dashboard`。
- OpenClaw 开关用于连接外部 OpenClaw 服务。
- OpenClaw 侧边面板有“OpenClaw 接入教程”入口，打开 `/api/agent/openclaw/guide`。
- OpenClaw 的可用性由 `/api/agent/openclaw/availability` 和 Agent capability 状态共同决定。

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 5.1 回到 Agent 弹窗 | `#${prefix}-btn-agent` | 复用 Day 4 已打开过的上下文。 |
| 5.2 用户插件开关 | `#${prefix}-agent-user-plugin` | 说明这是模型调用插件能力的许可。 |
| 5.3 用户插件侧边面板 | `#agent-user-plugin-actions` | hover / 引导展开 `data-neko-sidepanel-type="agent-user-plugin-actions"`。 |
| 5.4 管理面板入口 | `#neko-sidepanel-action-agent-user-plugin-management-panel` | 说明会打开插件管理面板，默认不自动打开新窗口。 |
| 5.5 OpenClaw 开关 | `#${prefix}-agent-openclaw` | 说明 OpenClaw 需要外部服务可用。 |
| 5.6 OpenClaw 侧边面板 | `#agent-openclaw-actions` | 展开 `data-neko-sidepanel-type="agent-openclaw-actions"`。 |
| 5.7 接入教程入口 | `#neko-sidepanel-action-agent-openclaw-openclaw-guide` | 说明入口用途，必要时按 handoff 演示。 |

生命周期注意：

- 侧边面板是 hover 面板，教程期间需要禁用普通 hover 自动收起或延长 guard。
- 只允许一个 Agent 侧边面板展开，展开新面板前调用 `collapseOtherSidePanels()`。
- 跨窗口演示结束后必须关闭临时打开的 Agent 面板和侧边面板。

## Day 6：设置弹窗的对话、主动能力与角色入口

目标：把设置弹窗内“会影响聊天体验”的侧边面板讲清楚。

讲解功能：

- 设置弹窗首层包含“对话设置”“动画设置”“主动搭话”“隐私模式”，桌面端还有角色设置、API 密钥、记忆浏览。
- 对话设置侧边面板包含合并消息、允许打断、表情气泡、回复字数限制。
- 主动搭话侧边面板包含最低间隔、媒体凭证、视觉/新闻/视频/个人动态/音乐/表情包/小游戏邀请等方式。
- 隐私模式是反向开关：UI 勾选隐私模式时，底层 `proactiveVisionEnabled` 为 false。
- 角色设置侧边面板包含模型/角色相关管理入口；API 密钥和记忆浏览是独立导航入口。

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 6.1 打开设置弹窗 | `#${prefix}-btn-settings` -> `#${prefix}-popup-settings` | 介绍设置弹窗是日常偏好入口。 |
| 6.2 对话设置入口 | `#${prefix}-menu-chat-settings` 或首个 settings menu item | 展开 `data-neko-sidepanel-type="chat-settings"`。 |
| 6.3 对话设置控件 | `#${prefix}-toggle-merge-messages`、`#${prefix}-toggle-focus-mode`、`#${prefix}-toggle-avatar-reaction-bubble`、文本上限滑条 | 说明每项影响聊天输出或打断体验。 |
| 6.4 主动搭话 | `#${prefix}-toggle-proactive-chat` | 展开 `interval-proactive-chat`，介绍最低间隔。 |
| 6.5 搭话方式 | `#${prefix}-proactive-vision-chat` 等 chat mode toggle | 介绍视觉、新闻、视频、个人动态、音乐、表情包、小游戏邀请。 |
| 6.6 媒体凭证 | `/api/auth/page` 入口 | 说明某些媒体来源需要授权，默认不打开新窗口。 |
| 6.7 隐私模式 | `#${prefix}-toggle-proactive-vision` -> `interval-proactive-vision` | 说明勾选后关闭主动视觉感知，并可调感知间隔。 |
| 6.8 角色/API/记忆入口 | `#${prefix}-menu-character`、`#${prefix}-menu-api-keys`、`#${prefix}-menu-memory` | 说明这些是独立管理页入口，不在本轮深入配置。 |

清理要求：

- 关闭设置弹窗和所有设置侧边面板。
- 恢复临时演示过的开关和滑条值。
- 不自动打开 API、记忆、角色管理、媒体凭证页面。

## Day 7：动画设置、模型控制与收尾

目标：介绍影响模型外观/行为的设置，并以锁定、请她离开、请她回来完成 7 日教程。

讲解功能：

- 动画设置侧边面板包含画质、帧率、跟踪鼠标、全屏/局部跟踪、锁定悬停淡化。
- Live2D 显示“全屏跟踪”；VRM/MMD 显示“局部跟踪”。
- 锁图标用于锁定/解锁模型交互。
- “请她离开”隐藏模型、悬浮按钮和锁图标，并显示“请她回来”。
- “请她回来”按钮可拖拽，点击后恢复模型。

建议小节：

| 小节 | 目标 DOM / API | 说明 |
| --- | --- | --- |
| 7.1 动画设置入口 | `#${prefix}-btn-settings` -> 动画设置 menu item | 展开 `data-neko-sidepanel-type="animation-settings"`。 |
| 7.2 画质与帧率 | 动画设置里的 render quality / frame rate slider | 说明会影响性能和模型刷新。 |
| 7.3 跟踪设置 | `#${prefix}-mouse-tracking-toggle`、全屏/局部跟踪行 | 说明模型是否跟随鼠标，以及跟随范围差异。 |
| 7.4 锁定悬停淡化 | 动画设置里的 locked hover fade 行 | 说明锁定时悬停淡化行为。 |
| 7.5 锁图标 | `#${prefix}-lock-icon` | 说明锁定交互；教程可演示但需恢复原锁定状态。 |
| 7.6 请她离开 | `#${prefix}-btn-goodbye` | 触发真实 `live2d-goodbye-click` 或对应模型类型事件。 |
| 7.7 请她回来 | `#${prefix}-btn-return` | 等返回按钮出现，说明可拖拽，最后点击恢复。 |

清理要求：

- 恢复画质、帧率、跟踪、锁定、悬停淡化的原值。
- 确保模型容器、悬浮按钮、锁图标、返回按钮状态正确。
- 标记 7 轮全部完成，不再自动弹出。

## 功能总表

| 功能组 | 包含功能 | 轮次 |
| --- | --- | --- |
| 现有首页教程 | 初次见面、语音入口、Agent/键鼠控制、插件预览、设置概览、归还控制权 | Day 1 |
| 麦克风弹窗 | 设备选择、扬声器音量、空间音频、降噪、麦克风增益、实时音量、录音中静音 | Day 2 |
| 屏幕来源弹窗 | 开始/停止共享、屏幕/窗口来源、缩略图、选中态、不可用/空状态 | Day 3 |
| Agent 基础 | 状态栏、总开关、键鼠控制、Browser Control、专属桌面、任务 HUD | Day 4 |
| Agent 扩展 | 用户插件、插件管理面板、OpenClaw、OpenClaw 接入教程 | Day 5 |
| 对话与主动能力 | 对话设置、主动搭话、隐私模式、媒体凭证、角色/API/记忆入口 | Day 6 |
| 动画与模型控制 | 画质、帧率、鼠标跟踪、全屏/局部跟踪、锁定、离开、回来 | Day 7 |

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
- `static/avatar-popup-common.js`：弹窗和侧边面板定位、边界避让、侧边面板互斥。
- `static/live2d-ui-buttons.js`、`static/vrm-ui-buttons.js`、`static/mmd-ui-buttons.js`：不同模型类型的悬浮按钮定位、锁图标和返回状态。
- `static/common-ui-hud.js`：Agent 弹窗内容和 Agent 任务 HUD。
- `static/app-agent.js`、`static/js/agent_ui_v2.js`：Agent 状态机、能力检查和开关联动。
- `static/app-ui.js`：语音、屏幕共享、请她离开/回来等全局事件处理。
- `static/app-audio-capture.js`：麦克风权限、设备、增益、降噪、音量可视化、静音状态。
- `static/app-screen.js`：屏幕来源选择、屏幕共享流、截图和视频帧发送。
- `static/avatar-ui-drag.js`：主动搭话方式开关、弹窗兼容逻辑、拖拽期间 UI 屏蔽。

## 验证清单

实现时至少检查：

1. Day 1 仍走现有首页 Yui 新手教程，不出现第二套 Day 1。
2. 现有首页教程完成或跳过后，新增排期能从 Day 2 开始。
3. 每天只自动展示一轮，完成/跳过后同日不重复展示。
4. 错过多天后不会连续播放多轮。
5. 每轮 skip 都会落到统一销毁路径。
6. 弹窗和侧边面板在完成、跳过、异常、页面隐藏、模型切换时全部关闭。
7. 每个 ghost cursor click 都对应真实 UI 状态变化；不存在只移动光标不执行操作的演示。
8. 目标 DOM 缺失时能安全跳过当前小节。
9. 移动端不尝试展示隐藏的 Agent 和“请她离开”按钮。
10. Agent 用户插件和 OpenClaw 侧边面板互斥展开，不残留 hover timer。
11. 设置类临时演示会恢复原值，不悄悄改用户偏好。
12. 麦克风、屏幕、跨窗口入口遇到权限失败或不可用时，不阻塞整轮教程。
13. `AvatarPerformance` session 在完成、跳过、失败时都 release。
14. reduced motion 下教程能完成，且不播放大幅转场。
