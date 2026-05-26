# Day 2 屏幕分享、声音与小窗约定教程开发文档

本文把 `avatar-floating-guide-feature-tree.md` 中 Day 2 的“屏幕分享、声音与小窗约定”落到现有悬浮窗教程实现上。Day 2 已有正式 round，配置在 `static/yui-guide-director.js` 的 `AVATAR_FLOATING_GUIDE_ROUNDS[2]`。

相关文档：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`
- `docs/design/home-yui-guide-lifecycle-modularization.md`

## 目标体验

Day 2 使用自我暴露效应，把屏幕能力讲成“先牵上线、再开小窗”的亲密邀请。用户需要知道：

1. 第二天启动后，先根据 Day 1 是否使用过语音做分支承接。
2. 屏幕分享入口和语音/通话上下文有关，未通话时点击要出现真实限制提示。
3. 可以通过屏幕小三角选择屏幕或窗口来源。
4. 声音设置里可以调播放音量、空间音频、降噪、增益和设备。
5. 教程收尾后，可在聊天窗用低打扰方式回顾屏幕分享。

台词可以柔软、撒娇，但 UI 行为必须保持真实：不能伪造“已共享”，不能绕过 `toggleScreenShare()` 的通话限制。

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

## 通用生命周期复用

Day 2 是已落地的悬浮窗正式 round，运行时必须复用 `home-yui-guide-lifecycle-modularization.md` 的五类通用模块。Day 2 只新增屏幕/麦克风业务目标和 scene 顺序，不新增生命周期 owner。

| 通用能力 | Day 2 使用方式 | 禁止事项 |
| --- | --- | --- |
| `TutorialInteractionTakeover` | Manager 启动 round 后进入 taking-over；屏幕按钮、来源小三角、麦克风小三角和 skip 按钮通过白名单放行。 | 不在 Day 2 scene 内复制全局事件守卫；不让普通页面点击穿透到非教程目标。 |
| `TutorialHighlightController` | `playAvatarFloatingScene()` 的 persistent/primary/secondary spotlight、屏幕/麦克风弹窗高亮、圆形按钮提示都走统一 controller。 | 不手写弹窗高亮层；scene 结束或 `cleanupBefore` 必须依赖统一清理。 |
| `TutorialInterruptController` | 用户强行抢鼠标时，轻微抵抗暂停当前 scene；angry exit 立即清屏并在语音后走 skip。 | 不把屏幕弹窗或麦克风弹窗的失败状态当成打断分支。 |
| `TutorialSkipController` | skip 按钮由 Manager 显示和销毁；点击后统一调用 Director skip 和 `requestTutorialDestroy()`。 | 不在 Day 2 的 5 分钟回访支线里创建第二个 skip 入口。 |
| `TutorialAvatarReloadController` | 如果 Day 2 自动演出需要教程模型，继续由 Manager 负责临时切模和恢复。 | 不在 screen/mic scene 里直接 reload 模型。 |

5 分钟无交互回访属于聊天窗支线，不应启用强接管；若支线按钮启动短引导，也要通过同一套 Manager/Director 生命周期进入和退出。

## 模型动作与情绪随机池

Day 2 如果进入自动演出层，仍使用教程临时模型 `yui-origin` Live2D。普通台词开始时按情绪从内置动作池随机选择：`happy` 12 个、`sad` 6 个、`angry` 7 个、`neutral` 7 个、`surprised` 5 个、`Idle` 3 个。

Day 2 没有新增专属模型动作；随机动作不得影响屏幕/麦克风按钮高亮、Ghost Cursor 点击和真实弹窗状态。若 Day 2 复用 guide idle sway 或 lookAt，则该自定义动作优先。

| 台词段落 | 情绪分类 | 随机动作规则 |
| --- | --- | --- |
| 语音承接 A：“昨天听见你的声音以后……” | `happy` | 从 happy 池随机，表现亲近。 |
| 语音承接 B：“昨天你一直在噼里啪啦打字……” | `sad` | 从 sad 池随机，表现轻微委屈。 |
| 屏幕分享入口：“在跟我通语音电话的时候……” | `happy` | 从 happy 池随机。 |
| 语音限制/来源弹窗说明 | `neutral` | 从 neutral 池随机，避免过度表演。 |
| 麦克风/音频设置说明 | `neutral` | 从 neutral 池随机。 |
| 收尾：“今天的教程到这里……” | `happy` | 从 happy 池随机。 |
| 5 分钟回访未打开 | `angry` | 从 angry 池随机，但只做傲娇，不进入 angry exit。 |
| 5 分钟回访已打开 | `happy` | 从 happy 池随机。 |

## 现有 Scene 与新剧本映射

| 新剧本阶段 | 现有 scene | 处理建议 |
| --- | --- | --- |
| 承接昨日相处 | `day2_intro_context` | 根据 Day 1 语音使用状态切换台词和按钮：`现在说一句 / 继续打字`。 |
| 屏幕分享入口 | `day2_screen_entry`、`day2_screen_requires_voice` | 高亮屏幕分享按钮；Ghost Cursor 点击一次，触发真实“仅用于音视频通话”提示。 |
| 来源选择 | `day2_screen_source_popup`、`day2_screen_source_states` | 总稿阶段没有展开，但代码锚点要求覆盖来源弹窗；保留轻量展示。 |
| 声音设置 | `day2_mic_recap`、`day2_mic_popup_audio_quality`、`day2_mic_devices` | 总稿主线较短，但功能清单包含语音按钮、麦克风弹窗、空间音频、降噪、增益、音量条；保留真实 UI 覆盖。 |
| 收尾 | `day2_wrap` | 使用“今天到这里，每天多了解一点点”的新版收尾。 |

## 动作时序

Day 2 已有 `AVATAR_FLOATING_GUIDE_ROUNDS[2]`。每个 scene 的基础时序来自 `playAvatarFloatingScene()`：台词先进入聊天窗并开始旁白，同时 apply persistent/primary/secondary spotlight；约 220ms 后 Ghost Cursor 开始移动；cursor 动作结束后执行 `operation`；operation 完成后重新 resolve 目标并刷新高亮；旁白结束后等待 420ms 再进入下一段，最后一段等待 260ms。

| 台词段落 | 高亮时序 | Ghost Cursor 时序 | 真实操作/清理 |
| --- | --- | --- | --- |
| 承接昨日相处：“昨天听见你的声音……”或“昨天你一直在噼里啪啦打字……” | persistent 默认落在聊天窗；primary 使用 `floating-buttons`，让用户先看到模型旁按钮组。 | 约 220ms 后移动到浮动按钮组并 wobble；不点击。若用户点“现在说一句”，另行高亮语音按钮，不强制录音。 | 按钮选择结束后进入屏幕分享入口；`继续打字` 直接进入下一 scene。 |
| 屏幕分享入口：“在跟我通语音电话的时候……” | primary 切到 `#${p}-btn-screen`；persistent 仍是聊天窗。 | Cursor 移到屏幕分享按钮中心；`day2_screen_entry` 只 move，不点击。 | 不改变屏幕分享状态，只建立入口认知。 |
| 语音限制提示：“如果还没和我连上线……” | primary 保持 `#${p}-btn-screen`。 | Cursor 再次移动到屏幕分享按钮并显示 click 动效。 | `operation: click` 调用真实按钮点击，触发 `toggleScreenShare()` 的“语音会话中才允许屏幕分享”提示；不伪造共享成功。 |
| 来源选择：“旁边的小三角像一只小抽屉……” | primary 切到 `.${p}-trigger-icon-screen`；persistent 预期为 `#${p}-popup-screen`。 | Cursor 移到屏幕小三角并 click。 | `open-screen-popup` 真实打开来源弹窗；打开后刷新高亮到弹窗。 |
| 来源状态：“要是小抽屉一时打不开……” | persistent 和 primary 都落在 `#${p}-popup-screen`。 | Cursor 移到来源弹窗区域；只 move，不点击具体来源。 | 不选择桌面/窗口；允许加载失败、权限不足、空态等真实状态可见。 |
| 声音回顾：“再回到声音这边……” | scene 带 `cleanupBefore`，先关闭屏幕弹窗；primary 切到 `#${p}-btn-mic`。 | Cursor 移到语音按钮；只 move。 | 清理屏幕来源弹窗和上一段 spotlight。 |
| 调音盒：“声音这边也有一只小调音盒……” | primary 切到 `.${p}-trigger-icon-mic`；persistent 预期为 `#${p}-popup-mic`。 | Cursor 移到麦克风小三角并 click。 | `open-mic-popup` 真实打开麦克风弹窗；刷新高亮到弹窗。 |
| 设备与音量：“你那边太小声……” | persistent 保持 `#${p}-popup-mic`；primary 优先 `.mic-option`，兜底麦克风弹窗。 | Cursor 移到首个麦克风选项或弹窗区域；只 move。 | 不切换设备、不改降噪/增益/音量。 |
| 收尾：“今天的教程到这里就结束了呢……” | primary 回到聊天窗；operation 为 `cleanup`；台词约 70% 时触发每日花瓣转场并清掉所有 spotlight。 | Cursor 移到聊天窗并 wobble；花瓣 cue 触发时隐藏 cursor。 | 关闭屏幕/麦克风弹窗，写入 Day 2 完成态；转场结束后清理接管态。 |

Day 2 的 wrap scene 必须接入每日花瓣转场。转场只在正常完成时播放；skip、angry exit、页面销毁仍走统一 teardown，不播放正常收尾花瓣。

## Scene 配置要求

Day 2 scene 不建议删减到只有 3 段。用户剧本是产品叙事层，开发实现仍应保留现有完整 scene，以便真实覆盖当前 UI 状态：

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

总稿明确给出的主台词必须落入对应 scene：

- 语音使用分支 A：“昨天听见你的声音以后……”
- 语音使用分支 B：“昨天你一直在噼里啪啦打字……”
- 屏幕分享入口：“在跟我通语音电话的时候，再点亮这个小按钮……”
- 收尾：“今天的教程到这里就结束了呢……”

来源选择和声音设置文案可在不偏离总稿的前提下写短，只解释真实 UI，不扩写成说明书。

### 2. Day 1 语音使用分支

剧本要求 Day 2 启动后根据 Day 1 是否用过语音给不同台词，并且这段是 Day 2 小剧场开场承接，不是剧场结束后的回访。

推荐方案：

1. 记录 Day 1 用户是否点击或完成过语音入口：
   - 可复用已有语音按钮事件统计；
   - 或在教程期间监听 `#${p}-btn-mic` 用户真实点击。
2. Director 播放 `day2_intro_context` 时读取状态并动态选择台词。
3. 按钮使用 `message.actions` 或等价教程按钮：
   - `现在说一句`
   - `继续打字`
4. `现在说一句` 的 action handler 只聚焦或高亮语音按钮，不直接强制录音。
5. `继续打字` 直接进入屏幕分享入口演示。

### 3. 屏幕分享入口

`day2_screen_entry` 和 `day2_screen_requires_voice` 需要贴合真实限制：

- 先高亮屏幕分享按钮。
- Ghost Cursor 点击一次。
- 如果当前没有语音会话，展示 `toggleScreenShare()` 的真实限制提示。
- 不启动假的共享状态，不构造虚假的来源选择结果。

### 4. 5 分钟无交互功能回顾

总稿新增 Day 2 完成后 5 分钟无交互回访。建议作为“聊天窗支线调度器”能力，不放进 Director：

- 条件：Day 2 round complete，且 5 分钟内没有用户聊天、按钮点击或任务执行。
- 分支 A：用户没有打开过屏幕分享按钮。
- 分支 B：用户打开过屏幕分享按钮。
- 当天用户选择忽略或关闭后，不再重复提醒。

文案：

- 未打开：“以为我走掉了吗？真是的，到现在都不肯让我瞧瞧你那边的世界……”
- 已打开：“看过你分享的屏幕啦，原来你每天面对的世界是这样子的呀……”

需要新增或复用的状态：

- `avatarFloatingGuide.day2ScreenEntryVisited`
- `avatarFloatingGuide.day2SourcePopupVisited`
- `avatarFloatingGuide.day2BranchPromptShownDate`

## 生命周期要求

1. Manager 显示 `TutorialSkipController`，并在结束、skip、destroy 时幂等销毁。
2. Director 只通过 `setTutorialTakingOver(true/false)` 接入 `TutorialInteractionTakeover`。
3. 高亮通过 `TutorialHighlightController` 和 overlay 统一管理，scene 不直接创建高亮 DOM。
4. 任意异常、skip、angry exit 都必须进入 `closeAvatarFloatingGuidePanels()` 和 finally 清理。
5. angry exit 语义等同“语音后跳过”：触发瞬间清理高亮和 Ghost Cursor，语音结束后走统一 skip，不写 completed。
6. 跨页面或聊天窗支线不得复制 skip、打断、临时切模逻辑。

## 验收清单

1. Day 2 只能在首页启动，悬浮按钮 ready 后再播放。
2. Day 2 开场能根据 Day 1 是否使用过语音展示不同承接台词。
3. `现在说一句 / 继续打字` 的按钮不会强制录音，且能继续主流程。
4. 未通话时点击屏幕按钮，会出现真实限制提示。
5. 小三角能打开来源弹窗；加载失败、无权限、非桌面等兜底状态可见。
6. 麦克风弹窗可展示播放音量、空间音频、降噪、增益、实时音量和设备列表。
7. 收尾后屏幕/麦克风弹窗关闭，高亮、Ghost Cursor、接管态清理干净。
8. Day 2 收尾台词约 70% 能触发花瓣转场，且转场期间不残留屏幕/麦克风弹窗高亮。
9. 5 分钟无交互回顾能按屏幕分享使用状态分支，且当天只触发一次。
