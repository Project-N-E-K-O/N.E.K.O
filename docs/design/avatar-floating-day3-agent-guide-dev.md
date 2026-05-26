# Day 3 互动、娱乐与摸得到的陪伴教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 3 的“互动、娱乐与摸得到的陪伴”落到当前聊天窗与悬浮窗能力上。

注意：文件名仍是 `avatar-floating-day3-agent-guide-dev.md`，但新版总稿已经把 Agent、猫爪与任务 HUD 后移到 Day 6。本文按新版 Day 3 更新；后续如整理文件名，可另行把旧 Agent 设计迁移到 Day 6 文档。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/design/home-yui-guide-text-highlight-cursor-flow.md`

## 目标体验

Day 3 使用具身感与玩耍回路，让用户感觉悠怡不是一个只会回复文本的窗口，而是能被互动、能一起娱乐、能在轻任务里陪伴的人格化存在。用户需要知道：

1. 聊天窗工具区不只用于打字，里面有娱乐和辅助入口。
2. Avatar 互动工具可以摸头、喂棒棒糖、使用猫爪或锤子等道具。
3. Galgame 模式能把对话变成互动剧情，小游戏邀请会以聊天窗选项出现。
4. 点歌台、字幕翻译、备忘/学习陪伴等能力适合作为剧场后支线，不进入 Day 3 主线强接管。

## 当前实现边界

总稿与当前代码已明确：Day 3 主线是正式强接管 round，接管范围聚焦在聊天窗工具区、Avatar 互动工具和 Galgame 入口；剧场后玩法选择、点歌台、字幕翻译、备忘/学习陪伴才走聊天窗低打扰支线。

因此 Day 3 已不再复用旧 Agent round，`AVATAR_FLOATING_GUIDE_ROUNDS[3]` 现在应保持互动娱乐主题：

- 主线：强接管聊天窗工具区、Avatar 互动工具、Galgame 按钮，收尾播放花瓣转场。
- 支线：用户未体验 Avatar 工具、点歌台或 Galgame 时，再用聊天窗 action buttons 低打扰邀请。
- 禁止：不要让 Day 3 同时讲 Agent/HUD；Agent 相关内容只在 Day 6 出现。

## 相关代码入口

聊天窗与工具区：

- React 聊天窗 `toolIconItems`
- 左侧常驻工具：导入图片、截图。
- 右侧工具：Galgame、字幕翻译、点歌、Emoji/Avatar 互动工具。
- 窄宽度折叠入口：`.composer-overflow-btn` 与 `.composer-overflow-popover`。
- `jukeboxButtonLabel`
- `translateButtonLabel`
- `galgameModeEnabled`
- `choicePrompt.source === 'mini_game_invite'`
- 插件管理 UI

Avatar 互动：

- Avatar 互动工具入口。
- 棒棒糖、猫爪、锤子等道具。
- 现有工具音效与 happy/neutral/angry 反应气泡。

小游戏与剧情：

- Galgame 模式按钮。
- 小游戏邀请 `choicePrompt`。
- 聊天窗 `message.actions`。

## 通用生命周期复用

Day 3 主线是强接管 round，必须复用 `home-yui-guide-lifecycle-modularization.md` 的通用语义；剧场后聊天窗支线不启用 taking-over。

| 通用能力 | Day 3 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | 主线 round 启动后由 Director 调用 `setTutorialTakingOver(true/false)`；聊天窗支线默认不启用。 | 不为普通聊天窗支线注册全局鼠标禁用。 |
| `TutorialHighlightController` | 聊天窗工具区、Avatar 互动工具、Galgame 按钮、action buttons 的 spotlight 都应走统一 controller 或等价页面 runtime。 | 不手写一次性高亮 DOM，不在支线关闭后残留 spotlight。 |
| `TutorialInterruptController` | 主线接管期间启用轻微抵抗和 angry exit；聊天窗支线默认没有打断分支。 | 不把用户点击“以后再玩”当 angry exit。 |
| `TutorialSkipController` | 主线由 Manager 提供 skip；普通聊天窗支线只提供“以后再玩”。 | 不在聊天消息里做第二套 skip teardown。 |
| `TutorialAvatarReloadController` | 主线演出由 Manager 临时切换到 `yui-origin` 并在完成/skip/destroy 后恢复。 | 不在工具菜单展示里直接 reload 模型。 |

如果 Avatar 工具或 Galgame 后续拆到独立页面 runtime，该 runtime 必须遵守同等语义：skip/destroy/angry exit 触发瞬间清掉本地 highlighter 和 Ghost Cursor，结果回传 Manager 统一入口，不能伪造 done。

## 模型动作与情绪随机池

Day 3 主线作为正式新手教程轮次演出，由 Manager 临时切换到 `yui-origin` Live2D。普通台词从内置动作池随机播放：`happy` 12 个、`sad` 6 个、`angry` 7 个、`neutral` 7 个、`surprised` 5 个、`Idle` 3 个。

Avatar 互动工具自身的点击反馈、道具音效、happy/neutral/angry 反应气泡属于交互反馈，不被随机台词动作替代；随机动作只服务旁白台词。

| 台词段落 | 情绪分类 | 随机动作规则 |
| --- | --- | --- |
| 聊天窗工具区：“来啦来啦……” | `happy` | 从 happy 池随机，表现兴奋邀请。 |
| Avatar 互动工具：“在这个小按钮里……” | `happy` | 从 happy 池随机；不自动触发道具互动。 |
| Galgame 与小游戏：“快点开这个……” | `surprised` | 从 surprised 池随机，表现互动冒险期待。 |
| 收尾：“今天带你认识……” | `happy` | 从 happy 池随机。 |
| 生活任务支线 | `neutral` | 从 neutral 或 Idle 池随机，保持低打扰。 |
| 互动选择支线 | `happy` | 从 happy 池随机。 |

## 剧本阶段与实现建议

| 新剧本阶段 | 建议实现方式 | 处理建议 |
| --- | --- | --- |
| 聊天窗工具区 | 强接管 scene | 高亮整个 composer 工具区，镜头扫过左侧导入/截图和右侧玩法按钮，但不逐个解释。 |
| Avatar 互动工具 | 工具按钮 spotlight + 可选展开 | 高亮 Avatar 互动工具按钮；窄布局下先打开“更多”菜单，再展示道具菜单；不强制用户对模型互动。 |
| Galgame 与小游戏 | Galgame 按钮 spotlight | 高亮 Galgame 模式按钮；窄布局下先打开“更多”菜单；说明小游戏邀请会以聊天窗选项出现，不强行触发一局。 |
| 收尾 | 强接管清理 scene | 鼓励用户挑一个玩法试试，播放每日花瓣转场后归还普通聊天状态。 |
| 生活任务插件支线 | 条件化聊天窗支线 | 只在用户近期表达时间、待办、学习、复习或生活安排意图后触发。 |
| 互动选择支线 | 条件化聊天窗支线 | 用户未使用过 Avatar 互动工具、点歌台或 Galgame 模式时触发。 |

## 动作时序

Day 3 新版总稿是聊天窗和工具区的强接管 round，不复用旧 Agent round。沿用悬浮窗 scene 的节奏：台词先进入聊天窗并设置 spotlight；约 220ms 后 Ghost Cursor 移动；只在需要展示菜单时执行真实展开；台词结束后保留不超过 420ms，再清理或切下一段。

| 台词段落 | 高亮时序 | Ghost Cursor 时序 | 真实操作/清理 |
| --- | --- | --- | --- |
| 聊天窗工具区：“来啦来啦！今天我们要好好聊聊这个最显眼的【对话框】哦……” | persistent 放到聊天窗或外置聊天窗；primary 放到聊天窗工具区容器，不遮挡输入框。 | 台词开始约 220ms 后 cursor 从默认原点/上一目标移动到工具区中心并 wobble。 | 不打开大型弹窗；只建立“这里有工具”的空间感。导入图片、截图、翻译、点歌只作为背景能力，主线不展开。 |
| Avatar 互动工具：“在这个小按钮里，有许多可以和人家互动的小道具呢……” | primary 切到 Avatar 互动工具按钮；如果按钮被折叠，先打开 `.composer-overflow-btn`，再定位 `.composer-emoji-btn`；展开道具菜单后，persistent 可落在菜单容器，secondary 可落在棒棒糖。 | Cursor 先移到“更多”按钮并 click（仅窄布局），再移到 Avatar 互动工具按钮并 click；随后移动到棒棒糖/猫爪/锤子区域做短 tour。 | 不自动消耗道具，不对模型执行真实互动；台词结束或收尾时收起道具菜单和更多菜单。 |
| Galgame 与小游戏：“快点开这个【Galgame模式】……” | primary 切到 Galgame 模式按钮；若按钮被折叠，先打开更多菜单；secondary 可落在小游戏邀请区域占位或聊天窗选项区域。 | Cursor 移到 Galgame 按钮并 wobble；不强制 click，不切换用户设置。 | 小游戏只说明会用 `choicePrompt.source === 'mini_game_invite'` 出现；不伪造小游戏 session。 |
| 收尾：“今天带你认识的这些功能……” | primary 回到聊天窗；可短暂保留工具区 secondary；台词约 70% 时触发每日花瓣转场并清掉所有 spotlight。 | Cursor 回到聊天窗输入区附近并 wobble；花瓣 cue 触发时隐藏 cursor。 | 清理 spotlight、道具菜单、更多菜单和 cursor；转场结束后恢复普通聊天状态并写入 Day 3 完成态。 |
| 生活任务支线：“如果你愿意，我不只会陪你玩……” | 不启用 takeover；聊天消息进入普通消息流；可高亮聊天输入或低调显示 action buttons。 | 默认不显示 Ghost Cursor；若用户选择“现在就试试”，再移动到对应备忘/学习入口。 | 只在用户表达待办/学习意图后触发；不打开插件大面板。 |
| 互动选择支线：“今天要不要选一个轻松一点的玩法……” | 聊天窗消息带 `message.actions`；按钮区高亮即可。 | 默认不显示 Ghost Cursor；用户点“喂点甜的/听首歌”后再分别移动到 Avatar 工具/点歌台入口。 | 用户点“以后再玩”后当天不重复提醒。 |

## 需要修改的内容

### 1. Day 3 主题迁移

当前代码已将 `AVATAR_FLOATING_GUIDE_ROUNDS[3]` 改造为互动娱乐 round，并把旧 Agent round 迁到 Day 6。后续维护要保持以下约束：

1. Day 3 不自动播放 Agent/HUD 主题。
2. Day 3 完成态只代表互动娱乐主线完成。
3. Day 6 才写入 Agent 主题完成态。

### 2. 文案

总稿明确给出的主台词：

- 聊天窗工具区：“来啦来啦！今天我们要好好聊聊这个最显眼的【对话框】哦……”
- Avatar 互动工具：“在这个小按钮里，有许多可以和人家互动的小道具呢……”
- Galgame 与小游戏：“快点开这个【Galgame模式】……”
- 收尾：“今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢……”
- 生活任务支线：“如果你愿意，我不只会陪你玩，也可以陪你把小事记住……”
- 互动选择支线：“今天要不要选一个轻松一点的玩法？不用学习新东西，就当陪我玩五分钟……”

如果新增 locale key，建议使用新命名，避免复用旧 Agent key：

- `tutorial.avatarFloating.day3.playToolsIntro`
- `tutorial.avatarFloating.day3.avatarTools`
- `tutorial.avatarFloating.day3.galgameMiniGame`
- `tutorial.avatarFloating.day3.wrap`
- `tutorial.avatarFloating.day3.lifeTaskBranch`
- `tutorial.avatarFloating.day3.playChoiceBranch`

### 3. Avatar 互动工具

实现目标：

- 高亮平滑移动到 Avatar 互动工具按钮。
- 如果聊天窗处于 compact 布局，先打开“更多”菜单，再高亮真实 Avatar 互动工具按钮；不要只高亮工具区容器。
- 可以展示棒棒糖、猫爪、锤子等道具。
- 不把锤子/猫爪描述成默认安抚手段，避免和冲突修复机制混淆。
- 不强制消耗道具或触发实际互动，除非用户自己点击。

### 4. Galgame 与小游戏

实现目标：

- 高亮 Galgame 模式按钮。
- 如果 Galgame 按钮被折进“更多”菜单，先打开更多菜单再移动 Ghost Cursor。
- 若当前已支持 `galgameModeEnabled`，只展示入口和状态，不强行切换用户设置。
- 小游戏邀请必须使用真实 `choicePrompt.source === 'mini_game_invite'` 或聊天窗按钮支线。
- 不伪造后端小游戏 session。

### 5. 点歌台、字幕翻译和生活任务

点歌台、字幕翻译、备忘/学习陪伴适合放在剧场后低打扰支线：

- 点歌台可作为“听首歌”按钮 action。
- 字幕翻译可在用户打开相关内容或表达看不懂时提示。
- 生活任务支线只在用户近期表达明确时间、待办、学习、复习或生活安排意图后触发。

### 6. 剧场后聊天窗支线

互动选择支线：

- 触发条件：用户未使用过 Avatar 互动工具、点歌台或 Galgame 模式。
- 台词：“今天要不要选一个轻松一点的玩法？不用学习新东西，就当陪我玩五分钟。五分钟也算约会哦。”
- 选项按钮：`喂点甜的 / 听首歌 / 以后再玩`。
- 用户选择“以后再玩”后，当天不再重复提醒。

生活任务插件支线：

- 触发条件：用户近期表达过明确时间、待办、学习、复习或生活安排意图。
- 台词：“如果你愿意，我不只会陪你玩，也可以陪你把小事记住。明天要做什么、今天要复习什么、等会儿别忘什么，都可以交给我轻轻拴一根小红绳。”
- 不打开插件大面板；只发低打扰邀请。

建议状态：

- `avatarFloatingGuide.day3AvatarToolUsed`
- `avatarFloatingGuide.day3JukeboxUsed`
- `avatarFloatingGuide.day3GalgameUsed`
- `avatarFloatingGuide.day3PlayBranchShownDate`
- `avatarFloatingGuide.day3LifeTaskBranchShownDate`

## 生命周期要求

1. Day 3 主线必须启用强接管式 takeover，并由 Manager/Director 统一进入和退出。
2. 所有 spotlight、按钮状态和临时展开菜单都必须通过通用 highlighter 或等价 runtime 在收尾、skip 或用户关闭时清理。
3. 不应修改用户 Galgame、点歌台、翻译或互动工具配置，除非用户明确点击。
4. 聊天窗支线必须尊重低打扰原则：用户忙碌、全屏、会议中、处于任务执行中或当天已拒绝时不触发。
5. 所有支线按钮在接入前必须有 action handler；未完成 handler 时只能作为设计目标，不能在正式 UI 中发不可点击按钮。
6. 主线正式 round 的 skip、临时切模、轻微打断和 angry exit 必须接入五个通用模块。
7. Day 3 主线收尾必须播放每日花瓣转场；普通聊天窗支线不单独播放花瓣。

## 验收清单

1. Day 3 不再向用户讲旧 Agent/HUD 主题；Agent 相关内容留到 Day 6。
2. 聊天窗工具区能被高亮，且不会遮挡输入。
3. Avatar 互动工具按钮可被 spotlight 标出，道具菜单展示后能正确收起。
4. Galgame 模式按钮可被高亮，不会强制切换用户设置。
5. 小游戏邀请只使用真实 choicePrompt 或有 handler 的聊天按钮。
6. 互动选择支线能按 Avatar 工具、点歌台、Galgame 使用状态分支，且当天只触发一次。
7. 生活任务支线只在用户表达相关意图后触发，不打开插件大面板。
8. Day 3 收尾花瓣转场正常播放，且工具菜单和高亮不会残留在转场层上。
