# Day 4 相处距离、主动陪伴与模型行为教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 4 的“相处距离、主动陪伴与模型行为”落到现有悬浮窗教程实现上。Day 4 已有正式 round，配置在 `static/yui-guide-director.js` 的 `AVATAR_FLOATING_GUIDE_ROUNDS[4]`。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 目标体验

Day 4 使用镜像情绪共鸣与相处距离，不继续堆入口，而是教用户如何调整“她陪伴你的方式”：

1. 对话频率、打断、表情气泡和回复长度可以调。
2. 动画画质、帧率、鼠标跟随、锁定、离开/回来可以调。
3. 隐私模式/主动视觉的反向语义必须讲清楚：隐私模式开启表示关闭主动视觉感知；关闭隐私模式才允许按间隔主动看屏幕。
4. 所有演示都要恢复用户原有设置状态，不保存教程临时变更。
5. 若用户打开了主动搭话并触发小游戏邀请，可在剧场后顺势发起小游戏支线。

## 现有代码入口

启动链路：

```text
UniversalTutorialManager.startAvatarFloatingGuideRound(4)
└─ YuiGuideDirector.playAvatarFloatingRound(4)
   └─ AVATAR_FLOATING_GUIDE_ROUNDS[4].scenes
```

相关实现：

- `static/yui-guide-director.js`
  - `AVATAR_FLOATING_GUIDE_ROUNDS[4]`
  - `show-settings-sidepanel:chat-settings`
  - `show-settings-sidepanel:interval-proactive-chat`
  - `show-settings-sidepanel:interval-proactive-vision`
  - `show-settings-sidepanel:animation-settings`
- 设置侧边栏：
  - `data-neko-sidepanel-type="chat-settings"`
  - `data-neko-sidepanel-type="interval-proactive-chat"`
  - `data-neko-sidepanel-type="interval-proactive-vision"`
  - `data-neko-sidepanel-type="animation-settings"`
- 悬浮按钮：
  - `#${p}-lock-icon`
  - `#${p}-btn-goodbye`
  - `#${p}-btn-return`

## 通用生命周期复用

Day 4 是已落地的悬浮窗正式 round，并且会频繁打开设置弹窗和多个侧边栏，所以必须完整复用通用生命周期模块，尤其是高亮清理、打断恢复和 skip teardown。

| 通用能力 | Day 4 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | round 启动后进入 taking-over；设置弹窗、锁定按钮、离开/回来按钮、skip 按钮按白名单放行。 | 不复制全局事件守卫，不让教程期间的普通设置点击穿透。 |
| `TutorialHighlightController` | 对话设置、主动搭话、隐私模式、动画设置侧边栏使用 union spotlight；锁定和离开/回来使用 primary/secondary spotlight。 | 不手写侧边栏高亮 DOM；切 scene 前用 `cleanupBefore` 或统一清理。 |
| `TutorialInterruptController` | 用户抢鼠标时暂停当前设置 tour；轻微抵抗后恢复同一 scene；angry exit 立即清理设置面板高亮和 Ghost Cursor。 | 不把隐私开关状态变化当打断结果保存。 |
| `TutorialSkipController` | Manager 统一显示 skip；skip 后关闭设置弹窗、侧边栏、锁定/离开按钮高亮。 | 不在 Day 4 小游戏支线里复制 skip 按钮。 |
| `TutorialAvatarReloadController` | 若 Day 4 使用教程模型，启动和恢复仍由 Manager 管。 | 不在动画设置或离开/回来 scene 里直接 reload 模型。 |

剧场后小游戏支线是普通聊天窗支线，默认不启用 takeover；如果支线按钮启动小游戏页面，页面 runtime 必须遵守同等 skip/清理语义。

## 模型动作与情绪随机池

Day 4 是正式悬浮窗 round，演出时使用临时 `yui-origin` Live2D。普通台词按情绪从内置动作池随机播放：`happy` 12 个、`sad` 6 个、`angry` 7 个、`neutral` 7 个、`surprised` 5 个、`Idle` 3 个。

Day 4 的随机动作不得改动用户动画设置，也不得与锁定、离开/回来、隐私模式展示抢交互焦点。若同时存在 Ghost Cursor LookAt 或 guide idle sway，自定义动作优先。

| 台词段落 | 情绪分类 | 随机动作规则 |
| --- | --- | --- |
| 开场：“今天，就让我悄悄跟上……” | `happy` | 从 happy 池随机，温柔靠近。 |
| 对话设置：“如果有时候你觉得我发消息太频繁……” | `neutral` | 从 neutral 池随机，说明设置。 |
| 动画/锁定/离开回来：“看这里看这里……” | `happy` | 从 happy 池随机；讲隐私或安静时可回落 neutral。 |
| 隐私模式：“当这个按钮关闭时……” | `neutral` | 从 neutral 池随机，避免夸张。 |
| 收尾：“真正舒服的陪伴……” | `neutral` | 从 neutral 或 Idle 池随机。 |
| 小游戏支线：“哈哈，是不是超级惊喜呀？” | `surprised` | 从 surprised 池随机。 |

## 现有 Scene 与新剧本映射

| 新剧本阶段 | 现有 scene | 处理建议 |
| --- | --- | --- |
| 对话节奏设置 | `day4_intro_companion`、`day4_chat_settings` | 保留开场，随后展示合并消息、允许打断、表情气泡、回复 token 上限。 |
| 动画、锁定与离开/回来 | `day4_animation_tracking`、`day4_lock_interaction`、`day4_goodbye_return` | 按新版顺序提前到隐私模式之前；只展示，不保存临时状态。 |
| 隐私模式与主动视觉 | `day4_privacy_mode`，可保留 `day4_proactive_chat` 轻量过渡 | 必须讲清反向语义。 |
| 低打扰收尾 | `day4_wrap` | 使用新版“舒服的陪伴”收尾。 |
| 剧场后小游戏支线 | 聊天窗支线 | 触发条件为用户打开主动搭话并触发小游戏邀请。 |

## 动作时序

Day 4 已有 `AVATAR_FLOATING_GUIDE_ROUNDS[4]`。如果按新版顺序调整 scenes，仍沿用 `playAvatarFloatingScene()` 的基础节奏：台词和 spotlight 同时出现；约 220ms 后 Ghost Cursor 移动；`tour` 会先移到 union spotlight，再巡游可见子控件；scene operation 完成后刷新高亮；旁白结束后再切下一段。

| 台词段落 | 高亮时序 | Ghost Cursor 时序 | 真实操作/清理 |
| --- | --- | --- | --- |
| 开场：“今天，就让我悄悄跟上你的步伐吧……” | persistent 默认聊天窗；primary 也是聊天窗。 | Cursor 移到聊天窗并 wobble。 | 不打开设置；只建立当天主题。 |
| 对话设置：“如果有时候你觉得我发消息太频繁……” | `operation: show-settings-sidepanel:chat-settings` 先打开设置弹窗并展开对话设置；persistent 为 `#${p}-popup-settings`；primary 为侧边栏与锚点 union。 | Cursor 移到对话设置 union；`tour` 继续巡游前几个可见控件：合并消息、允许打断、表情气泡、回复 token 上限。 | 不点击和不改值；scene 结束保留设置弹窗给下一段使用。 |
| 动画设置：“看这里看这里！在这儿你能决定……” | 打开/切换到 `animation-settings`；persistent 保持设置弹窗；primary 为动画侧边栏 union。 | Cursor 先移到动画设置入口/面板 union，再 tour 画质、帧率、鼠标跟踪、全屏/局部跟踪、锁定悬停淡化等可见控件。 | 不改画质、帧率或跟踪设置。 |
| 锁定：“看到那个小锁图标了吗？” | scene 带 `cleanupBefore` 时先关闭设置弹窗；primary 切到 `#${p}-lock-icon`。 | Cursor 移到锁定图标；只 move，不 click。 | 不切换锁定状态，除非实现保存并恢复原状态。 |
| 离开/回来：“如果你突然要开会……” | primary 为 `#${p}-btn-goodbye`；`#${p}-btn-return` 只在当前真实可见时作为 secondary。 | Cursor 先移到“请她离开”按钮并 wobble；如果“回来”按钮不可见，不移动到空位置，也不为了展示而点击离开。 | 不真的让 Yui 离开；若后续真实演示，必须保证 return target 和模型可见性恢复。 |
| 隐私模式：“当这个按钮关闭时……” | `cleanupBefore` 先清理离开/回来高亮；打开设置并展开 `interval-proactive-vision`；persistent 为设置弹窗；primary 为主动视觉/隐私模式开关。 | Cursor 移到主动视觉/隐私模式开关；只 move，不 click。 | 不改变用户隐私模式；文案和 UI 必须明确“隐私模式开启 = 关闭主动视觉”。 |
| 收尾：“真正舒服的陪伴……” | primary 回到聊天窗；operation 为 `cleanup`；台词约 70% 时触发每日花瓣转场并清掉所有 spotlight。 | Cursor 移到聊天窗并 wobble；花瓣 cue 触发时隐藏 cursor。 | 关闭设置弹窗和侧边栏，恢复用户原有设置状态；转场结束后写入 Day 4 完成态。 |
| 剧场后小游戏支线：“哈哈，是不是超级惊喜呀？” | 普通聊天消息或真实 `choicePrompt`；不启用 takeover。 | 默认不显示 Ghost Cursor；用户点小游戏按钮后由小游戏邀请 UI 自己接管。 | 只在主动搭话已打开且真实触发 mini-game invite 后出现；当天不重复。 |

## Scene 顺序建议

当前代码顺序：

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

新版剧本顺序：

```text
day4_chat_settings
day4_animation_tracking
day4_lock_interaction / day4_goodbye_return
day4_privacy_mode
day4_wrap
```

建议实现为：

1. 保留 `day4_intro_companion` 做“今天，让我悄悄跟上你的步伐吧”的开场。
2. `day4_chat_settings` 先讲对话节奏。
3. `day4_animation_tracking`、`day4_lock_interaction`、`day4_goodbye_return` 提前到主动视觉之前。
4. `day4_proactive_chat` 只作为主动搭话入口的轻量展示，避免和 Day 1 主动搭话重复长讲。
5. `day4_privacy_mode` 放在后半段，用较短文案讲清楚反向语义。
6. `day4_wrap` 收尾并恢复用户原有设置状态。

如果调整顺序，要同步检查语音资源、指标和任何按 index 判断的等待逻辑。目前 `playAvatarFloatingRound()` 按 scenes 数组顺序播放，没有硬编码 Day 4 index。

## 需要修改的内容

### 1. 文案替换

保留这些 text key：

- `tutorial.avatarFloating.day4.intro`
- `tutorial.avatarFloating.day4.chatSettings`
- `tutorial.avatarFloating.day4.proactiveChat`
- `tutorial.avatarFloating.day4.privacyMode`
- `tutorial.avatarFloating.day4.animationTracking`
- `tutorial.avatarFloating.day4.lockInteraction`
- `tutorial.avatarFloating.day4.goodbyeReturn`
- `tutorial.avatarFloating.day4.wrap`

总稿明确给出的主台词：

- 对话节奏：“今天，就让我悄悄跟上你的步伐吧……”
- 对话设置：“如果有时候你觉得我发消息太频繁……”
- 动画/锁定/离开回来：“看这里看这里！在这儿你能决定让我看起来更精致细腻……”
- 隐私模式：“当这个按钮关闭时，我就能看着你正在忙碌的画面……”
- 收尾：“真正舒服的陪伴，并不是一刻不停地缠着你……”

台词可以使用甜美版本，但实现说明里必须保留精确语义，尤其是隐私模式反向语义和临时设置不落盘。

### 2. 对话设置侧边栏

`day4_chat_settings` 应高亮这些控件或其所在区域：

- 合并消息。
- 允许打断。
- 表情气泡。
- 回复 token 上限。

如果控件分散，使用 union spotlight 或面板 tour，不要逐项点击改值。

### 3. 动画、锁定、离开/回来

`day4_animation_tracking` 应展示：

- 画质。
- 帧率。
- 鼠标跟踪。
- 全屏/局部跟踪。
- 锁定悬停淡化。

`day4_lock_interaction` 只高亮锁，不实际切换锁定状态，除非保存进入前状态并恢复。

`day4_goodbye_return` 只展示“请她离开/回来”入口。当前导演策略是优先高亮“请她离开”，只有 return 按钮已经真实可见时才补 secondary；不要为了让 return 出现而真的让 Yui 离开导致教程丢失目标。如必须演示真实点击，需要 Director 支持临时 return button target 和恢复。

### 4. 主动视觉与隐私模式

`day4_privacy_mode` 必须在实现说明中明确反向语义：

- 隐私模式开启 = 关闭主动视觉感知。
- 隐私模式关闭 = 允许按间隔主动看屏幕。

交互要求：

- 不自动改变用户开关值。
- 如果为了展示临时切换 UI，必须保存进入前状态并在 scene 结束或 finally 中恢复。
- 台词层可以柔和表达“私密空间”和“仍然守候”，不要写成隐私声明。

### 5. 剧场后小游戏支线

总稿当前标题写作“主动视觉邀请”，但触发条件和台词实际是小游戏邀请：

- 触发条件：用户打开了主动搭话，并触发了小游戏邀请。
- 台词：“哈哈，是不是超级惊喜呀？我不光能陪你聊天，居然还能主动邀请你一起打游戏哦！快快快，来一局紧张刺激的足球小游戏~”

建议通过现有聊天窗 `choicePrompt.source === 'mini_game_invite'` 或小游戏邀请事件接入：

1. Day 4 完成后监听主动搭话/小游戏邀请状态。
2. 如果出现 mini-game invite，优先让真实 invite UI 呈现。
3. 如果只是教程支线按钮，使用 `message.actions`，不要伪造 `choicePrompt` 的后端 session。

状态建议：

- `avatarFloatingGuide.day4ProactiveChatSeen`
- `avatarFloatingGuide.day4MiniGameInviteSeen`
- `avatarFloatingGuide.day4MiniGameBranchShownDate`

## 生命周期要求

1. Day 4 仍由 Manager 启动临时模型、skip 按钮和 taking-over。
2. 打开设置弹窗和多个侧边栏时，必须保证同一时间只展开一个主要侧边栏。
3. Scene 切换前后调用 `cleanupBefore` 或 `closeAvatarFloatingGuidePanels()`，避免侧边栏叠住。
4. 收尾时恢复用户原有设置状态，不保存教程演示中的临时开关。
5. skip / angry exit 时必须清理 settings 面板、sidepanel、lock highlight、goodbye/return secondary spotlight。
6. destroy / pagehide / remote terminate 必须走 Manager 统一 teardown，确保 `TutorialAvatarReloadController` 能恢复用户模型。
7. angry exit 不能写 completed round；语音结束后走统一 skip。
8. Day 4 正常收尾必须播放每日花瓣转场；skip、angry exit、destroy 不播放正常收尾花瓣。

## 验收清单

1. Day 4 能打开设置弹窗，并按新版顺序展示对话设置、动画设置、锁定、离开/回来、隐私模式。
2. 对话设置 tour 不会真的改变用户设置。
3. 动画设置 tour 后侧边栏可正常关闭。
4. 锁定按钮、请她离开按钮能被高亮；回来按钮若真实可见也可被 secondary 高亮。教程结束后按钮状态和模型可见性恢复。
5. 隐私模式反向语义在 UI 或说明中明确，不依赖台词硬讲。
6. Day 4 收尾后所有设置面板、sidepanel、高亮、Ghost Cursor 和 taking-over 都清理干净。
7. 小游戏支线只在真实触发条件满足时出现，且当天不重复弹。
8. Day 4 收尾花瓣转场正常播放，且设置弹窗和隐私模式高亮不会残留。
