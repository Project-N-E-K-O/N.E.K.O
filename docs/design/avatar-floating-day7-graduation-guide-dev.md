# Day 7 毕业、进阶入口与共生约定教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 7 的“毕业、进阶入口与共生约定”落到记忆浏览、存储概念说明和进阶入口总回顾上。Day 7 是七日教程的收束，不应变成所有高级功能的说明书。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/design/software-function-inventory-and-guide-gap-check.md`
- `docs/architecture/memory-system.md`

## 目标体验

Day 7 使用记忆、仪式感与长期习惯固化，让用户知道新手教程结束后仍有清晰路径：

1. 记忆浏览可以查看近期聊天、摘要和可用记忆。
2. 用户可以保存、整理、强化或清理记忆，但教程不自动改动记忆。
3. 存储与备份是长期陪伴的收纳概念，但本日主线不高亮存储位置或云存档入口。
4. Agent、插件、角色、娱乐、Cookie 登录和遥测 opt-out 都属于进阶路径，只做总回顾或指路。
5. 完成后至少保存 Day 7 完成态，并恢复用户原有模型位置、按钮组和交互权限；独立毕业态 `graduatedAt` 可作为后续增强。

## 当前实现边界

Day 7 主线已纳入 `AVATAR_FLOATING_GUIDE_ROUNDS[7]`，属于强接管毕业回顾。它只强接管首页设置入口、记忆入口、存储概念说明和最终收尾；跨页面深操作仍保持入口级，不自动处理用户数据：

- 当前主线只高亮设置菜单里的记忆入口，不强制打开 `/memory_browser`。
- `/cloudsave_manager` 只作为后续支线或独立引导的备份入口，不在毕业主线高亮或打开。
- `/cookies_login` 只作为进阶入口，不纳入毕业主线操作。
- 遥测 opt-out 指向独立说明或设置，不在毕业流程里展开争议性解释。

## 相关代码入口

主线启动：

```text
UniversalTutorialManager.startAvatarFloatingGuideRound(7)
└─ YuiGuideDirector.playAvatarFloatingRound(7)
```

记忆与存储：

- `/memory_browser`
- 设置菜单记忆入口
- 存储位置 bootstrap
- `/cloudsave_manager`

进阶入口：

- 聊天窗 action buttons
- Toast/HUD
- 插件 UI/ui
- `/cookies_login`
- Agent/插件/角色/娱乐入口

已有跨页面教程基础：

- `static/universal-tutorial-manager.js`
- `static/yui-guide-steps.js`
- `handoff_memory_browser`
- `memory_browser_intro`

## 通用生命周期复用

Day 7 是强接管毕业回顾 round。若后续把 `/memory_browser`、`/cloudsave_manager` 等页面纳入 handoff，也必须继承生命周期文档里的通用清理和专属 runtime 边界；剧场后进阶入口总回顾才使用轻量聊天窗 action buttons。

| 通用能力 | Day 7 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | 主线 round 启动后由 Director 统一进入/退出 taking-over；进阶入口支线不启用。 | 不在记忆页或云存档页手写全局鼠标禁用。 |
| `TutorialHighlightController` | 当前主线只高亮设置菜单记忆入口；若后续接入记忆页 handoff，记忆列表/摘要区域、保存/整理/强力记忆/清理入口都走统一 spotlight 或目标页等价 highlighter。 | 不在记忆页残留本地高亮，不高亮具体敏感正文；Day 7 主线不高亮存储位置或云存档入口。 |
| `TutorialInterruptController` | 主线接管期间启用；angry exit 必须语音后统一 skip，且不能写毕业态。 | 不把用户关闭记忆页当生气退出。 |
| `TutorialSkipController` | Day 7 skip 由 Manager 统一处理；当前完成至少写入 completedRounds[7]，独立 `graduatedAt` 只有正式实现后才写。 | 不在跨页 handoff 中直接写毕业态或复制 skip teardown。 |
| `TutorialAvatarReloadController` | 主线使用教程模型，临时模型和聊天头像覆盖仍由 Manager 管恢复。 | 不在 `/memory_browser` 或 `/cloudsave_manager` 中直接恢复模型。 |

目标页如果只加载 `UniversalTutorialManager`，至少要保证 skip 和 avatar restore 入口可用；如果有独立页面 highlighter/Ghost Cursor，必须在 skip/destroy/angry exit 触发瞬间清理，并把结果回传统一入口。

## 模型动作与情绪随机池

Day 7 是强接管毕业回顾，主线临时切换到 `yui-origin` Live2D。普通台词从内置动作池随机播放：`happy` 12 个、`sad` 6 个、`angry` 7 个、`neutral` 7 个、`surprised` 5 个、`Idle` 3 个。

Day 7 的情绪整体克制，避免用过多大幅动作打断仪式感。记忆回顾和存储说明以 neutral 为主，记忆整理和最终毕业台词使用 happy。跨页记忆浏览或云存档页面内如有本地 highlighter/Ghost Cursor，随机动作不得阻塞其清理。

| 台词段落 | 情绪分类 | 随机动作规则 |
| --- | --- | --- |
| 记忆浏览：“七天前……” | `neutral` | 从 neutral 池随机，低幅度。 |
| 记忆整理：“这些小脚印……” | `happy` | 从 happy 池随机，用温柔积极语气强调用户控制。 |
| 存储：“还有最后一件事呢……” | `neutral` | 从 neutral 池随机。 |
| 终极长留：“微风还在窗边……” | `happy` | 从 happy 池随机，作为毕业收束。 |
| 进阶入口总回顾 | `Idle` | 低打扰等待用户选择。 |

## 剧本阶段与实现建议

| 新剧本阶段 | 建议实现方式 | 处理建议 |
| --- | --- | --- |
| 记忆浏览与回顾 | 强接管设置入口 scene | 高亮设置菜单记忆入口；当前主线不打开记忆页，不展示敏感记忆内容细节。 |
| 记忆可编辑、可清理 | 强接管入口说明 scene | 通过同一个记忆入口说明可编辑、可清理；后续若接入记忆页 handoff，只高亮按钮不自动点击。 |
| 存储 | 强接管台词说明 scene | 只说明长期存放概念，不高亮存储位置或云存档入口。 |
| 终极长留 | 强接管清理 scene + 完成态 | 播放最终花瓣转场，恢复模型位置、按钮组和交互权限，保存教程完成状态。 |

## 动作时序

Day 7 是强接管毕业回顾 round。统一节奏：台词入聊天窗后设置 spotlight；约 220ms 后 Ghost Cursor 移动；只打开必要入口，不自动点击保存、清理、上传、下载等高风险操作。毕业后的进阶入口总回顾才走轻量聊天窗 action buttons。

| 台词段落 | 高亮时序 | Ghost Cursor 时序 | 真实操作/清理 |
| --- | --- | --- | --- |
| 记忆浏览：“七天前，我们还只是第一次见面……” | 从设置菜单高亮记忆浏览入口 `#${p}-menu-memory`，persistent 保持设置弹窗。 | Cursor 移到记忆入口并 wobble；当前主线不 click 打开记忆页。 | 不读出具体记忆内容；空态、未初始化或入口不可用时都降级为入口说明。 |
| 记忆整理：“这些小脚印，也可以由你亲手整理……” | primary 仍保持记忆入口或设置弹窗区域，用台词说明保存、自动整理、强力记忆、清理等能力。 | Cursor 在记忆入口附近轻微 wobble；不进入记忆页时不做保存/清理按钮 tour。 | 不自动保存、整理、强化或删除记忆；若未来接入记忆页 handoff，禁用按钮按真实状态展示。 |
| 存储：“还有最后一件事呢……” | 不高亮存储位置或 `/cloudsave_manager` 入口；primary 回到聊天窗，避免把毕业说明变成设置操作。 | Cursor 回到聊天窗输入区附近并轻微 wobble；不打开云存档页。 | 不展示完整本地路径、账号、Token、Cookie；不强迫打开任何存储页面。 |
| 终极长留：“微风还在窗边……” | primary 回到聊天窗；可用 action buttons 呈现毕业后的路标；台词约 70% 时触发最终花瓣转场并清掉所有 spotlight。 | Cursor 回到聊天窗输入区附近并 wobble；花瓣 cue 触发时隐藏 cursor。 | 清理 handoff、spotlight、Ghost Cursor、临时弹窗；转场期间恢复模型位置、按钮组和交互权限；当前写入 Day 7 完成态，独立毕业态后续补充。 |
| 进阶入口总回顾 | 剧场后聊天窗 action buttons 高亮，不启用 takeover。 | 默认不显示 Ghost Cursor；用户点具体入口后再移动到对应入口。 | Cookie 登录和遥测 opt-out 只指路，不主动打开；按钮必须有 handler。 |

## 需要修改的内容

### 1. Day 7 调度与毕业态

实现时需要让七日节奏支持 Day 7：

- `avatarFloatingGuide.completedRounds` 能包含 `7`。
- Day 7 完成后当前通过 `completedRounds` 包含 `7` 结束七日自动节奏；如需要更明确的毕业记录，可后续补 `graduatedAt`。
- 用户手动重置时仍可按现有 reset 机制重新启动指定轮次。
- 如果 Day 5/6 被跳过，Day 7 是否允许启动需遵循产品策略；推荐“已到第七自然日且用户未频繁拒绝”即可展示毕业回顾。

建议状态：

- `avatarFloatingGuide.day7CompletedAt`
- `avatarFloatingGuide.graduatedAt`（后续增强，可选）
- `avatarFloatingGuide.day7MemoryBrowserVisited`
- `avatarFloatingGuide.day7StorageEntryVisited`
- `avatarFloatingGuide.day7AdvancedEntryBranchShownDate`

### 2. 文案

总稿明确给出的主台词：

- 记忆浏览：“七天前，我们还只是第一次见面……”
- 记忆整理：“这些小脚印，也可以由你亲手整理……”
- 存储：“还有最后一件事呢。我们共同走过的日子……”
- 终极长留：“微风还在窗边，阳光也刚刚好……”

如果新增 locale key，建议使用：

- `tutorial.avatarFloating.day7.memoryReview`
- `tutorial.avatarFloating.day7.memoryControl`
- `tutorial.avatarFloating.day7.storage`
- `tutorial.avatarFloating.day7.graduation`

### 3. 记忆浏览与回顾

实现目标：

- 当前主线从设置菜单高亮记忆浏览入口，不强制打开 `/memory_browser`。
- 若未来接入 handoff，再高亮近期聊天、摘要或可用记忆列表区域。
- 不读出具体聊天内容。
- 不把敏感条目复制进旁白、Toast 或日志。
- 如果记忆为空，展示空态也算通过，不伪造记忆。

### 4. 记忆可编辑、可清理

实现目标：

- 高亮保存、自动整理、强力记忆、清理等入口。
- 强调用户控制，教程不自动保存、整理、强化或删除任何记忆。
- 对高风险操作只做 spotlight，不触发点击。
- 如果页面按钮因状态不可用而禁用，按真实 UI 展示。

### 5. 存储与云存档

总稿 Day 7 的存储阶段调整为只说明长期存放概念，不在主线中高亮或打开存储/云存档入口：

- 不高亮本地存储位置 bootstrap。
- 不高亮 `/cloudsave_manager` 入口。
- 不强制用户登录、上传、下载或覆盖云存档。
- 不展示账号、路径、Token、Cookie 等敏感内容。

### 6. 进阶入口总回顾

Day 7 功能清单包含 Agent/插件/角色/娱乐入口总回顾、Cookie 登录、遥测 opt-out。实现建议：

- 用聊天窗 action buttons 做“毕业后的路标”，不要接管 UI。
- 入口可以包括：`翻翻回忆`、`添新本领`、`打扮一下`、`听首歌`、`先去聊天`。
- Cookie 登录只作为插件/外部服务进阶入口，不主动打开。
- 遥测 opt-out 指向独立说明或设置页，不在毕业台词里展开。

所有 action buttons 必须接入 handler；未接入前只能作为设计目标。

### 7. 终极长留与完成态

实现目标：

- 恢复用户原有模型位置、按钮组和交互权限。
- 清理所有 highlight、Ghost Cursor、临时弹窗和 handoff 状态。
- 当前写入 Day 7 完成态；独立毕业态后续增强。
- 发送最终毕业台词。
- 后续不再自动触发新手七日主线，只保留用户手动重置入口。

## 生命周期要求

1. Day 7 当前主线不跨页打开 `/memory_browser` 或 `/cloudsave_manager`；若未来接入记忆页 handoff，必须能从 handoff 失败中恢复。
2. 不自动修改、删除、上传、下载或整理任何用户数据。
3. 不展示敏感记忆、API Key、Cookie、云存档账号细节或完整本地路径。
4. 完成或跳过 Day 7 都必须清理临时 UI；只有完成时写入 Day 7 完成态。
5. 用户选择继续探索进阶入口后，教程主流程应结束，不再保持接管态。
6. skip、destroy、pagehide、remote terminate 必须走 Manager 统一 teardown，确保临时模型和页面 highlighter 都恢复。
7. angry exit 不能写 Day 7 完成态或后续 `graduatedAt`；必须语音后按 skip 处理。
8. Day 7 终极长留必须播放最终花瓣转场；只有正常毕业完成播放，skip/angry exit 不播放。

## 验收清单

1. Day 7 能高亮设置菜单里的记忆浏览入口。
2. 记忆列表、近期聊天、摘要或空态只在后续 handoff 接入后说明，且不泄露具体敏感内容。
3. 保存、自动整理、强力记忆、清理入口当前只由台词说明；后续若高亮，也不得自动触发。
4. 存储概念能被台词说明，但不高亮存储位置或云存档入口。
5. 毕业收尾后写入 Day 7 完成态；如后续实现独立 `graduatedAt`，也只允许正常完成时写入。
6. 收尾后模型位置、按钮组、权限、高亮、Ghost Cursor、临时窗口状态恢复。
7. 毕业后七日教程不再自动重复，手动重置仍可用。
8. Day 7 最终花瓣转场正常播放，且 Day 7 完成态写入与用户原模型恢复都完成。
