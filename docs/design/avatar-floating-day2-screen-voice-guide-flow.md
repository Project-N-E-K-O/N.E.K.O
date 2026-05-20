# Day 2 悬浮窗教程：屏幕分享、语音与通话上下文

本文按 Day 2 新手教程期间文本输出的先后顺序，记录屏幕分享、语音上下文、来源弹窗和麦克风弹窗的高亮与 ghost cursor 流程。它只描述 Day 2 的文本、spotlight/highlight、ghost cursor、真实 UI 点击和场景清理；通用生命周期边界看 `home-yui-guide-lifecycle-modularization.md`，总览和跨天排期看 `avatar-floating-panel-functions.md`。

若本文与当前代码冲突，以当前代码为准。主要代码入口：

1. `static/universal-tutorial-manager.js`：Day 2 启动、状态持久化、临时切模、完成/跳过。
2. `static/yui-guide-director.js`：`AVATAR_FLOATING_GUIDE_ROUNDS[2]`、文本输出、高亮、ghost cursor、真实 UI 操作。
3. `static/avatar-floating-guide-reset.js`：首页“第 2 天”重置按钮入口。

## 介绍内容树

```text
Day 2：屏幕分享、语音与通话上下文
├─ 屏幕分享入口
│  ├─ 悬浮按钮组在哪里
│  ├─ 屏幕分享按钮在哪里
│  └─ 未进入通话时为什么不能直接分享
├─ 屏幕来源弹窗
│  ├─ 小三角打开来源列表
│  ├─ 屏幕 / 窗口来源分组
│  ├─ 缩略图和选中态
│  └─ loading / 无来源 / 失败状态
├─ 语音上下文回顾
│  ├─ 语音按钮和屏幕分享的关系
│  └─ 录音中静音按钮只展示不点击
├─ 麦克风弹窗
│  ├─ 播放音量
│  ├─ 空间音频
│  ├─ 降噪
│  ├─ 麦克风增益
│  ├─ 实时音量
│  └─ 默认 / 指定麦克风设备
└─ 收尾
   ├─ 不默认开始录音
   ├─ 不默认开始屏幕共享
   └─ 不修改用户设备和音频设置
```

## 当前顺序

Day 2 主线顺序来自 `AVATAR_FLOATING_GUIDE_ROUNDS[2].scenes`：

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

## 1. 通话上下文开场

文本输出：

1. `tutorial.avatarFloating.day2.intro`
2. 语音 key 占位：`avatar_floating_day2_intro`
3. 临时文案：“今天先教你一个很实用的按钮：如果你想让我看屏幕，入口就在我旁边。”

高亮流程：

1. 等待 `#${p}-floating-buttons` 可见。
2. persistent spotlight 放到悬浮按钮组。
3. 不打开弹窗，不点击真实 UI。

ghost cursor 流程：

1. cursor 从聊天窗口或默认原点出现。
2. 移动到悬浮按钮组附近。
3. 轻微 wobble，提示接下来会讲按钮组。

注意：

1. 这一段只建立上下文。
2. persistent spotlight 可延续到下一段。

## 2. 屏幕分享入口

文本输出：

1. `tutorial.avatarFloating.day2.screenEntry`
2. 语音 key 占位：`avatar_floating_day2_screen_entry`
3. 临时文案：“这个是屏幕分享。点它之前，要先和我进入语音或音视频通话。”

高亮流程：

1. persistent spotlight 仍放到悬浮按钮组。
2. action spotlight 放到 `#${p}-btn-screen`。
3. 圆形高亮覆盖屏幕分享主按钮。

ghost cursor 流程：

1. cursor 移动到 `#${p}-btn-screen`。
2. 只 hover / wobble 展示。
3. 不点击主按钮，避免开启真实屏幕共享。

真实 UI 操作：

1. 不调用屏幕共享开关。

## 3. 屏幕分享需要通话

文本输出：

1. `tutorial.avatarFloating.day2.screenRequiresVoice`
2. 语音 key 占位：`avatar_floating_day2_screen_requires_voice`
3. 临时文案：“如果还没开始通话，它会提醒你：屏幕分享只在通话里使用。”

高亮流程：

1. action spotlight 继续放到 `#${p}-btn-screen`。
2. 非录音状态可让现有 toast 或限制文案成为临时说明目标。

ghost cursor 流程：

1. cursor 在屏幕分享按钮上显示一次可见 click。
2. 如果当前没有语音通话，可触发真实 click 展示限制提示。
3. 如果当前已经在通话或共享中，降级为文字说明，不改变用户状态。

真实 UI 操作：

1. 非录音状态允许触发现有“屏幕分享需要通话”提示。
2. 录音或共享状态不点击主按钮。

清理：

1. 清掉限制提示相关虚拟 spotlight。

## 4. 屏幕来源弹窗

文本输出：

1. `tutorial.avatarFloating.day2.screenSourcePopup`
2. 语音 key 占位：`avatar_floating_day2_screen_source_popup`
3. 临时文案：“旁边的小三角可以打开来源列表，你可以选整个屏幕，也可以只选某个窗口。”

高亮流程：

1. action spotlight 放到 `.${p}-trigger-icon-screen`。
2. cursor click 后等待 `#${p}-popup-screen` 显示。
3. popup 显示后，persistent spotlight 切到屏幕来源弹窗。
4. 如果存在屏幕 / 窗口分组，用 scene extra spotlight 标记分组标题。
5. 如果没有来源或加载失败，改为高亮状态说明区域。

ghost cursor 流程：

1. cursor 移动到小三角。
2. visible click。
3. 调用真实 trigger click 或等价打开逻辑。
4. 等待 `renderScreenSourceList()` 完成或出现 loading / 失败状态。
5. cursor 指向屏幕来源，再指向窗口来源。
6. 不点击任何来源。

真实 UI 操作：

1. 打开 `#${p}-popup-screen`。
2. 允许渲染来源列表。
3. 不调用 `selectScreenSource(...)`。

## 5. 来源列表状态说明

文本输出：

1. `tutorial.avatarFloating.day2.screenSourceStates`
2. 语音 key 占位：`avatar_floating_day2_screen_source_states`
3. 临时文案：“如果系统暂时拿不到窗口列表，这里也会告诉你原因，不会偷偷共享任何东西。”

高亮流程：

1. 如果存在 `.screen-source-option.selected`，action spotlight 放到选中项。
2. 如果存在 loading、not available、no sources 或 failed 文案，使用 virtual spotlight 包住状态说明。
3. 如果存在屏幕 / 窗口分组标题，使用 scene extra spotlight 标记两个分组标题。

ghost cursor 流程：

1. cursor 在弹窗内短距离移动。
2. 指向缩略图、名称、选中态或状态说明。
3. 不点击任何来源。

真实 UI 操作：

1. 无。

清理：

1. 关闭屏幕来源弹窗。
2. 清理 screen popup 相关 action / scene extra / virtual spotlight。

## 6. 回到语音按钮

文本输出：

1. `tutorial.avatarFloating.day2.micRecap`
2. 语音 key 占位：`avatar_floating_day2_mic_recap`
3. 临时文案：“再回到语音按钮。你和我说话时，屏幕分享才有上下文。”

高亮流程：

1. persistent spotlight 回到悬浮按钮组。
2. action spotlight 放到 `#${p}-btn-mic`。
3. 如果 `#${p}-btn-mic-mute` 当前可见，增加 retained extra spotlight。

ghost cursor 流程：

1. cursor 移动到 `#${p}-btn-mic`。
2. 不点击主语音按钮，避免开始或停止录音。
3. 如果静音按钮可见，cursor 移动到静音按钮并 wobble，不点击。

真实 UI 操作：

1. 无。

## 7. 麦克风弹窗与音频质量

文本输出：

1. `tutorial.avatarFloating.day2.micAudioQuality`
2. 语音 key 占位：`avatar_floating_day2_mic_audio_quality`
3. 临时文案：“麦克风设置里可以调播放音量、空间音频、降噪、增益，还能看实时音量。”

高亮流程：

1. action spotlight 放到 `.${p}-trigger-icon-mic`。
2. cursor click 后打开 `#${p}-popup-mic`。
3. persistent spotlight 切到麦克风弹窗。
4. 依次用 virtual spotlight 标记播放音量、空间音频、降噪、麦克风增益、实时音量。

ghost cursor 流程：

1. cursor 点击 mic 小三角。
2. 等待 `renderFloatingMicList()` 完成。
3. cursor 在左栏从上到下移动，每个区域停顿。
4. 不拖动滑条，不切换开关。

真实 UI 操作：

1. 打开 `#${p}-popup-mic`。
2. 不改变音量、空间音频、降噪、增益。

## 8. 麦克风设备

文本输出：

1. `tutorial.avatarFloating.day2.micDevices`
2. 语音 key 占位：`avatar_floating_day2_mic_devices`
3. 临时文案：“右边是设备列表。一般用系统默认就好，遇到多个麦克风时再手动选择。”

高亮流程：

1. action spotlight 放到右栏设备标题或第一个 `.mic-option`。
2. 如果存在 `.mic-option.selected`，额外高亮选中项。
3. 如果无设备或权限失败，高亮对应错误文案。

ghost cursor 流程：

1. cursor 移动到系统默认麦克风。
2. cursor 移动到一个具体设备项。
3. 不点击设备，避免改用户设置。

真实 UI 操作：

1. 无。

清理：

1. 关闭麦克风弹窗。
2. 清理 mic popup 相关 spotlight。

## 9. Day 2 收尾

文本输出：

1. `tutorial.avatarFloating.day2.wrap`
2. 语音 key 占位：`avatar_floating_day2_wrap`
3. 临时文案：“今天就到这里。记住：先通话，再决定要不要让我看屏幕。”

高亮流程：

1. 清掉 action spotlight。
2. persistent spotlight 短暂回到聊天窗口。

ghost cursor 流程：

1. cursor 移动回聊天窗口或视口中心。
2. wobble 后隐藏。

完成：

1. `setTutorialTakingOver(false)`。
2. 标记 Day 2 complete 或 skip。

## 清理要求

1. 关闭屏幕来源弹窗和麦克风弹窗。
2. 不改变用户选择的屏幕来源、麦克风、增益、降噪、空间音频和扬声器音量。
3. 如果教程临时打开过麦克风权限请求，失败时不阻塞 Day 2 后续小节。
4. 不默认开始录音。
5. 不默认开始屏幕共享。
6. 不默认选择真实窗口。
