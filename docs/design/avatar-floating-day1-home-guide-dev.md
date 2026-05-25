# Day 1 首页 Yui 新手教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 1 的新剧本，落到当前首页 Yui 教程实现上。Day 1 不是新增 `AVATAR_FLOATING_GUIDE_ROUNDS[1]`，而是继续复用现有首页教程 `HOME_SCENE_ORDER`。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/home-yui-guide-text-highlight-cursor-flow.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`
- `docs/design/home-tutorial-yui-guide-performance-owner-stage-breakdown.md`

## 目标体验

Day 1 的目标是“初次见面、语音入口、猫爪粗略介绍、设置与主动搭话一瞥”。用户不需要记住所有入口，只要建立三个印象：

1. 悠怡可以和用户说话，也期待听见用户声音。
2. 猫爪是未来帮忙做事的入口，但需要用户允许。
3. 齿轮里可以慢慢调整角色、声音、记忆和相处方式。

主剧本的 Day 1 功能清单仍提到截图、导入图片、翻译和点歌入口，但当前四阶段台词没有给它们安排独立演出段落。开发时不要把这些入口强行塞回主线；除非产品后续补充支线，否则只作为 Day 1 完成后的低打扰提示或后续天数入口。

## 现有代码入口

Day 1 主线来自 `static/yui-guide-steps.js` 的 `HOME_SCENE_ORDER`：

```text
intro_basic
takeover_capture_cursor
takeover_plugin_preview
takeover_settings_peek
takeover_return_control
```

运行时由 `static/yui-guide-director.js` 负责：

- 输入框激活与首句正式旁白。
- 语音按钮高亮与 ghost cursor look-at。
- 猫爪按钮、Agent 总开关、键鼠控制开关自动演示。
- 用户插件入口和插件管理面板 handoff。
- 设置面板入口、角色设置侧边栏和归还控制权。

生命周期能力必须继续复用：

- `TutorialInteractionTakeover`
- `TutorialHighlightController`
- `TutorialInterruptController`
- `TutorialSkipController`
- `TutorialAvatarReloadController`

不要在 Day 1 专属逻辑里复制接管、高亮、打断、跳过或临时切模逻辑。

## 剧本到现有 Scene 的映射

| 剧本阶段 | 现有 scene | 实现策略 |
| --- | --- | --- |
| 初次见面与聊天输入 | intro prelude + `intro_basic` 前置问候 | 沿用现有输入框激活和首句聊天消息。 |
| 语音入口 | `intro_basic` | 更新台词，保留语音按钮圆形 spotlight 和 ghost cursor 指向。 |
| 猫爪粗略介绍 | `takeover_capture_cursor` | 更新台词，保留打开 Agent 面板、总开关、键鼠控制演示。 |
| 设置一瞥与主动搭话 | `takeover_settings_peek` | 更新台词，并决定是否增加主动搭话入口高亮。 |
| 归还控制权 | `takeover_return_control` | 剧本文案没有单独列出，但代码必须保留该收尾 scene。 |

`takeover_plugin_preview` 当前已经落地插件管理面板 handoff。新剧本 Day 1 没有单独展开插件阶段，但“猫爪粗略介绍”仍提到装备盒；建议保留 `takeover_plugin_preview`，只把文案改为轻量预览，避免破坏现有 handoff 验证链路。

## 需要修改的内容

### 1. 文案与音频 key

优先保持现有 key 稳定：

- `tutorial.yuiGuide.lines.introGreetingReply`
- `tutorial.yuiGuide.lines.introBasic`
- `tutorial.yuiGuide.lines.takeoverCaptureCursor`
- `tutorial.yuiGuide.lines.takeoverPluginPreviewHome`
- `tutorial.yuiGuide.lines.takeoverPluginPreviewDashboard`
- `tutorial.yuiGuide.lines.takeoverSettingsPeekIntro`
- `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart1`
- `tutorial.yuiGuide.lines.takeoverSettingsPeekDetailPart2`
- `tutorial.yuiGuide.lines.takeoverReturnControl`

如果只替换中文文案，修改 `static/locales/zh-CN.json` 即可。若要同步更新默认兜底文案，再改 `static/yui-guide-steps.js` 的 `performance.bubbleText`。

预录语音文件名目前在 `static/yui-guide-director.js` 的 `GUIDE_AUDIO_FILE_NAMES` 中绑定。新文案上线前可继续使用 TTS/旧音频兜底；正式发版时再替换对应 mp3 并更新时长配置。

### 2. 设置一瞥加入主动搭话

当前 `takeover_settings_peek` 主要围绕角色设置入口。如果要严格对齐新剧本，需要在 `runSettingsPeekScene()` 或其附近的设置高亮逻辑中增加主动搭话入口：

- 主按钮/菜单：`#${p}-toggle-proactive-chat` 或对应设置菜单项。
- 侧边栏：`data-neko-sidepanel-type="interval-proactive-chat"`。

建议做法：

1. 第一段继续高亮齿轮按钮并打开设置弹窗。
2. 角色/API/记忆仍作为“长期入口”轻扫，不深讲。
3. 在第二句“这个小按钮对我很重要哦”附近，把 action spotlight 切到主动搭话入口。
4. 不实际改用户配置；只打开/展开面板做展示，收尾时恢复面板状态。

### 3. Day 1 完成态

Day 1 完成或跳过后，仍由现有 Manager 流程把 `avatarFloatingGuide.completedRounds` 标记包含 `1`。不要新增另一套 Day 1 完成标记。

## 支线能力

Day 1 剧本当前没有强制聊天窗支线。若后续恢复“聊天工具小提示”，应走聊天窗消息按钮，而不是插进首页主线：

- 触发：Day 1 完成后，用户未用过截图、导入图片、翻译或点歌入口。
- UI：聊天消息 `message.actions`。
- 按钮：`看看小糖豆 / 以后再说`。
- 注意：当前教程按钮还需要 action handler，不要只发按钮但没有响应。

## 验收清单

1. 首次进入首页时，输入框激活提示、首句问候、语音按钮高亮顺序正常。
2. `intro_basic` 期间 ghost cursor 指向语音按钮，Yui 视线跟随，结束后视线恢复。
3. `takeover_capture_cursor` 能真实打开 Agent 面板并演示总开关、键鼠控制。
4. `takeover_plugin_preview` 的插件管理 handoff 成功；弹窗受阻时仍能手动继续。
5. `takeover_settings_peek` 能打开设置面板，展示角色/长期入口；若实现主动搭话展示，收尾后不保存临时改动。
6. `takeover_return_control` 正常清理高亮、面板、ghost cursor、接管态和临时模型。
7. skip、轻微打断、生气退出在任意 takeover 场景中仍走通用模块语义。
