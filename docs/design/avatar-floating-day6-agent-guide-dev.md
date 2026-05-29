# Day 6 Agent、任务 HUD 与能力节奏教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 6 的主线内容。Day 6 每日开场小剧场只包含四段：Agent 入口与总状态、用户插件介绍、任务 HUD 与终止权、安心收尾。

键鼠控制、Browser Control、专属桌面、OpenClaw 等能力可以作为 Agent 面板中的真实状态背景，但不扩写成独立主线阶段。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

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

Day 6 迁移到 N.E.K.O.-PC 全局透明 overlay 时，只替换视觉演出层；Agent 入口与总状态、用户插件介绍、任务 HUD 与终止权、安心收尾四段主线不改。网页端继续使用当前 DOM overlay。

PC 端 Agent 按钮、Agent 面板、用户插件入口、插件管理入口、任务 HUD 和收尾聊天窗高光都由全局 overlay 渲染。HUD 可以为空状态或模拟高亮，但不能创建假的后台任务；插件管理页如果跨窗口打开，也只上报目标 screen 坐标给全局 overlay。收尾台词期间重新高亮聊天窗，并在花瓣 cue 同步隐藏 Ghost Cursor、清理高光和播放花瓣。

## 情绪动作

| 段落 | 情绪分类 |
| --- | --- |
| Agent 入口：“噔噔噔噔……” | `happy` |
| 用户插件：“除了之前介绍的功能……” | `happy` |
| 插件能力：“有了它们……” | `happy` |
| 任务 HUD：“看这里看这里……” | `happy` |
| 安心收尾：“呼……” | `happy` |

随机动作不得干扰 Agent 面板、插件侧边面板或 HUD 的真实状态展示。

## 主线阶段

高亮去重按导演通用规则执行：Agent 按钮、Agent 面板、用户插件入口、管理面板入口和任务 HUD 都只创建当前 scene 需要的一套 spotlight；普通 scene 不做 operation 后 `settled` 二次高亮刷新，只有收尾 `cleanup` 重新高亮聊天窗。

### 阶段 1：Agent 入口与总状态

- 动作：台词开始时高亮 Agent/猫爪按钮；约 220ms 后 Ghost Cursor 移到按钮并 click，真实打开 Agent 弹窗。弹窗出现后 persistent 放到 Agent 面板，primary 切到状态栏和总开关；Ghost Cursor 移到总开关区域停留，不自动授予权限。
- 台词：“噔噔噔噔！今天必须要打起精神，好好跟你聊聊咱们的【猫爪】啦！前两天虽然简单提过一下，但它里面藏着的厉害功能可多着呢。快跟我老实交代，这两天你有没有点开它试用一下呀？”

### 阶段 2：用户插件介绍

- 动作：保持 Agent 面板高亮。Ghost Cursor 移到用户插件开关，再移到“管理面板”入口；主路径真实打开用户插件管理面板，失败时保留入口高亮等待手动打开；不自动启用具体插件。
- 台词：“除了之前介绍的功能，这里还有超多好玩的插件呢！”
- 台词：“有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！”

### 阶段 3：任务 HUD 与终止权

- 动作：台词开始前显示任务 HUD；primary 切到 `#agent-task-hud`，Ghost Cursor 移到 HUD 后短 tour 运行/排队计数、折叠、终止全部、单任务终止区域。HUD 可为空状态或模拟高亮，但不创建假的后台任务。
- 台词：“看这里看这里！当我决定使用【猫爪】帮你干活的时候，这里就会咕噜咕噜的显示我的工作进度哦。你要是计划有变，随时都可以戳一下让我停下来。嘿嘿，今天也是打起精神努力打工挣小鱼干的一天呢，冲呀！”

### 阶段 4：安心收尾

- 动作：收尾台词开始前关闭 Agent 弹窗、任务 HUD 和侧边面板；若 HUD 或开关是教程临时打开，恢复进入前状态。随后完全复用 Day 1 `takeover_return_control` 的收尾动作：收尾台词播放期间 primary 重新回到聊天窗，Ghost Cursor 移到聊天窗并 wobble；外置聊天窗模式同步高亮独立聊天窗；台词约 70% 处触发与 Day 1 相同的花瓣转场 cue，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高亮和所有 spotlight；转场结束后写入 Day 6 完成态。
- 台词：“呼……把这些繁琐的界面都收起来，这样就不会打扰到你啦。你可以放心地继续做你自己的事情，不管是需要我用小爪子帮你忙，还是只想让我安安静静地陪着你，我都一直在守候着你，今天也要开开心心的呀。”

## 剧场后聊天窗支线

Day 6 猫爪生态回访支线已移入 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。Day 6 主线文档不再维护这些支线的触发条件、按钮或 handler。

## 验收清单

1. Day 6 主线只包含 Agent 入口与总状态、用户插件、任务 HUD、收尾。
2. 不自动授予权限，不自动启用插件。
3. 任务 HUD 不创建假的后台任务。
4. 收尾恢复 Agent 面板、侧边面板和 HUD 的进入前状态。
5. 同一目标同一时刻只保留一套主 spotlight，不创建后再隐藏重复高亮。
6. 收尾动作与 Day 1 一致：收尾台词播放期间重新高亮聊天窗，约 70% 用同一套花瓣转场 cue 同步隐藏 Ghost Cursor 并清理内置/外置 spotlight。
