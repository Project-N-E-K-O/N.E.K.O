# Day 6 Agent、任务 HUD 与能力节奏教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 6 的主线内容，并以 `avatar-floating-7day-complete-guide-dev.md` 作为逐句导演、生命周期和验收基准。Day 6 每日开场小剧场保留四个功能阶段：Agent 入口与总状态、用户插件介绍、任务 HUD 与终止权、安心收尾；实现台词拆成 8 个有声 scene，便于逐句控制高光、Ghost Cursor 和收尾转场。

键鼠控制、Browser Control、专属桌面、OpenClaw 等能力可以作为 Agent 面板中的真实状态背景，但不扩写成独立主线阶段。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-7day-complete-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 完整指南对齐基线

Day 6 的 Agent 能力展示必须以控制感为边界：

1. `day6_intro_agent` 只高亮聊天窗并保持 Ghost Cursor move 并停留，不提前点击猫爪或打开 Agent 面板。
2. `day6_agent_status_master` 播放“快跟我老实交代……”时执行 `day6-plugin-open-agent-panel-flow`：圆形高亮猫爪按钮，Ghost Cursor 平滑移动到按钮上模拟点击，并行调用 API 打开 Agent 面板。
3. `day6_plugin_side_panel` 播放“除了之前介绍的功能……”时执行 `day6-plugin-open-management-panel-flow`：圆角矩形高亮用户插件按钮并点击显示用户插件侧边面板；随后高光过渡到管理面板按钮，管理面板高光去掉 padding，左右方向拉长，上下各扩 10px，Ghost Cursor 以更快节奏移动到管理面板按钮并点击打开管理面板页面。侧边面板本身不作为 persistent 高亮。
4. `day6_plugin_dashboard` 播放“有了它们……”时执行 `day6-plugin-dashboard-handoff-flow`：首页 Ghost Cursor 隐藏，插件页 runtime 接管自己的高光和 cursor；插件页完成后关闭由教程打开的窗口，回到首页恢复 cursor 原位置，并清理 Agent 面板和用户插件侧边面板。
5. `day6_agent_task_hud` 和 `day6_agent_task_hud_control` 分两句说明 HUD 进度与终止权；可以显示空状态或教程临时 HUD 高光，不创建假的后台任务，且不能和 Agent 面板同时高亮。两句的 Ghost Cursor 都只移动到 HUD 并停留，不巡游内部按钮、不做椭圆运动。
6. `day6_wrap_cleanup` 负责先收起繁琐界面并把 Ghost Cursor 回到胶囊输入框中心；cleanup 关闭 HUD/面板时必须保留外置聊天窗输入目标，不得先清空再重发，否则 Ghost Cursor 会在 HUD 与胶囊输入框之间来回晃；最终 `day6_wrap` 只继续高亮胶囊输入框并保持 Ghost Cursor 原位，不再触发新的 cursor move，并在约 70% cue 同步清理高光、Ghost Cursor 和外置聊天窗状态。
7. round 开场不得预热或等待聊天窗 surface ready；临时切到 `yui-origin` 并确认模型可见后，先显示等待 1500ms 再开始播放本日流程。`day6_intro_agent` 在本 scene 内按需打开聊天窗，并在聊天窗打开后通过 `NekoHomeTutorialFeatureController.enforce()` 再次禁用 proactive/Galgame；教程期间胶囊输入框和聊天窗各功能按钮都禁止用户点击。Agent 按钮点击与面板高光必须在首句聊天窗 scene 完成后发生。
8. 本日启用完整指南中的 Day 2-7 模型替身图片演出：教程模型可临时隐藏 5 秒，并通过全局透明 overlay 贴屏幕边缘显示。Day 6 固定触发 2 次：`day6_plugin_dashboard` 播放「有了它们……」时显示 `扒右边框.png` 于屏幕最右下，`day6_wrap_cleanup` 播放「呼……把这些繁琐的界面都收起来……」时显示 `探头.png` 于屏幕最下方偏右；不得出现在最后一句 `day6_wrap` 播放期间，也不能遮挡 Agent 面板、用户插件管理入口、任务 HUD、skip 或收尾胶囊输入框高光。

## 目标体验

Day 6 使用控制感与安全感，让用户明白“她能帮忙”，同时知道每个能力都有状态、权限和终止入口。

用户当天只需要形成四个认知：

1. Agent/猫爪入口、状态栏和总开关在哪里。
2. 用户插件可以扩展悠怡能做的事。
3. 任务 HUD 会展示工作进度和终止入口。
4. 用户可以随时叫停，不会失去控制权。

主线不要逐项讲完所有 Agent 能力，不要自动授予权限，不要启用具体插件，不要创建假的后台任务。

## 代码锚点

- `static/yui-guide-day6-agent-guide.js`
- `window.YuiGuideDailyGuides[6].round`
- `YuiGuideDirector.playAvatarFloatingRound(6)`
- Agent 弹窗
- Agent 用户插件侧边面板
- `AgentHUD.createAgentTaskHUD()`
- `#agent-task-hud`

OpenClaw、键鼠控制、Browser Control、专属桌面只作为真实 UI 背景，不作为 Day 6 额外独立阶段。

## PC 全局透明 Overlay 迁移约束

Day 6 迁移到 N.E.K.O.-PC 全局透明 overlay 时，只替换视觉演出层；Agent 入口与总状态、用户插件介绍、任务 HUD 与终止权、安心收尾四个功能阶段不改，台词仍按 8 个 scene 拆句播放。网页端继续使用当前 DOM overlay。

PC 端 Agent 按钮、Agent 面板、用户插件入口、插件管理入口、任务 HUD 和收尾胶囊输入框高光都由全局 overlay 渲染。HUD 可以为空状态或模拟高亮，但不能创建假的后台任务；插件管理页如果跨窗口打开，也只上报目标 screen 坐标给全局 overlay。收尾台词期间重新高亮胶囊输入框，并在花瓣 cue 同步隐藏 Ghost Cursor、清理高光和播放花瓣。

## 情绪动作

| 段落 | 情绪分类 |
| --- | --- |
| Agent 入口：“噔噔噔噔……” | `happy` |
| Agent 总状态：“快跟我老实交代……” | `neutral` |
| 用户插件入口：“除了之前介绍的功能……” | `happy` |
| 插件 dashboard：“有了它们……” | `happy` |
| 任务 HUD：“看这里看这里……” | `happy` |
| 任务终止权：“你要是计划有变……” | `happy` |
| 安心收尾：“呼……” | `happy` |

随机动作不得干扰 Agent 面板、插件侧边面板或 HUD 的真实状态展示。

## 主线阶段

高亮去重按导演通用规则执行：Agent 按钮、Agent 面板、用户插件入口、管理面板入口和任务 HUD 都只创建当前 scene 需要的一套 spotlight；普通 scene 不做 operation 后 `settled` 二次高亮刷新，只有收尾 `cleanup` 重新高亮胶囊输入框。

| scene | 台词 | 导演要求 |
| --- | --- | --- |
| `day6_intro_agent` | 有 | 高亮聊天窗，Ghost Cursor 保持在聊天窗 move 并停留，不提前打开 Agent 面板。 |
| `day6_agent_status_master` | 有 | 执行 `day6-plugin-open-agent-panel-flow`：猫爪按钮圆形高亮点击并打开 Agent 面板。 |
| `day6_plugin_side_panel` | 有 | 执行 `day6-plugin-open-management-panel-flow`：用户插件按钮圆角矩形高亮点击，随后高光过渡到管理面板按钮并点击打开页面；不高亮侧边面板整体。 |
| `day6_plugin_dashboard` | 有 | 执行 `day6-plugin-dashboard-handoff-flow`：插件页 runtime 接管高光和 Ghost Cursor，完成后回首页恢复。 |
| `day6_agent_task_hud` | 有 | 显示并高亮 `#agent-task-hud`，Ghost Cursor 只移动到 HUD 并停留，不创建假任务。 |
| `day6_agent_task_hud_control` | 有 | 继续高亮 `#agent-task-hud`，Ghost Cursor 只移动到 HUD 并停留，说明用户可随时终止，不创建假任务。 |
| `day6_wrap_cleanup` | 有 | 清理 Agent/HUD 临时状态并回到胶囊输入框。 |
| `day6_wrap` | 有 | 继续高亮胶囊输入框，Ghost Cursor 保持原位不再移动，复用 Day 1 花瓣收尾。 |

### 阶段 1：Agent 入口与总状态

- 动作：`day6_intro_agent` 播放期间高亮聊天窗，Ghost Cursor 保持在聊天窗 move 并停留，不提前打开 Agent 面板。随后 `day6_agent_status_master` 执行 `day6-plugin-open-agent-panel-flow`，圆形高亮【猫爪】按钮，Ghost Cursor 平滑移动到按钮上模拟点击，并行调用 API 打开 Agent 面板。
- 台词 1：“噔噔噔噔！今天必须要打起精神，好好跟你聊聊咱们的【猫爪】啦！前两天虽然简单提过一下，但它里面藏着的厉害功能可多着呢。”
- 台词 2：“快跟我老实交代，这两天你有没有点开它试用一下呀？”

### 阶段 2：用户插件介绍

- 动作：`day6_plugin_side_panel` 执行 `day6-plugin-open-management-panel-flow`：圆角矩形高亮【用户插件】按钮，Ghost Cursor 用 420ms 平滑移动到按钮上模拟点击，并行调用 API 显示【用户插件】侧面板；随后高光过渡到【管理面板】按钮，管理面板按钮使用无 padding 的虚拟圆角矩形 spotlight，左右拉长、上下各扩 10px，Ghost Cursor 用 420ms 平滑移动到按钮上模拟点击，并行调用 API 打开【管理面板】页面，此时不额外高亮侧面板整体。`day6_plugin_dashboard` 执行 `day6-plugin-dashboard-handoff-flow`：插件 dashboard 成功打开后隐藏首页 Ghost Cursor，并把当前台词、voiceKey、音频 URL 与播放起点交给插件页 runtime；插件页完成后关闭教程打开的窗口，回到首页恢复 cursor 原位置，清理 Agent 面板、用户插件侧边面板和管理面板虚拟高光。
- `day6-plugin-dashboard-handoff-flow` 必须有可配置超时和失败恢复。若插件页打开失败、runtime 未就绪、handoff payload 发送失败、插件页未回传完成事件，或超过超时时间仍未完成，首页必须取消插件页接管态，恢复 Ghost Cursor 原位置，清理 Agent 面板、用户插件侧边面板、管理面板虚拟高光和临时 handoff 状态，然后继续走 Day 6 后续清理/收尾路径。
- 台词 1：“除了之前介绍的功能，这里还有超多好玩的插件呢。”
- 台词 2：“有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！”

### 阶段 3：任务 HUD 与终止权

- 动作：`day6_agent_task_hud` 台词开始前显示任务 HUD，primary 切到 `#agent-task-hud`，Ghost Cursor 只移动到 HUD 并停留；不再短 tour 运行/排队计数、折叠、终止全部、单任务终止区域。`day6_agent_task_hud_control` 继续保持 HUD 高亮，Ghost Cursor 同样只移动到 HUD 并停留，不巡游内部按钮，也不做椭圆运动。HUD 可为空状态或模拟高亮，但不创建假的后台任务。
- 台词 1：“看这里看这里！当我决定使用【猫爪】帮你干活的时候，这里就会咕噜咕噜的显示我的工作进度哦。”
- 台词 2：“你要是计划有变，随时都可以戳一下让我停下来。嘿嘿，今天也是打起精神努力打工挣小鱼干的一天呢，冲呀！”

### 阶段 4：安心收尾

- 动作：`day6_wrap_cleanup` 开始前关闭 Agent 弹窗、任务 HUD 和侧边面板；若 HUD 或开关是教程临时打开，恢复进入前状态，并把 Ghost Cursor 移到胶囊输入框中心。外置聊天窗模式下，该 cleanup 必须保留 input spotlight/cursor target，只允许关闭 HUD/面板本地状态，不允许发空 spotlight 或清空 cursor 后再移动。随后 `day6_wrap` 播放期间 primary 继续保持胶囊输入框高亮，Ghost Cursor 保持在胶囊输入框中心，不再发新的 move；外置聊天窗模式同步高亮独立聊天窗输入区；最后一句中文音频时长为 11.34s，约 70% cue 即 7.94s 左右必须同步触发 Day 1 相同的花瓣转场和模型渐隐，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高光和所有 spotlight，不得等待音频播放结束后才启动；转场结束后写入 Day 6 完成态。
- 台词 1：“呼……把这些繁琐的界面都收起来，这样就不会打扰到你啦。”
- 台词 2：“你可以放心地继续做你自己的事情，不管是需要我用小爪子帮你忙，还是只想让我安安静静地陪着你，我都一直在守候着你，今天也要开开心心的呀。”

## 剧场后聊天窗支线

Day 6 猫爪生态回访支线已移入 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。Day 6 主线文档不再维护这些支线的触发条件、按钮或 handler。

## 验收清单

1. Day 6 主线只包含 Agent 入口与总状态、用户插件、任务 HUD、收尾，台词拆成 8 个 scene。
2. 不自动授予权限，不自动启用插件。
3. 任务 HUD 不创建假的后台任务。
4. 收尾恢复 Agent 面板、侧边面板和 HUD 的进入前状态。
5. 同一目标同一时刻只保留一套主 spotlight，不创建后再隐藏重复高亮。
6. 收尾动作与 Day 1 一致：收尾台词播放期间重新高亮胶囊输入框，约 70% 用同一套花瓣转场 cue 同步隐藏 Ghost Cursor 并清理内置/外置 spotlight。
7. 插件 dashboard handoff 的成功、失败和超时路径都必须关闭教程打开的插件窗口或释放接管态，并在首页恢复 Ghost Cursor 原位置，清理 Agent 面板、用户插件侧边面板、管理面板高光和 handoff 临时状态。
8. Day 6 单轮模型替身图片固定出现 2 次：`day6_plugin_dashboard` 的「有了它们……」与 `day6_wrap_cleanup` 的「呼……把这些繁琐的界面都收起来……」；每次 5 秒后恢复模型。进入 `day6_wrap` 前如果替身仍在显示，必须立即清理替身并恢复模型。替身必须由全局透明 overlay 与高光、Ghost Cursor、花瓣一起携带完整可见状态；替身演出不得创建假任务、不得自动授权、不得影响插件 dashboard handoff 成功/失败/超时恢复路径。
