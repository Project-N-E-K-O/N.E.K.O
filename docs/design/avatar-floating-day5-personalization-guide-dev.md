# Day 5 个性化与长期配置教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 5 的“个性化与长期配置”落到当前设置弹窗与子页面入口上。Day 5 主线已纳入 `AVATAR_FLOATING_GUIDE_ROUNDS[5]`，属于强接管入口级导览：会接管设置弹窗和相关入口 spotlight，但不在主线里完成模型、声音、API、角色卡或云存档的深操作，也不在主线中高亮角色卡或云存档入口。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/design/software-function-inventory-and-guide-gap-check.md`

## 目标体验

Day 5 使用自我定制与所有权效应，让用户开始把“通用软件”改造成“自己的陪伴对象”。用户需要知道：

1. 角色设置里可以调整外观、声音和长期配置。
2. 模型管理、声音克隆和 API Key 是本日主线可认门的长期入口；角色卡管理、创意工坊和云存档只作为后续支线路标。
3. 这些入口今天只认门，不要求用户立刻完整配置。
4. 角色替换要用轻松的吃醋/傲娇台词表达，但不能阻止用户真实操作。
5. 剧场后可用聊天窗支线邀请用户挑一个个性化方向。

## 当前实现边界

当前实现已明确：Day 5-7 主线都纳入悬浮窗导演，Day 5 通过强接管 round 展示角色设置、模型/声音入口和记忆浏览入口。

因此 Day 5 主线实现边界是“强接管入口导览”：

- 启动 `AVATAR_FLOATING_GUIDE_ROUNDS[5]`，由 Manager/Director 管 skip、打断、临时切模和收尾。
- 不强制跳转到所有子页面。
- 不在同一轮里完成模型上传、声音克隆、API 填写或角色卡导入。
- 剧场后个性化选择按钮必须有真实 action handler；未接入前只能写设计目标。

## 相关代码入口

设置弹窗与侧边栏：

- `data-neko-sidepanel-type="character-settings"`
- `static/avatar-ui-popup.js`
- `static/avatar-ui-popup-config.js`

子页面入口：

- `/model_manager`
- `/voice_clone`
- `/api_key`
- `/character_card_manager`
- `/cloudsave_manager`

已有跨页面教程基础：

- `static/universal-tutorial-manager.js`
- `static/yui-guide-steps.js`
- `handoff_api_key`
- `handoff_memory_browser`

## 通用生命周期复用

Day 5 主线是强接管入口导览，设置弹窗 spotlight、skip/完成态、打断处理和临时模型恢复必须遵守通用生命周期边界；剧场后聊天窗支线不启用 taking-over。

| 通用能力 | Day 5 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | 主线 round 启动后由 Director 统一进入/退出 taking-over；普通个性化支线不启用。 | 不为普通个性化支线禁用全局鼠标。 |
| `TutorialHighlightController` | 设置齿轮、`character-settings` 侧边栏、模型/声音/API 入口组、记忆浏览入口都走统一 spotlight/union spotlight。 | 不手写入口高亮层；Day 5 主线不高亮角色卡或云存档入口。 |
| `TutorialInterruptController` | 主线接管期间启用轻微抵抗和 angry exit；角色替换吃醋台词只是剧情表现。 | 不把角色替换吃醋台词做成真正的生气退出。 |
| `TutorialSkipController` | 主线由 Manager 提供 skip；聊天窗支线只提供“以后再说”。 | 不在支线按钮里实现第二套 skip。 |
| `TutorialAvatarReloadController` | 主线使用教程模型时，由 Manager 管 begin/restore。 | 不在角色设置或模型管理入口里直接 reload 当前模型。 |

跨页面打开 `/model_manager`、`/voice_clone`、`/character_card_manager`、`/cloudsave_manager` 时，如果目标页有独立 runtime 或只加载 Manager，也必须遵守同等清理语义：handoff 失败可回退入口高亮，skip/destroy 不得留下本地高亮或 Ghost Cursor。

## 模型动作与情绪随机池

Day 5 主线作为正式教程轮次演出，临时切换到 `yui-origin` Live2D。普通台词从内置动作池随机播放：`happy` 12 个、`sad` 6 个、`angry` 7 个、`neutral` 7 个、`surprised` 5 个、`Idle` 3 个。

角色替换吃醋反应是人格表现，不等同 angry exit。当前主线 scene 使用 `surprised` 表现“突然发现可替换”的慌张；后续如拆分台词，可再细分为 `surprised` -> `angry`，但不得阻止用户真实操作，也不得覆盖后续设置/记忆入口 spotlight。

| 台词段落 | 情绪分类 | 随机动作规则 |
| --- | --- | --- |
| 角色设置入口：“从今天起……” | `happy` | 从 happy 池随机，表现专属感。 |
| 替换反应：“咦，这里居然还能把我换掉吗……” | `surprised` | 当前实现用 surprised；后续拆段后可补 angry 细分，不触发 angry exit。 |
| 记忆浏览：“如果你不小心忘记……” | `angry` | 从 angry 池随机，表现傲娇；不进入生气退出。 |
| 收尾：“好啦好啦……” | `happy` | 从 happy 池随机。 |
| 个性化支线 | `happy` | 从 happy 或 Idle 池随机。 |

## 剧本阶段与实现建议

| 新剧本阶段 | 建议实现方式 | 处理建议 |
| --- | --- | --- |
| 角色设置入口 | 强接管设置 scene | 打开设置弹窗，进入 `character-settings` 侧边面板，只高亮入口，不强制跳转。 |
| 角色替换吃醋反应 | 强接管旁白 scene | 当 spotlight 靠近替换角色或模型入口时播放台词；不要拦截用户操作。 |
| 记忆浏览 | 强接管设置入口 scene | 高亮记忆浏览入口，Ghost Cursor 平滑移动到入口；不展示敏感记忆内容。 |
| 第五天收尾 | 强接管清理 scene | 鼓励用户试试定制功能，播放每日花瓣转场后回到普通聊天状态。 |
| 个性化选择支线 | 聊天窗 `message.actions` | 用户尚未打开模型管理、声音克隆或角色卡管理时触发。 |

## 动作时序

Day 5 当前是入口级强接管导览层，使用与 Day 2-4 一致的视觉节奏：台词进入聊天窗后立即设置 spotlight；约 220ms 后 Ghost Cursor 移动；只有打开设置弹窗和侧边栏属于真实操作，模型/声音/API 子页面不在主线里逐个打开；角色卡/云存档入口不在主线里高亮。

| 台词段落 | 高亮时序 | Ghost Cursor 时序 | 真实操作/清理 |
| --- | --- | --- | --- |
| 角色设置入口：“从今天起，我就真正成为只属于你的专属猫娘啦……” | 先高亮设置齿轮 `#${p}-btn-settings`；打开设置后 persistent 放到设置弹窗；primary 放到 `character-settings` 侧边栏入口或入口+侧边栏 union。 | Cursor 移到设置齿轮并 click；设置弹窗出现后移到角色设置入口，再移到模型管理/声音克隆等入口组中心。 | 只展开角色设置侧边栏；不跳转子页面，不修改角色、模型或声音；不高亮角色卡/云存档入口。 |
| 替换反应：“咦，这里居然还能把我换掉吗？” | spotlight 保持在角色/模型相关入口组；不要切到 Yui 本体。 | Cursor 在角色替换/模型管理入口附近做短 wobble 或小范围巡游，表现“她看见了这个入口”。 | 不拦截用户真实点击；台词结束后仍允许用户自己进入页面。 |
| 记忆浏览：“如果你不小心忘记了我能为你做什么……” | primary 切到设置菜单记忆浏览入口 `#${p}-menu-memory` 或等价按钮。 | Cursor 平滑移动到记忆浏览入口并 wobble；当前主线只认门，不 click 打开 `/memory_browser`。 | 不打开具体记忆条目，不读出敏感内容；若后续接入跨页 handoff，失败时保留入口高亮。 |
| 收尾：“好啦好啦，快去试试这些好玩的定制功能吧……” | primary 回到聊天窗或设置弹窗关闭后的主按钮组；台词约 70% 时触发每日花瓣转场并清掉所有 spotlight。 | Cursor 移回聊天窗输入区附近并 wobble；花瓣 cue 触发时隐藏 cursor。 | 关闭设置弹窗和临时 spotlight；转场结束后写入 Day 5 完成态。 |
| 个性化支线：“今天想动动手帮我改点什么新花样吗？” | 聊天窗 action buttons 高亮；不启用 takeover。 | 默认不显示 Ghost Cursor；用户点“换件衣服”后移动到 `/model_manager` 入口，点“改个声音”后移动到 `/voice_clone` 入口。 | 点“以后再说”后当天不重复提醒；所有按钮必须有 handler。 |

## 需要修改的内容

### 1. Day 5 调度

Day 5 已扩展为 `AVATAR_FLOATING_GUIDE_ROUNDS[5]`：

1. Manager 负责按七日节奏启动 Day 5、显示 skip、临时切换 `yui-origin` 并恢复。
2. 每个 scene 只做入口展示，不执行子页面深操作。
3. Day 5 完成态写入 `avatarFloatingGuide.completedRounds`，保持七日节奏可追踪。

建议状态：

- `avatarFloatingGuide.day5CompletedAt`
- `avatarFloatingGuide.day5ModelManagerVisited`
- `avatarFloatingGuide.day5VoiceCloneVisited`
- `avatarFloatingGuide.day5CharacterCardVisited`
- `avatarFloatingGuide.day5PersonalizationBranchShownDate`

### 2. 文案

总稿明确给出的主台词：

- 角色设置入口：“从今天起，我就真正成为只属于你的专属猫娘啦……”
- 角色替换反应：“咦，这里居然还能把我换掉吗？等一下呀……”
- 记忆浏览：“如果你不小心忘记了我能为你做什么……”
- 收尾：“好啦好啦，快去试试这些好玩的定制功能吧……”
- 个性化支线：“今天想动动手帮我改点什么新花样吗……”

如果新增 locale key，建议使用：

- `tutorial.avatarFloating.day5.characterSettings`
- `tutorial.avatarFloating.day5.replaceReaction`
- `tutorial.avatarFloating.day5.memoryBrowser`
- `tutorial.avatarFloating.day5.wrap`
- `tutorial.avatarFloating.day5.personalizationBranch`

### 3. 角色设置入口

实现目标：

- 打开设置弹窗。
- 展开 `character-settings` 侧边面板。
- 高亮模型管理、声音克隆、API Key 等入口之一或入口组。
- 不高亮角色卡或云存档入口；这些入口只在支线或后续独立引导中指路。
- 不自动打开所有子页面。
- 不修改当前角色、模型、声音或 API 配置。

角色替换吃醋反应只作为人格化表现，不应阻止用户切换角色。用户真实点击替换入口时，流程应尊重真实 UI。

### 4. 记忆浏览入口

总稿 Day 5 第二阶段写“记忆浏览”，但 Day 7 也会正式讲记忆编辑与整理。Day 5 只做“认门”：

- 可高亮设置菜单的记忆浏览入口。
- Ghost Cursor 平滑移动到入口。
- 不打开具体记忆条目，不展示敏感内容。
- 当前 Day 5 主线默认不打开 `/memory_browser`；如果未来接入 handoff，只停留在列表级入口，不读出用户历史内容。

### 5. 个性化选择支线

触发条件：

- Day 5 主导览完成。
- 用户尚未打开过模型管理、声音克隆或角色卡管理。
- 用户不在任务、会议、全屏或频繁关闭引导状态。
- 当天没有拒绝过该支线。

文案：

- “今天想动动手帮我改点什么新花样吗？嘿嘿，暂时不改、只是随便逛逛看看也完全没问题哒！还没想好吗？那也不用着急，等哪天有灵感了再改也行哦！”

选项按钮：

- `换件衣服`：打开或高亮 `/model_manager`。
- `改个声音`：打开或高亮 `/voice_clone`。
- `以后再说`：当天不再重复提醒。

## 生命周期要求

1. Day 5 主线必须使用强接管 round，剧场后个性化支线不启用强接管。
2. 打开设置弹窗或子页面 handoff 后，必须能回到普通聊天状态。
3. 跨页面 handoff 失败时，只提示入口位置，不阻塞完成态。
4. 不展示用户敏感配置内容，例如完整 API Key、私密记忆正文、云存档账号细节。
5. 用户选择“以后再说”后，当天不再重复触发个性化支线。
6. 完成、skip、destroy 都必须走 Manager 统一完成态/跳过态和临时模型恢复。
7. 所有 spotlight 必须通过通用 highlighter 或等价页面 runtime 清理。
8. Day 5 主线收尾必须播放每日花瓣转场；个性化选择支线不单独播放花瓣。

## 验收清单

1. Day 5 能打开设置弹窗并展示角色设置入口。
2. 模型管理、声音克隆、API Key 入口能被说明或高亮；角色卡、云存档入口不在 Day 5 主线高亮。
3. 吃醋台词不会阻止用户真实切换角色或打开管理页。
4. 记忆浏览入口只做认门，不读出敏感记忆内容。
5. 个性化支线按钮都有 handler；未实现 handler 时不在正式 UI 中发按钮。
6. 收尾后设置弹窗、spotlight、Ghost Cursor 和临时状态都清理干净。
7. Day 5 收尾花瓣转场正常播放，且跨页入口高亮不会残留。
