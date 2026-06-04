# Home Yui Guide Tutorial Chat Interaction Dev Notes

本文记录首页新手教程期间，React compact 胶囊聊天、高光目标、教程点击接管和台词流式展示相关的开发约定。

## 目标

- 首页普通模式下，新手教程的聊天上下文高亮优先落到 compact 胶囊输入框或胶囊 surface。
- N.E.K.O.-PC 外置 `/chat` 窗口模式下，不再高亮旧整窗区域，改为同步独立聊天窗里的 compact 胶囊输入框。
- 教程台词可以进入胶囊预览，并在长文本时支持横向滚动和自动跟随最新文本。
- 新手教程运行期间，用户对 neko 本体、Live2D/VRM/MMD 容器、悬浮按钮和回归按钮的真实点击必须被统一禁用。
- 新手教程最后一句收尾台词播放期间，需要高亮胶囊输入框，直到收尾转场 cue 开始清理聚光。

## 高光目标

`static/yui-guide-director.js` 是首页教程高光的主入口。

- `getChatInputTarget()`：优先查找 compact input/capsule/surface，再回退 legacy composer。
- `getChatWindowTarget()`：聊天上下文 spotlight 优先使用 compact capsule/input/surface，再回退旧 shell。
- `getChatIntroActivationTarget()`：教程前奏等待用户激活时，同样优先允许 compact 胶囊输入框。
- `highlightChatWindow()` 和 `focusAndHighlightChatInput()`：外置聊天窗时调用 `setExternalizedChatSpotlight('input')`，不要回退为 `window`。
- `getSceneSpotlightTarget('takeover_return_control')`：最后一句归还控制权台词优先返回 `getChatInputTarget()`，不要再使用 neko 容器作为常驻高光目标。
- `playManagedScene()`：`takeover_return_control` 开场不要调用 `clearPersistentSpotlight()`；普通首页模式设置 input persistent spotlight，外置聊天窗模式通过 `focusAndHighlightChatInput()` 同步 input spotlight。
- `takeover_return_control` 仍会在 `returnPetalTransition` cue 到达后清理 persistent/action spotlight、隐藏 Ghost Cursor、关闭面板并禁用 interrupts；这里的要求是“收尾台词播放阶段高亮输入框”，不是让聚光穿过最终转场清理。
- 如果 compact input/capsule 不可见，`getSceneSpotlightTarget('takeover_return_control')` 会回退到原 selector target，避免 spotlight 目标为空导致收尾演出异常。

外置 `/chat` 窗口的实际 DOM 高光在 `static/app-interpage.js`：

- `getYuiGuideChatSpotlightTarget('input')` 优先选择 `data-compact-geometry-owner="surface"` + `data-compact-geometry-item="input"`。
- 其次选择 capsule、compact surface frame/shell。
- 最后才回退 composer panel/input shell。

## 台词进入胶囊

教程台词仍由 `appendGuideChatMessage()` 进入 React chat。

- 首页内嵌聊天：直接调用 `window.reactChatWindowHost.appendMessage()`。
- N.E.K.O.-PC 外置聊天窗：通过 BroadcastChannel 发送 `yui_guide_append_chat_message`。
- 流式 patch 通过 `yui_guide_update_chat_message` 同步到外置聊天窗。
- 活跃教程消息在终止/销毁时由 `finalizeActiveGuideChatMessages()` 收尾为完整 `blocks` + `status: 'sent'`。

React 胶囊侧在 `frontend/react-neko-chat/src/App.tsx`：

- `isGuideMessageId()` 识别 `yui-guide-*`。
- `getCompactMessagePreview()` 标记 `isGuide`。
- `getCompactMessagePreview()` 不合并 `yui-guide-*` 教程消息；上一句教程台词播完后，下一句开始流式播放时只显示当前句，避免两句接在一起。
- 串句修复发生在“最新 streaming assistant 消息”向前合并上一轮 assistant 文本时：只要最新 streaming 是 guide，或向前遇到的上一条 assistant 是 guide，就停止合并。
- 普通助手消息仍保留原来的相邻时间窗合并逻辑；guide 消息的隔离只作用于 `yui-guide-*`，不要把所有 streaming assistant 都改成禁止合并。
- `compactSpeechModeActive` 排除 guide 消息，避免教程台词等待普通助手语音播放进度。
- guide 消息从 `streaming` 收尾为 `sent` 后，胶囊内完整台词会保留到对应 `neko_speech_playback_state` 播放结束；没有再使用固定时长延迟清空。
- 教程旁白是 `static/yui-guide-director.js` 的本地 Audio/AudioContext 播放，不走普通助手 TTS；因此 `appendGuideChatMessage()` 会把 `voiceKey -> yui-guide-* message.id` 记录下来，`speakGuideLine()` 再把该 id 作为 `playbackTurnId` 传给教程音频播放状态广播。
- `data-compact-preview-scrollable="true"` 让 guide 台词长文本不显示省略号，并复用自动滚到尾部逻辑。

## 教程点击禁用

`static/universal-tutorial-manager.js` 负责教程期间的全局用户点击拦截。

- `blockNekoTutorialClickEvents()` 在 `startTutorial()` 锁定 body 滚动后安装捕获阶段监听。
- `unblockNekoTutorialClickEvents()` 在 `_teardownTutorialUI()` 开始时移除监听，确保教程结束后恢复正常点击。
- 监听使用 `{ capture: true, passive: false }`，覆盖 `pointerdown`、`pointerup`、`mousedown`、`mouseup`、`click`、`dblclick`、`auxclick`、`contextmenu`、`touchstart`、`touchend`。
- `_isNekoTutorialClickBlocked` 防止重复安装/重复移除监听。
- `blockNekoTutorialClickEvent()` 只在 `isTutorialRunning` 或 `window.isInTutorial` 时工作，并且放行 `.driver-popover` / `#neko-tutorial-skip-btn` 这类教程控制。
- `blockNekoTutorialClickEvent()` 只拦截 `event.isTrusted !== false` 的真实用户事件，保留教程脚本内部的 `.click()` / synthetic event。
- 命中 neko 目标后会 `preventDefault()`，并优先 `stopImmediatePropagation()`，防止目标自身和后续监听继续响应。
- `isNekoTutorialClickTarget()` 覆盖 neko 本体容器、Live2D/VRM/MMD canvas/container、返回 idle 按钮、悬浮按钮、reaction bubble、lock icon 等目标。

## 对抗机制

`static/yui-guide-director.js` 负责新手教程期间的“用户试图抢回控制”反馈。它和“点击禁用”不是同一层：点击禁用负责挡住 neko 真实点击；对抗机制负责采样真实鼠标移动/按下，在接管演出中播放抵抗反馈或生气退出。

场景配置在 `static/yui-guide-steps.js`：

- `intro_basic`、`takeover_capture_cursor`、`takeover_plugin_preview`、`takeover_settings_peek`、`takeover_return_control` 都设置 `performance.interruptible = true`。
- 主线 takeover 场景的 `interrupts.mode` 是 `theatrical_abort`，默认阈值 `threshold = 3`，节流 `throttleMs = 500`。
- `interrupt_resist_light` 是轻微抵抗分支，提供两句抵抗台词和对应 voice key：`interrupt_resist_light_1`、`interrupt_resist_light_3`。
- `interrupt_angry_exit` 是连续打断达到阈值后的生气退出分支，使用 `interrupt_angry_exit` 台词、语音和 `angry` 情绪。

采样入口在 Director：

- `enableInterrupts(step)` 会在捕获阶段监听 `mousemove` 和 `mousedown`，并按 `resetOnStepAdvance` 决定是否跨场景保留 `interruptCount`。
- `disableInterrupts()` 移除监听，并重置 `lastPointerPoint`、`interruptAccelerationStreak`、`lastPassiveResistanceAt`。
- `onPointerDown()` 只记录真实用户按下时的鼠标点，并清掉连续加速度计数。
- `handleInterrupt()` 只处理 trusted 事件；destroyed、angry exit、当前正在抵抗、未启用 interrupts、当前 scene 不允许打断、首页失焦时都会直接返回。

有效打断判定不是“移动一下鼠标就算”。当前常量：

- `DEFAULT_PASSIVE_RESISTANCE_DISTANCE = 10`，移动超过该距离且速度足够时，只触发被动 cursor 小回弹，不计数。
- `DEFAULT_INTERRUPT_DISTANCE = 32`
- `DEFAULT_INTERRUPT_SPEED_THRESHOLD = 1.8`
- `DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD = 0.09`
- `DEFAULT_INTERRUPT_ACCELERATION_STREAK = 3`
- 用户鼠标显示阈值是 `DEFAULT_USER_CURSOR_REVEAL_DISTANCE = 14`、`DEFAULT_USER_CURSOR_REVEAL_MOVES = 2`、间隔 `160ms`。

`handleInterrupt()` 每次采样会计算相邻鼠标点的 distance、speed、acceleration：

- distance / speed / acceleration 任一不达标，`interruptAccelerationStreak` 清零。
- 连续达到 `DEFAULT_INTERRUPT_ACCELERATION_STREAK` 后才算一次有效打断。
- 有效打断命中后受 `interrupts.throttleMs` 限制，默认 `500ms` 内不会重复计数。
- `interruptCount < threshold` 时进入 `playLightResistance(x, y)`。
- `interruptCount >= threshold` 时进入 `abortAsAngryExit('pointer_interrupt')`。

轻微抵抗流程：

- `playLightResistance()` 先短暂显示真实鼠标；如果用户已经多次试图找回鼠标，会进入 `yui-user-cursor-revealed` 状态。
- 读取 `interrupt_resist_light` 配置，根据 `interruptCount` 选择 `interrupt_resist_light_1` 或 `interrupt_resist_light_3`。
- 调用 `pauseCurrentSceneForResistance()` 暂停当前 scene，再调用 `interruptNarrationForResistance()` 截停当前旁白并记录恢复进度。
- 抵抗台词通过 `appendGuideChatMessage()` 进入 React chat，`streamPauseWithScene: false`，所以不会被当前 scene 暂停状态卡住。
- Ghost Cursor 执行 `cursor.resistTo(x, y)`：朝真实鼠标方向被拉近一小段、wobble，再回到上一个目标点。
- 同时调用 `runInterruptResistPerformance()` 给 avatar 演出一个抵抗动作。
- 抵抗语音、cursor 回弹和 avatar 演出结束后，`resumeCurrentSceneAfterResistance()` 恢复 scene；如果原旁白被截断，`scheduleNarrationResume()` 会等待用户停止移动约 `720ms` 后从记录位置继续。

生气退出流程：

- `abortAsAngryExit(source)` 记录 `angry_exit` experience metric，设置 `angryExitTriggered = true`。
- 它会清理 scene timers、禁用 interrupts、取消当前旁白，并进入 interrupt presentation 状态。
- UI 上保持 `yui-taking-over`，`overlay.setAngry(true)`，隐藏插件 preview 和普通气泡。
- 生气台词通过 `appendGuideChatMessage()` 进入 React chat，并设置 `streamAllowDuringAngryExit: true`，避免 angry exit 期间流式台词被拦住。
- 同时播放 `speakGuideLine()` 和 `runAngryExitPerformance()`。
- 演出结束后调用 `requestTermination(source || 'angry_exit', 'angry_exit')`，走统一教程终止和页面恢复。

插件 Dashboard 页的跨页打断：

- 首页和插件 Dashboard 使用 `neko:yui-guide:plugin-dashboard:interrupt-request` / `interrupt-ack` 通信。
- `handlePluginDashboardInterruptRequest()` 会按 `requestId` 去重，并同步 Dashboard 回传的 `interruptCount`。
- 回传 `kind === 'interrupt_resist_light'` 时，首页执行 `playLightResistance(x, y, { suppressCursorReaction: true, suppressCursorReveal: true })`，避免首页重复做 cursor 反应。
- 回传 `kind === 'interrupt_angry_exit'` 时，首页执行 `abortAsAngryExit('pointer_interrupt')`。
- 首页处理完后向 Dashboard `postMessage` ack，Dashboard 本地演出和首页旁白由这个回执对齐。

## 不要再用的旧目标

- 不要把 `#chat-container` 当作新手教程聊天高光目标。
- 不要把 `#react-chat-window-shell` 作为优先高光目标。
- 外置聊天窗不要发送 `setExternalizedChatSpotlight('window')`。
- 收尾台词 `takeover_return_control` 开场不要立即清掉 persistent spotlight，否则胶囊输入框不会被高亮。
- 教程期间不要只禁用部分 neko 控件；真实用户点击需要从 document 捕获阶段统一挡住。
- 不要把对抗机制写成单纯 click blocker；它依赖鼠标移动距离、速度、加速度和连续命中次数来区分被动回弹、轻微抵抗和 angry exit。
- 不要在抵抗台词期间把 guide chat streaming 绑定到 scene pause，否则轻微抵抗/生气退出台词可能不会及时显示。

## 验证

相关测试：

- `tests/unit/test_yui_guide_director_static.py`
  - compact 胶囊选择器优先于旧 composer/shell。
  - 外置聊天窗 spotlight 使用 `input`。
  - 外置 `/chat` spotlight 目标优先 compact capsule/input。
  - 教程流式消息在终止/销毁时收尾为 `sent`。
  - `takeover_return_control` 最后一句收尾台词播放期间高亮 compact input，且开场不清空 persistent spotlight。
  - 对抗分支可补充静态覆盖：`enableInterrupts()` / `disableInterrupts()` 监听成对安装移除；`handleInterrupt()` 的 distance/speed/acceleration/streak/threshold 判定；`playLightResistance()` 的 `streamPauseWithScene: false`；`abortAsAngryExit()` 的 `streamAllowDuringAngryExit: true`。
- `tests/unit/test_universal_tutorial_manager_static.py`
  - 教程开始时安装 neko 点击拦截，教程 teardown 时移除。
  - 拦截范围覆盖 Live2D/VRM/MMD、悬浮按钮、返回按钮和 reaction bubble，同时允许脚本合成点击。
- `frontend/react-neko-chat/src/App.test.tsx`
  - `shows tutorial guide streaming text in the compact capsule immediately`
  - 覆盖 guide 台词不等待普通助手语音进度、可滚动、自动滚到尾部，且收尾完整文本保留到对应音频播放结束后清空。
  - `does not merge the previous tutorial guide line into the next compact capsule line`

常用检查：

```bash
uv run pytest -q tests/unit/test_yui_guide_director_static.py tests/unit/test_universal_tutorial_manager_static.py tests/unit/test_react_chat_window_static.py
node --check static/yui-guide-director.js && node --check static/app-interpage.js && node --check static/tutorial-interaction-takeover.js
cd frontend/react-neko-chat && npm run test -- src/App.test.tsx
```
