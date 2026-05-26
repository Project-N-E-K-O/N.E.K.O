# Day 6 Agent、任务 HUD 与能力节奏教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 6 的“Agent、任务 HUD 与能力节奏”落到现有 Agent 弹窗、任务 HUD 和插件侧边面板上。

注意：旧版 Day 3 开发文档曾承载 Agent 主题；新版总稿已把 Agent 能力后移到 Day 6。当前代码已新增 `AVATAR_FLOATING_GUIDE_ROUNDS[6]`，旧 Day 3 Agent 主题不再作为 Day 3 自动演出。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/avatar-floating-day3-agent-guide-dev.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/architecture/task-hud-system.md`

## 目标体验

Day 6 使用控制感与安全感，让用户明白“她能帮忙”，同时知道每个能力都有状态、权限和终止入口。用户需要学会：

1. 猫爪入口、状态栏和总开关在哪里。
2. 键鼠控制、Browser Control、专属桌面、用户插件和 OpenClaw 都是授权后的能力。
3. 用户插件可以扩展悠怡能做的事。
4. 任务 HUD 会展示工作进度、运行/排队计数和终止入口。
5. 用户可以随时叫停，不会失去控制权。

## 当前实现边界

Day 6 已复用旧 Agent round 的真实 UI 覆盖能力，并调整为第六天主线：

- 不再让 Day 3 自动讲 Agent。
- Day 6 才启动 Agent 主题。
- `AVATAR_FLOATING_GUIDE_ROUNDS[6]` 是正式强接管 round。
- 任务 HUD 可展示空状态或模拟高亮，但不要创建假的后台任务。

## 现有代码入口

启动链路候选：

```text
UniversalTutorialManager.startAvatarFloatingGuideRound(6)
└─ YuiGuideDirector.playAvatarFloatingRound(6)
```

已接入能力：

```text
AVATAR_FLOATING_GUIDE_ROUNDS[6]
open-agent
agent-capabilities
show-task-hud
show-agent-sidepanel:user-plugin:management-panel
```

Agent 弹窗：

- `#${p}-popup-agent`
- `agent-master`
- `agent-keyboard`
- `agent-browser`
- `agent-openfang`
- `agent-user-plugin`
- `agent-openclaw`

任务 HUD：

- `AgentHUD.createAgentTaskHUD()`
- `AgentHUD.showAgentTaskHUD()`
- `AgentHUD.hideAgentTaskHUD()`
- `#agent-task-hud`

## 通用生命周期复用

Day 6 是正式强接管 round，且包含 Agent 面板、插件管理 handoff 和任务 HUD，必须完整复用通用生命周期模块。维护时不要把旧生命周期逻辑复制到 scene 内，只保留 scene 业务目标和文案。

| 通用能力 | Day 6 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | round 启动后进入 taking-over；Agent 按钮、Agent 面板、用户插件管理入口、任务 HUD、skip 按钮按白名单放行。 | 不在 Agent scene 内重新注册 document 级鼠标守卫。 |
| `TutorialHighlightController` | Agent 按钮圆形 spotlight、总开关、能力项 union、用户插件侧边栏、管理入口 virtual spotlight、HUD spotlight 都走统一 controller。 | 不直接创建 HUD 高亮 DOM，不残留 retained/scene extra spotlight。 |
| `TutorialInterruptController` | 用户抢鼠标时暂停当前 Agent/HUD scene；angry exit 触发瞬间关闭本地高亮和 Ghost Cursor，语音后统一 skip。 | 不把插件管理面板 done 当成 angry exit 后的完成。 |
| `TutorialSkipController` | Manager 显示 skip；插件 dashboard skip/angry exit 必须转发回首页 Manager 统一入口。 | 不在插件 dashboard 或 Agent HUD 内拼独立 skip teardown。 |
| `TutorialAvatarReloadController` | Day 6 使用教程模型时，仍由 Manager 管临时切模、聊天头像覆盖和恢复。 | 不在 Agent 能力启用/关闭时直接 reload 模型。 |

插件管理页如果作为独立 Vue runtime 打开，继续遵守生命周期文档的专属 runtime 规则：本地 spotlight/Ghost Cursor 在 skip、destroy、angry exit 触发瞬间清理；插件页 skip 控件防点击穿透；结果回传统一 skip 入口，不能伪造 done。

## 模型动作与情绪随机池

Day 6 是 Agent 强接管 round，演出时使用临时 `yui-origin` Live2D。普通台词从内置动作池随机播放：`happy` 12 个、`sad` 6 个、`angry` 7 个、`neutral` 7 个、`surprised` 5 个、`Idle` 3 个。

任务 HUD、Agent 面板和插件 dashboard handoff 是 UI 演出；随机动作不得抢插件页 runtime 的本地 spotlight/Ghost Cursor，也不得干扰 HUD 恢复。若插件 dashboard 页面内有自己的动作 runtime，以页面 runtime 为准，首页随机动作暂停或降级。

| 台词段落 | 情绪分类 | 随机动作规则 |
| --- | --- | --- |
| Agent 入口：“噔噔噔噔……” | `happy` | 从 happy 池随机，表现兴奋介绍工具箱。 |
| 能力边界：“有的小爪子适合点点写写……” | `surprised` | 从 surprised 池随机，配合 Agent 能力项 tour。 |
| 用户插件：“除了之前介绍的功能……” | `happy` | 从 happy 池随机，表现自信炫耀。 |
| 插件能力：“有了它们……” | `happy` | 从 happy 池随机。 |
| 任务 HUD：“看这里看这里……” | `happy` | 从 happy 池随机；HUD tour 优先。 |
| 安心收尾：“呼……” | `happy` | 从 happy 池随机，轻松安心收束。 |
| 1 小时未使用支线 | `sad` | 从 sad 池随机。 |
| 1 小时已使用支线 | `happy` | 从 happy 池随机。 |

## 剧本阶段与实现建议

| 新剧本阶段 | 建议实现方式 | 处理建议 |
| --- | --- | --- |
| Agent 入口与总状态 | Agent 弹窗 scene | 高亮并打开 Agent 按钮；展示状态栏和总开关。 |
| 能力边界 | Agent 能力项 union spotlight | Ghost Cursor 在键鼠控制、浏览器控制、专属桌面、用户插件和 OpenClaw 等能力项之间 tour，不修改真实开关。 |
| 用户插件介绍 | Agent 用户插件侧边面板 | 打开猫爪总开关，高亮用户插件，打开用户插件管理面板或降级为入口高亮。 |
| 任务 HUD 与终止权 | HUD 空状态/模拟高亮 | 展示运行/排队计数、折叠、终止全部、单任务终止区域。 |
| 安心收尾 | 清理 scene | 清理 Agent 弹窗、任务 HUD 和侧边面板。 |
| 猫爪生态支线 | 1 小时聊天窗回访 | 根据 1 小时内是否使用过猫爪分支。 |

## 动作时序

Day 6 的主线已接入 `AVATAR_FLOATING_GUIDE_ROUNDS[6]`，沿用 `playAvatarFloatingScene()` 的时序：台词和 spotlight 同时出现；约 220ms 后 Ghost Cursor 移动/点击；真实 operation 完成后刷新高亮；旁白结束后进入下一段。

| 台词段落 | 高亮时序 | Ghost Cursor 时序 | 真实操作/清理 |
| --- | --- | --- | --- |
| Agent 入口：“噔噔噔噔！今天必须要打起精神……” | primary 为 `#${p}-btn-agent`；persistent 预期为 `#${p}-popup-agent`。 | Cursor 移到 Agent/猫爪按钮并 click。 | `open-agent` 真实打开 Agent 弹窗；弹窗打开后刷新高亮。 |
| 总状态：“看这里的小灯和总开关……” | persistent 保持 Agent 弹窗；primary 切到 `agent-master` 或 `#${p}-toggle-agent-master`。 | Cursor 移到总开关区域；只 move，除非演示策略明确需要 click。 | 不自动授予权限；如果临时打开总开关，必须保存并恢复原状态。 |
| 用户插件：“除了之前介绍的功能，这里还有超多好玩的插件呢！” | 先打开 Agent 用户插件侧边面板；primary 为 `#${p}-toggle-agent-user-plugin`；secondary 为 `#neko-sidepanel-action-agent-user-plugin-management-panel`。 | Cursor 移到用户插件开关；再移到管理面板入口。若 `activateSecondaryAction` 为 true，显示 click 动效。 | 主路径真实打开用户插件管理面板；失败时保留入口高亮并允许手动打开；不自动启用具体插件。 |
| 任务 HUD：“看这里看这里！当我决定使用【猫爪】帮你干活的时候……” | scene 带 `show-task-hud` 时先显示 HUD；primary 为 `#agent-task-hud`。 | Cursor 移到 HUD；如 HUD 内终止/折叠按钮可见，可短 tour 到运行/排队计数、折叠、终止全部、单任务终止区域。 | 不创建假的任务；若 HUD 是教程临时打开，收尾时关闭；若教程前已可见，收尾后保持可见。 |
| 安心收尾：“呼……把这些繁琐的界面都收起来……” | primary 回到聊天窗；operation 为 `cleanup`；台词约 70% 时触发每日花瓣转场并清掉所有 spotlight。 | Cursor 移到聊天窗并 wobble；花瓣 cue 触发时隐藏 cursor。 | 关闭 Agent 弹窗、用户插件/OpenClaw 侧边栏和教程临时 HUD；恢复临时开关状态；转场结束后写入 Day 6 完成态。 |
| 1 小时支线：猫爪未使用/已使用分支 | 普通聊天消息，不启用 takeover；按钮区低调高亮。 | 默认不显示 Ghost Cursor；用户点“现在试试”类按钮时再移动到 Agent 按钮。 | 根据真实 Agent 使用事件分支；当天只触发一次。 |

## Scene 映射

当前 Day 6 使用以下 scene id：

```text
day6_intro_agent
day6_agent_status_master
day6_agent_capabilities
day6_plugin_side_panel
day6_agent_task_hud
day6_wrap
```

旧 scene 已不再作为 Day 3 自动演出；如查历史可参考这些迁移来源：

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

推荐保留“能力边界”相关 scene，但压缩文案：Day 6 重点是状态、授权和叫停，不需要把每个高级入口都讲成独立章节。

## 需要修改的内容

### 1. 调度与完成态

实现时需要让七日节奏支持 Day 6：

- `avatarFloatingGuide.completedRounds` 能包含 `6`。
- `lastAutoShownRound`、`pendingRound`、`manualResetRound` 等字段能处理 round 6。
- Day 6 自动触发前应确认 Day 1-5 已完成或已跳过，具体策略与现有 Day 2-4 保持一致。
- Day 6 已写入重置入口和 round 调度；后续新增/删除 scene 时要同步更新测试和重置入口。

建议状态：

- `avatarFloatingGuide.day6CompletedAt`
- `avatarFloatingGuide.day6AgentUsed`
- `avatarFloatingGuide.day6BranchPromptShownDate`

### 2. 文案

总稿明确给出的主台词：

- Agent 入口：“噔噔噔噔！今天必须要打起精神……”
- 用户插件：“除了之前介绍的功能，这里还有超多好玩的插件呢！”
- 插件能力：“有了它们，我不光能看 B 站弹幕……”
- 任务 HUD：“看这里看这里！当我决定使用【猫爪】帮你干活的时候……”
- 收尾：“呼……把这些繁琐的界面都收起来……”
- 支线 A：“那个……今天好像一次都没有用过【猫爪】帮你的忙呢……”
- 支线 B：“好耶，今天也是用自己的【猫爪】努力换来了小鱼干呢……”

如果新增 locale key，建议使用：

- `tutorial.avatarFloating.day6.intro`
- `tutorial.avatarFloating.day6.statusMaster`
- `tutorial.avatarFloating.day6.pluginSidePanel`
- `tutorial.avatarFloating.day6.taskHud`
- `tutorial.avatarFloating.day6.wrap`
- `tutorial.avatarFloating.day6.agentUnusedBranch`
- `tutorial.avatarFloating.day6.agentUsedBranch`

### 3. Agent 入口与总状态

实现目标：

- 高亮并打开 Agent 按钮。
- 展示状态栏和总开关。
- 如果为了演示打开总开关，必须保存进入前状态并在收尾或 finally 中恢复。
- 不自动授予键鼠、浏览器、专属桌面或 OpenClaw 权限。
- 权限不可用时展示真实禁用/不可用状态。

### 4. 用户插件管理面板

总稿要求“打开用户插件管理面板”。实现建议：

1. 主路径：打开 Agent 用户插件侧边面板，再触发管理面板入口。
2. 兜底路径：跨窗口、权限或弹窗受阻时，只高亮侧边入口和“管理面板”按钮。
3. 不展开具体插件配置，不自动启用插件。
4. OpenClaw 可以作为高级能力一笔带过，不抢占 Day 6 主线。

### 5. 任务 HUD 与终止权

`show-task-hud` 应确保空状态也能看见：

- 运行/排队计数。
- 折叠入口。
- 终止全部入口。
- 单任务终止区域；如无任务，不伪造真实任务。

不要创建假的后台任务。如需演示，使用纯 UI 空状态或 demo highlight，并明确恢复原 HUD 可见状态。

### 6. 1 小时猫爪生态支线

触发条件：

- Day 6 主导览完成后 1 小时。
- 用户不在任务、会议、全屏或频繁关闭引导状态。
- 当天未触发过该支线。

分支：

- A：用户在 1 小时内没有用过猫爪。
- B：用户在 1 小时内使用过猫爪。

“用过猫爪”建议监听真实事件：

- Agent 面板打开。
- Agent 任务提交。
- 键鼠/浏览器/专属桌面能力使用。
- 用户插件或 OpenClaw 入口打开。

## 生命周期要求

1. Day 6 必须由 Manager 启动临时模型、skip 按钮和 taking-over。
2. 打开 Agent 面板、侧边面板、HUD 后，finally 必须关闭或恢复。
3. 用户 skip 或 angry exit 时不得留下 Agent 总开关、用户插件开关或 HUD 可见状态的临时修改。
4. 任务 HUD 若教程前已可见，收尾后应恢复为原可见状态；若教程临时打开，收尾后关闭。
5. 聊天窗 1 小时支线必须当天只触发一次。
6. 插件 dashboard handoff 的 skip、angry exit 和本地 runtime 清理必须遵守生命周期文档中插件页专属适配规则。
7. destroy / pagehide / remote terminate 必须走统一 teardown，恢复模型和清理 highlighter/interrupt late callback。
8. Day 6 正常收尾必须播放每日花瓣转场；插件 dashboard 子页面不单独播放花瓣，由首页收尾统一触发。

## 验收清单

1. Day 6 能打开 Agent 弹窗并高亮状态栏/总开关。
2. 能展示键鼠、浏览器、专属桌面、用户插件、OpenClaw 等能力项的真实启用/不可用状态。
3. 用户插件侧边入口可见；管理面板按钮可被 spotlight 标出或真实打开。
4. 任务 HUD 能出现，空状态不遮挡主流程，收尾后恢复原可见状态。
5. 收尾后 Agent 弹窗、侧边栏、HUD、高亮、Ghost Cursor 全部清理。
6. 1 小时猫爪生态支线能按使用状态分支，且每天只触发一次。
7. Day 6 收尾花瓣转场正常播放，且 Agent 弹窗、HUD 和插件窗口不会残留在转场层上。
