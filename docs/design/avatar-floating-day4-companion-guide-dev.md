# Day 4 相处距离、主动陪伴与模型行为教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 4 的主线内容，并以 `avatar-floating-7day-complete-guide-dev.md` 作为逐句导演、生命周期和验收基准。Day 4 的功能剧情按 8 个讲解点落地：开场、对话节奏设置、模型行为、视线跟随、隐私模式与主动视觉、模型锁定、回到小猫窝、收尾。

剧场后“主动视觉邀请 / 小游戏邀请”不属于主线，统一放到独立支线文档。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-7day-complete-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 完整指南对齐基线

Day 4 的相处距离教学按本文 8 个 scene 落地；与完整指南对齐时重点遵守：

1. 首句 `day4_intro_companion` 只高亮聊天窗，不提前打开设置。
2. `day4_chat_settings` 先从设置按钮进入，再切到对话设置按钮与侧边栏；后续设置类 scene 只高亮侧边栏容器或具体开关，不高亮整张设置弹窗。
3. `day4_model_behavior` 和 `day4_gaze_follow` 只负责动画设置侧边栏，后续不再切到锁定按钮、离开/回来按钮。
4. `day4_privacy_mode` 不展开隐私模式侧边栏；台词播放时高亮隐私模式按钮并移动 Ghost Cursor，本句播完后收起设置弹窗，并保持隐私模式反向语义说明。
5. 收尾 `day4_wrap` 先关闭设置弹窗和侧边栏，重新高亮聊天窗，并在约 70% cue 同步清理高光、Ghost Cursor 和外置聊天窗状态。
6. round 开场由 `playAvatarFloatingRound(4)` 统一先执行 `ensureChatVisible()`，并在聊天窗打开后通过 `NekoHomeTutorialFeatureController.enforce()` 再次禁用 proactive/Galgame；首句聊天窗高光只能在这个前置完成后显示。
7. 本日启用完整指南中的 Day 2-7 模型替身图片演出：教程模型可临时隐藏 5 秒，并通过全局透明 overlay 将替身贴屏幕边缘显示；单轮固定触发 2 次，分别在 `day4_gaze_follow` 播放“开启这个功能后……”时显示 `探头.png`，以及在 `day4_return_home` 播放“如果你现在需要专注……”时显示 `扒左边框.png`。替身不得出现在最后一句 `day4_wrap` 播放期间，也不能遮挡设置、隐私、锁定、离开/回来按钮或收尾聊天窗高光。

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

Day 4 迁移到 N.E.K.O.-PC 全局透明 overlay 时，只替换 Ghost Cursor、高光和花瓣的渲染层；对话节奏设置、模型行为、视线跟随、隐私模式与主动视觉、模型锁定、回到小猫窝、收尾的导演动作不改。网页端继续使用当前 DOM overlay。

PC 端设置侧边栏、动画设置、锁定按钮、离开/回来按钮和隐私模式开关都必须以 screen 坐标发送给全局 overlay。设置类高光只框选当前说明对象：侧边栏容器、具体开关或具体按钮，不再叠加整张设置弹窗高光。收尾台词期间重新高亮聊天窗，约 70% cue 同步隐藏 Ghost Cursor、清理高光并播放花瓣。

## 当前 Scene 表

| 顺序 | scene id | 目标 | cursor | operation | 说明 |
| --- | --- | --- | --- | --- | --- |
| 1 | `day4_intro_companion` | `chat-window` | `move` | 无 | 每日第一句，高亮聊天窗。 |
| 2 | `day4_chat_settings` | 设置按钮 + `settings-sidepanel:chat-settings` | `click` + `ellipse` | `open-settings` + `show-settings-sidepanel:chat-settings` | 先高亮并点击设置按钮，再转入对话设置侧边栏。 |
| 3 | `day4_model_behavior` | 动画设置按钮 + `settings-sidepanel:animation-settings` | `move` + `ellipse` | `show-settings-sidepanel:animation-settings` | 先收起对话设置侧边栏并高亮动画设置按钮，再转入动画设置侧边栏。 |
| 4 | `day4_gaze_follow` | `#${p}-mouse-tracking-toggle` 外层开关行 | `move` | 无 | 高亮并指向跟踪鼠标按钮，不点击。 |
| 5 | `day4_privacy_mode` | 隐私模式按钮 / `#${p}-toggle-proactive-vision` 外层开关行 | `move` | `close-settings-panel` | 不展开隐私侧边栏，高亮隐私模式按钮并移动 cursor；本句播完后收起设置弹窗。 |
| 6 | `day4_model_lock` | `#${p}-lock-icon` | `move` | 无 | 圆形高亮模型锁定按钮，cursor 平滑移动过去并 move 并停留。 |
| 7 | `day4_return_home` | `#${p}-btn-goodbye` | `move` | 无 | 展示回到小猫窝按钮，可 secondary 高亮回来按钮。 |
| 8 | `day4_wrap` | `chat-window` | `move` | `cleanup` | 收尾重新高亮聊天窗并播放花瓣转场。 |

Day 4 不保留空台词 scene。8 个 scene 分别对应用户看到的 8 个讲解点：开场、聊天设置、模型行为、视线跟随、主动视觉/隐私模式、模型锁定、回到小猫窝、收尾。

## 高亮规则

1. 同一目标同一时刻只允许一套主 spotlight，不创建后再隐藏重复高亮。
2. 设置侧边栏 scene 只高亮已展开的侧边栏或具体开关，不再用整张设置弹窗做 persistent 高亮，也不把入口按钮和侧边栏合成过宽 union。
3. 设置侧边栏使用圆角矩形 spotlight，并保留猫耳和猫爪装饰。
4. 锁定、离开/回来等圆形按钮使用圆形图片高亮，不显示猫耳和猫爪。
5. 普通 scene 不做 operation 后的 `settled` 二次高亮刷新；只有收尾 `cleanup` scene 会重新高亮聊天窗。
6. 外置聊天窗模式下，聊天窗高亮和 Ghost Cursor 走外置窗口 spotlight/cursor；进入非聊天窗 scene 时要清理外置高亮。

## 高亮与 Ghost Cursor 时序总则

1. 每段台词进入聊天窗后，先建立本段 spotlight，再播放/继续本段语音；Ghost Cursor 不抢在 spotlight 前出现。
2. 首句 `day4_intro_companion` 是每日通用开场：播放第一句时立即高亮聊天窗，Ghost Cursor 直接出现在聊天窗或输入区中心并 move 并停留；第一句播放完后清理聊天窗高亮。
3. 设置类 scene 在台词开始前的准备阶段先打开设置弹窗和对应侧边栏；台词开始时只高亮最终要讲的侧边栏或开关。
4. 非首句 scene 建立 spotlight 后约 220ms 再移动 Ghost Cursor。第一次移动默认约 760ms，tour 后续控件之间约 520ms；移动完成后按本段动作要求 停留。
5. 隐私模式台词结束后必须关闭设置弹窗和侧边栏；后续锁定/离开按钮 scene 再把 spotlight 切到对应圆形按钮，随后 Ghost Cursor 平滑移动过去。
6. 收尾花瓣 cue 触发时，Ghost Cursor 和所有高亮必须同步清理，不允许花瓣层上残留指针或 spotlight。

## 情绪动作

| 段落 | 情绪分类 |
| --- | --- |
| 开场：“今天，就让我悄悄跟上……” | `happy` |
| 对话设置：“在这里可以决定……” | `neutral` |
| 模型行为：“如果你想要看到……” | `happy` |
| 视线跟随：“开启这个功能后……” | `happy` |
| 隐私模式：“这个是控制人家能不能看屏幕……” | `neutral` |
| 模型锁定：“总是小心不触碰到……” | `happy` |
| 回到小猫窝：“如果你现在需要专注……” | `happy` |
| 收尾：“真正舒服的陪伴才不是……” | `happy` |

随机动作不得改变用户设置，也不得与 Ghost Cursor 和 spotlight 时序抢焦点。

## 主线阶段

### 阶段 1：对话节奏设置

- 动作 1：`day4_intro_companion` 播放第一句时立即高亮聊天窗；Ghost Cursor 出现在聊天窗或输入区中心并 move 并停留。本句全程不移动到任何模型旁按钮，不打开设置。第一句播放完后取消聊天窗高亮，为下一段设置侧边栏高亮让位。
- 台词：“今天，就让我悄悄跟上你的步伐吧。特别希望能在这个温馨的日子里，再多了解你一点点呢。”
- 动作 2：`day4_chat_settings` 台词开始时先用圆形 spotlight 高亮设置按钮；约 220ms 后 Ghost Cursor 平滑移动到设置按钮并播放模拟点击，同时并行调用打开设置 API。设置按钮高光作为 persistent 保持到 `day4_privacy_mode` 台词播放完毕。设置弹窗打开后，spotlight 切到“对话设置”按钮的圆角矩形高亮，Ghost Cursor 平滑移动到该按钮，并调用 `ensureAvatarFloatingSettingsSidePanel('chat-settings')` 展开对话设置侧边栏。随后取消“对话设置”按钮主高亮，把圆角矩形主高亮切到对话设置侧边栏；Ghost Cursor 在侧边栏范围内做椭圆运动直至本句台词播放完毕。全程只指认，不改值。
- 台词：“在这里可以决定我回复你的长短，还能决定要不要让我带上可爱的表情，或者在人家唠叨的时候打断我哦！都可以调到让你最舒服的节奏”

### 阶段 2：模型行为设置

- 动作：`day4_model_behavior` 台词开始时先收起上一段的对话设置侧边栏，设置按钮 persistent 高光继续保留；主 spotlight 从对话设置侧边栏平滑过渡到“动画设置”按钮的圆角矩形高光。约 220ms 后 Ghost Cursor 平滑移动到“动画设置”按钮，随后调用 `ensureAvatarFloatingSettingsSidePanel('animation-settings')` 展开动画设置侧边栏。侧边栏出现后，主 spotlight 从“动画设置”按钮平滑过渡到动画设置侧边栏；Ghost Cursor 在侧边栏范围内做椭圆运动直至本句台词播放完毕。全程只指认，不保存任何临时变更。
- 台词：“如果你想要看到更精致、细节更满满的我，或者想要更丝滑、更流畅的动作体验，都可以在这里进行调整哦！不管哪一种，我都会展现出最可爱的一面哒~”

### 阶段 3：视线跟随

- 动作：`day4_gaze_follow` 继续使用已展开的 `animation-settings` 侧边栏，设置按钮 persistent 高光继续保留。台词开始时主 spotlight 从动画设置侧边栏平滑移动到“跟踪鼠标”按钮外层开关行；约 220ms 后 Ghost Cursor 平滑移动到该按钮。全程不点击、不改变开关状态。
- 台词：“开启这个功能后，无论你的鼠标移动到哪里，人家的目光都会紧紧跟随着你哟！是不是有种被时刻关注的幸福感呢？”

### 阶段 4：隐私模式与主动视觉

- 动作：`day4_privacy_mode` 台词开始时先清理动画设置侧边栏和隐私模式侧边栏，不展开 `interval-proactive-vision`，也不显示隐私模式旁边的侧边框；设置按钮 persistent 高光继续保留到本句播放结束。primary spotlight 改为高亮“隐私模式”按钮或 `#${p}-toggle-proactive-vision` 外层开关行；约 220ms 后 Ghost Cursor 从上一位置平滑移动到该按钮，默认移动约 620ms。本句台词播放完毕后调用 `closeSettingsPanel()` 收起设置弹窗。全程不点击、不改变隐私/主动视觉开关状态。UI 说明和台词必须同步强调反向语义：隐私模式开启表示关闭主动视觉感知，隐私模式关闭才允许按间隔主动看屏幕。
- 台词：“这个是控制人家能不能看屏幕的‘终极防护开关’喵！把它关闭人家就能看到你的屏幕啦，要是开启它，前两天介绍的【屏幕分享】就统统失效、人家就绝对不会偷看哟~”

### 阶段 5：模型锁定

- 动作：`day4_model_lock` 进入准备阶段时先执行 `cleanupBefore`，确保设置弹窗、侧边栏和设置按钮 persistent 高光已经清理；台词开始时圆形高亮锁定按钮 `#${p}-lock-icon`，约 220ms 后 Ghost Cursor 平滑移动到锁定按钮并 move 并停留。全程不点击、不真的锁定。
- 台词：“总是小心不触碰到、把我点歪吗？那就快把我牢牢固定在当前的位置吧！开启锁定后，我就哪儿也不去，乖乖在原地陪着你~”

### 阶段 6：回到小猫窝

- 动作：`day4_return_home` 台词开始时 primary spotlight 高亮“请她离开”按钮 `#${p}-btn-goodbye`，“回来”按钮 `#${p}-btn-return` 只在真实可见时作为 secondary 高亮。Ghost Cursor 平滑移动到离开按钮并 move 并停留，再移动到回来按钮并 move 并停留；全程不点击、不真的让 Yui 离开。
- 台词：“如果你现在需要专注、担心我打扰的话，可以让我暂时回到小猫窝里收起来哦！等你想我的时候，随时一键就能把我重新唤回身边，喵呜~”

### 阶段 7：低打扰收尾

- 动作：`day4_wrap` 准备阶段先执行 `cleanup`，关闭设置弹窗、侧边栏、临时菜单和跨窗口高亮，恢复干净状态。收尾台词开始时重新高亮聊天窗；约 220ms 后 Ghost Cursor 从上一位置平滑移动到聊天窗或输入区附近，默认移动约 760ms，移动完成后 move 并停留。外置聊天窗模式同步高亮独立聊天窗并使用外置 cursor。台词约 70% 处触发与 Day 1 相同的花瓣转场 cue，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高亮、action/persistent/secondary/extra/virtual spotlight；转场结束后写入 Day 4 完成态。
- 台词：“真正舒服的陪伴才不是一刻不停地粘着主人呢~ 而是懂得什么时候该悄悄靠近抓抓你的衣角撒个娇，什么时候该安安静静地趴在一旁，用目光默默守候着主人喵~”

## 剧场后聊天窗支线

Day 4 主动视觉与小游戏邀请支线已移入 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。Day 4 主线文档不再维护这些支线的触发条件、按钮或 handler。

## 验收清单

1. Day 4 注册 8 个 scene：开场聊天窗、对话设置、模型行为、视线跟随、隐私模式、模型锁定、回到小猫窝、收尾。
2. Day 4 不保留空台词、空语音 scene。
3. 对话设置先以设置按钮圆形高光进入，再切换到对话设置按钮与侧边栏；动画设置只高亮对应侧边栏，不高亮整张设置弹窗，不创建入口加面板的 union 范围。
4. 隐私模式阶段不展开隐私侧边栏，高亮隐私模式按钮并移动 cursor；本句播完后收起设置弹窗，不点击、不改变任何开关状态。
5. 锁定和离开/回来拆成独立有声 scene；模型锁定句才圆形高亮锁定按钮并移动 cursor，不真的锁定、不真的让 Yui 离开。
6. 圆角矩形高亮保留猫耳和猫爪；圆形按钮高亮只使用圆形图片，不出现猫耳和猫爪。
7. 同一目标同一时刻只保留一套主 spotlight，不创建后再隐藏重复高亮。
8. 收尾动作与 Day 1 一致：播放收尾台词期间重新高亮聊天窗，约 70% 用同一套花瓣转场 cue 同步隐藏 Ghost Cursor 并清理内置/外置 spotlight。
9. Day 4 单轮模型替身图片固定出现 2 次：`day4_gaze_follow` 使用 `探头.png`，`day4_return_home` 使用 `扒左边框.png`；每次 5 秒后恢复模型。进入 `day4_wrap` 前如果替身仍在显示，必须立即清理替身并恢复模型。替身必须由全局透明 overlay 与高光、Ghost Cursor、花瓣一起携带完整可见状态；替身演出期间不得改变设置弹窗、侧边栏、隐私模式、锁定或离开/回来按钮的真实状态。
