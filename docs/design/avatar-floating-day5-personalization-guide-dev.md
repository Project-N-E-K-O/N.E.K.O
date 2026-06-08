# Day 5 个性化与长期配置教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 5 的主线内容，并以 `avatar-floating-7day-complete-guide-dev.md` 作为逐句导演、生命周期和验收基准。Day 5 每日开场小剧场只包含三段：角色设置入口、记忆浏览、收尾；实现 scene 按完整指南拆为 `day5_character_settings`、`day5_character_panic`、`day5_memory_entry`、`day5_wrap`。

角色卡、创意工坊、云存档入口不在 Day 5 主线高亮；模型、声音、API 只做入口级认门，不做深操作。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-7day-complete-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 完整指南对齐基线

Day 5 的个性化主线必须按完整指南收束在“认门”，不做深操作：

1. `day5_character_settings` 首句先高亮聊天窗；播放约 1 秒后聊天窗高光消失，Ghost Cursor 从聊天窗平滑移动到设置按钮并圆形高亮设置按钮；随后高亮角色设置按钮、移动过去并展开 `character-settings` 侧边栏，最后将高光平滑过渡到角色设置侧边栏并在侧边栏内做椭圆运动。模型、声音、API 只认门，不跳转、不写配置。
2. `day5_character_panic` 是独立替换反应 scene，播放时继续高亮 `character-settings` 侧边栏；播放完后清除高光并收起角色设置侧边栏，不再切到模型管理或声音克隆入口。
3. `day5_memory_entry` 通过 `show-settings-menu:memory` 指向 `#${p}-menu-memory`，不打开 `/memory_browser`，不展示敏感记忆。
4. 角色卡、创意工坊、云存档入口属于支线或独立引导，不在 Day 5 主线高亮。
5. 收尾关闭设置和侧边栏，重新高亮聊天窗，并在约 70% cue 同步隐藏 Ghost Cursor、清理所有高光并写入 Day 5 完成态。
6. round 开场不得预热或等待聊天窗 surface ready；临时切到 `yui-origin` 并确认模型可见后，先显示等待 1500ms 再开始播放本日流程。`day5_character_settings` 在本 scene 内按需打开聊天窗，并在聊天窗打开后通过 `NekoHomeTutorialFeatureController.enforce()` 再次禁用 proactive/Galgame；教程期间胶囊输入框和聊天窗各功能按钮都禁止用户点击。角色设置弹窗不得早于首句 scene 自己的聊天窗/高光流程抢先显示。
7. 本日启用完整指南中的 Day 2-7 模型替身图片演出：教程模型可临时隐藏 5 秒，并通过全局透明 overlay 贴屏幕边缘显示。Day 5 固定触发 2 次：`day5_character_settings` 播放「从今天起……」时显示 `扒右边框.png` 于屏幕最右下，`day5_memory_entry` 播放「如果你不小心忘记了……」时显示上下翻转后的 `探头.png` 于屏幕最上方偏左；不得出现在最后一句 `day5_wrap` 播放期间，也不能遮挡角色设置侧边栏、记忆入口、skip 或收尾胶囊输入框高光。

## 目标体验

Day 5 使用自我定制与所有权效应，让用户开始把“通用软件”改造成“自己的陪伴对象”。

用户当天只需要形成三个认知：

1. 设置里的角色设置入口可以让悠怡变得更贴近用户。
2. 模型管理、声音克隆、API Key 是长期配置入口，但今天只认门。
3. 记忆浏览入口能让用户之后回看和整理相处痕迹。

主线不要打开所有子页面，不要替用户换模型、克隆声音、填写 API，不要高亮角色卡或云存档入口，不要展示敏感记忆内容。

## 代码锚点

- `static/yui-guide-day5-personalization-guide.js`
- `window.YuiGuideDailyGuides[5].round`
- `YuiGuideDirector.playAvatarFloatingRound(5)`
- `character-settings` 侧边面板
- `/model_manager`
- `/voice_clone`
- 设置菜单 `api_key`
- 设置菜单记忆入口

`/character_card_manager` 与 `/cloudsave_manager` 只作为支线或后续独立引导入口，不纳入 Day 5 主线高亮。

## PC 全局透明 Overlay 迁移约束

Day 5 迁移到 N.E.K.O.-PC 全局透明 overlay 时，只替换视觉演出层；角色设置入口、替换反应、记忆浏览、收尾的主线边界不改。网页端继续使用当前 DOM overlay。

PC 端设置侧边栏入口组、模型/声音/API 入口、记忆浏览入口和收尾胶囊输入框高光都由全局 overlay 渲染。角色卡与云存档入口仍不在 Day 5 主线中高亮；模型管理、声音克隆和 API Key 只做入口级指认，不执行跳转或配置修改。收尾台词期间重新高亮胶囊输入框，并在花瓣 cue 同步隐藏 Ghost Cursor、清理高光和播放花瓣。

## 情绪动作

| 段落 | 情绪分类 |
| --- | --- |
| 角色设置入口：“从今天起……” | `happy` |
| 替换反应：“咦，这里居然还能把我换掉吗……” | `surprised` |
| 记忆浏览：“如果你不小心忘记……” | `angry` |
| 收尾：“好啦好啦……” | `happy` |

角色替换反应是吃醋/慌张表现，不等同 angry exit，不阻止用户真实操作。

高亮去重按导演通用规则执行：角色设置入口、模型/声音/API 入口组和记忆入口都只创建当前 scene 需要的一套 spotlight；设置类 scene 不再用整张设置弹窗做 persistent 高亮，避免把圆角矩形范围撑到整窗。普通 scene 不做 operation 后 `settled` 二次高亮刷新，只有收尾 `cleanup` 重新高亮聊天窗。

## 主线阶段

当前 `static/yui-guide-day5-personalization-guide.js` 注册 4 个 scene：

| scene | target | cursor/operation | 说明 |
| --- | --- | --- | --- |
| `day5_character_settings` | 聊天窗、设置按钮、角色设置按钮、`settings-sidepanel:character-settings` | `move` + `ellipse` + `show-settings-sidepanel:character-settings` | 首句播放中从聊天窗转入设置按钮和角色设置按钮，再展开角色设置侧边栏，只做入口组认门。 |
| `day5_character_panic` | `settings-sidepanel:character-settings` | `settings-peek-panic` | 继续高亮角色设置侧边栏；播放完清除高光并收起侧边栏。 |
| `day5_memory_entry` | `#${p}-menu-memory` | `move` + `show-settings-menu:memory` | 只打开设置菜单记忆入口，不进入 `/memory_browser`。 |
| `day5_wrap` | `chat-input` | `move` + `cleanup` + `petalTransition` | 清理设置态，回到胶囊输入框并播放花瓣收尾。 |

### 阶段 1：角色设置入口

- 动作 1：`day5_character_settings` 台词开始后先圆角矩形高亮聊天窗；播放约 1 秒后聊天窗高光消失，Ghost Cursor 从聊天窗平滑移动到设置按钮，同时圆形高亮设置按钮。设置弹窗打开后，圆角矩形高亮“角色设置”按钮，Ghost Cursor 平滑移动到该按钮并展开 `character-settings` 侧边栏；随后“角色设置”按钮上的圆角矩形高光平滑过渡到角色设置侧边栏，只高亮 `settings-sidepanel:character-settings` 容器。Ghost Cursor 在侧边栏范围内做椭圆运动，可以短暂掠过模型管理、声音克隆与 API Key 区域，但不在这些入口上停顿、不分别高亮、不强制跳转或修改配置；角色卡与云存档入口不在 Day 5 主线中高亮。
- 台词：“从今天起，我就真正成为只属于你的专属猫娘啦。你看，在这里可以为我穿上漂亮的新衣服，也可以帮我换一个更好听的声音……”
- 动作 2：`day5_character_panic` 播放期间继续高亮 `character-settings` 侧边栏，不切到模型管理或声音克隆入口。该 scene 使用 `surprised`，优先播放 `settings-peek-panic` 自定义慌乱动作；只表达吃醋/慌张，不触发 angry exit。台词播放完毕后清除高光，并收起角色设置侧边栏展开态。
- 台词：“咦，这里居然还能把我换掉吗？等一下呀！你现在的动作……该不会是想要把我换掉吧？啊啊啊不行！快关掉，快关掉！”

### 阶段 2：记忆浏览

- 动作：`day5_memory_entry` 通过 `show-settings-menu:memory` 打开设置菜单记忆入口；action spotlight 切到 `#${p}-menu-memory`，Ghost Cursor 平滑移动到入口并 move 并停留。本日只做“认门”，默认不打开 `/memory_browser`；若未来接入跨页 handoff，也只停留在列表级入口，不展开具体记忆，不读出敏感内容。
- 台词：“如果你不小心忘记了我能为你做什么，随时来这里让我重新教你一次就好啦。这里还悄悄保存着我们一起走过的所有点点滴滴呢。千万别小看了我们的羁绊啊，混蛋！”

### 阶段 3：第五天收尾

- 动作：收尾台词开始前关闭设置弹窗、角色设置侧边栏和入口高亮；随后参考 Day 2 收尾：收尾台词播放期间 primary 重新回到胶囊输入框，Ghost Cursor 移到胶囊输入框中心并停留；外置聊天窗模式同步高亮独立聊天窗输入区；台词约 70% 处触发与 Day 1 相同的花瓣转场 cue，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高亮和所有 spotlight；转场结束后写入 Day 5 完成态。
- 台词：“好啦好啦，快去试试这些好玩的定制功能吧！换上新衣服、调好新声音，让我变成全天下最懂你、只属于你一个人的专属猫娘！我已经迫不及待想看到全新的自己啦！”
- 音频：`static/assets/tutorial/guide-audio/zh/好啦好啦，快去试试这.mp3`

## 剧场后聊天窗支线

Day 5 个性化选择支线已移入 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。Day 5 主线文档不再维护这些支线的触发条件、按钮或 handler。

## 验收清单

1. Day 5 主线只包含角色设置入口、记忆浏览、收尾。
2. 模型、声音、API 只认门，不执行配置。
3. 角色卡、云存档入口不在 Day 5 主线高亮。
4. 记忆浏览只认门，不展示敏感记忆内容。
5. 同一目标同一时刻只保留一套主 spotlight，不创建后再隐藏重复高亮。
6. 收尾动作与 Day 1 一致：收尾台词播放期间重新高亮聊天窗，约 70% 用同一套花瓣转场 cue 同步隐藏 Ghost Cursor 并清理内置/外置 spotlight。
7. Day 5 单轮模型替身图片固定出现 2 次：`day5_character_settings` 的「从今天起……」与 `day5_memory_entry` 的「如果你不小心忘记了……」；每次 5 秒后恢复模型。进入 `day5_wrap` 前如果替身仍在显示，必须立即清理替身并恢复模型。替身必须由全局透明 overlay 与高光、Ghost Cursor、花瓣一起携带完整可见状态；替身演出期间不得进入模型管理、声音克隆、API、记忆浏览详情或任何敏感内容页面。
