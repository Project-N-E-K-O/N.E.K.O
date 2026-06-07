# Day 2 个性化、声音与主动搭话教程开发文档

本文对齐当前前三天新手教程主线。Day 2 不再演示屏幕分享入口；屏幕分享入口如需保留，应放在 Day 1 主线或后续支线中处理。Day 2 的首句承接台词保持不变，后续直接演示设置入口、设置侧边栏和主动搭话入口，最后三句收尾。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-7day-complete-guide-dev.md`
- `docs/design/avatar-floating-pc-global-overlay-migration-plan.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 完整指南对齐基线

Day 2 的 scene 和台词按本文落地；导演硬约束以 `avatar-floating-7day-complete-guide-dev.md` 为准：

1. Day 2 必须使用统一教程生命周期，不在本日 scene 中重写接管、skip、打断、临时切模或高光清理。
2. `day2_intro_context` 的文案、text key 和 voice key 保持原样；只播放承接台词，不恢复“现在说一句 / 继续打字”选项，也不补高亮语音按钮。
3. 设置入口、设置侧边栏和主动搭话入口都必须写清楚真实目标、Ghost Cursor 移动方式、是否点击和是否修改配置；不得只写“沿用某日流程”。
4. 设置相关 scene 不保存用户设置，不打开深层页面，不触发主动搭话。
5. 收尾三句期间重新高亮胶囊输入框；最终句约 70% cue 同步隐藏 Ghost Cursor、清理所有高光并写入 Day 2 完成态。
6. 本日启用 `avatar-floating-7day-complete-guide-dev.md` 中的 Day 2-7 模型替身图片演出：模型可在教程期间临时隐藏 5 秒，并通过全局透明 overlay 显示替身图片。本 round 固定在 `day2_intro_context` 播放“昨天你一直在噼里啪啦打字……”时显示 `探头.png`，以及在 `day2_proactive_chat` 播放“这个小按钮也很重要哦……”时显示 `扒右边框.png`；结束或异常清理时必须恢复模型。

## 主线流程

| 顺序 | scene | 台词 | 高光与 Ghost Cursor |
| --- | --- | --- | --- |
| 1 | `day2_intro_context` | 昨天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。 | 播放期间高亮聊天窗；外置聊天窗模式使用 `window` kind。Ghost Cursor 移到聊天窗中心或输入区附近并停留，不左右晃动。台词结束后清理聊天窗高光，保留上一段可见 cursor 锚点，供下一句平滑移动使用。 |
| 2 | `day2_personalization_space` | 在这个只属于我们的小空间里，你可以由着自己的心意，慢慢描绘出最希望能一直陪着你的那个我。 | 收起前一段聊天窗高光后，圆形高亮设置按钮 `#${p}-btn-settings`。Ghost Cursor 从聊天窗锚点平滑移动到设置按钮并停留；到达打开设置的 cue 时播放点击动画，同时调用设置面板打开 API。设置弹窗出现后清理设置按钮主高光，等待面板稳定；本句不展开【角色设置】按钮侧边栏。 |
| 3 | `day2_personalization_detail` | 不管是说话的温度、相处的小脾气，还是我每天那些细腻的小心思，都可以一点一点调成你喜欢的样子。 | 圆角矩形高亮【角色设置】按钮；Ghost Cursor 平滑移动到【角色设置】按钮，播放完整模拟点击动画，点击动画完成后才触发【角色设置】按钮侧边栏显示。侧边栏出现后，圆角矩形高亮从【角色设置】按钮过渡到【角色设置】按钮侧边栏，且【角色设置】按钮自身作为 persistent 高光继续保留；Ghost Cursor 平滑移动到侧边栏，并在侧边栏内做椭圆运动直到本句台词播放完毕；本句播放完后隐藏【角色设置】按钮侧边栏，并同步清理【角色设置】按钮和侧边栏上的所有高光。不保存临时配置。 |
| 4 | `day2_proactive_chat` | 这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦。 | primary 平滑切到主动搭话开关 `#${p}-toggle-proactive-chat` 本体，不再保留【角色设置】按钮 persistent 高光。Ghost Cursor 平滑移动到该开关并停留指认，不左右晃动；不点击，不打开 `interval-proactive-chat` 侧边栏，不改变用户配置。台词播放完后清理主动搭话开关高光，并关闭教程临时打开的【设置】面板和设置侧边栏。 |
| 5 | `day2_wrap_intro` | 今天的教程到这里就结束了呢。 | 收尾开始前关闭临时面板，恢复按钮原状态；随后圆角矩形高亮胶囊输入框 `chat-input`。Ghost Cursor 从上一句主动搭话开关位置平滑移动回胶囊输入框中间并停留，不左右晃动。 |
| 6 | `day2_wrap_companion` | 其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。 | 继续圆角矩形高亮胶囊输入框；Ghost Cursor 保持在胶囊输入框附近，不左右晃动，不触发花瓣。 |
| 7 | `day2_wrap` | 我们不需要着急，每天都多了解彼此一点点就好。今天接下来的时间，你想让我陪你做点什么呢？ | 继续圆角矩形高亮胶囊输入框；台词约 70% cue 同步启动花瓣层、隐藏 Ghost Cursor、清理内置/外置胶囊输入框高光和所有 spotlight，随后写入 Day 2 完成态。 |

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
- 设置与主动搭话目标：
  - `#${p}-btn-settings`
  - 设置弹窗侧边栏容器 / `[data-neko-sidepanel-type]`
  - `#${p}-toggle-proactive-chat`
- 外置聊天窗同步：
  - `TutorialInteractionTakeover.setExternalizedChatSpotlight()`
  - `TutorialInteractionTakeover.setExternalizedChatCursor()`

### Cursor anchor 保持与复用

外置聊天窗 / PC 全局 overlay 模式下，Day 2 需要保存并复用 Ghost Cursor 的目标锚点，避免跨 scene 或跨窗口切换时从默认点硬跳。建议入口 API：

- `saveCursorAnchor(anchor)`：在播放结束、目标移动完成或收到 settled anchor 后保存 `{ sceneId, kind, x, y, settled, updatedAt }`。
- `readCursorAnchor(sceneId)`：scene 进入时优先读取同 scene / 同 kind 的未过期 anchor；可用时平滑移动到该 anchor，不可用时再走默认目标解析和 fallback jump。
- `invalidateCursorAnchor(sceneId)`：目标 DOM 消失、窗口关闭、尺寸变化过大或教程结束/跳过/生气退出时失效对应 anchor。
- `syncAnchorAcrossWindows(windowId, anchor)`：外置聊天窗回传 anchor 后同步到首页和 PC overlay；窗口 id 不匹配或 anchor 过期时丢弃。

锚点必须有过期时间；外置窗口传播时只传坐标、目标 kind、sceneId 和 settled 状态，不携带点击 effect。播放结束保存，下一 scene 进入先读；读不到或目标不可信时使用当前默认目标重新解析。

## 约束

1. Day 2 不再包含 `day2_screen_entry` / `day2_screen_entry_invite`。
2. 首句承接台词不变，不等待用户选择，不展示选项按钮。
3. 设置入口只允许打开教程临时设置面板；设置侧边栏只展示区域，不点击或保存配置。
4. 主动搭话开关只展示入口，不点击、不打开子侧栏、不改变开关状态。
5. 收尾前必须关闭 Day 2 临时打开的设置面板和侧边栏；收尾三句必须圆角矩形高亮胶囊输入框；最终句 cue 必须同步清理高光、Ghost Cursor 和外置聊天窗状态。
6. 外置聊天窗 / PC 全局 overlay 模式下，设置按钮和主动搭话开关的高光与 Ghost Cursor 必须由同一个 overlay 状态包同时携带；cursor 移动、spotlight refresh 或目标矩形重算都不得只发送其中一半，避免远端渲染层交替清空高亮或光标造成闪烁。
7. 模型替身图片演出不能在每日最后一句 `day2_wrap` 播放期间出现；进入 `day2_wrap` 前如果替身仍在显示，必须立即清理替身并恢复模型。替身必须由全局透明 overlay 与高光、Ghost Cursor、花瓣一起携带完整可见状态，不能遮挡 skip、设置按钮、主动搭话开关或胶囊输入框高光；skip、destroy、angry exit 和收尾花瓣 cue 必须立即清理替身并恢复模型。

## 验收清单

1. Day 2 scene 顺序与本文一致。
2. `day2_intro_context` 文案、text key 和 voice key 保持原样。
3. 设置按钮、设置侧边栏、主动搭话入口都有明确高光目标、Ghost Cursor 路径和禁止点击/保存约束。
4. Day 2 主线不触发屏幕分享、主动搭话或用户配置保存。
5. Day 2 主线普通指认统一使用平滑移动和停留。
6. Day 2 完成、skip、destroy 和 angry exit 的结束态语义不变。
7. Day 2 单轮模型替身图片固定出现 2 次：`day2_intro_context` 使用 `探头.png`，`day2_proactive_chat` 使用 `扒右边框.png`；每次 5 秒后恢复模型，且不得出现在 `day2_wrap` 最后一句台词播放期间；替身层不拦截点击、不影响 Ghost Cursor 锚点和高光。
