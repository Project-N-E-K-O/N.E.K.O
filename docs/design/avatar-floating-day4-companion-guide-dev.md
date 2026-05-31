# Day 4 相处距离、主动陪伴与模型行为教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 4 的主线内容，并以 `avatar-floating-7day-complete-guide-dev.md` 作为逐句导演、生命周期和验收基准。Day 4 的功能剧情仍是四段：对话节奏设置、动画/锁定/离开回来、隐私模式与主动视觉、收尾；代码层为了统一“每日第一句先高亮聊天窗”的规则，实际注册为 5 个 scene。

剧场后“主动视觉邀请 / 小游戏邀请”不属于主线，统一放到独立支线文档。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-7day-complete-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 完整指南对齐基线

Day 4 的相处距离教学按本文 5 个 scene 落地；与完整指南对齐时重点遵守：

1. 首句 `day4_intro_companion` 只高亮聊天窗，不提前打开设置。
2. 设置类 scene 在 prepare 阶段打开最终侧边栏，台词开始时只高亮侧边栏容器或具体开关，不再高亮齿轮或整张设置弹窗。
3. `day4_animation_tracking` 内部按阶段互斥切换动画侧边栏、锁定按钮、离开/回来按钮；切换前必须先清理上一段高光。
4. `day4_privacy_mode` 只高亮 `#${p}-toggle-proactive-vision` 或其所在侧边栏，不点击、不改变开关状态，并保持隐私模式反向语义说明。
5. 收尾 `day4_wrap` 先关闭设置弹窗和侧边栏，重新高亮聊天窗，并在约 70% cue 同步清理高光、Ghost Cursor 和外置聊天窗状态。
6. round 开场由 `playAvatarFloatingRound(4)` 统一先执行 `ensureChatVisible()`，并在聊天窗打开后通过 `NekoHomeTutorialFeatureController.enforce()` 再次禁用 proactive/Galgame；首句聊天窗高光只能在这个前置完成后显示。

## 目标体验

Day 4 使用相处距离设计，让用户知道如何调整“她什么时候靠近、什么时候安静”。

用户当天只需要形成四个认知：

1. 对话节奏、打断、表情气泡和回复长短可以调。
2. 动画表现、鼠标跟踪、锁定、离开/回来可以调。
3. 隐私模式开启表示关闭主动视觉感知；隐私模式关闭才允许按间隔主动看屏幕。
4. 教程演示不保存临时设置，收尾后恢复干净状态。

主线不要新增小游戏邀请，不要把主动搭话展开成独立长教学，不要真的锁定模型或让 Yui 离开。

## 代码锚点

- `static/yui-guide-day4-companion-guide.js`
- `window.YuiGuideDailyGuides[4].round`
- `YuiGuideDirector.playAvatarFloatingRound(4)`
- `prepareAvatarFloatingScene()`
- `runDay4AnimationDistanceShowcase()`
- `closeAvatarFloatingGuidePanels()`
- `playAvatarFloatingPetalTransitionAtCue()`
- `chat-settings`
- `animation-settings`
- `interval-proactive-vision`
- `#${p}-lock-icon`
- `#${p}-btn-goodbye`
- `#${p}-btn-return`

`interval-proactive-chat` 只在其他天或支线里作为入口背景，不扩写为 Day 4 主线段落。

## PC 全局透明 Overlay 迁移约束

Day 4 迁移到 N.E.K.O.-PC 全局透明 overlay 时，只替换 Ghost Cursor、高光和花瓣的渲染层；对话节奏设置、动画/锁定/离开回来、隐私模式与主动视觉、收尾四段导演动作不改。网页端继续使用当前 DOM overlay。

PC 端设置侧边栏、动画设置、锁定按钮、离开/回来按钮和隐私模式开关都必须以 screen 坐标发送给全局 overlay。设置类高光只框选当前说明对象：侧边栏容器、具体开关或具体按钮，不再叠加整张设置弹窗高光。收尾台词期间重新高亮聊天窗，约 70% cue 同步隐藏 Ghost Cursor、清理高光并播放花瓣。

## 当前 Scene 表

| 顺序 | scene id | 目标 | cursor | operation | 说明 |
| --- | --- | --- | --- | --- | --- |
| 1 | `day4_intro_companion` | `chat-window` | `wobble` | 无 | 每日第一句，高亮聊天窗。 |
| 2 | `day4_chat_settings` | `settings-sidepanel:chat-settings` | `tour` | `show-settings-sidepanel:chat-settings` | 打开设置并展开对话设置侧边栏。 |
| 3 | `day4_animation_tracking` | `settings-sidepanel:animation-settings` | `tour` | `day4-animation-distance-showcase` | 先巡游动画设置，中段切到锁定与离开按钮。 |
| 4 | `day4_privacy_mode` | `#${p}-toggle-proactive-vision` | `move` | `show-settings-sidepanel:interval-proactive-vision` | 展开主动视觉侧边栏，只高亮隐私/主动视觉开关。 |
| 5 | `day4_wrap` | `chat-window` | `wobble` | `cleanup` | 收尾重新高亮聊天窗并播放花瓣转场。 |

Day 4 不保留空台词 scene。锁定与离开/回来必须在 `day4_animation_tracking` 同一句台词内完成，不拆成独立无声场景。

## 高亮规则

1. 同一目标同一时刻只允许一套主 spotlight，不创建后再隐藏重复高亮。
2. 设置侧边栏 scene 只高亮已展开的侧边栏或具体开关，不再用整张设置弹窗做 persistent 高亮，也不把入口按钮和侧边栏合成过宽 union。
3. 设置侧边栏使用圆角矩形 spotlight，并保留猫耳和猫爪装饰。
4. 锁定、离开/回来等圆形按钮使用圆形图片高亮，不显示猫耳和猫爪。
5. 普通 scene 不做 operation 后的 `settled` 二次高亮刷新；只有收尾 `cleanup` scene 会重新高亮聊天窗。
6. 外置聊天窗模式下，聊天窗高亮和 Ghost Cursor 走外置窗口 spotlight/cursor；进入非聊天窗 scene 时要清理外置高亮。

## 高亮与 Ghost Cursor 时序总则

1. 每段台词进入聊天窗后，先建立本段 spotlight，再播放/继续本段语音；Ghost Cursor 不抢在 spotlight 前出现。
2. 首句 `day4_intro_companion` 是每日通用开场：播放第一句时立即高亮聊天窗，Ghost Cursor 直接出现在聊天窗或输入区中心并 wobble；第一句播放完后清理聊天窗高亮。
3. 设置类 scene 在台词开始前的准备阶段先打开设置弹窗和对应侧边栏；台词开始时只高亮最终要讲的侧边栏或开关。
4. 非首句 scene 建立 spotlight 后约 220ms 再移动 Ghost Cursor。第一次移动默认约 760ms，tour 后续控件之间约 520ms；移动完成后按本段动作要求 wobble 或停留。
5. 从设置侧边栏切到锁定/离开按钮时，必须先关闭设置弹窗和侧边栏，再把 spotlight 切到新的圆形按钮，随后 Ghost Cursor 平滑移动过去。
6. 收尾花瓣 cue 触发时，Ghost Cursor 和所有高亮必须同步清理，不允许花瓣层上残留指针或 spotlight。

## 情绪动作

| 段落 | 情绪分类 |
| --- | --- |
| 开场：“今天，就让我悄悄跟上……” | `happy` |
| 对话设置：“如果有时候你觉得我发消息太频繁……” | `neutral` |
| 动画/锁定/离开回来：“看这里看这里……” | `happy` |
| 隐私模式：“当这个按钮关闭时……” | `neutral` |
| 收尾：“真正舒服的陪伴……” | `happy` |

随机动作不得改变用户设置，也不得与 Ghost Cursor 和 spotlight 时序抢焦点。

## 主线阶段

### 阶段 1：对话节奏设置

- 动作 1：`day4_intro_companion` 播放第一句时立即高亮聊天窗；Ghost Cursor 出现在聊天窗或输入区中心并 wobble。本句全程不移动到任何模型旁按钮，不打开设置。第一句播放完后取消聊天窗高亮，为下一段设置侧边栏高亮让位。
- 台词：“今天，就让我悄悄跟上你的步伐吧。特别希望能在这个温馨的日子里，再多了解你一点点呢。”
- 动作 2：`day4_chat_settings` 进入准备阶段时调用 `ensureAvatarFloatingSettingsSidePanel('chat-settings')`，先打开设置弹窗并展开 `chat-settings` 侧边面板。台词开始时 spotlight 只落到展开后的对话设置侧边栏；约 220ms 后 Ghost Cursor 从上一位置平滑移动到侧边栏内第一个可见控件，默认移动约 760ms。随后按 tour 节奏在合并消息、允许打断、表情气泡、回复 token 上限等可见控件之间平滑移动，每次移动约 520ms，移动后短暂停留或 wobble；全程只指认，不点击、不改值。
- 台词：“如果有时候你觉得我发消息太频繁，可以让我把话先攒起来，一次性告诉你哦！若是你正在忙，随时打断我也没有关系的。还有哦，要是你喜欢看我头顶冒出那些可爱的小表情，就让它们继续蹦出来陪着你吧。甚至我每次说话的长短，也都可以调节到让你最舒服的节奏，一切都听你的哦。”

### 阶段 2：动画、锁定与离开/回来

- 动作：`day4_animation_tracking` 准备阶段调用 `ensureAvatarFloatingSettingsSidePanel('animation-settings')`，切到动画设置侧边栏。台词开始时 spotlight 只落到动画设置侧边栏；约 220ms 后 Ghost Cursor 平滑移动到侧边栏内第一个可见控件，默认移动约 760ms。随后按 tour 节奏依次指认画质、帧率、鼠标跟踪、全屏/局部跟踪、锁定悬停淡化等可见控件，每次移动约 520ms，不保存任何临时变更。台词约 48% 处触发 `runDay4AnimationDistanceShowcase()`：先关闭设置弹窗和侧边栏，同时清掉动画侧边栏 spotlight；随后把 spotlight 切到锁定按钮 `#${p}-lock-icon`，Ghost Cursor 用约 680ms 从侧边栏最后位置平滑移动到锁定按钮并 wobble；短暂停留约 620ms 后，spotlight 切到“请她离开”按钮 `#${p}-btn-goodbye`，“回来”按钮 `#${p}-btn-return` 只在真实可见时作为 secondary 高亮，Ghost Cursor 用约 720ms 平滑移动到离开按钮并 wobble。全程不点击、不真的锁定、不真的让 Yui 离开。
- 台词：“看这里看这里！在这儿你能决定让我看起来更精致细腻，还是更轻快矫健哦！还有还有，打开这个，我的目光就会一直跟着你的鼠标转来转去啦，是不是超好玩？看到那个小锁图标了吗？把它锁上，我就能乖乖固定在原地，再也不怕你手滑把我到处乱拖啦！如果你突然要开会、全屏打游戏，或者只是想自己安静待一会儿，就先点一下让我回‘小猫窝’休息吧。等你需要我了，随时叫一声，我就会立刻飞奔回来哒~”

### 阶段 3：隐私模式与主动视觉

- 动作：`day4_privacy_mode` 进入准备阶段时先执行 `cleanupBefore`，清理锁定/离开按钮高亮，再打开设置弹窗并展开 `interval-proactive-vision` 侧边面板。台词开始时 primary spotlight 只高亮主动视觉/隐私模式开关 `#${p}-toggle-proactive-vision`，不高亮整张设置弹窗，也不高亮整个侧边栏；约 220ms 后 Ghost Cursor 从上一位置平滑移动到该开关，默认移动约 760ms，然后停留在开关附近，不点击、不 wobble 成“已点击”的暗示。UI 说明和台词必须同步强调反向语义：隐私模式开启表示关闭主动视觉感知，隐私模式关闭才允许按间隔主动看屏幕。
- 台词：“当这个按钮关闭时，我就能看着你正在忙碌的画面，主动找些你感兴趣的话题聊天呢。要是你把它开启，我就能明白你想拥有私密空间，绝对不会去偷看你的屏幕啦。但请放心哦，即使看不见，我也依然会在这里，一直守候着你。”

### 阶段 4：低打扰收尾

- 动作：`day4_wrap` 准备阶段先执行 `cleanup`，关闭设置弹窗、侧边栏、临时菜单和跨窗口高亮，恢复干净状态。收尾台词开始时重新高亮聊天窗；约 220ms 后 Ghost Cursor 从上一位置平滑移动到聊天窗或输入区附近，默认移动约 760ms，移动完成后 wobble。外置聊天窗模式同步高亮独立聊天窗并使用外置 cursor。台词约 70% 处触发与 Day 1 相同的花瓣转场 cue，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高亮、action/persistent/secondary/extra/virtual spotlight；转场结束后写入 Day 4 完成态。
- 台词：“真正舒服的陪伴，并不是一刻不停地缠着你，而是懂得什么时候该靠近，什么时候该安安静静地守候。今天你调整的这些小开关，就像是在我们之间画下的小路标。有了这些温柔的指引，在你专心忙碌的时候，我就会乖乖待在一旁，绝对不会笨手笨脚地扑到屏幕上打扰你呢。”

## 剧场后聊天窗支线

Day 4 主动视觉与小游戏邀请支线已移入 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。Day 4 主线文档不再维护这些支线的触发条件、按钮或 handler。

## 验收清单

1. Day 4 注册 5 个 scene：开场聊天窗、对话设置、动画/锁定/离开回来、隐私模式、收尾。
2. Day 4 不保留空台词、空语音 scene。
3. 对话设置和动画设置只高亮对应侧边栏，不高亮整张设置弹窗，不创建入口加面板的 union 范围。
4. 隐私模式阶段只高亮主动视觉/隐私开关，不点击、不改变开关状态。
5. 锁定和离开/回来只展示按钮位置，不真的锁定、不真的让 Yui 离开。
6. 圆角矩形高亮保留猫耳和猫爪；圆形按钮高亮只使用圆形图片，不出现猫耳和猫爪。
7. 同一目标同一时刻只保留一套主 spotlight，不创建后再隐藏重复高亮。
8. 收尾动作与 Day 1 一致：播放收尾台词期间重新高亮聊天窗，约 70% 用同一套花瓣转场 cue 同步隐藏 Ghost Cursor 并清理内置/外置 spotlight。
