# Day 3 悬浮窗教程：Agent、插件与管理入口

本文按 Day 3 新手教程期间文本输出的先后顺序，记录 Agent 能力、任务 HUD、插件入口、OpenClaw 和长期管理入口的高亮与 ghost cursor 流程。它只描述 Day 3 的文本、spotlight/highlight、ghost cursor、真实 UI 点击和场景清理；通用生命周期边界看 `home-yui-guide-lifecycle-modularization.md`，总览和跨天排期看 `avatar-floating-panel-functions.md`。

若本文与当前代码冲突，以当前代码为准。主要代码入口：

1. `static/universal-tutorial-manager.js`：Day 3 启动、状态持久化、临时切模、完成/跳过。
2. `static/yui-guide-director.js`：`AVATAR_FLOATING_GUIDE_ROUNDS[3]`、文本输出、高亮、ghost cursor、真实 UI 操作。
3. `static/avatar-floating-guide-reset.js`：首页“第 3 天”重置按钮入口。
4. `static/common-ui-hud.js`：Agent 弹窗内容和任务 HUD。

## 介绍内容树

```text
Day 3：Agent、插件与管理入口
├─ Agent 能力中心
│  ├─ Agent 按钮在哪里
│  ├─ Agent 状态栏
│  ├─ 总开关语义
│  └─ 能力开关不会被教程擅自改变
├─ Agent 能力分层
│  ├─ 键鼠控制
│  ├─ 浏览器控制
│  ├─ 专属桌面 / OpenFang
│  ├─ 用户插件
│  └─ OpenClaw
├─ 任务 HUD
│  ├─ 运行 / 排队计数
│  ├─ 任务列表
│  ├─ 折叠与拖拽
│  └─ 终止按钮只讲解不触发
├─ 插件与 OpenClaw 侧边面板
│  ├─ 用户插件管理面板入口
│  ├─ OpenClaw 接入教程入口
│  └─ 跨窗口入口默认不自动打开
└─ 长期管理入口
   ├─ 角色设置
   ├─ 模型管理
   ├─ 声音克隆
   ├─ API 密钥
   └─ 记忆浏览
```

## 当前顺序

Day 3 主线顺序来自 `AVATAR_FLOATING_GUIDE_ROUNDS[3].scenes`：

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

## 1. Agent 入口

文本输出：

1. `tutorial.avatarFloating.day3.intro`
2. 语音 key 占位：`avatar_floating_day3_intro`
3. 临时文案：“今天看看我的小帮手能力。这里不是普通设置，是我帮你操作电脑时会用到的工具箱。”

高亮流程：

1. persistent spotlight 放到悬浮按钮组。
2. action spotlight 放到 `#${p}-btn-agent`。

ghost cursor 流程：

1. cursor 移动到 Agent 按钮。
2. visible click。
3. 调用真实按钮 click 或 `openAgentPanel()`。
4. 等待 `#${p}-popup-agent` 显示。

真实 UI 操作：

1. 打开 Agent 弹窗。

清理：

1. persistent spotlight 切到 Agent 弹窗。

## 2. Agent 状态与总开关

文本输出：

1. `tutorial.avatarFloating.day3.statusMaster`
2. 语音 key 占位：`avatar_floating_day3_status_master`
3. 临时文案：“最上面会显示 Agent 状态。总开关没准备好时，下面的能力也不会乱动。”

高亮流程：

1. persistent spotlight 放到 `#${p}-popup-agent`。
2. action spotlight 放到状态栏。
3. virtual spotlight 放到 Agent 总开关行。

ghost cursor 流程：

1. cursor 移动到状态栏。
2. cursor 移动到 Agent 总开关。
3. 不点击总开关，避免改用户权限。

真实 UI 操作：

1. 无。

## 3. Agent 能力分层

文本输出：

1. `tutorial.avatarFloating.day3.capabilities`
2. 语音 key 占位：`avatar_floating_day3_capabilities`
3. 临时文案：“键鼠控制、浏览器控制、专属桌面、插件和 OpenClaw，都是不同层级的帮忙方式。”

高亮流程：

1. 依次高亮 `agent-keyboard`、`agent-browser`、`agent-openfang`、`agent-user-plugin`、`agent-openclaw`。
2. 使用 virtual spotlight 包住实际可视行，避免只高亮隐藏 checkbox。

ghost cursor 流程：

1. cursor 逐项移动。
2. 每项短暂停顿。
3. 不点击任何能力开关。

真实 UI 操作：

1. 无。

清理：

1. 清理每项 action / virtual spotlight。

## 4. Agent 任务 HUD

文本输出：

1. `tutorial.avatarFloating.day3.taskHud`
2. 语音 key 占位：`avatar_floating_day3_task_hud`
3. 临时文案：“如果我真的开始执行任务，旁边会出现任务面板。你能看到进度，也能随时终止。”

高亮流程：

1. 如果 `#agent-task-hud` 已存在且可见，persistent spotlight 暂时放到 HUD。
2. 如果不可见，教程可以调用 `AgentHUD.showAgentTaskHUD()` 或 `AgentHUD.createAgentTaskHUD()` 临时展示空 HUD。
3. action spotlight 依次放到运行/排队统计、折叠按钮、终止按钮、任务列表。

ghost cursor 流程：

1. cursor 从 Agent 弹窗移动到 HUD。
2. 指向运行/排队计数。
3. 指向折叠按钮，不点击或只在临时 HUD 中点击并恢复。
4. 指向终止全部按钮，不点击，避免弹确认框。

真实 UI 操作：

1. 可临时展示 HUD。
2. 不创建真实 Agent 任务。
3. 不调用终止接口。

清理：

1. 如果 HUD 是教程临时显示的，恢复原显示、折叠和拖拽位置。
2. persistent spotlight 回到 Agent 弹窗。

## 5. 用户插件侧边面板

文本输出：

1. `tutorial.avatarFloating.day3.pluginSidePanel`
2. 语音 key 占位：`avatar_floating_day3_plugin_side_panel`
3. 临时文案：“用户插件这里还有一个侧边入口，能打开插件管理面板。”

高亮流程：

1. action spotlight 放到用户插件能力行。
2. hover 或调用侧边面板 `_expand()` 展开 `data-neko-sidepanel-type="agent-user-plugin-actions"`。
3. retained extra spotlight 保留用户插件开关。
4. virtual spotlight 放到用户插件“管理面板”入口。

ghost cursor 流程：

1. cursor 移动到用户插件行。
2. 不点击开关。
3. cursor 移动到侧边面板“管理面板”入口。
4. 不点击跨窗口入口，除非后续明确引入 handoff。

真实 UI 操作：

1. 展开侧边面板。
2. 不打开 `/api/agent/user_plugin/dashboard`。

清理：

1. 收起用户插件侧边面板，或在下一小节前调用侧边面板互斥收起。

## 6. OpenClaw 侧边面板

文本输出：

1. `tutorial.avatarFloating.day3.openclawSidePanel`
2. 语音 key 占位：`avatar_floating_day3_openclaw_side_panel`
3. 临时文案：“OpenClaw 需要外部服务配合。如果不可用，我会把原因告诉你。”

高亮流程：

1. action spotlight 放到 OpenClaw 能力行。
2. 展开 `data-neko-sidepanel-type="agent-openclaw-actions"`。
3. virtual spotlight 放到“OpenClaw 接入教程”入口。
4. 如果 OpenClaw disabled 或 unavailable，额外高亮该行状态说明。

ghost cursor 流程：

1. cursor 移动到 OpenClaw 行。
2. cursor 移动到“OpenClaw 接入教程”入口。
3. 不点击跨窗口入口。

真实 UI 操作：

1. 展开侧边面板。
2. 不调用开关变更。
3. 不打开 guide 页面。

清理：

1. 收起 OpenClaw 侧边面板。
2. 清理 Agent 侧边面板 retained / virtual spotlight。

## 7. 设置里的长期管理入口

文本输出：

1. `tutorial.avatarFloating.day3.managementEntries`
2. 语音 key 占位：`avatar_floating_day3_management_entries`
3. 临时文案：“角色、模型、声音、API 和记忆这些长期配置，都放在设置里的管理入口。”

高亮流程：

1. 关闭 Agent 弹窗。
2. action spotlight 放到 `#${p}-btn-settings`。
3. cursor click 打开 `#${p}-popup-settings`。
4. persistent spotlight 切到设置弹窗。
5. 展开 `data-neko-sidepanel-type="character-settings"`。
6. scene extra spotlight 包含角色设置入口、角色侧边面板、API 密钥入口、记忆浏览入口。

ghost cursor 流程：

1. cursor 点击设置按钮。
2. cursor 移动到角色设置入口。
3. cursor 移动到角色侧边面板里的通用设置、模型管理、声音克隆入口。
4. cursor 移动到 API 密钥和记忆浏览入口。
5. 不点击跨页入口。

真实 UI 操作：

1. 打开设置弹窗。
2. 展开角色设置侧边面板。
3. 不打开跨页面管理页。

清理：

1. 关闭角色设置侧边面板和设置弹窗。

## 8. Day 3 收尾

文本输出：

1. `tutorial.avatarFloating.day3.wrap`
2. 语音 key 占位：`avatar_floating_day3_wrap`
3. 临时文案：“今天你只需要记住：让我做事之前，先看看 Agent 状态和权限。”

高亮流程：

1. persistent spotlight 回到聊天窗口。
2. 清理所有 Agent / settings extra spotlight。

ghost cursor 流程：

1. cursor 回到聊天窗口或视口中心。
2. wobble 后隐藏。

完成：

1. `setTutorialTakingOver(false)`。
2. 标记 Day 3 complete 或 skip。

## 清理要求

1. 关闭 Agent 弹窗、设置弹窗和所有侧边面板。
2. 如果教程临时展示 HUD，结束后恢复原显示状态、折叠状态和拖拽位置。
3. 不强制打开 Agent 总开关或任何子能力。
4. 不打开用户插件、OpenClaw、API、记忆、角色等跨窗口入口。
5. 不创建真实 Agent 任务。
6. 不调用终止任务接口。
