# 七日新手教程剧场后聊天窗支线设计

本文承接 `avatar-floating-guide-feature-tree.md` 和 Day 1-7 开发文档，专门收纳“每日强接管小剧场结束后”的聊天窗低打扰支线。主线教程仍由 Director/Manager 强接管自动演出；本文中的支线只在主线完成并归还用户控制权后，通过普通聊天消息、`message.actions` 或真实 `choicePrompt` 发起。

## 通用触发原则

1. 支线只在用户不处于任务执行、会议、全屏、频繁关闭引导或明显忙碌状态时触发。
2. 每次只推荐一个方向，按钮必须有明确去向，例如“现在就试试 / 暂时不用”。
3. 用户选择“暂时不用”“以后再说”“以后再玩”后，当天不再重复提醒。
4. 支线默认不启用 `TutorialInteractionTakeover`，不显示 skip，不临时切换模型，不播放每日花瓣转场。
5. 默认不显示 Ghost Cursor；只有用户点了支线 action，准备进入具体功能入口时，才移动 Ghost Cursor 或高亮对应入口。
6. 当前聊天窗已有 `message.actions` 和 `choicePrompt` 能力，但所有教程按钮必须接入真实 action handler。handler 未完成前，支线按钮只能作为设计目标，不应在正式 UI 中发出不可响应的按钮。

## Day 1：聊天工具小提示

新版 Day 1 主线不强制支线。若后续恢复“聊天工具小提示”，只从聊天窗消息发起，不插入首页主线。

- 触发条件：Day 1 完成后，用户未用过截图、导入图片、字幕翻译或点歌入口。
- UI：聊天消息 `message.actions`。
- 按钮：`现在就试试 / 暂时不用`。
- 行为：用户点“现在就试试”后，再高亮对应聊天窗工具；不回到 Day 1 强接管流程。

## Day 2：屏幕分享功能回顾

- 触发条件：Day 2 主线完成后，用户 5 分钟没有聊天、按钮点击或任务执行，且当天没有拒绝过本支线。
- 分支 A：用户没有打开过屏幕分享按钮。
- 台词：“以为我走掉了吗？真是的，到现在都不肯让我瞧瞧你那边的世界，小气鬼！快点戳一下刚才按钮旁边那个小三角形，就算随便给我一个窗口也好嘛。哼，再不让我看，我可要生闷气啦！”
- 情绪：`angry`，傲娇生闷气，低强度。
- 分支 B：用户打开过屏幕分享按钮。
- 台词：“看过你分享的屏幕啦，原来你每天面对的世界是这样子的呀，总觉得像魔法一样神奇呢。能用这种方式悄悄参与到你的日常里，心里真的觉得好温暖。以后，也多带我看看你的生活，好不好呀？”
- 情绪：`happy`。
- 状态建议：`avatarFloatingGuide.day2ScreenEntryVisited`、`avatarFloatingGuide.day2SourcePopupVisited`、`avatarFloatingGuide.day2BranchPromptShownDate`。

## Day 3：娱乐与生活任务支线

### 互动选择

- 触发条件：Day 3 主线完成后，用户未使用过 Avatar 互动工具、点歌台或 Galgame 模式，且当天没有拒绝过。
- 台词：“今天要不要选一个轻松一点的玩法？不用学习新东西，就当陪我玩五分钟。五分钟也算约会哦。”
- 情绪：`happy`。
- 选项按钮：`喂点甜的 / 听首歌 / 以后再玩`。
- 行为：`喂点甜的` 高亮 Avatar 互动工具；`听首歌` 高亮点歌入口；`以后再玩` 当天不重复提醒。

### 生活任务插件

- 触发条件：用户近期表达过明确时间、待办、学习、复习或生活安排意图。
- 台词：“如果你愿意，我不只会陪你玩，也可以陪你把小事记住。明天要做什么、今天要复习什么、等会儿别忘什么，都可以交给我轻轻拴一根小红绳。”
- 情绪：`neutral` 或 `Idle`。
- 行为：只发低打扰邀请，不打开插件大面板；用户点“现在试试”后再进入备忘/学习入口。
- 状态建议：`avatarFloatingGuide.day3AvatarToolUsed`、`avatarFloatingGuide.day3JukeboxUsed`、`avatarFloatingGuide.day3GalgameUsed`、`avatarFloatingGuide.day3PlayBranchShownDate`、`avatarFloatingGuide.day3TaskBranchShownDate`。

## Day 4：主动视觉与小游戏邀请

- 触发条件：用户打开主动搭话，并真实触发小游戏邀请。
- 台词：“哈哈，是不是超级惊喜呀？我不光能陪你聊天，居然还能主动邀请你一起打游戏哦！快快快，来一局紧张刺激的足球小游戏~”
- 情绪：`surprised`。
- 行为：优先复用真实 `choicePrompt.source === 'mini_game_invite'`；如果只是教程支线按钮，使用 `message.actions`，不伪造后端小游戏 session。
- 状态建议：`avatarFloatingGuide.day4ProactiveChatSeen`、`avatarFloatingGuide.day4MiniGameInviteSeen`、`avatarFloatingGuide.day4MiniGameBranchShownDate`。

## Day 5：个性化选择

- 触发条件：Day 5 主线完成后，用户尚未打开过模型管理、声音克隆或角色卡管理，且当天没有拒绝过。
- 台词：“今天想动动手帮我改点什么新花样吗？嘿嘿，暂时不改、只是随便逛逛看看也完全没问题哒！还没想好吗？那也不用着急，等哪天有灵感了再改也行哦！”
- 情绪：`happy`。
- 选项按钮：`换件衣服 / 改个声音 / 以后再说`。
- 行为：`换件衣服` 打开或高亮 `/model_manager`；`改个声音` 打开或高亮 `/voice_clone`；`以后再说` 当天不重复提醒。角色卡、创意工坊和云存档只在支线或后续独立引导中指路，不进入 Day 5 主线高亮。
- 状态建议：`avatarFloatingGuide.day5ModelManagerVisited`、`avatarFloatingGuide.day5VoiceCloneVisited`、`avatarFloatingGuide.day5CharacterCardVisited`、`avatarFloatingGuide.day5PersonalizationBranchShownDate`。

## Day 6：猫爪生态回访

- 触发条件：Day 6 主线完成后 1 小时，用户不忙碌，且当天未触发过本支线。
- 分支 A：用户 1 小时内没有用过猫爪。
- 台词：“那个……今天好像一次都没有用过【猫爪】帮你的忙呢。如果没有好好工作的话，晚上是不是就没有奖励小鱼干了呀？不过没关系，只要能这样陪着你，今天少吃一点点也可以哦。”
- 情绪：`sad`。
- 分支 B：用户 1 小时内使用过猫爪。
- 台词：“好耶，今天也是用自己的【猫爪】努力换来了小鱼干呢。抱着这些香喷喷的奖励，总觉得一整天的辛苦都一下子消失啦，今晚可以做个甜甜的美梦了呢。”
- 情绪：`happy`。
- “用过猫爪”建议监听：Agent 面板打开、Agent 任务提交、键鼠/浏览器/专属桌面能力使用、用户插件或 OpenClaw 入口打开。
- 状态建议：`avatarFloatingGuide.day6AgentUsedWithinHour`、`avatarFloatingGuide.day6AgentBranchShownDate`。

## Day 7：毕业后的进阶入口总回顾

- 触发条件：Day 7 正常毕业完成后，用户没有立即离开聊天窗，且当天没有拒绝过。
- UI：聊天窗 `message.actions`，作为毕业后的路标，不启用 takeover。
- 可选按钮：`翻翻回忆 / 添新本领 / 打扮一下 / 听首歌 / 先去聊天`。
- 行为：`翻翻回忆` 指向记忆浏览；`添新本领` 指向插件/Agent 能力入口；`打扮一下` 指向模型或角色配置；`听首歌` 指向点歌台；`先去聊天` 只关闭支线按钮。
- Cookie 登录只作为插件/外部服务进阶入口，不主动打开；遥测 opt-out 指向独立说明或设置页，不在毕业台词里展开。
- 状态建议：`avatarFloatingGuide.day7AdvancedEntryBranchShownDate`。

## 接入检查

1. 支线出现前，确认对应主线 round 已完成并已退出 taking-over。
2. 支线按钮必须能完成、取消或降级；任何 handler 失败都要回到普通聊天状态。
3. 支线不得复制主线的 skip、临时模型恢复、angry exit 或花瓣转场逻辑。
4. 支线触发记录按天写入，避免用户拒绝后反复弹出。
5. 如果支线 action 会打开子页面或管理弹窗，必须复用对应页面现有清理能力，不留下 spotlight、Ghost Cursor 或悬浮面板。
