# Day 4 相处距离与主动陪伴教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 4 的“相处距离、主动陪伴与模型行为”落到现有悬浮窗教程实现上。Day 4 已有正式 round，配置在 `static/yui-guide-director.js` 的 `AVATAR_FLOATING_GUIDE_ROUNDS[4]`。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 目标体验

Day 4 的目标不是继续堆功能，而是让用户知道怎么调节“她陪伴你的方式”：

1. 对话频率、打断、表情气泡和回复长度可以调。
2. 动画画质、帧率、鼠标跟随、锁定、离开/回来可以调。
3. 主动视觉/隐私模式的反向语义必须讲清楚，但台词可以更柔和。
4. 若用户已经打开主动搭话并触发小游戏邀请，可在剧场后顺势邀请小游戏。

注意：主剧本文档中这段支线标题仍写作“主动视觉邀请”，但触发条件和台词实际是小游戏邀请。开发文档按当前内容理解为“小游戏邀请支线”；正式实现前建议把主剧本文档标题同步改名，避免后续排期误读。

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

## 现有 Scene 与新剧本映射

| 新剧本阶段 | 现有 scene | 处理建议 |
| --- | --- | --- |
| 对话节奏设置 | `day4_chat_settings` | 保留，更新文案。 |
| 动画、锁定与离开/回来 | `day4_animation_tracking`、`day4_lock_interaction`、`day4_goodbye_return` | 新剧本希望更早讲动画；可调整顺序或保留现有顺序。 |
| 隐私模式与主动视觉 | `day4_privacy_mode` | 保留，必须讲反向语义。 |
| 低打扰收尾 | `day4_wrap` | 更新文案。 |
| 主动搭话/小游戏支线 | `day4_proactive_chat` + 聊天窗支线 | 主剧本阶段没有单列主动搭话演示，但支线触发依赖用户打开主动搭话；保留该 scene 或确保状态来自真实用户操作。 |

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

用户新剧本顺序：

```text
day4_chat_settings
day4_animation_tracking
day4_lock_interaction / day4_goodbye_return
day4_privacy_mode
day4_wrap
```

建议实现为：

1. 保留 `day4_intro_companion` 做开场。
2. `day4_chat_settings` 先讲对话节奏。
3. `day4_animation_tracking`、`day4_lock_interaction`、`day4_goodbye_return` 提前到主动视觉之前。
4. `day4_proactive_chat` 可放在隐私模式前后均可，但如果支线依赖主动搭话，应保留主线展示。
5. `day4_privacy_mode` 放在后半段，用较短文案讲清楚。
6. `day4_wrap` 收尾。

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

台词可使用用户剧本中的甜美版本，但实现说明里必须保留精确语义，尤其是：

- 隐私模式开启表示关闭主动视觉。
- 隐私模式关闭后才允许按间隔主动看屏幕。
- 教程演示期间不应保存临时设置变更。

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

`day4_goodbye_return` 只展示“请她离开/回来”入口。不要真的让 Yui 离开导致教程丢失目标；如必须演示真实点击，需要 Director 支持临时 return button target 和恢复。

### 4. 主动视觉与隐私模式

`day4_privacy_mode` 必须在实现说明中明确反向语义，但台词不必像隐私声明。建议：

- UI 动作层：显示“开启隐私模式 = 关闭主动视觉”。
- 台词层：使用“小帘子”隐喻。
- 交互层：不自动改变用户开关值。

### 5. 剧场后小游戏支线

用户剧本当前支线标题写作“主动视觉邀请”，但实际要求是小游戏邀请：

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

## 验收清单

1. Day 4 能打开设置弹窗，并依次展示对话设置、主动搭话、隐私模式、动画设置等侧边栏。
2. 对话设置 tour 不会真的改变用户设置。
3. 动画设置 tour 后侧边栏可正常关闭。
4. 锁定按钮、请她离开/回来按钮能被高亮；教程结束后按钮状态和模型可见性恢复。
5. 隐私模式反向语义在 UI 或说明中明确，不依赖台词硬讲。
6. Day 4 收尾后所有设置面板、sidepanel、高亮、ghost cursor 和 taking-over 都清理干净。
7. 小游戏支线只在真实触发条件满足时出现，且当天不重复弹。
