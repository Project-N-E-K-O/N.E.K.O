# 新手教程 Timeline/Command 重构方案

更新日期：2026-06-15

本文替代旧版阶段流水账文档。旧文档混合了已完成事项、历史问题、临时修复和未来设想，已经不能作为后续重构依据。新的目标是把 7 日新手教程收敛到“时间轴控制”和“模块解耦”两条主线：音频、虚拟鼠标、UI 高亮、花瓣转场、替身演出和业务操作都由统一 timeline 调度，但各自仍由独立 runtime/handler 执行。

## 0. 当前落地状态

2026-06-15 已完成第一批无行为变更的结构落地：

- 新增 `static/tutorial/core/command-registry.js`，提供 timeline command 注册与派发边界。
- 新增 `static/tutorial/core/script-normalizer.js`，把旧 `scene` 字段标准化为 `TimelineScene.timeline[]`。
- 新增 `static/tutorial/core/timeline-engine.js`，提供基于时间点、阻塞 command、pause/resume 和 run token 的调度器基础。
- 新增 `static/tutorial/core/visual-runtime.js`，把 `chat.message`、`emotion.set`、`spotlight.show`、`cursor.*`、`operation.run`、`compactToolWheel.rotateGalgameIntoCenter`、`settingsTour.play`、`settingsPanel.close`、`petal.play` 等 timeline command 转发到现有 Director/controller 边界，不直接创建 DOM 视觉层。
- `static/tutorial/yui-guide/common.js` 已聚合导出 `createTutorialCommandRegistry()`、`normalizeTutorialScene()`、`createTutorialTimelineEngine()`、`createTutorialVisualRuntime()`。
- `templates/index.html`、`templates/chat.html`、`templates/api_key_settings.html`、`templates/memory_browser.html` 已在 `tutorial/yui-guide/common.js` 前加载 timeline/visual runtime 模块。
- `static/tutorial/core/scene-orchestrator.js` 已暴露 `normalizeSceneToTimeline(scene, options)`，作为后续 `playGenericScene` 接入 timeline engine 的低风险落点。
- `static/tutorial/core/scene-orchestrator.js` 已支持显式 `scene.timelinePlayback === true` 的 timeline 播放路径；默认旧 scene 不走新 engine，避免一次性改变 Day 1-7 行为。
- `static/tutorial/yui-guide/days/day7-graduation-guide.js` 的 `day7_memory_review`、`day7_memory_control`、`day7_graduation_wrap` 已作为首批真实 scene 接入 timeline playback；毕业收尾继续复用现有 `playAvatarFloatingPetalTransitionAtCue` 花瓣 cue 清理路径。
- `static/tutorial/yui-guide/days/day2-screen-voice-guide.js` 的 7 个 round scene 已全部接入 timeline playback；其中 `day2_personalization_space` 复用旧 `day2-open-settings-personalization` operation，在 Ghost Cursor click 开始时打开设置面板，`day2_personalization_detail` 通过显式 `timeline[]` 调用 `settingsTour.play`，旁白、角色侧栏巡游和高光清理由 `SettingsTourFlow` 继续控制，`day2_proactive_chat` 通过 `afterAudioEnd` + `settingsPanel.close` 在旁白结束后关闭设置面板并折叠侧栏。
- `static/tutorial/yui-guide/days/day3-interaction-guide.js` 的 7 个 round scene 已全部接入 timeline playback；其中 `day3_galgame_entry` 通过 `compactToolWheel.rotateGalgameIntoCenter` command 复用原 Galgame 弧线旋转演出。
- `static/tutorial/yui-guide/days/day4-companion-guide.js` 的 8 个 round scene 已全部接入 timeline playback；其中 4 个设置巡游 scene 通过显式 `timeline[]` 调用 `settingsTour.play`，旁白、面板巡游、隐私模式收口仍由 `SettingsTourFlow` 控制。
- `static/tutorial/yui-guide/days/day5-personalization-guide.js` 的 4 个 round scene 已全部接入 timeline playback；其中 `day5_character_settings` 与 `day5_character_panic` 通过显式 `timeline[]` 调用 `settingsTour.play`，旁白、Day 5 专用替身图调度、设置面板打开、角色设置侧栏巡游、慌乱演出和高光清理由现有 Director/`SettingsTourFlow` 继续控制。
- `static/tutorial/yui-guide/days/day6-agent-guide.js` 的 8 个 round scene 已全部接入 timeline playback；其中 `day6_agent_status_master` 通过显式 timeline 在旁白启动后立即执行 `day6-plugin-open-agent-panel-flow`，保留旧 operation 内的猫爪高光、cursor click 和 Agent 面板打开时序；`day6_plugin_side_panel` 通过显式 timeline 在旁白启动后立即执行 `day6-plugin-open-management-panel-flow`，保留用户插件侧栏、管理入口高光、管理窗口预览和 preview state；`day6_plugin_dashboard` 通过显式 timeline 在旁白启动后立即执行 `day6-plugin-dashboard-handoff-flow`，跨窗口旁白同步、插件页演出和首页状态恢复仍由旧 handoff operation 承接；`day6_agent_task_hud` 继续复用 `prepareAvatarFloatingScene()` 的 `cleanupBefore` 与 `show-task-hud` 准备逻辑，先清理 Agent 面板再显示真实 HUD。
- `static/app-interpage.js` 已清理 standalone chat 状态请求里的裸 `window.addEventListener` / `document.addEventListener` 旧路径，统一纳入 `ScopedTutorialResources` 清理边界。
- `static/tutorial/core/visual-runtime.js` 的 `cursor.move`、`cursor.click.onStart`、`cursor.wobble`、`operation.run(trigger: afterCursorMove)` 已补齐外置聊天窗 cursor effect 与 settled anchor 等待语义，timeline path 不再只能走本窗口 `moveCursorToElement()`；`settingsTour.play` 已提供 SettingsTourFlow 的 timeline runtime 入口，后续迁设置 scene 时必须显式 timeline，避免 normalizer 自动旁白和 SettingsTourFlow 旁白重复播放；`settingsPanel.close` 提供只关闭设置面板/侧栏、不隐藏 Ghost Cursor 的窄 lifecycle command。
- `static/tutorial/yui-guide/days/day1-home-guide.js` 的 9 个 round scene 已全部接入 timeline playback；其中 `day1_intro_activation` / `day1_intro_greeting` 通过 `day1-intro-activation-flow` / `day1-intro-greeting-flow` operation 复用现有用户点击激活、intro 输入高光和问候旁白流程；首句问候旁白开始时会把外置聊天窗 Ghost Cursor 固定到胶囊输入框，直到后续 scene 显式移动到第一个目标；`day1_intro_basic_voice` 通过 `day1-intro-basic-voice-showcase` operation 复用旧 `runIntroVoiceControlButtonShowcase()`，保留 16% 语音按钮 cue、历史小条 cursor handoff 和 pre-takeover look-at adopt 语义；`day1_takeover_capture_cursor` 通过 `day1-managed-scene:takeover_capture_cursor` operation 复用键鼠控制演出序列，保留 Agent 面板、总开关、键鼠控制开关和收尾 cursor anchor。
- Day 2-7 的每日首个 Avatar Floating 场景由 `SceneOrchestrator` 统一执行首句 cursor prelude：第一句台词开始前先把外置聊天窗 Ghost Cursor 固定到胶囊输入框，并把本地/外置 spotlight 强制指向 `chat-capsule-input` / `capsule-input`，直到该日首个显式 `cursor.move`/目标移动命令接管位置。timeline 场景的 `spotlight.show` 也必须复用该首句规则，不能退回普通 `chat-input`。Day 1 仍由专用 `day1-intro-greeting-flow` 保持同等语义。
- 非首句的 timeline `spotlight.show` 在外置聊天窗模式下也必须在台词开始时同步调用 `setExternalizedChatSpotlight()`，并沿用旧 generic path 的 `persistent` 优先语义；不能等后续 cursor click 或 operation 打开菜单后再补高亮，否则会露出上一句胶囊 spotlight 或出现目标切换闪烁。
- 替身演出排程必须发生在 scene surface 准备完成之后。`SceneOrchestrator` 会把 `day` 透传给 timeline/generic scene，并在 `prepareAvatarFloatingScene()`、清理与面板准备之后调用 `scheduleAvatarStandInForScene(scene, day, sceneRunId)`，避免过早排程被后续 surface cleanup 或 panel setup 清掉。
- PC 透明全局 overlay 必须把 `avatarStandIn` 作为与 `spotlights`、`cursor`、`petal` 同级的独立视觉通道接收、保留和渲染；N.E.K.O 端只负责发送替身资源、位置和时长，真实图片层由 `N.E.K.O.-PC/src/tutorial-global-overlay-service.js` 与 `preload-tutorial-global-overlay.js` 创建。
- 教程聊天消息清理需要尊重当前消息的真实音频时长：`appendGuideChatMessage()` 根据 `voiceKey` 记录当前消息的最短保留时间，`clearGuideChatMessages()` 在保留时间到达前延后清理，避免胶囊聊天框中的本句台词在音频播放结束前消失。
- 外置聊天窗的 `clearExternalizedChatFx()` 只能清 spotlight/cursor/菜单等视觉 FX，不能直接发送 `yui_guide_clear_chat_messages`；教程消息清理必须统一回到 Director 的 `clearGuideChatMessages()`，否则 PC compact 胶囊会在两句台词之间露出 React empty state（如“现在开始跟我聊天吧！”）。
- `appendGuideChatMessage()` 发送到 React/外置聊天窗的初始 `streaming` guide message 必须带非空正文，但不能直接带完整台词；当前规则是首帧显示第一个字符，后续 stream patch 按当前 `voiceKey` 的真实音频时长逐步补齐文本，并在音频时长结束后把消息状态收为 `sent`。在此之前保持 `streaming`，因为 PC compact 胶囊预览只消费当前 streaming 助手消息。
- React compact 胶囊在 `yui-guide-chat-buttons-disabled` 教程锁定期间不得显示普通聊天 empty state 文案；即使 guide message 尚未 append 或被临时清空，也只能保持空预览，不能显示“现在开始跟我聊天吧！”。
- Day 1-7 正式新手教程 scene 不再使用 `interruptible: false` 关闭对抗链路；对抗机制必须贯穿全流程，统一走现有 `enableInterrupts -> ResistanceController -> pause/resume -> angry exit/skip/destroy` 语义，不拆“只做 Ghost Cursor 物理对抗、不触发打断/生气退出”的特殊开关。
- Day 1-7 正式新手教程所有 `audioFilesByKey` 已补齐 `GUIDE_AUDIO_DURATIONS_BY_KEY` 实测时长配置。教程时序、cue、跨窗口 handoff 和旁白结束等待必须根据当前 `voiceKey` 的真实音频时长推进；不得再根据台词文本长度估算旁白时长。漏配 voice key 应由 `static/yui-guide-audio-files.test.cjs` 暴露。

本阶段已把 Day 1-7 正式 `round.scenes` 全部接入 timeline playback；复杂 scene 仍通过 operation command 回调现有 Director/flow/controller 承接真实业务与演出，不在 day 文件里复制 engine 或重写视觉层。下一阶段如果继续拆分，应按风险把这些 operation 内部再逐步拆成更细 command。优先验收旁白启动、聊天消息追加、情绪、高光、cursor、operation、外置聊天窗 anchor、设置面板 lifecycle、替身演出、Agent/HUD 状态恢复和花瓣 cue 的相对时机。

架构原则：Timeline-based Command Pattern 是全局共享框架，不按“每天一套”复制。`TutorialTimelineEngine`、`TutorialCommandRegistry`、`TutorialScriptNormalizer`、`TutorialVisualRuntime` 只有一套；Day 1-7 的脚本只通过 `timelinePlayback: true` 或显式 `timeline[]` 决定某个 scene 是否进入共享 timeline。每天保留差异的是台词、target、operation、cue 和少量专用 flow，不能在 day 文件里复制新的 engine/controller。

## 1. 当前代码现实

现有实现不是从零开始。代码已经拆出了一批可复用模块，但主流程仍偏命令式 async 编排：

- `static/tutorial/yui-guide/director.js`
  - 仍是教程生命周期总入口。
  - 持有语音队列、情绪、PC overlay、视觉控制器、pause/resistance、sceneRunId、termination 等核心状态。
  - 目前也承担了大量时序细节，例如 narration cue、cursor 起点、外置聊天窗 cursor 等。
- `static/tutorial/core/scene-orchestrator.js`
  - 已经承担 round/scene 播放顺序。
  - 但 `prepare narration -> apply spotlight -> start audio -> cursor/operation -> finish` 仍写成固定流程，timeline 事件无法自由组合。
- `static/tutorial/core/operation-registry.js`
  - 已经具备 operation registry 雏形。
  - 但 operation 仍以 scene 的单字段 `operation` 触发，不能表达多个操作在不同音频 cue 上发生。
- `static/tutorial/core/target-geometry-registry.js`
  - 已经把 `chat-capsule-input`、`chat-input`、`chat-tool-toggle` 等语义 target 映射到本地 selector 和外置聊天窗 kind。
  - 这是后续 TargetResolver 的基础。
- `static/tutorial/visual/overlay-renderer.js`
  - PC 全局透明 overlay 的 renderer 和完整状态 store 已存在。
  - `spotlights`、`cursor`、`petal`、`avatarStandIn` 必须保持独立通道，任何 cursor-only patch 都不能隐式清空 spotlight。
- `static/app-interpage.js`
  - 外置聊天窗会解析自身 DOM 几何，并通过 `window.nekoTutorialOverlay` 发送 PC overlay patch。
  - PC 端不应恢复外置聊天窗本地高亮、本地 Ghost Cursor 或本地替身 DOM。
- `static/tutorial/visual/resistance-controllers.js`
  - 对抗机制已经具备 pause/resume 语义。
  - 暂停期间高亮应保持上一帧，Ghost Cursor 播放对抗动画后继续原目标。
- `static/yui-guide-day*-*.js`
  - 七天教程已经是 `scenes[]` 数据结构。
  - 现有字段如 `voiceKey`、`cursorAction`、`operation`、`petalTransition` 是隐式时序，应先通过 normalizer 映射成 timeline command，而不是一次性重写全部 day 文件。

结论：重构不是“推倒重来”，而是把已经分散的时序逻辑收敛成一个可测试的 Timeline Engine，并让现有 controller/registry 成为 command handler。

## 2. 重构目标

### 2.1 核心目标

1. 用音频时钟驱动教程时序。
2. 让 narration、cursor、spotlight、operation、petal、avatarStandIn 在同一条 timeline 上声明。
3. 让 Director 只负责生命周期和业务决策，不直接编排每个视觉细节。
4. 让 PC overlay 成为 PC 端唯一教程视觉层。
5. 支持 pause/resume、skip、angry exit、自然结束的统一取消和资源销毁。
6. 保留现有 scene 数据，通过渐进 normalizer 迁移，避免一次性大改七天脚本。

### 2.2 非目标

- 不把全部 7 日教程一次性改成纯 JSON。
- 不把业务逻辑搬进 PC overlay。
- 不恢复本地 Ghost Cursor、本地聊天窗高亮、本地替身 DOM。
- 不删除花瓣 DOM fallback；它是收尾兜底，不是主视觉层。
- 不让 renderer 自己决定教程下一步。
- 不用一个“完整 overlay payload”覆盖所有视觉通道。

## 3. 目标架构

```text
TutorialDirector
  ├─ TutorialTimelineEngine
  ├─ TutorialScriptNormalizer
  ├─ TutorialCommandRegistry
  │   ├─ AudioRuntime
  │   ├─ VisualRuntime
  │   │   ├─ Spotlight commands
  │   │   ├─ Cursor commands
  │   │   ├─ Petal commands
  │   │   └─ AvatarStandIn commands
  │   ├─ OperationRuntime
  │   ├─ TargetResolver
  │   └─ LifecycleRuntime
  ├─ PauseCoordinator / ResistanceController
  └─ TerminationRouter
```

### 3.1 TutorialDirector

职责：

- 决定是否启动教程、启动哪一天、启动哪一轮。
- 管理教程运行身份：`tutorialRunId`、`roundRunId`、`sceneRunId`。
- 管理 skip、angry exit、自然结束和 PC 透明 BrowserWindow 销毁。
- 维护业务状态：每日完成状态、manual reset pending、用户输入锁、教程接管状态。
- 持有 runtime 依赖并注入给 timeline engine。

不再直接做：

- 不直接写具体 cursor/highlight 演出顺序。
- 不在 scene 播放主流程里硬编码“先高亮、再播放语音、再移动鼠标、再 operation”。
- 不把外置聊天窗几何、PC overlay patch 细节散落到多个 scene 分支。

### 3.2 TutorialTimelineEngine

职责：

- 接收标准化后的 `TimelineScene`。
- 以音频播放快照和已登记的实测音频时长为主时钟；正式 Day 1-7 新手教程不得按台词文本长度估算旁白时长。
- 按 `at` 或 `cue` 触发 command。
- 处理 pause/resume，暂停期间不推进 fallback clock。
- 用 run token 防止旧 scene 的异步回调污染新 scene。
- 支持 command 的阻塞/非阻塞语义。

核心规则：

- `at` 可以是毫秒，也可以是百分比 cue。
- command 默认非阻塞；只有 `blocking: true` 或 handler 明确返回 blocking promise 时才阻塞后续关键节点。
- scene 结束必须同时满足：
  - 所有 blocking command 已 settled。
- narration 已结束，或当前 `voiceKey` 对应的实测音频时长已到达。
  - waitForUserAction 已完成或超时策略已触发。
- engine 只调度 command，不直接读写 DOM。

### 3.3 TutorialScriptNormalizer

职责：

- 把旧 scene 字段映射成 timeline command。
- 保持 day 文件可逐步迁移。
- 统一默认时序，减少 SceneOrchestrator 里的硬编码分支。

旧 scene 示例：

```js
{
    id: 'day1_capsule_drag_hint',
    voiceKey: 'day1_capsule_drag_hint',
    target: 'chat-capsule-input',
    cursorAction: 'wobble'
}
```

标准化后：

```js
{
    id: 'day1_capsule_drag_hint',
    audio: {
        voiceKey: 'day1_capsule_drag_hint',
        textKey: 'tutorial.avatarFloating.day1.capsuleDragHint',
        minDurationMs: 1800
    },
    timeline: [
        { at: 0, command: 'emotion.set', emotion: 'happy' },
        { at: 0, command: 'spotlight.show', target: 'chat-capsule-input' },
        { at: 220, command: 'cursor.move', target: 'chat-capsule-input', durationMs: 760 },
        { cue: 'afterCursorArrive', command: 'cursor.wobble', durationMs: 360 }
    ],
    completion: {
        mode: 'audio'
    }
}
```

### 3.4 TutorialCommandRegistry

职责：

- 注册 command type 到 handler。
- 给 handler 传入统一上下文：`director`、`scene`、`runToken`、`targetResolver`、`visualRuntime`、`audioRuntime`。
- 所有 handler 必须 token-aware，旧 token 返回后不能继续写状态。

建议命令表：

| Command | 处理模块 | 说明 |
| --- | --- | --- |
| `audio.play` | AudioRuntime | 播放旁白并提供时钟快照 |
| `chat.message` | Director/ChatRuntime | 追加教程聊天文本和按钮 |
| `emotion.set` | Director/EmotionBridge | 设置教程表情 |
| `spotlight.show` | VisualRuntime | 根据 target 显示 PC overlay 高亮 |
| `spotlight.clear` | VisualRuntime | 只清指定 spotlight channel |
| `cursor.show` | VisualRuntime | 显示 Ghost Cursor |
| `cursor.move` | VisualRuntime | 移动到 target 或 point |
| `cursor.click` | VisualRuntime | 点击动画，可触发 operation |
| `cursor.wobble` | VisualRuntime | 提示动画 |
| `operation.run` | OperationRuntime | 调用现有 `OperationRegistry` |
| `petal.play` | VisualRuntime | 播放花瓣转场 |
| `avatarStandIn.show` | VisualRuntime | 显示替身演出 |
| `wait.userAction` | Director/EventRuntime | 等用户动作 |
| `lifecycle.cleanup` | LifecycleRuntime | scene/round 清理 |

### 3.5 TargetResolver

职责：

- 输入语义 target，例如 `chat-capsule-input`、`chat-tool-toggle`、`settings-memory-entry`。
- 输出目标几何或外置聊天窗 kind。
- 按优先级解析：
  1. PC overlay external kind。
  2. 本窗口 DOM selector。
  3. 已缓存 anchor。
  4. 明确 fallback group。

要求：

- target 在 command 执行时解析，不在脚本加载时解析。
- 外置聊天窗 target 必须等待 anchor/rect settle，不能提前用旧坐标。
- `chat-capsule-input` 必须保持独立 target，不退化成普通 `chat-input`，除非 fallback 明确触发。
- 解析失败要返回可观测结果，方便测试和日志定位。

### 3.6 VisualRuntime

职责：

- 成为 cursor、spotlight、petal、avatarStandIn 的唯一视觉写入入口。
- PC 端统一写 PC 全局透明 overlay。
- 浏览器调试兜底只允许 spotlight/petal renderer，不允许恢复本地 Ghost Cursor、本地聊天窗高亮、本地替身 DOM。

PC overlay 状态规则：

- `spotlights`、`cursor`、`petal`、`avatarStandIn` 是独立通道。
- patch 只更新显式包含的通道。
- cursor-only patch 不能带空 `spotlights`。
- spotlight-only patch 不能隐式隐藏 cursor。
- petal 播放可以按 scene cleanup 策略清理其它通道，但必须显式声明。
- external chat 和 home 发送 patch 时必须使用同一个 overlay run id。

这条规则直接防止“胶囊输入框高亮在对抗机制触发时闪烁”这类问题：对抗 cursor 动画来自 home，胶囊 spotlight 来自外置聊天窗；两者必须合并，而不是互相覆盖。

### 3.7 AudioRuntime

职责：

- 包装现有 `YuiGuideVoiceQueue`。
- 提供：
  - `play(voiceKey, options)`
  - `capturePlaybackSnapshot()`
  - `waitForCue(voiceKey, cueName)`
  - `getDurationMs(voiceKey, locale)`
- Timeline Engine 优先使用真实播放 `currentTimeMs`。
- 正式 Day 1-7 新手教程缺少播放快照时，只能使用 `GUIDE_AUDIO_DURATIONS_BY_KEY[voiceKey][locale]` 里的实测音频时长；不能回退到文本长度估算。
- fallback clock 必须扣除 pause/resistance 的暂停时间。
- 胶囊聊天框消息的可见生命周期也必须以当前 `voiceKey` 的实测音频时长为下限；除跳过、退出、销毁等终止路径外，不得在音频结束前清掉本句台词。
- 外置聊天窗 FX 生命周期与聊天消息生命周期分离：清理 cursor/spotlight/tool fan/history 不等于清理 guide message。
- guide message 初始渲染不能出现空正文状态；PC compact 胶囊没有正文时会显示 React empty state，占位文案会被用户误认为教程台词。但初始正文也不能直接给完整台词，应以首字符兜底，再按真实音频时长流式推进。
- React compact 胶囊本身还必须提供兜底：教程锁定 class 存在时禁用普通 empty state fallback，避免 bridge 抖动或跨窗口延迟时露出默认占位文案。

### 3.8 Pause / Resistance

职责仍由 `PauseCoordinator` 与 `ResistanceController` 承担，但需要对 timeline engine 暴露统一 pause token。

规则：

- 对抗开始时，timeline 进入 paused。
- spotlight 保持上一帧，不重算、不清空。
- Ghost Cursor 播放对抗动画属于 side-channel command，不推进原 scene timeline。
- 对抗结束后，原 cursor command 从暂停点继续。
- 被打断的 narration 可以恢复或重新调度，但恢复时必须保留 spotlight。
- angry exit 必须等待生气音频/演出结束，再进入 skip/destroy。

## 4. 数据结构

推荐先用 JS object schema，而不是立即改成外部 JSON。这样可以继续复用现有 day 文件、i18n key、helper function 和测试。

```js
{
    id: 'day1_takeover_return_control',
    audio: {
        voiceKey: 'takeover_return_control',
        textKey: 'tutorial.avatarFloating.day1.takeoverReturnControl',
        minDurationMs: 1800
    },
    targets: {
        primary: 'chat-capsule-input',
        cursorStart: { anchorFromScene: 'day1_takeover_capture_cursor' }
    },
    timeline: [
        { at: 0, command: 'chat.message' },
        { at: 0, command: 'emotion.set', emotion: 'relieved' },
        { at: 0, command: 'spotlight.show', target: 'chat-capsule-input', channel: 'primary' },
        { at: 220, command: 'cursor.move', target: 'chat-capsule-input', durationMs: 760 },
        {
            cue: 'cursorClick',
            command: 'cursor.click',
            target: 'chat-capsule-input',
            onStart: [
                { command: 'operation.run', operation: 'cleanup' }
            ]
        },
        {
            cue: 'returnPetal',
            command: 'petal.play',
            clear: ['cursor', 'spotlights'],
            blocking: true
        }
    ],
    completion: {
        mode: 'audio-and-blocking-commands',
        afterSceneDelayMs: 420
    },
    cleanup: {
        clearCursor: true,
        clearSpotlights: true,
        clearAvatarStandIn: true
    }
}
```

字段约定：

- `id`：稳定 scene id，用于日志、测试、run token。
- `audio`：旁白和音频时钟配置。
- `targets`：命名 target，方便多个 command 复用。
- `timeline[]`：按时间触发的 command。
- `completion`：scene 结束条件。
- `cleanup`：scene 或 round 结束后的显式资源策略。

## 5. 与现有代码的落地映射

### 5.1 新增文件

- `static/tutorial/core/timeline-engine.js`
  - 纯调度器，不依赖 DOM。
  - 单测覆盖 cue、pause/resume、cancel token、blocking command。
- `static/tutorial/core/command-registry.js`
  - command 注册和派发。
  - 可以包一层现有 `OperationRegistry`。
- `static/tutorial/core/script-normalizer.js`
  - 把旧 `scene` 转成 `TimelineScene`。
  - 迁移完成前，所有 day 文件先通过它进入 timeline。
- `static/tutorial/core/visual-runtime.js`
  - 统一封装 spotlight/cursor/petal/avatarStandIn 写入。
  - 内部复用现有 `TutorialVisualControllers` 和 PC overlay bridge。

### 5.2 逐步瘦身文件

- `static/tutorial/core/scene-orchestrator.js`
  - 保留 round loop。
  - `playGenericScene` 改成 normalize + timelineEngine.playScene。
  - built-in special flow 暂时保留，后续逐个迁移成 command group。
- `static/tutorial/yui-guide/director.js`
  - 保留生命周期、业务状态、音频、termination。
  - 把 cue 等待、cursor/spotlight 编排迁出。
- `static/tutorial/core/operation-registry.js`
  - 保持业务 operation handler。
  - 新增 command context 后，不再从 scene 主流程推导时序。
- `static/app-interpage.js`
  - 保持外置聊天窗几何解析。
  - 只发送独立通道 patch，不创建本地高亮/本地 cursor。
- `static/tutorial/visual/overlay-renderer.js`
  - 保持 renderer。
  - 加强完整状态 store 测试，保证多源 patch 合并。

## 6. 迁移计划

### 阶段 0：冻结行为与测试

目标：

- 不改用户可见行为。
- 为现有关键约束补测试。

验证：

- `node --test static/yui-guide-overlay-renderer.test.cjs`
- `node --test static/yui-guide-scene-orchestrator.test.cjs`
- `node --test static/yui-guide-avatar-standin.test.cjs`
- `node --test static/yui-guide-visual-controllers.test.cjs`

必须覆盖：

- cursor-only patch 不清空 external spotlight。
- resistance pause 保留 spotlight。
- angry exit 等演出结束后再 destroy。
- skip/natural end 销毁 PC overlay BrowserWindow。

### 阶段 1：引入 Timeline Engine 空壳

目标：

- 新增 engine/registry/normalizer，但不接管生产流程。
- 用单测验证时钟、cue、pause、cancel。

状态：已完成第一版。

验收：

- engine 能按 fake audio clock 触发 command。
- pause 期间不触发新 command。
- sceneRunId 变化后旧 promise 不写状态。

### 阶段 2：Generic Scene 接入 Normalizer

目标：

- `playGenericScene` 改成：
  1. normalize 旧 scene。
  2. 注册当前 Director runtime。
  3. 交给 timeline engine 播放。
- 旧 `voiceKey/cursorAction/operation/petalTransition` 行为保持一致。

状态：已完成正式 round scene 接入。`SceneOrchestrator` 已能执行显式 `timelinePlayback: true` scene，且显式 timeline 会先于旧 `SettingsTourFlow` 分支接管 scene，再由 `settingsTour.play` command 回调专用 flow。Day 1-7 正式 `round.scenes` 已全部进入 timeline playback；`settingsTour.play`、`settingsPanel.close`、`day1-*` flow operation、Day 6 插件 operation 等负责承接尚未细拆的复杂演出。

验收：

- Day 1 胶囊输入框高亮和 cursor 不闪烁。
- Day 2 屏幕入口 cursor 起点不回跳。
- Day 3 外置聊天窗 cursor 等 anchor settle。
- 所有现有 scene orchestrator 测试通过。

### 阶段 3：迁移低风险日程

优先迁移：

- Day 7 记忆浏览、记忆控制与毕业收尾已完成首批试点。
- Day 1 全 round 已完成首批试点；激活、问候、语音按钮和猫爪演出通过 operation delegate 保留旧专用时序。
- Day 2 全 round 已完成首批试点；设置入口复用 click onStart operation 打开设置面板，角色设置 detail 通过 `settingsTour.play` 保留 SettingsTourFlow 的面板和高光生命周期，主动搭话通过 `afterAudioEnd` + `settingsPanel.close` 保留旁白结束后关闭设置面板语义。
- Day 3 全 round 已完成首批试点；Galgame 入口旋转已拆成 `compactToolWheel.rotateGalgameIntoCenter` timeline command，内部复用现有导演演出。
- Day 4 全 round 已完成首批试点；设置巡游通过 `settingsTour.play` 复用 `SettingsTourFlow`。
- Day 5 全 round 已完成首批试点；角色设置与替换反应通过 `settingsTour.play` 保留 SettingsTourFlow 的设置面板、侧栏巡游、慌乱演出和专用替身图调度。
- Day 6 全 round 已完成首批试点；插件 dashboard 仍复用现有 `day6-plugin-dashboard-handoff-flow` operation 承接跨窗口演出和状态恢复。
- Day 1-7 暂无剩余正式 round scene 留在旧 generic 路径。
- 后续可选工作是继续把 operation delegate 内部拆成更小 timeline command，而不是再迁 scene 入口。

验收：

- day 文件里可直接写 `timeline`。
- normalizer 对已有旧字段仍兼容。
- 新旧 scene 可以在同一天共存。

### 阶段 4：迁移外置聊天窗与设置巡游

目标：

- 把 `chat-capsule-input`、history handle、avatar tools、galgame 等外置聊天窗 scene 迁成 timeline command。
- 已新增 `settingsTour.play` runtime command 作为 SettingsTourFlow 入口；下一步按 scene 显式接入，避免双旁白。

验收：

- external chat spotlight 只通过 PC overlay。
- capsule/input/history 等 target 解析稳定。
- 设置面板巡游期间 ellipse/rounded-rect 高亮不被 cursor patch 清空。

### 阶段 5：删除旧隐式编排

目标：

- 删除 `playGenericScene` 中的固定顺序分支。
- 删除已经迁移的 special-case cue 等待。
- 保留必要的 lifecycle 和业务 operation。

验收：

- 新文档成为重构进度源。
- 旧阶段流水账不再恢复。
- `docs/design/avatar-floating-7day-complete-guide-dev.md` 与本设计无冲突。

## 7. 验收标准

功能验收：

- 7 日教程可按原规则启动、跳过、自然结束。
- manual reset 只清状态并标记 pending/manual reset，刷新 Neko 后才启动每日教程。
- skip、生气退出、自然结束都销毁 PC 全局透明 BrowserWindow。
- angry exit 等生气音频/演出结束后再走 termination。
- PC overlay 保持唯一视觉层：Ghost Cursor、高光、替身、花瓣主路径统一走 overlay。
- 外置聊天窗不创建本地高亮、本地 Ghost Cursor、本地替身 DOM。
- 胶囊输入框 spotlight 在对抗机制触发时不闪烁。
- 每日首句台词播放时，Ghost Cursor 必须已经显示在胶囊输入框中，spotlight 必须高亮胶囊输入框，并保持到后续首个显式目标移动接管。
- 替身演出必须在场景表面准备完成后排程，不能被 scene cleanup、设置面板准备或 HUD 准备阶段清掉。
- PC overlay 的 `avatarStandIn` 通道必须能在没有 spotlight/cursor/petal 更新时继续保留，并在收到 `avatarStandIn: null` 或 inactive 状态时清理图片层。

工程验收：

- Timeline Engine 可用 fake clock 单测。
- Command handler 可单测，不依赖真实 DOM 时可注入 TargetResolver stub。
- VisualRuntime 多通道 patch 有回归测试。
- Scene normalizer 有 snapshot 或结构化断言。
- 旧 scene 和新 timeline scene 能并存。

## 8. 风险与防线

风险：一次性迁移七天教程导致回归面过大。

防线：先 normalizer 后迁移，按天/按 scene 分批切换。

风险：timeline engine 变成新的上帝对象。

防线：engine 只负责时间、pause、cancel、dispatch，不解析 DOM、不操作 UI、不决定业务。

风险：PC overlay 多源 patch 再次互相覆盖。

防线：VisualRuntime 和 renderer store 都必须坚持“显式通道更新”规则，并保留测试。

风险：对抗机制恢复时重复触发 command。

防线：command 需要 idempotency key，engine 保存已触发 command id；pause 只暂停未触发的时间推进，不重放已完成事件。

风险：音频时长配置遗漏导致 timeline cue 或旁白结束等待提前触发。

防线：`static/yui-guide-audio-files.test.cjs` 校验每日教程所有 `audioFilesByKey` 都有 `GUIDE_AUDIO_DURATIONS_BY_KEY` 实测时长；运行期缺配置只走保守固定保护或 0 时长，不按台词文本长度猜测。

## 9. 推荐下一步

1. 继续把高风险 operation 内部拆成更细 timeline command，优先顺序建议为 Day 1 `takeover_capture_cursor`、Day 6 `day6-plugin-dashboard-handoff-flow`、Day 2/4/5 `settingsTour.play` 内部面板巡游。
2. 为 `PauseCoordinator` / `ResistanceController` 增加跨 scene 回归测试：对抗暂停期间 spotlight 保持上一帧，Ghost Cursor 完成对抗动画后继续原目标，不重放已触发 command。
3. 做一次桌面端人工验收：Day 1 全流程、Day 6 插件跨窗口 handoff、Day 7 收尾花瓣、skip、angry exit、自然结束后的 PC 全局透明 BrowserWindow 销毁。
4. 后续新增或替换教程录音时，先更新 `GUIDE_AUDIO_DURATIONS_BY_KEY`，再改 scene；不得引入文本估时兜底。

后续推进仍遵守当前边界：一套共享 Timeline/Command 框架，业务逻辑留在 Director/flow/controller，PC overlay 只做视觉渲染，对抗机制全流程启用。
