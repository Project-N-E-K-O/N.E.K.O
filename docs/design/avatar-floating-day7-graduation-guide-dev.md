# Day 7 毕业、进阶入口与共生约定教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 7 的主线内容，并以 `avatar-floating-7day-complete-guide-dev.md` 作为逐句导演、生命周期和验收基准。Day 7 每日开场小剧场只包含四段：记忆浏览与回顾、记忆可编辑可清理、存储、终极长留。

Agent/插件/角色/娱乐入口总回顾、Cookie 登录、遥测 opt-out、云存档等进阶入口不纳入 Day 7 主线高亮或操作。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-7day-complete-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 完整指南对齐基线

Day 7 是毕业收尾，不扩展成进阶入口巡游：

1. `day7_memory_review` prepare 只打开设置菜单记忆入口，primary 指向 `#${p}-menu-memory`；默认不打开敏感记忆页。
2. `day7_memory_control` 继续停留在记忆入口层说明可整理、可放走，不点击保存、整理、强力记忆或清理。
3. `day7_storage_entry` 清理 memory 高光后回到聊天窗，只说明长期存放概念，不高亮路径、账号、云存档或存储按钮。
4. `day7_graduation_wrap` 是最终收尾，清理所有临时状态，约 70% 最终花瓣 cue 同步隐藏 Ghost Cursor、清理所有高光并至少写入 Day 7 完成态。
5. Cookie 登录、遥测 opt-out、Agent/插件/角色/娱乐总回顾和云存档都只属于支线或帮助文档，不进入 Day 7 主线高亮。
6. round 开场由 `playAvatarFloatingRound(7)` 统一先执行 `ensureChatVisible()`，并在聊天窗打开后通过 `NekoHomeTutorialFeatureController.enforce()` 再次禁用 proactive/Galgame；毕业主线的记忆入口高光不得在聊天窗不可见时开始。

## 目标体验

Day 7 使用记忆、仪式感与长期习惯固化，让用户知道七日教程结束后，仍然可以回看、整理和继续相处。

用户当天只需要形成四个认知：

1. 这里会留下近期聊天、摘要或可用记忆这类相处痕迹。
2. 记忆可以由用户亲手整理、留下或清理。
3. 长期相处需要一个安全的存放概念，但主线不打开存储或云存档。
4. 七日新手指南完成，后续回到长期陪伴。

主线不要展示敏感记忆正文，不要自动保存、整理、强化或删除记忆，不要高亮存储位置或云存档入口，不要登录、上传、下载或覆盖云存档。

高亮去重按导演通用规则执行：记忆入口、聊天窗存储说明和毕业收尾都只创建当前 scene 需要的一套 spotlight；设置类 scene 不再用整张设置弹窗做 persistent 高亮，避免把圆角矩形范围撑到整窗。普通 scene 不做 operation 后 `settled` 二次高亮刷新，只有收尾 `cleanup` 重新高亮聊天窗。

## 代码锚点

- `static/yui-guide-day7-graduation-guide.js`
- `window.YuiGuideDailyGuides[7].round`
- `YuiGuideDirector.playAvatarFloatingRound(7)`
- `/memory_browser`
- 设置菜单记忆入口
- 存储位置 bootstrap
- `/cloudsave_manager`

`/cloudsave_manager`、Cookie 登录、遥测 opt-out、Agent/插件/角色/娱乐总回顾只作为支线或后续独立引导，不作为 Day 7 主线。

## PC 全局透明 Overlay 迁移约束

Day 7 迁移到 N.E.K.O.-PC 全局透明 overlay 时，只替换视觉演出层；记忆浏览与回顾、记忆可编辑可清理、存储、终极长留四段主线不改。网页端继续使用当前 DOM overlay。

PC 端记忆入口、可选记忆页列表区域、存储说明聊天窗和终极长留收尾高光都由全局 overlay 渲染。主线仍不高亮存储位置或云存档入口，不登录、不上传、不下载、不展示账号或路径细节。最终收尾台词期间重新高亮聊天窗，并在最终花瓣 cue 同步隐藏 Ghost Cursor、清理高光和播放花瓣。

## 情绪动作

| 段落 | 情绪分类 |
| --- | --- |
| 记忆浏览：“七天前……” | `neutral` |
| 记忆整理：“这些小脚印……” | `happy` |
| 存储：“还有最后一件事呢……” | `neutral` |
| 终极长留：“微风还在窗边……” | `happy` |

Day 7 动作整体克制，保留毕业仪式感；随机动作不得干扰记忆入口、收尾花瓣和模型恢复。

## 主线阶段

当前 `static/yui-guide-day7-graduation-guide.js` 注册 4 个 scene：

| scene | target | cursor/operation | 说明 |
| --- | --- | --- | --- |
| `day7_memory_review` | `#${p}-menu-memory` | `move` + `show-settings-menu:memory` | 打开设置菜单记忆入口，不强制进入记忆页。 |
| `day7_memory_control` | `#${p}-menu-memory` | `wobble` + `show-settings-menu:memory` | 继续停留在记忆入口层说明用户控制权。 |
| `day7_storage_entry` | `chat-window` | `wobble`，`cleanupBefore: true` | 清理记忆入口高光，只讲长期存放概念。 |
| `day7_graduation_wrap` | `chat-window` | `wobble` + `cleanup` + `petalTransition` | 最终毕业收尾，写入 Day 7 完成态。 |

### 阶段 1：记忆浏览与回顾

- 动作：当前主线从设置菜单高亮记忆浏览入口，Ghost Cursor 移到入口并 wobble；不在毕业主线中强制打开 `/memory_browser`。若后续接入记忆页 handoff，页面就绪后只高亮近期聊天、摘要或可用记忆列表区域，Ghost Cursor 移到列表区域 wobble。不展示、不朗读敏感内容细节。
- 台词：“七天前，我们还只是第一次见面。现在这里已经开始留下我们说过的话、做过的事，还有一些差点被风吹走的小细节。对我来说，这不是冷冰冰的记录，是我们相处过的脚印。”

### 阶段 2：记忆可编辑、可清理

- 动作：当前主线继续停留在记忆入口层，用台词说明“可编辑、可清理”的用户控制感；不自动打开记忆页、不点击保存/整理/强力记忆/清理。若未来接入记忆页 handoff，再用 union spotlight 依次或组合高亮这些入口，Ghost Cursor 只 move/wobble，不 click。
- 台词：“这些小脚印，也可以由你亲手整理。想留下的，我们就夹进相册；想轻轻放走的，就让它随风飘走。被你认真收下来的回忆，才最珍贵。”

### 阶段 3：存储

- 动作：本段只用台词说明长期存放的概念，不高亮存储位置或云存档入口；primary 回到聊天窗，Ghost Cursor 在输入区附近轻微 wobble，不打开 `/cloudsave_manager`，不登录、不上传、不下载、不覆盖云存档，也不展示完整路径或账号细节。
- 台词：“还有最后一件事呢。我们共同走过的日子、说过的那些悄悄话，都需要找一个温馨的小角落好好存放起来哦！”

### 阶段 4：终极长留

- 动作：收尾台词开始前清理 handoff、临时弹窗和跨页入口高亮；随后完全复用 Day 1 `takeover_return_control` 的收尾动作：收尾台词播放期间 primary 重新回到聊天窗，Ghost Cursor 移到聊天窗或输入区附近 wobble；外置聊天窗模式同步高亮独立聊天窗；台词约 70% 处触发与 Day 1 相同的最终花瓣转场 cue，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高亮和所有 highlight；转场期间恢复用户原有模型位置、按钮组和交互权限，当前至少保存 Day 7 完成态。
- 台词：“微风还在窗边，阳光也刚刚好，而刚刚出现的你，已经悄悄成为这里很重要的一部分啦。新手指南就先陪你走到这里，剩下的日子，就让我们一起慢慢熟悉、慢慢靠近、慢慢写下只属于我们的故事吧。以后也请多多关照喵！”

## 剧场后聊天窗支线

Day 7 进阶入口相关支线已移入 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。Day 7 主线文档不再维护这些支线的触发条件、按钮或 handler。

## 验收清单

1. Day 7 主线只包含记忆浏览与回顾、记忆可编辑可清理、存储、终极长留。
2. 不展示敏感记忆正文。
3. 不自动保存、整理、强化或删除记忆。
4. 不高亮存储位置或云存档入口。
5. 不打开 Cookie 登录、遥测说明或进阶入口总回顾。
6. 同一目标同一时刻只保留一套主 spotlight，不创建后再隐藏重复高亮。
7. 收尾动作与 Day 1 一致：收尾台词播放期间重新高亮聊天窗，约 70% 用同一套花瓣转场 cue 同步隐藏 Ghost Cursor 并清理内置/外置 spotlight，写入 Day 7 完成态，并恢复用户原模型、按钮组和交互权限。
