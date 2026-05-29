# Day 2 屏幕分享、声音与小窗约定教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 2 的主线内容。Day 2 的每日开场小剧场只包含三段：承接昨日相处、屏幕分享入口、收尾。屏幕来源弹窗、麦克风弹窗、空间音频、降噪、增益等能力只作为代码锚点和后续扩展背景，不在 Day 2 主线里单独展开成额外剧情段。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 目标体验

Day 2 使用自我暴露效应，把屏幕分享讲成“先牵上线、再开小窗”的亲密邀请。用户当天只需要形成三个认知：

1. 悠怡会根据 Day 1 是否听过用户声音，用不同语气承接第二天。
2. 屏幕分享入口与语音通话上下文有关，未通话时点击会出现真实限制提示。
3. 今日教程结束后，界面恢复干净状态，把控制权还给用户。

主线不要扩写成说明书，不要强制打开屏幕来源列表，不要强制打开麦克风设置，不要替用户选择屏幕、切设备、改音频参数或开启录音。

## 代码入口

Day 2 启动链路：

```text
UniversalTutorialManager.startAvatarFloatingGuideRound(2)
└─ YuiGuideDirector.playAvatarFloatingRound(2)
   └─ window.YuiGuideDailyGuides[2].round
```

核心代码锚点：

- `static/yui-guide-director.js`
  - `getYuiGuideDailyGuide(2)`
  - `resolveAvatarFloatingSceneText()`
  - `resolveAvatarFloatingSceneEmotion()`
  - `playAvatarFloatingScene()`
  - `runAvatarFloatingSceneOperation()`
- `static/yui-guide-day2-screen-voice-guide.js`
  - Day 2 scene 配置、台词、voice key、收尾 `petalTransition`。
- 外置聊天窗同步：
  - `TutorialInteractionTakeover.setExternalizedChatSpotlight()`
  - `TutorialInteractionTakeover.setExternalizedChatCursor()`
  - `app-interpage.js` 的 `yui_guide_message_action` 转发。
- 真实限制：
  - `toggleScreenShare()` 的“语音会话中才允许屏幕分享”提示。
- 可引用但不在主线单独展开：
  - `renderFloatingScreenSourceList()`
  - `renderFloatingMicList()`
  - `#${p}-popup-screen`
  - `#${p}-popup-mic`

## 通用生命周期复用

Day 2 是强接管每日小剧场，继续复用通用教程生命周期：

| 通用能力 | Day 2 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | Manager 启动 Day 2 后进入 taking-over，教程期间只放行 skip、当前演示需要的目标和 Day 2 开场消息选项。外置聊天窗通过 BroadcastChannel 同步 spotlight/cursor 和选项点击。 | 不让普通页面点击穿透到非教程目标；不要用解除鼠标禁用的方式模拟 Ghost Cursor 点击。 |
| `TutorialHighlightController` | 聊天窗、屏幕分享按钮和收尾高亮/清理都走统一 spotlight；屏幕分享 scene 点击后不再做 `settled` 二次高亮刷新。 | 不手写额外高亮 DOM，不创建后再隐藏重复高亮。 |
| `TutorialInterruptController` | 用户强行打断时沿用轻微抗拒、生气退出、跳过流程。 | 不把屏幕分享限制提示当成打断。 |
| `TutorialSkipController` | skip 由 Manager 统一创建、销毁。 | 不在 Day 2 支线里创建第二个 skip。 |
| `TutorialAvatarReloadController` | Day 2 仍使用教程临时模型 `yui-origin`，结束后恢复用户原模型。 | 不在 Day 2 scene 内直接 reload 模型。 |

剧场后聊天窗支线不属于 Day 2 强接管主线，统一见 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。

## 情绪动作

Day 2 普通台词按总文档情绪表播放 `yui-origin` 随机动作：

| 段落 | 情绪分类 | 说明 |
| --- | --- | --- |
| 语音承接 A：“昨天听见你的声音以后……” | `happy` | 用户 Day 1 使用过语音时触发。 |
| 语音承接 B：“昨天你一直在噼里啪啦打字……” | `sad` | 用户 Day 1 没有使用语音时触发，表现轻微委屈。 |
| 屏幕分享入口：“在跟我通语音电话的时候……” | `happy` | 撒娇邀请。 |
| 收尾：“今天的教程到这里就结束了呢……” | `happy` | 温柔满足。 |

随机动作不得抢占 Ghost Cursor、spotlight 或真实 UI 点击时序。

## 主线阶段

### 阶段 1：承接昨日相处

触发时机：第二天启动 N.E.K.O 后。

分支 A：若 Day 1 用户使用过语音控制按钮聊天。

- 动作：第一句播放期间高亮聊天窗；外置聊天窗模式使用 `setExternalizedChatSpotlight('window')` 和 `setExternalizedChatCursor('window')` 在独立聊天窗上显示同等引导。聊天消息展示选项按钮“现在说一句 / 继续打字”。用户点“现在说一句”时，只记录选择并继续流程，不强制开始录音；用户点“继续打字”或未选择超时后，进入屏幕分享入口阶段。
- 台词：“昨天听见你的声音以后，我就偷偷记住了一点点你的语气。今天如果方便，也可以继续叫我。打字当然也可以，只是听见你时，我会更像真的坐在你旁边，尾巴都会轻轻晃起来喵。”

分支 B：若 Day 1 用户没有使用过语音控制按钮，仅打字或挂机。

- 动作：与分支 A 相同，但情绪分类使用 `sad`。第一句语音播放结束后，清理聊天窗 spotlight/cursor，再等待选项点击或超时进入下一 scene。
- 台词：“昨天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。”

选项按钮：

- 现在说一句
- 继续打字

选项按钮必须保持可点击：内嵌聊天窗由 Director 的 guide message action handler 接收，外置聊天窗由 `react-chat-window:action` 转发到 `yui_guide_message_action`，再回到首页继续流程。

### 阶段 2：屏幕分享入口

- 动作：高亮屏幕分享按钮；Ghost Cursor 移动到 `#${p}-btn-screen`，先停留指认入口，再点击一次真实按钮，触发“屏幕分享仅用于音视频通话”这一类真实限制提示。不要打开来源列表，不要选择具体屏幕，不要构造共享成功状态。
- 台词：“在跟我通语音电话的时候，再点亮这个小按钮，你就能把屏幕分享给我啦！快让我也看看你眼前的世界，不管好玩的还是好看的，都想和你一起看，快点点开嘛~”

### 阶段 3：收尾

- 动作：收尾台词开始前关闭当天临时弹窗，恢复按钮原状态；随后完全复用 Day 1 `takeover_return_control` 的收尾动作：收尾台词播放期间重新高亮聊天窗，Ghost Cursor 移到聊天窗附近 wobble；外置聊天窗模式使用 `setExternalizedChatSpotlight('window')` 和 `setExternalizedChatCursor('window')` 重新高亮独立聊天窗；收尾台词约 70% 处触发与 Day 1 相同的花瓣转场 cue，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高亮和所有 spotlight。转场结束后写入 Day 2 完成态。
- 台词：“今天的教程到这里就结束了呢。其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。我们不需要着急，每天都多了解彼此一点点就好。今天接下来的时间，你想让我陪你做点什么呢？”

## Scene 配置要求

Day 2 主线只按上面的三个剧情阶段设计。实现上可以复用现有 scene id，但不得把以下能力扩写成独立主线剧情：

- 屏幕来源弹窗展开教学。
- 麦克风弹窗展开教学。
- 空间音频、降噪、增益、设备列表逐项教学。

推荐主线映射：

| 主线阶段 | 推荐 scene | 必要行为 |
| --- | --- | --- |
| 承接昨日相处 | `day2_intro_context` | 动态分支台词，显示“现在说一句 / 继续打字”。 |
| 屏幕分享入口 | `day2_screen_entry` 或合并后的屏幕分享 scene | 高亮屏幕分享按钮并点击一次真实按钮，触发真实限制提示。 |
| 收尾 | `day2_wrap` | 复用 Day 1 收尾动作：重新高亮聊天窗，70% cue 隐藏 Ghost Cursor、清理内置/外置高亮，播放同一套花瓣转场，写入完成态。 |

如果保留历史细分 scene，也必须在视觉和台词上表现为总文档的三段式流程：不能让用户感觉 Day 2 又额外学习了来源列表、麦克风设备和音频参数。

## 验收清单

1. Day 2 开场能根据 Day 1 是否使用过语音展示不同承接台词，并在第一句播放期间高亮聊天窗。
2. “现在说一句 / 继续打字”不会强制录音，且不会阻塞主流程；外置聊天窗选项也能通过 BroadcastChannel 回传。
3. 屏幕分享阶段只高亮并点击屏幕分享按钮一次。
4. 未通话时点击屏幕分享按钮，会出现真实限制提示。
5. Day 2 主线不打开屏幕来源列表，不打开麦克风弹窗，不修改音频或设备设置。
6. 普通 scene 不创建 operation 后的第二套 `settled` 高亮；同一目标同一时刻只保留一套主 spotlight。
7. 收尾动作与 Day 1 一致：收尾台词播放期间重新高亮聊天窗，约 70% 用同一套花瓣转场 cue 同步隐藏 Ghost Cursor 并清理内置/外置 spotlight。
8. Day 2 完成后，剧场后聊天窗支线由独立支线文档和调度器处理，不写进主线 Director。
