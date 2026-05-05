# 首页新手引导 Yui Guide 现状参考

本文档按当前代码实现重写，仅作为日后维护首页新手引导时的参考。它不再是“演出层负责人阶段拆解”计划，也不再描述已经废弃的 driver.js popover 流程。

当前基准日期：`2026-05-06`。

## 1. 当前范围

首页新手引导现在是一条 Yui 专用演出流，由首页脚本、场景注册表、Director、Overlay、跨页握手和通用教程管理器共同驱动。核心目标是：

- 首次进入首页时，通过提示弹窗或手动入口启动新手引导。
- 在教程期间临时切换到 `yui-origin` Live2D 模型，不写入用户配置。
- 由 Yui 旁白、聊天消息、表情轨道、Ghost Cursor、高亮和真实 UI 操作共同完成引导。
- 演示猫爪、插件面板、设置面板后归还控制权。
- 教程期间统一锁住非引导要求的首页交互和主动输出入口，完成、跳过、失败或销毁后恢复正常功能。
- 用户强行抢夺鼠标时触发轻度抵抗，第三次有效打断进入生气退出。
- 正常完成、跳过、生气退出、页面销毁都必须走统一清理。

## 2. 主要文件

首页加载入口在 `templates/index.html`，当前会加载：

- `static/app-tutorial-prompt.js`
- `static/yui-guide-steps.js`
- `static/yui-guide-overlay.js`
- `static/yui-guide-page-handoff.js`
- `static/yui-guide-wakeup.js`
- `static/yui-guide-director.js`
- `static/universal-tutorial-manager.js`
- `static/app-buttons.js`
- `static/app-react-chat-window.js`
- `static/app-websocket.js`
- `static/css/tutorial-styles.css`
- `static/css/yui-guide.css`

核心职责分布：

- `static/universal-tutorial-manager.js`：教程生命周期、首页步骤推进、Yui Director 创建、教程期间模型覆盖、模型位置和大小自适应、结束清理。
- `static/yui-guide-steps.js`：Yui 场景注册表、首页场景顺序、文案 key、voice key、interrupt 策略。
- `static/yui-guide-director.js`：首页主演出控制器，负责旁白、聊天消息、表情、Ghost Cursor、spotlight、真实 UI 点击、打断处理、插件 Dashboard 握手、清理。
- `static/yui-guide-overlay.js`：首页演出 DOM 层，负责 spotlight、圆形高亮、Ghost Cursor、预览层等。
- `static/yui-guide-page-handoff.js`：跨页 handoff token、目标页打开包装、插件 Dashboard 打开辅助能力。
- `frontend/plugin-manager/src/yui-guide-runtime.ts`：插件 Dashboard 页面内的本地演出、spotlight、Ghost Cursor、本地打断检测和与首页通信。
- `static/app-tutorial-prompt.js`：首次引导提示、用户决策、教程开始/完成状态上报、统一首页教程交互锁。
- `static/app-buttons.js`：首页旧发送入口、最终文本发送、截图、图片导入、剪贴板图片粘贴等入口的教程锁业务拦截。
- `static/app-react-chat-window.js`：首页嵌入 React chat 宿主层，负责同步教程锁到 composer，并在宿主提交入口兜底拒绝。
- `frontend/react-neko-chat/src/App.tsx` 与 `frontend/react-neko-chat/src/message-schema.ts`：React chat 组件层的 `composerDisabled`、输入/按钮/选项禁用状态和消息 schema。
- `static/app-websocket.js`：前端 greeting check 阻塞、pending 重试、首页教程阻塞状态上报。
- `main_routers/websocket_router.py`：后端 `home_tutorial_state` 接收和 `greeting_check` 教程态兜底。
- `main_routers/system_router.py` 与 `utils/tutorial_prompt_state.py`：提示状态、教程生命周期、handoff token 后端状态。

聊天 UI 只以 React 实现为准，入口是 `frontend/react-neko-chat/` 构建出的组件。旧的 `#chat-container` 不应作为当前实现依据。

## 3. 启动与提示状态

首页提示逻辑在 `static/app-tutorial-prompt.js`。它会维护前台停留、弱交互、聊天轮次、语音使用等状态，并通过后端决定是否提示用户开始教程。

后端接口：

- `GET /api/tutorial-prompt/state`
- `POST /api/tutorial-prompt/heartbeat`
- `POST /api/tutorial-prompt/shown`
- `POST /api/tutorial-prompt/decision`
- `POST /api/tutorial-prompt/tutorial-started`
- `POST /api/tutorial-prompt/tutorial-completed`

状态实现位于 `utils/tutorial_prompt_state.py`。对外快照会隐藏内部 token；开始和完成事件会通过 `neko:tutorial-started`、`neko:tutorial-completed` 与前端教程管理器联动。

### 3.1 首页教程交互锁

首页新手引导不只依赖演出层遮罩来挡点击。当前实现以 `static/app-tutorial-prompt.js` 暴露的统一锁作为业务判断入口：

```js
window.isNekoHomeTutorialInteractionLocked()
window.isNekoHomeTutorialBlockingGreeting()
```

锁只在首页生效，覆盖提示判定/展示、提示弹窗打开、用户接受后等待 Yui flow 真正进入 running、教程 running、`window.isInTutorial`、以及 completed/skipped 前的运行窗口。状态变化会派发 `neko:home-tutorial-lock-changed`，供聊天、WebSocket greeting 和宿主 UI 同步。

该锁的目标是让“教程期间只允许教程要求的交互”成为业务规则，而不是只靠捕获事件层、CSS 层或某个单独入口的临时判断。锁释放后必须恢复正常聊天、截图、导入、greeting check 和已有主动搭话链路，不额外改变这些链路的原始触发条件。

## 4. 场景注册表

首页主流程顺序由 `HOME_SCENE_ORDER` 定义：

1. `intro_basic`
2. `takeover_capture_cursor`
3. `takeover_plugin_preview`
4. `takeover_settings_peek`
5. `takeover_return_control`
6. `interrupt_resist_light`
7. `interrupt_angry_exit`
8. `handoff_api_key`
9. `handoff_memory_browser`
10. `handoff_plugin_dashboard`

另有目标页恢复场景：

- `api_key_intro`
- `memory_browser_intro`
- `plugin_dashboard_landing`

当前首页实际主线主要使用前五个 takeover 场景，`interrupt_*` 由打断机制临时触发，`handoff_*` 是跨页接力场景的注册能力。

`intro_basic`、`takeover_capture_cursor`、`takeover_plugin_preview`、`takeover_settings_peek`、`takeover_return_control` 都设置为可打断，并且 `resetOnStepAdvance = false`，所以打断次数会跨主线场景累计。

## 5. Director 生命周期

`static/yui-guide-director.js` 暴露：

```js
window.createYuiGuideDirector = function createYuiGuideDirector(options) {}
```

当前 Director 的主要方法：

- `startPrelude()`
- `enterStep(stepId, context)`
- `leaveStep(stepId)`
- `skip(reason)`
- `abortAsAngryExit(source)`
- `destroy()`

`UniversalTutorialManager` 创建 Director 后，会在 prelude、step enter、step leave、tutorial end 等节点通知它。Director 自身还会广播或接收若干 `neko:yui-guide:*` 事件，用于跨窗口、外置聊天窗口和插件 Dashboard 协作。

## 6. 首页主流程

### 6.1 前奏与输入激活

首页引导启动后，Yui 会先等待必要的用户交互以解锁浏览器音频播放。当前实现会围绕 React 聊天输入区、聊天窗口和语音按钮建立高亮与旁白。

`intro_basic` 会向聊天窗口追加教程消息，播放本地预录语音，并驱动表情轨道。外置 `/chat` 窗口模式下，教程消息和按钮锁定状态通过 `appInterpage` / BroadcastChannel 同步。
首页普通模式的 prelude 激活提示是刻意例外：输入框上方的 overlay 气泡（例如“点一下这里，我就能开始说话啦～”）不进入聊天记录；正式教程旁白（例如 `intro_basic` 及后续旁白）才追加到对话窗。首页内嵌聊天直接 append 到 React chat；N.E.K.O.-PC 的外置 `/chat` 窗口通过 BroadcastChannel 注入教程消息，只有外置聊天窗通信失败时才退回 overlay 气泡兜底。

教程锁开启时，首页嵌入 React chat 必须进入硬禁用状态：输入框 disabled/readOnly，发送、截图、图片导入、工具按钮、GalGame 选项和 ChoicePrompt 选项不可触发。宿主层 `handleComposerSubmit()` 也要检查同一锁，防止键盘提交、焦点残留或程序调用绕过组件状态。

首页旧发送链路和最终发送函数也要检查同一锁。`sendTextPayload()`、`sendTextPayloadInternal()`、截图捕获、图片导入和剪贴板图片粘贴在锁开启时直接返回，不进入 `start_session`、`stream_data`、用户消息追加、TTS、状态机或主动搭话重置链路。这样教程期间不会污染正常对话链，教程结束后也不会留下半截 session。

独立 `/chat` 页已有 BroadcastChannel 禁用/恢复链，当前改造不替换这条链路。首页嵌入 React chat 的硬禁用只是补齐同等级状态锁，避免出现外置窗口可锁、首页嵌入窗口只靠遮罩挡点击的不一致。

### 6.1.1 greeting 与主动输出隔离

角色首次打招呼、久别打招呼和 greeting check 需要被教程锁延后，但不能被永久吞掉。前端 `static/app-websocket.js` 在锁开启时保留 `_greetingCheckPending`，不发送 `greeting_check`；锁解除后重新尝试 `_sendGreetingCheckIfReady()`，让原本符合条件的 greeting 继续按既有规则判断。

前端还会把首页教程阻塞状态通过 `home_tutorial_state` 发送给后端。后端只用这个状态兜底 `greeting_check`，并带 60 秒 TTL，避免前端断线或异常退出后让后端长期误以为仍在教程中。这个兜底不拦截普通 `stream_data`、`start_session`、语音会话、主动搭话开关或其他 WebSocket action。

### 6.2 猫爪接管

`takeover_capture_cursor` 会高亮猫爪按钮，Ghost Cursor 移动并模拟点击，随后真实打开猫爪面板，并启用相关 Agent 开关，例如总开关和键鼠控制能力。

猫爪侧边二级面板会根据视口空间自动在按钮右侧或左侧展开。当前 Director 在采样二级面板内按钮坐标前，会调用 `waitForAgentSidePanelLayoutStable()` 等待 `AvatarPopupUI` 的展开动画和边缘自校正结束，避免“面板实际翻到左侧，但第一次点击仍打到右侧旧坐标”的问题。

### 6.3 插件 Dashboard 预览

`takeover_plugin_preview` 会打开猫爪插件开关，悬停显示管理面板入口，并由 Ghost Cursor 点击打开插件 Dashboard。

注意这里有两套路径概念：

- 场景注册表里的 `handoff_plugin_dashboard.navigation.openUrl` 仍标为 `/ui/`。
- 当前实际插件 Dashboard 演出由专用逻辑打开，`yui-guide-page-handoff.js` 会构造 `/api/agent/user_plugin/dashboard`，并附带 opener origin 等参数。
- Dashboard 页面内演出由 `frontend/plugin-manager/src/yui-guide-runtime.ts` 执行。

首页与 Dashboard 通过 `postMessage` 握手：

- `neko:yui-guide:plugin-dashboard:start`
- `neko:yui-guide:plugin-dashboard:ready`
- `neko:yui-guide:plugin-dashboard:done`
- `neko:yui-guide:plugin-dashboard:terminate`
- `neko:yui-guide:plugin-dashboard:narration-finished`
- `neko:yui-guide:plugin-dashboard:interrupt-request`
- `neko:yui-guide:plugin-dashboard:interrupt-ack`
- `neko:yui-guide:plugin-dashboard:skip-request`

插件 Dashboard 页面可以做本地 spotlight、Ghost Cursor 和打断检测，但旁白播放、抵抗台词和 angry exit 的最终控制权仍在首页 Director。

### 6.4 设置面板一瞥

`takeover_settings_peek` 会关闭猫爪面板，恢复首页主 UI，然后高亮设置按钮。第一段旁白播放到 `openSettingsPanel` cue 时才点击设置按钮；cue 由真实音频时长比例映射，不按语言写死延迟。

设置面板打开后，Director 会定位“角色设置”、角色外形入口、声音克隆入口或它们所在的二级区域，用联合 spotlight 和 Ghost Cursor 椭圆轨迹展示。

### 6.5 归还控制权

`takeover_return_control` 会清理高亮，播放归还控制权旁白，Ghost Cursor 平滑回到视口中心并隐藏，然后禁用打断监听并以 `complete` 结束教程。

## 7. 教程期间 Yui 模型覆盖

首页教程开始时，`UniversalTutorialManager.beginTutorialAvatarOverride()` 会临时切换当前角色为 Live2D `yui-origin`：

- 不写入用户配置。
- 会捕获并覆盖教程期间聊天头像身份。
- 失败时不会阻塞整个教程，但会记录警告。
- 教程结束、跳过、生气退出或管理器销毁时，`restoreTutorialAvatarOverride()` 负责恢复原始模型和聊天身份。

### 7.1 当前位置和大小策略

当前不再依赖固定 CSS 位置，也不再通过直接 `renderer.resize(...)` 改全局渲染器尺寸。位置由 `applyTutorialLive2dViewportPlacement()` 根据真实模型边界计算：

- 优先使用 `live2dManager.getModelScreenBounds()`。
- 若不可用，回退到当前模型的 `getBounds()`。
- 目标位置是视口中间偏右：小屏约 `56%` 宽度处，大屏约 `63%` 宽度处；垂直方向约 `50%` 到 `52%`。
- 使用左右、顶部、底部安全边距夹取中心点，保证模型完整显示。
- 会按模型真实边界和当前视口计算目标缩放，最大不超过 `0.5`，同时不超过可见宽高。
- 若第一次移动后仍溢出，会再按溢出比例缩小并重新夹取。
- 监听 `resize` 和 `electron-display-changed`，视口变化后延迟重排。
- 恢复教程模型时会移除这些监听。

因此，教程模型大小会根据实际模型边界和当前视口自适应；它不是固定像素尺寸，也不是只改容器位置。

## 8. 打断与抵抗

首页有效打断检测的默认阈值：

- 位移距离：`32px`
- 速度：`1.8 px/ms`
- 加速度：`0.09`
- 连续命中：`3`
- 相邻有效打断节流：`500ms`

采样由 `mousemove` / `pointermove` 事件触发；命中计数基于连续移动采样，实际有效打断频率由“相邻有效打断节流：`500ms`”约束。

轻微移动只触发被动回弹，不计入次数。有效打断计数跨主线场景累计：

- 第一次和第二次有效打断：暂停当前旁白，追加抵抗台词，短暂显示真实鼠标，Ghost Cursor 拉扯回弹，然后恢复旁白。
- 第三次有效打断：进入 `abortAsAngryExit('pointer_interrupt')`，播放生气退出台词和 `z3` 表情，之后走统一终止。

插件 Dashboard 页也做本地 pointer/touch/wheel/click 拦截和打断采样。它通过 `interrupt-request` 通知首页，由首页播放抵抗或生气退出，再通过 `interrupt-ack` 回执。

## 9. 语音、文案与表情

教程语音资源目录：

```text
/static/assets/tutorial/guide-audio/
```

首页 Director 根据 voice key 选择 zh、ja、en、ko、ru 语音；不支持的 locale 回退到英文语音。可见文本走 i18n key，例如 `bubbleTextKey`，不要只改硬编码中文。
预录音频播放失败时只做静默等待，避免浏览器 TTS 串音：按该段预估播放时长等待；若音频 load/error 触发或加载超时，则跳过当前音频段并继续下一段，不弹浏览器 TTS 兜底。

当前主要表情轨道：

- `intro_greeting_reply`：`sbx`、`xxy`
- `intro_basic`：`swz`
- `takeover_capture_cursor`：`szhs`、`syhs`
- `takeover_plugin_preview_home`：`by`
- `takeover_plugin_preview_dashboard`：`syhs`
- `takeover_settings_peek_intro`：`xxy`
- `takeover_settings_peek_detail`：`sbx`
- `interrupt_resist_light_1` / `interrupt_resist_light_3`：随机 `z2` 或 `z3`
- `interrupt_angry_exit`：`z3`

## 10. 跨页 handoff

普通跨页接力使用 `static/yui-guide-page-handoff.js` 和后端 token：

- `POST /api/yui-guide/handoff/create`
- `POST /api/yui-guide/handoff/consume`

token 由后端签名、带 TTL、同源校验、单次消费。目标页消费后恢复对应 scene。

插件 Dashboard 是特殊路径：当前主要使用专用 `postMessage` 握手和 `frontend/plugin-manager/src/yui-guide-runtime.ts`，不应简单套普通 handoff token 逻辑。

## 11. 清理与收口

以下路径都必须完整清理：

- 正常完成
- 用户点击跳过
- 第三次有效打断触发生气退出
- 页面关闭
- Director 或 TutorialManager 销毁
- 插件 Dashboard 中请求跳过或终止

清理目标包括：

- 隐藏 skip button。
- 移除 spotlight、precise highlight、Ghost Cursor、临时演出 DOM。
- 移除 `yui-taking-over`、`yui-guide-plugin-dashboard-running` 等接管 class。
- 停止或销毁语音队列、表情轨道、wakeup、监听器、计时器。
- 解锁 React 聊天按钮和输入区。
- 释放首页教程交互锁，并派发锁状态变化事件。
- 恢复首页旧发送入口、React composer、截图、图片导入、剪贴板图片粘贴入口的正常可用状态。
- 通知 WebSocket greeting 链路恢复 pending 检查；后端教程阻塞状态依赖 TTL 自动兜底，不能长期污染 greeting。
- 恢复首页真实 UI，不把用户原本的猫娘或主界面一起隐藏。
- 如插件 Dashboard 窗口由教程创建，则在需要时关闭。
- 恢复教程前的 Agent 开关快照。
- 恢复教程前的模型和聊天身份覆盖。
- 移除教程模型视口重排监听。

## 12. 修改时的注意点

- 不要把旧 `#chat-container` 当作活跃聊天 UI。
- 不要把模型位置写成固定 `left/top` 或固定像素尺寸；当前以模型真实 bounds 和视口安全区为准。
- 不要直接 resize 全局 Live2D renderer 来服务教程模型，否则会影响正常 Live2D 体验。
- 采样猫爪二级面板按钮前，必须等面板布局稳定，尤其是右侧空间不足时面板会翻到左侧。
- 插件 Dashboard 的教程演出不要只看 `/ui/` 字面路径，要核对 `YuiGuidePageHandoff` 和 `yui-guide-runtime.ts` 的专用握手。
- 任何新增教程文案或 i18n key，按项目规则同步所有 locale。
- 任何退出路径都要验证清理，不能只在正常完成路径恢复状态。
- 不要绕过 `isNekoHomeTutorialInteractionLocked()` 增加新的首页发送、截图、导入或粘贴入口。
- 不要把教程态判断散落成多套互不一致的条件；greeting、首页发送和 React composer 应消费同一把锁。
- 后端教程态兜底只应限制 greeting check，不能扩大到普通聊天、语音会话、主动搭话开关或其他正常 WebSocket 链路。
- 锁释放必须以教程完成、跳过、失败恢复和页面销毁等完整退出路径为准，避免慢网络或脚本异常下提前释放或永久残留。

## 13. 回归检查清单

- 首次首页提示能出现，接受后能启动教程。
- 手动启动教程能触发同一条 Yui 流程。
- 教程期间模型切到 `yui-origin`，结束后恢复用户原模型。
- 在不同窗口尺寸下，Yui 模型保持中间偏右并完整显示。
- resize 或 Electron 显示器变化后，教程模型会重新放置。
- 猫爪二级面板在右侧空间不足翻到左侧时，Ghost Cursor 第一次点击落在真实按钮上。
- 插件 Dashboard 打开、ready、done、narration-finished、terminate 事件能正确闭环。
- `/ui/` 页面打断能回传首页，并由首页播放抵抗或 angry exit。
- 跳过、完成、angry exit 后，首页可继续正常使用。
- React 聊天输入、语音按钮、设置面板、猫爪面板不会残留教程锁定状态。
- 教程期间首页 React chat 输入、发送、截图、图片导入、工具按钮、GalGame 选项和 ChoicePrompt 选项均不可触发。
- 教程期间通过键盘提交、焦点残留或程序调用触发首页发送时，业务链路会拒绝，不产生 `start_session` 或 `stream_data`。
- 接受教程弹窗后到 Yui flow running 前，greeting check 不会穿透。
- 教程完成、跳过或失败恢复后，首页普通对话、截图、图片导入和 greeting check 按原逻辑恢复。
- 弱网、慢设备、头像覆盖超时或脚本初始化延迟下，教程锁不会提前释放或永久残留。

## 14. 快速验证命令

文档修改本身不需要运行前端构建。若同时改了教程脚本，至少执行：

```powershell
node --check static\app-tutorial-prompt.js
node --check static\app-buttons.js
node --check static\app-react-chat-window.js
node --check static\app-websocket.js
node --check static\universal-tutorial-manager.js
node --check static\yui-guide-director.js
npm.cmd test
uv run python -m py_compile main_routers\websocket_router.py
uv run pytest tests\unit\test_websocket_home_tutorial_guard.py tests\unit\test_session_start_guard.py -q
git diff --check -- docs\design\home-tutorial-yui-guide-performance-owner-stage-breakdown.md
```
