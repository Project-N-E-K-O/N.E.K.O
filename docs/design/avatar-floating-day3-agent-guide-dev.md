# Day 3 Agent、猫爪与任务 HUD 教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 3 的“Agent、任务 HUD 与能力节奏”落到现有悬浮窗教程实现上。Day 3 已有正式 round，配置在 `static/yui-guide-director.js` 的 `AVATAR_FLOATING_GUIDE_ROUNDS[3]`。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/design/home-yui-guide-text-highlight-cursor-flow.md`

## 目标体验

Day 3 要让用户把“猫爪”理解成帮忙做事的工具箱，而不是一个不可控的自动化黑箱。用户需要学会：

1. 猫爪入口、状态灯和总开关在哪里。
2. 插件生态能扩展悠怡的能力。
3. 她执行任务时旁边会有进度小看板。
4. 用户可以随时叫停。

剧本语气可以夸张可爱，但实现上必须清楚展示状态、权限和终止入口。

## 现有代码入口

启动链路：

```text
UniversalTutorialManager.startAvatarFloatingGuideRound(3)
└─ YuiGuideDirector.playAvatarFloatingRound(3)
   └─ AVATAR_FLOATING_GUIDE_ROUNDS[3].scenes
```

相关实现：

- `static/yui-guide-director.js`
  - `AVATAR_FLOATING_GUIDE_ROUNDS[3]`
  - `open-agent`
  - `show-task-hud`
  - `show-agent-sidepanel:user-plugin:management-panel`
  - `show-agent-sidepanel:openclaw:openclaw-guide`
  - `show-settings-management`
- Agent 弹窗：
  - `#${p}-popup-agent`
  - `agent-master`
  - `agent-keyboard`
  - `agent-browser`
  - `agent-openfang`
  - `agent-user-plugin`
  - `agent-openclaw`
- 任务 HUD：
  - `AgentHUD.createAgentTaskHUD()`
  - `AgentHUD.showAgentTaskHUD()`
  - `#agent-task-hud`

## 现有 Scene 与新剧本映射

| 新剧本阶段 | 现有 scene | 处理建议 |
| --- | --- | --- |
| 猫爪入口与总状态 | `day3_intro_agent`、`day3_agent_status_master` | 更新文案为“猫爪/小灯/工具箱”。 |
| 用户插件介绍 | `day3_plugin_side_panel` | 目标是打开用户插件管理面板；如果跨窗口或弹窗受阻，再降级为侧边入口和管理按钮高亮。可把 OpenClaw 作为高级装备一笔带过。 |
| 任务 HUD 与终止权 | `day3_agent_task_hud` | 保留 HUD 空状态/模拟高亮，强调进度和叫停。 |
| 安心收尾 | `day3_wrap` | 更新文案，清理 Agent 弹窗和 HUD。 |

现有 `day3_agent_capabilities`、`day3_openclaw_side_panel`、`day3_settings_management_entries` 不在用户剧本里单独展开，但建议保留为“装备更多、以后慢慢整理”的真实 UI 展示。不要删除 scene，除非同步调整测试、音频和指标。

## Scene 配置要求

当前 Day 3 scene 顺序：

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

如果要严格贴合用户剧本，可调整为：

```text
day3_intro_agent
day3_agent_status_master
day3_plugin_side_panel
day3_agent_task_hud
day3_wrap
```

但更推荐保留完整顺序，把额外 scene 文案改短，因为它们已经覆盖当前真实 UI 能力边界。

## 需要修改的内容

### 1. 文案替换

保留这些 text key：

- `tutorial.avatarFloating.day3.intro`
- `tutorial.avatarFloating.day3.statusMaster`
- `tutorial.avatarFloating.day3.capabilities`
- `tutorial.avatarFloating.day3.taskHud`
- `tutorial.avatarFloating.day3.pluginSidePanel`
- `tutorial.avatarFloating.day3.openclawSidePanel`
- `tutorial.avatarFloating.day3.managementEntries`
- `tutorial.avatarFloating.day3.wrap`

将文案更新为 Day 3 新剧本的“猫爪、小鱼干、小看板”风格。`AVATAR_FLOATING_GUIDE_ROUNDS[3].scenes[].text` 只作为兜底。

### 2. 插件管理面板行为

Day 3 剧本要求打开用户插件管理面板。当前 Day 3 的 `day3_plugin_side_panel` 只展示 Agent 用户插件侧边入口，不等同于 Day 1 插件 dashboard handoff。

实现目标：

1. **主路径**：复用 Day 1 的插件 dashboard handoff 能力，真实打开用户插件管理面板。
2. **兜底路径**：如果跨窗口、弹窗权限或当前环境导致 handoff 失败，只高亮侧边入口和“管理面板”按钮，并给出可继续教程的提示。

实现时要确认跨窗口 skip、angry exit、回到首页清理都仍正确。Day 3 重点仍是猫爪与 HUD，插件管理面板打开后只做短停留，不展开完整插件配置。

### 3. 任务 HUD 状态

当前 `show-task-hud` 会展示 HUD，可能是空状态。需要确保空状态也能看见：

- 运行/排队计数。
- 任务列表空态。
- 折叠入口。
- 终止全部入口，如果没有任务应禁用或呈现空态。
- 单任务终止区域，如无任务则不伪造真实任务。

不要创建假的后台任务；如需模拟，使用纯 UI 空状态或 demo highlight。

### 4. 1 小时猫爪生态支线

剧本新增 Day 3 完成后 1 小时回访：

- 分支 A：1 小时内没有用过猫爪。
- 分支 B：1 小时内使用过猫爪。

建议由聊天窗支线调度器实现：

- 记录 Day 3 完成时间。
- 监听 Agent/猫爪相关真实使用事件，例如 Agent 面板打开、Agent 任务提交、键鼠/浏览器能力使用。
- 到 1 小时时，如果当天未触发过该支线且用户不在任务/会议/全屏中，再发送消息。

状态建议：

- `avatarFloatingGuide.day3CompletedAt`
- `avatarFloatingGuide.day3AgentUsed`
- `avatarFloatingGuide.day3BranchPromptShownDate`

## 生命周期要求

1. 进入 Day 3 时必须由 Manager 启动临时 Yui 模型、skip 按钮和 taking-over。
2. 打开 Agent 面板、侧边面板、HUD 后，finally 必须关闭或恢复：
   - Agent 弹窗；
   - 用户插件/OpenClaw 侧边面板；
   - 临时展示的 HUD；
   - retained / scene extra / virtual spotlight。
3. 用户 skip 或 angry exit 时不得留下 Agent 总开关或用户插件开关的临时修改；如 scene 真实改了开关，必须保存进入前状态并恢复。

## 验收清单

1. Day 3 自动启动后能打开 Agent 弹窗并高亮状态栏/总开关。
2. 能展示键鼠、浏览器、专属桌面、用户插件、OpenClaw 等能力项的启用/不可用状态。
3. 任务 HUD 能出现，空状态不遮挡主流程，收尾后恢复原可见状态。
4. 用户插件侧边入口可见；管理面板按钮可被 spotlight 标出。
5. 收尾后 Agent 弹窗、侧边栏、HUD、高亮、ghost cursor 全部清理。
6. 1 小时猫爪生态支线能按使用状态分支，且每天只触发一次。
