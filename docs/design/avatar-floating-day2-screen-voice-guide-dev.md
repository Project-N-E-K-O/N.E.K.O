# Day 2 屏幕分享与声音教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 2 的“屏幕分享、声音与小窗约定”落到现有悬浮窗教程实现上。Day 2 已有正式 round，配置在 `static/yui-guide-director.js` 的 `AVATAR_FLOATING_GUIDE_ROUNDS[2]`。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 目标体验

Day 2 的目标是让用户知道：

1. 屏幕入口和语音/通话上下文有关。
2. 可以通过小三角选择屏幕或窗口来源。
3. 声音设置里可以调播放音量、空间音频、降噪、增益和设备。
4. 第二天启动后，先根据用户 Day 1 是否使用过语音做分支承接，再进入屏幕分享入口演示。

台词可以甜一点，但 UI 行为必须保持真实：不能伪造“已共享”，不能绕过 `toggleScreenShare()` 的通话限制。

## 现有代码入口

启动链路：

```text
UniversalTutorialManager.startAvatarFloatingGuideRound(2)
└─ YuiGuideDirector.playAvatarFloatingRound(2)
   └─ AVATAR_FLOATING_GUIDE_ROUNDS[2].scenes
```

相关实现：

- `static/yui-guide-director.js`
  - `AVATAR_FLOATING_GUIDE_ROUNDS[2]`
  - `playAvatarFloatingRound()`
  - `playAvatarFloatingScene()`
  - `runAvatarFloatingSceneOperation()`
  - `open-screen-popup`
  - `open-mic-popup`
- 屏幕来源 UI：
  - `renderFloatingScreenSourceList()`
  - `#${p}-popup-screen`
  - `.${p}-trigger-icon-screen`
- 麦克风 UI：
  - `renderFloatingMicList()`
  - `#${p}-popup-mic`
  - `.${p}-trigger-icon-mic`
- 屏幕分享限制：
  - `toggleScreenShare()` 中“语音会话中才允许屏幕分享”的真实提示。

## 现有 Scene 与新剧本映射

| 新剧本阶段 | 现有 scene | 处理建议 |
| --- | --- | --- |
| 承接昨日相处 | 新增 prelude 或动态 `day2_intro_context` | 必须在第二天启动后、屏幕入口演示前出现。根据 Day 1 语音使用状态切换台词和按钮。 |
| 屏幕分享入口 | `day2_screen_entry`、`day2_screen_requires_voice` | 保留真实点击，触发未通话提示。 |
| 来源选择 | `day2_screen_source_popup`、`day2_screen_source_states` | 保留小三角、来源列表和失败/不可用兜底说明。 |
| 声音设置 | `day2_mic_recap`、`day2_mic_popup_audio_quality`、`day2_mic_devices` | 新剧本主线较短，但建议保留声音弹窗演示。 |
| 收尾 | `day2_wrap` | 更新成“今天先到这里，每天多了解一点点”。 |

## Scene 配置要求

Day 2 scene 不建议删减到只有 3 段。用户剧本是产品叙事层，开发实现仍应保留现有 9 个 scene，以便真实覆盖当前 UI 状态：

```text
day2_intro_context
day2_screen_entry
day2_screen_requires_voice
day2_screen_source_popup
day2_screen_source_states
day2_mic_recap
day2_mic_popup_audio_quality
day2_mic_devices
day2_wrap
```

可改的是每个 scene 的 `text`、`textKey` 对应 locale 文案和语音资源；不应随意改 scene id，因为它们会影响经验指标、调试和后续音频映射。

## 需要修改的内容

### 1. 文案替换

推荐保留这些 text key：

- `tutorial.avatarFloating.day2.intro`
- `tutorial.avatarFloating.day2.screenEntry`
- `tutorial.avatarFloating.day2.screenRequiresVoice`
- `tutorial.avatarFloating.day2.screenSourcePopup`
- `tutorial.avatarFloating.day2.screenSourceStates`
- `tutorial.avatarFloating.day2.micRecap`
- `tutorial.avatarFloating.day2.micAudioQuality`
- `tutorial.avatarFloating.day2.micDevices`
- `tutorial.avatarFloating.day2.wrap`

新文案优先写入 locale；`AVATAR_FLOATING_GUIDE_ROUNDS[2].scenes[].text` 作为兜底同步更新。

### 2. Day 1 语音使用分支

剧本要求 Day 2 启动后根据 Day 1 是否用过语音给不同台词，并且这段是 Day 2 小剧场的开场承接，不是剧场结束后的回访。不要把它放到 `day2_wrap` 之后。

推荐方案：

1. 记录 Day 1 用户是否点击或完成过语音入口：
   - 可复用已有语音按钮事件统计；
   - 或在教程期间监听 `#${p}-btn-mic` 用户真实点击。
2. Day 2 启动时在屏幕分享入口前播放分支：
   - 方案 A：在调用 `playAvatarFloatingRound(2)` 前，先发一条聊天窗 prelude 消息。
   - 方案 B：让 Director 在播放 `day2_intro_context` 时读取状态并动态选择台词。
3. 按钮使用 `message.actions` 或等价的教程按钮：
   - `现在说一句`
   - `继续打字`
4. `现在说一句` 的 action handler 应聚焦或高亮语音按钮，不直接强制录音；`继续打字` 直接进入屏幕分享入口演示。

### 3. 5 分钟无交互功能回顾

剧本新增 Day 2 完成后 5 分钟无交互回访。建议作为“聊天窗支线调度器”能力，不要放进 Director：

- 条件：Day 2 round complete，且 5 分钟内没有用户聊天、按钮点击或任务执行。
- 分支 A：用户没有打开过屏幕分享按钮或小三角。
- 分支 B：用户打开过屏幕分享按钮或小三角。
- 当天用户选择忽略或关闭后，不再重复提醒。

需要新增或复用的状态：

- `avatarFloatingGuide.day2ScreenEntryVisited`
- `avatarFloatingGuide.day2SourcePopupVisited`
- `avatarFloatingGuide.day2BranchPromptShownDate`

## 生命周期要求

Day 2 运行时必须继续复用通用模块：

- Manager 显示 `TutorialSkipController`。
- Director 调用 `setTutorialTakingOver(true/false)`。
- 高亮通过 `TutorialHighlightController` 和 overlay 统一管理。
- 任意异常、skip、angry exit 都必须进入 `closeAvatarFloatingGuidePanels()` 和 finally 清理。

## 验收清单

1. Day 2 只能在首页启动，悬浮按钮 ready 后再播放。
2. 未通话时点击屏幕按钮，会出现真实限制提示。
3. 小三角能打开来源弹窗；加载失败、无权限、非桌面等兜底状态可见。
4. 麦克风弹窗可展示播放音量、空间音频、降噪、增益、实时音量和设备列表。
5. 收尾后屏幕/麦克风弹窗关闭，高亮、ghost cursor、接管态清理干净。
6. Day 2 完成后能根据 Day 1 语音使用状态发一次聊天窗回访。
7. 5 分钟无交互回顾当天只触发一次。
