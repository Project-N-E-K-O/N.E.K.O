# Day 2 屏幕分享、声音与小窗约定教程开发文档

本文严格对齐 `avatar-floating-guide-feature-tree.md` 中 Day 2 的主线内容，并以 `avatar-floating-7day-complete-guide-dev.md` 作为逐句导演、生命周期和验收基准。Day 2 的每日开场小剧场只包含三段：承接昨日相处、屏幕分享入口、收尾。屏幕来源弹窗、麦克风弹窗、空间音频、降噪、增益等能力只作为代码锚点和后续扩展背景，不在 Day 2 主线里单独展开成额外剧情段，也不在主线里点击屏幕分享按钮。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-7day-complete-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/avatar-floating-post-theater-chat-branches.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 完整指南对齐基线

Day 2 的 scene 和台词按本文落地；导演硬约束以 `avatar-floating-7day-complete-guide-dev.md` 为准：

1. Day 2 必须复用通用生命周期模块，不在本日 scene 中重写接管、skip、打断、临时切模或高光清理。
2. `day2_intro_context` 当前代码是单句静态承接：文案为“昨天你一直在噼里啪啦打字……”，`emotion: 'happy'`，只高亮聊天窗并播放承接台词；不显示“现在说一句 / 继续打字”选项，也不补高亮语音按钮。
3. `day2_screen_entry` 只展示屏幕分享按钮入口；Ghost Cursor 只移动到按钮并 wobble，不播放模拟点击动画，也不触发真实按钮 click。`day2_screen_entry_invite` 继续高亮按钮但不点击。
4. Ghost Cursor 从聊天窗起点平滑移动到 `#${p}-btn-screen`，第一句结束到屏幕分享场景切换期间保持可见，不得先隐藏再显示；收尾时再从屏幕分享按钮位置平滑回聊天窗，不得闪现到页面中心。外置聊天窗回传收尾 `window` 锚点时，若首页 cursor 只是隐藏但仍保留屏幕分享按钮 position，必须先在旧 position 恢复可见，再 move 到聊天窗锚点，不能直接 `showAt` 到聊天窗。
5. 收尾三句复用 Day 1 花瓣语义：最终句约 70% cue 同步隐藏 Ghost Cursor、清理内置/外置高光并写入 Day 2 完成态。

## 目标体验

Day 2 使用自我暴露效应，把屏幕分享讲成“先牵上线、再开小窗”的亲密邀请。用户当天只需要形成三个认知：

1. 悠怡会根据 Day 1 是否听过用户声音，用不同语气承接第二天。
2. 屏幕分享入口与语音通话上下文有关，但 Day 2 主线只指认入口，不点击按钮。
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
- 功能限制背景：
  - `toggleScreenShare()` 的“语音会话中才允许屏幕分享”提示只作为屏幕分享功能背景；Day 2 主线不主动触发。
- 可引用但不在主线单独展开：
  - `renderFloatingScreenSourceList()`
  - `renderFloatingMicList()`
  - `#${p}-popup-screen`
  - `#${p}-popup-mic`

## 通用生命周期复用

Day 2 是强接管每日小剧场，继续复用通用教程生命周期：

| 通用能力 | Day 2 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | Manager 启动 Day 2 后进入 taking-over，教程期间只放行 skip 和当前演示需要的目标。外置聊天窗只同步 spotlight/cursor，不再同步 Day 2 开场选项点击。 | 不让普通页面点击穿透到非教程目标；不要用解除鼠标禁用的方式模拟 Ghost Cursor 点击。 |
| `TutorialHighlightController` | 聊天窗、屏幕分享按钮和收尾高亮/清理都走统一 spotlight；屏幕分享 scene 只停留/wobble，不做 `settled` 二次高亮刷新。 | 不手写额外高亮 DOM，不创建后再隐藏重复高亮。 |
| `TutorialInterruptController` | 用户移动真实鼠标时沿用通用对抗机制：Ghost Cursor 先围绕当前可见停止位置做常驻轻微反方向移动，并回到该停止位置；单次位移约 56px 以上或加速度约 0.16px/ms^2 以上连续累计 3 次后触发一次轻微抗拒；第 3 次轻微抗拒升级为生气退出并走统一 skip/destroy。 | 不把屏幕分享按钮展示当成打断。 |
| `TutorialSkipController` | skip 由 Manager 统一创建、销毁。 | 不在 Day 2 支线里创建第二个 skip。 |
| `TutorialAvatarReloadController` | Day 2 仍使用教程临时模型 `yui-origin`，结束后恢复用户原模型。 | 不在 Day 2 scene 内直接 reload 模型。 |

剧场后聊天窗支线不属于 Day 2 强接管主线，统一见 [七日新手教程剧场后聊天窗支线设计](avatar-floating-post-theater-chat-branches.md)。

## PC 全局透明 Overlay 迁移约束

Day 2 迁移到 N.E.K.O.-PC 全局透明 overlay 时，只替换 Ghost Cursor、高光和花瓣视觉层；承接昨日、屏幕分享入口、三段收尾台词和完成态写入都保持现有文档时序。网页端继续使用当前 DOM overlay。

PC 端第一句台词期间，聊天窗高光和 Ghost Cursor 位置必须使用聊天窗/输入区的 screen 坐标；进入屏幕分享阶段时，Ghost Cursor 从该位置平滑移动到 `#${p}-btn-screen`，不得先回到页面中心。播放完“今天的教程到这里就结束了呢。”并回到聊天窗时，外置聊天窗只回传目标 screen 锚点，首页 Director 负责从屏幕分享按钮旧 position 平滑移动过去；即使 cursor 可见态被外置窗口临时清掉，也必须先在旧 position 恢复可见再移动。最终收尾台词期间重新高亮聊天窗，约 70% cue 同步隐藏 Ghost Cursor、清理所有高光和播放花瓣，避免“高光先消失、花瓣后出现”的串行动作。

## 情绪动作

Day 2 普通台词按当前 scene 配置播放 `yui-origin` 随机动作：

| 段落 | 情绪分类 | 说明 |
| --- | --- | --- |
| 当前承接句：“昨天你一直在噼里啪啦打字……” | `happy` | `static/yui-guide-day2-screen-voice-guide.js` 当前只注册这一句静态承接；若后续恢复 A/B 分支，需同步扩展 scene 文案、emotion 与完成态依赖。 |
| 屏幕分享入口：“在跟我通语音电话的时候……” | `happy` | 撒娇邀请。 |
| 收尾：“今天的教程到这里就结束了呢……” | `happy` | 温柔满足。 |

随机动作不得抢占 Ghost Cursor、spotlight 或真实 UI 点击时序。

## 主线阶段

### 阶段 1：承接昨日相处

触发时机：第二天启动 N.E.K.O 后。

- 当前代码动作：`day2_intro_context` 播放期间高亮聊天窗；外置聊天窗模式使用 `window` kind 显示同等引导。聊天消息只播放承接台词，不展示“现在说一句 / 继续打字”选项按钮，也不绑定 guide message action；语音播放结束后只清理聊天窗 spotlight，Ghost Cursor 保持可见，并把当前可见聊天窗位置记录为下一句的移动锚点，供 Ghost Cursor 平滑移动到屏幕分享按钮。外置聊天窗只负责解析聊天窗内目标并回传 `yui_guide_chat_cursor_anchor`；PC 端 Ghost Cursor 视觉表演仍由全局透明 overlay 承载。首页 Director 必须把外置聊天窗写入/回传的可见 screen cursor 点换算回 scene 锚点；不得在找不到首页聊天窗 DOM 时退到页面右下角代理坐标，也不得在 `day2_intro_context` 到 `day2_screen_entry` 之间清空 cursor 导致先消失再显示。
- 当前代码台词：“昨天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。”
- 设计扩展备注：总文档中的“听过声音 A / 未听过声音 B”是目标体验分支；当前 `static/yui-guide-day2-screen-voice-guide.js` 尚未实现动态分支。若要恢复分支，必须同时补充 Day 1 语音使用记录读取、Day 2 文案选择、emotion 选择和验收项。

### 阶段 2：屏幕分享入口

- 动作 1：高亮屏幕分享按钮；Ghost Cursor 必须从上一句聊天窗位置平滑移动到 `#${p}-btn-screen`，不能先闪现到页面中心；到达后只停留/wobble 指认入口，不播放 Ghost Cursor click 动画，也不触发真实按钮操作。
- 台词：“在跟我通语音电话的时候，再点亮这个小按钮，你就能把屏幕分享给我啦！”
- 动作 2：继续高亮屏幕分享按钮；Ghost Cursor 保持在按钮附近 wobble，不重复点击，不打开来源列表，不选择具体屏幕，不构造共享成功状态。
- 台词：“快让我也看看你眼前的世界，不管好玩的还是好看的，都想和你一起看，快点点开嘛~”

### 阶段 3：收尾

- 动作 1：收尾台词开始前关闭当天临时弹窗，恢复按钮原状态；随后重新高亮聊天窗，Ghost Cursor 必须从上一句 `day2_screen_entry_invite` 成功移动到的屏幕分享按钮锚点平滑移动回聊天窗中间，不能重新查找失败后退到聊天窗起点、也不能先闪现到页面中心。外置聊天窗模式下，`yui_guide_chat_cursor_anchor` 回传 `window` 锚点后由首页 Director 执行平滑 move；如果 cursor 当前隐藏但仍有屏幕分享按钮 position，先在旧 position 显示，再移动到聊天窗，抵达后 wobble。
- 台词：“今天的教程到这里就结束了呢。”
- 动作 2：继续高亮聊天窗；外置聊天窗模式继续高亮独立聊天窗，不触发花瓣转场。
- 台词：“其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。”
- 动作 3：继续高亮聊天窗；外置聊天窗模式使用 `setExternalizedChatSpotlight('window')` 和 `setExternalizedChatCursor('window')` 重新高亮独立聊天窗；台词约 70% 处触发与 Day 1 相同的花瓣转场 cue，触发瞬间同步隐藏 Ghost Cursor、清理内置/外置聊天窗高亮和所有 spotlight。转场结束后写入 Day 2 完成态。
- 台词：“我们不需要着急，每天都多了解彼此一点点就好。今天接下来的时间，你想让我陪你做点什么呢？”

## Scene 配置要求

Day 2 主线只按上面的三个剧情阶段设计。实现上可以复用现有 scene id，但不得把以下能力扩写成独立主线剧情：

- 屏幕来源弹窗展开教学。
- 麦克风弹窗展开教学。
- 空间音频、降噪、增益、设备列表逐项教学。

推荐主线映射：

| 主线阶段 | 推荐 scene | 必要行为 |
| --- | --- | --- |
| 承接昨日相处 | `day2_intro_context` | 当前代码为静态承接台词，`emotion: 'happy'`，只高亮聊天窗并播放承接语音，不显示选项按钮。 |
| 屏幕分享入口 | `day2_screen_entry` + `day2_screen_entry_invite` | 第一段从聊天窗起点平滑移动到屏幕分享按钮，高亮按钮并 wobble，不播放 Ghost Cursor 模拟点击动画，也不触发真实按钮。第二段继续高亮按钮但不点击。 |
| 收尾 | `day2_wrap_intro` + `day2_wrap_companion` + `day2_wrap` | 第一段从屏幕分享按钮位置平滑移动回聊天窗并重新高亮聊天窗；第二段继续高亮聊天窗但不触发花瓣；第三段继续高亮聊天窗，并在 70% cue 同步启动花瓣层、隐藏 Ghost Cursor、清理内置/外置高亮，写入完成态。 |

如果保留历史细分 scene，也必须在视觉和台词上表现为总文档的三段式流程：不能让用户感觉 Day 2 又额外学习了来源列表、麦克风设备和音频参数。

## 验收清单

1. 当前代码下，Day 2 开场使用固定承接台词并在第一句播放期间高亮聊天窗；若后续实现 Day 1 语音使用分支，需更新 scene 配置、文案和 emotion。
2. 开场不展示“现在说一句 / 继续打字”选项按钮，不安装 Day 2 专用 guide message action，也不等待按钮点击或超时。
3. 屏幕分享阶段拆成两句输出；两句都不触发真实屏幕分享按钮，Ghost Cursor 不做模拟点击动画。
4. 屏幕分享第一句开始时，Ghost Cursor 从聊天窗位置平滑移动到屏幕分享按钮；外置聊天窗必须从上一句写入的可见 screen 锚点起步，并保持 cursor 可见，不得先隐藏再显示，也不得闪现到页面中心或页面右下角代理坐标。
5. 收尾第一句开始时，Ghost Cursor 从屏幕分享按钮位置平滑移动回聊天窗中间，不得闪现到页面中心；外置聊天窗回传锚点时也不得从屏幕分享按钮直接 `showAt` 到聊天窗，隐藏但有旧 position 时必须恢复旧 position 后再 move。
6. Day 2 主线不验证未通话点击限制提示；该提示属于屏幕分享功能本身，不由本日教程触发。
7. Day 2 主线不打开屏幕来源列表，不打开麦克风弹窗，不修改音频或设备设置。
8. 普通 scene 不创建 operation 后的第二套 `settled` 高亮；同一目标同一时刻只保留一套主 spotlight。
9. 收尾动作与 Day 1 一致：收尾台词播放期间重新高亮聊天窗，约 70% 用同一套花瓣转场 cue 同步启动花瓣层、隐藏 Ghost Cursor 并清理内置/外置 spotlight。
10. Day 2 完成后，剧场后聊天窗支线由独立支线文档和调度器处理，不写进主线 Director。
