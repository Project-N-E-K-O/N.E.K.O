# Day 6 Agent、任务 HUD 与能力节奏教程文字说明

本文只保留 Day 6 新手教程的体验、台词和行为边界。

Day 6 使用控制感与安全感，让用户明白“悠怡能帮忙”，同时知道每个能力都有状态、权限和终止入口。

键鼠控制、Browser Control、专属桌面、OpenClaw 等能力可以作为 Agent 面板中的真实状态背景，但不扩写成独立主线阶段。

## 当前实现状态

Day 6 的 8 个 round scene 已全部接入共享 Timeline/Command 播放路径，覆盖猫爪开场的聊天窗指认、Agent 面板打开、用户插件侧栏、管理入口预览、插件 dashboard handoff、HUD 展示、HUD 终止权说明、收尾 cleanup、胶囊输入框高光、Ghost Cursor move/hold 和最终花瓣 cue。首句台词开始前由共享 `SceneOrchestrator` 先把 Ghost Cursor 固定到胶囊输入框，直到第一个显式目标移动接管。`day6_agent_status_master` 通过显式 timeline 在旁白启动后立即执行 `day6-plugin-open-agent-panel-flow`，保留旧 operation 内的猫爪高光、cursor click 和 Agent 面板打开时序。`day6_plugin_side_panel` 通过显式 timeline 在旁白启动后立即执行 `day6-plugin-open-management-panel-flow`，用户插件侧栏和管理入口两段 Ghost Cursor move 使用 420ms 且必须传 `exactDuration: true`，避免 GhostCursorController 对短距离移动追加慢放时长；click 使用 320ms，插件 dashboard 窗口等待 900ms 以稳定进入下一幕 handoff，避免页面打开后插件页 Ghost Cursor 演出被跳过。`day6_plugin_dashboard` 通过显式 timeline 在旁白启动后立即执行 `day6-plugin-dashboard-handoff-flow`，跨窗口旁白同步、插件页演出和首页状态恢复仍由旧 handoff operation 承接；当前插件管理页没有接入 PC overlay bridge，插件页 runtime 必须创建自己的可见 pointer 并同步 `cursorPosition`，同时首页/PC 全局 cursor 在 handoff 期间先 hide，插件页关闭后再恢复。`day6_agent_task_hud` 继续复用 `prepareAvatarFloatingScene()` 的 `cleanupBefore` 与 `show-task-hud` 准备逻辑，先清理 Agent 面板再显示真实 HUD。模型替身演出在场景表面准备完成后排程，避免被开场清理、Agent 面板准备或 HUD 准备阶段清掉。

## 目标体验

用户当天只需要形成四个认知：

1. Agent/猫爪入口、状态栏和总开关在哪里。
2. 用户插件可以扩展悠怡能做的事。
3. 任务 HUD 会展示工作进度和终止入口。
4. 用户可以随时叫停，不会失去控制权。

主线不要逐项讲完所有 Agent 能力，不要自动授予权限，不要启用具体插件，不要创建假的后台任务。

## 主线流程

| 顺序 | 主题 | 台词 | 演出描述 |
| --- | --- | --- | --- |
| 1 | Agent 入口 | 噔噔噔噔！今天必须要打起精神，好好跟你聊聊咱们的【猫爪】啦！前两天虽然简单提过一下，但它里面藏着的厉害功能可多着呢。 | 高亮聊天窗，Ghost Cursor 停在聊天区域附近，不提前打开 Agent 面板。 |
| 2 | Agent 总状态 | 快跟我老实交代，这两天你有没有点开它试用一下呀？ | 高亮猫爪入口，Ghost Cursor 移动过去并演示打开 Agent 面板。 |
| 3 | 用户插件入口 | 除了之前介绍的功能，这里还有超多好玩的插件呢。 | 指认用户插件入口，并演示进入插件管理相关区域。只展示入口，不启用插件。 |
| 4 | 插件能力想象 | 有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！ | 继续围绕插件能力做想象说明，强调能力来自用户可管理的扩展。 |
| 5 | 任务 HUD | 看这里看这里！当我决定使用【猫爪】帮你干活的时候，这里就会咕噜咕噜的显示我的工作进度哦。 | 显示或指认任务 HUD。只说明进度展示，不创建假的后台任务。 |
| 6 | 终止权 | 你要是计划有变，随时都可以戳一下让我停下来。嘿嘿，今天也是打起精神努力打工挣小鱼干的一天呢，冲呀！ | 继续高亮任务 HUD，说明用户可以随时终止。Ghost Cursor 只停留在 HUD 附近，不巡游内部按钮。 |
| 7 | 安心收尾 | 呼……把这些繁琐的界面都收起来，这样就不会打扰到你啦。 | 关闭 Agent 面板、插件面板和任务 HUD 等临时界面，Ghost Cursor 回到胶囊输入框。 |
| 8 | 长期陪伴 | 你可以放心地继续做你自己的事情，不管是需要我用小爪子帮你忙，还是只想让我安安静静地陪着你，我都一直在守候着你，今天也要开开心心的呀。 | 继续高亮胶囊输入框，台词后段清理高光和 Ghost Cursor，并播放花瓣效果。 |

## 情绪动作

| 段落 | 情绪 |
| --- | --- |
| Agent 入口 | 开心 |
| Agent 总状态 | 平静 |
| 用户插件入口 | 开心 |
| 插件能力想象 | 开心 |
| 任务 HUD | 开心 |
| 终止权 | 开心 |
| 安心收尾 | 开心 |

随机动作不得干扰 Agent 面板、插件区域或 HUD 的真实状态展示。

## 体验约束

1. Day 6 只讲 Agent 入口与总状态、用户插件、任务 HUD 和收尾。
2. 不自动授权，不自动启用插件。
3. HUD 可以为空状态或教程高亮，但不能创建假任务。
4. 插件管理只做入口和能力想象，不要求用户完成跨页操作。
5. 模型替身图片只作为短暂视觉演出，不能遮挡 Agent 面板、插件入口、任务 HUD、跳过按钮或收尾输入框。
6. 首句台词播放时 Ghost Cursor 必须已经在胶囊输入框中，并保持到猫爪入口移动演出开始。
6. 收尾恢复进入教程前的面板和 HUD 状态。

## 验收要点

1. Day 6 主线包含 Agent 入口与总状态、用户插件、任务 HUD、终止权和安心收尾。
2. 用户能理解悠怡能帮忙，也能理解自己随时拥有终止权。
3. 全程不自动授权、不启用插件、不创建假任务。
4. 收尾关闭临时界面，回到胶囊输入框，并完成花瓣转场。
