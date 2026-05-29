# 猫娘空闲态重构 — 代码现状参考文档

> 参考入口：原始目标见 [cat-idle-states-feature.md](./cat-idle-states-feature.md)
> 本文档以当前仓库实际代码为准，整合并替代旧的 `cat-idle-states-design.md` 与 `cat-idle-states-implementation-flow.md`。
> 如果代码与本文档冲突，以当前代码和可复现行为为准。

---

## 一、用途与范围

这份文档只回答三件事：

1. 当前代码里“猫娘空闲态重构”到底是怎样实现的。
2. 哪些产品语义已经收敛，后续不能再按早期设想误改。
3. 目前还剩哪些明确未收口项。

它不是新的设计提案，也不是历史讨论归档，而是给后续实现、排查和 review 使用的单一参考文档。

---

## 二、当前代码的总实现结论

### 2.1 这不是独立 idle 业务状态机

当前代码已经明确收敛为：

1. 空闲态不新建独立业务状态。
2. 到点且无阻断时，系统自动复用现有 goodbye 底座。
3. `CAT1 / CAT2 / CAT3` 只是 goodbye 之后的视觉表现层，不是新的会话语义。

对应的实际代码入口是：

1. `static/app-auto-goodbye.js`
2. 自动路径只派发一次现有 `live2d-goodbye-click`
3. goodbye 之后再由 `visualTier` 驱动 `CAT1 / CAT2 / CAT3`

### 2.2 回来仍走当前 return 链，不切到 `start_session`

当前实现保持的关键语义是：

1. auto-goodbye 进入方式继续复用 `live2d-goodbye-click`
2. 回来方式继续对应当前 `handleReturnClick` 链
3. 也就是继续使用：
   - `live2d-return-click`
   - `vrm-return-click`
   - `mmd-return-click`
4. 没有把恢复主语义改成 `returnSessionButton -> start_session`

这点在这些文件上可以直接对应：

1. [static/app-auto-goodbye.js](/Users/tonnodoubt/N.E.K.O/static/app-auto-goodbye.js:474)
2. [static/avatar-ui-buttons.js](/Users/tonnodoubt/N.E.K.O/static/avatar-ui-buttons.js:723)
3. [static/app-ui.js](/Users/tonnodoubt/N.E.K.O/static/app-ui.js:2528)

### 2.3 当前代码等价于“Phase 1-5 已全部落地”

按旧实施流拆分，当前代码已经覆盖：

1. Phase 1：首页 auto-goodbye 控制器
2. Phase 2：CAT1 占位表现
3. Phase 3：CAT2 / CAT3 表现退化
4. Phase 4：React 聊天球在 CAT2 / CAT3 下的独立停靠
5. Phase 5：对应的静态/契约测试文件已在仓库中

但仍有 3 个明确未收口项：

1. 阈值仍是联调值 `5s / 10s / 15s`，尚未切回正式 `20min / 30min / 40min`
2. 视觉资源仍以占位资源为主，未替换成正式猫图
3. 网页端与 `/Users/tonnodoubt/N.E.K.O.-PC` 桌面端仍需要最终肉眼 UI 验收

---

## 三、当前产品语义与行为边界

### 3.1 auto-goodbye 的真实语义

当前代码里的 auto-goodbye 等价于：

1. 用户长时间无有效交互
2. 当前没有阻断条件
3. 系统自动帮用户触发一次现有 goodbye

这意味着它会主动复用 goodbye 已有副作用，而不是绕开它们重写一套轻量隐藏逻辑。

### 3.2 `CAT1 / CAT2 / CAT3` 的真实语义

当前代码里：

1. `CAT1`：goodbye 后的基础回来入口表现
2. `CAT2`：长时间 idle 后的第二档表现
3. `CAT3`：更长时间 idle 后的第三档表现

它们不会：

1. 改会话逻辑
2. 改 `_goodbyeClicked` 语义
3. 改回来后的会话恢复协议

### 3.3 手动 goodbye / return 语义未被改写

当前代码保持：

1. 用户手动点击“请她离开”仍走原有 goodbye 逻辑
2. 用户点击 return-ball / 猫形象回来仍走原有 return 链
3. auto-goodbye 只是自动触发条件，不是新业务分支

### 3.4 当前不引入自动唤醒

当前代码没有把以下行为定义成“自动回来”：

1. 任意 focus
2. 任意鼠标轻扫
3. 任意滚轮
4. 任意页面可见性切换

回来仍然以显式点击现有回来入口为主。

### 3.5 goodbye 后的交互不重置 tier

当前代码里，已经进入 goodbye / `CAT1-CAT3` 后：

1. 普通 `pointerdown` / `touchstart` / `keydown` / `wheel` 不刷新 idle 基线。
2. 拖拽猫形象期间产生的 `dragging` suppression，不会在进入或清除时刷新 idle 基线。
3. 因此 CAT2 / CAT3 下拖拽和松手都不会把视觉 tier 拉回 CAT1。
4. 真正点击回来入口触发 `*-return-click` 时，才按恢复流程清除 tier 并重置交互基线。

---

## 四、页面范围与启动方式

### 4.1 控制器只运行在首页

当前 `app-auto-goodbye.js` 只注入首页：

1. [templates/index.html](/Users/tonnodoubt/N.E.K.O/templates/index.html:381)
2. 不注入 `/chat`
3. 静态资源版本跟踪在 [main_routers/pages_router.py](/Users/tonnodoubt/N.E.K.O/main_routers/pages_router.py:35)

控制器自身也再次用 pathname 约束：

1. 只接受 `/`
2. 或 `/index.html`

### 4.2 启动前会等待 storage startup barrier

当前控制器会等待：

1. `window.waitForStorageLocationStartupBarrier()`
2. 或 `window.__nekoStorageLocationStartupBarrier`

之后才正式开始 idle 计时与 UI 协作。

### 4.3 `/chat` 不跑控制器，但会回传真实交互

当前策略不是“完全忽略 `/chat`”，而是：

1. `/chat` 不运行 auto-goodbye 控制器本体
2. `/chat` 中真实用户交互会回传首页
3. 首页收到后刷新闲置基线

对应桥接在：

1. [static/app-interpage.js](/Users/tonnodoubt/N.E.K.O/static/app-interpage.js:1)
2. [static/app-auto-goodbye.js](/Users/tonnodoubt/N.E.K.O/static/app-auto-goodbye.js:556)

---

## 五、当前 auto-goodbye 控制器

主文件：

1. [static/app-auto-goodbye.js](/Users/tonnodoubt/N.E.K.O/static/app-auto-goodbye.js:1)

### 5.1 当前阈值

当前代码里的实际阈值是：

1. `CAT1`: `5s`
2. `CAT2`: `10s`
3. `CAT3`: `15s`

这只是联调值，还没切回正式值。

### 5.2 当前公开接口

当前 `window.nekoAutoGoodbye` 实际暴露的是：

1. `noteUserInteraction(source)`
2. `hasBlockingActiveWork()`
3. `hasActiveConversationState()`
4. `hasActiveSystemExecutionState()`
5. `getIdleBlockReasons()`
6. `tryAutoGoodbye(reason)`
7. `setVisualTier(tier, meta)`
8. `clearTimers(reason)`
9. `getState()`

说明：

1. 这个公开面大于早期最小设计面
2. 这是当前代码事实，不代表后续一定要继续扩大依赖

### 5.3 当前状态字段

当前控制器内部至少维护：

1. `lastInteractionAt`
2. `autoGoodbyeTriggered`
3. `visualTier`
4. `idleSuppressed`
5. `idleSuppressionReasons`
6. `conversationGraceUntil`

### 5.4 进入 auto-goodbye 的真实条件

当前代码要求同时满足：

1. 在首页
2. 基础设施 ready
3. WebSocket 已打开并完成 priming
4. 当前还不在 goodbye
5. 当前没有任何阻断条件
6. 距离上次有效交互时间已达到 `AUTO_GOODBYE_MS`

满足后只做一件事：

1. 派发一次 `live2d-goodbye-click`

不会：

1. 复制 goodbye 业务逻辑
2. 自己直接调用 `end_session`
3. 自己拼一套 return / hide / disable 逻辑

---

## 六、当前阻断条件与抑制逻辑

### 6.1 当前真正会阻止进入闲置的条件

当前已经收紧为三大类：

1. 持续任务
2. 真实对话
3. 真实执行或真实操作

外加教程守卫与拖拽态。

### 6.2 持续任务

来源：

1. `window._agentTaskMap`

阻断口径：

1. 只要存在 `queued`
2. 或 `running`

就视为 blocker。

### 6.3 真实对话

当前代码会阻断：

1. `isRecording`
2. `voiceStartPending`
3. `isPlaying`
4. assistant turn 尚未结束
5. `conversation grace`

其中 `conversation grace` 当前保留，是为了覆盖：

1. 用户刚发完请求
2. 还在等第一段回复
3. 开发阈值很短时的误触发空窗

### 6.4 真实执行 / 真实操作

当前代码会阻断：

1. `isSwitchingMode`
2. `isSwitchingCatgirl`
3. `gameRouteActive`
4. `gameVoiceSttGateActive`
5. `gameVoiceSttListening`
6. 手动屏幕共享
7. 拖拽中

### 6.5 教程守卫

当前代码还会阻断：

1. `window.NekoHomeTutorialFeatureController.isActive()`
2. `window.isNekoHomeTutorialInteractionLocked()`
3. `body.yui-taking-over`

### 6.6 当前明确不再作为 blocker 的内容

当前代码已经明确不再把这些内容当成闲置阻断：

1. `voiceChatActive`
2. `isTextSessionActive`
3. `isMicStarting`
4. 只是子窗口打开
5. 只是主页失焦
6. 只是静态前台 UI 可见

---

## 七、当前视觉层实现

### 7.1 return 入口仍复用原 DOM 协议

return 相关 DOM 仍由 [static/avatar-ui-buttons.js](/Users/tonnodoubt/N.E.K.O/static/avatar-ui-buttons.js:638) 创建。

必须继续保留的协议包括：

1. 容器 ID
   - `live2d-return-button-container`
   - `vrm-return-button-container`
   - `mmd-return-button-container`
2. 按钮 ID
   - `live2d-btn-return`
   - `vrm-btn-return`
   - `mmd-btn-return`
3. 返回事件
   - `live2d-return-click`
   - `vrm-return-click`
   - `mmd-return-click`

### 7.2 当前视觉 tier 是如何同步到 return 按钮的

当前 `avatar-ui-buttons.js` 会：

1. 读取 `window.nekoAutoGoodbye.getState().visualTier`
2. 给 return button / container / art 写入 `data-neko-idle-tier`
3. 监听 `neko:auto-goodbye:state-change`
4. 在 `visual-tier` 变化时同步所有 return 按钮

### 7.2.1 当前 tier 切换与 hover 播放

当前视觉层还负责两类纯表现逻辑：

1. `CAT1 / CAT2 / CAT3` 之间切换时，不直接替换图片，而是创建临时 overlay，旧图淡出、新图淡入；完成后清理 overlay。
2. 鼠标 hover 到猫形象上时切到当前 tier 的 `*-click.gif`。
3. 鼠标离开时，代码会 fetch 并解析该 GIF 的 Graphic Control Extension 帧延迟，按一轮动画总时长决定何时恢复默认 GIF。
4. GIF 时长按 URL 缓存；解析失败或环境不支持 fetch 时使用 fallback。
5. 同一个 click GIF 正在播放时反复进入，不会重复设置同一 `src`，避免 GIF 一直从第一帧重新播放。
6. hover 恢复使用 token / timer 防护，旧 timer 不会覆盖新一轮 hover 或新的 tier。

### 7.3 当前资源路径

当前代码实际使用的是：

1. `CAT1`
   - `/static/assets/neko-idle/cat-idle-cat1.gif`
   - `/static/assets/neko-idle/cat-idle-cat1-click.gif`
2. `CAT2`
   - `/static/assets/neko-idle/cat-idle-cat2.gif`
   - `/static/assets/neko-idle/cat-idle-cat2-click.gif`
3. `CAT3`
   - `/static/assets/neko-idle/cat-idle-cat3.gif`
   - `/static/assets/neko-idle/cat-idle-cat3-click.gif`

注意：旧文档和旧测试里出现过 `.png` 口径；当前代码、资源目录、版本跟踪和测试应统一按 GIF。

### 7.4 当前视觉层与业务层的分工

当前分工仍然是：

1. `avatar-ui-buttons.js`
   - 创建 return DOM
   - 绑定拖拽
   - 绑定点击并派发 `*-return-click`
   - 同步猫形象资源
2. `app-ui.js`
   - 控制 show / hide
   - 控制位置
   - 执行 `handleReturnClick`

这意味着后续如果再改视觉层，仍应优先改显示层，不要重写 return 业务协议。

---

## 八、当前 React 聊天气泡停靠实现

主文件：

1. [static/app-react-chat-window.js](/Users/tonnodoubt/N.E.K.O/static/app-react-chat-window.js:1)

### 8.1 作用范围

当前 idle-dock 只作用于：

1. 首页 React chat host
2. `CAT2 / CAT3`
3. 已显示聊天窗口时的最小化球停靠

它不作用于：

1. `/chat` 独立窗口
2. `CAT1`
3. 手动 goodbye 业务语义本身

### 8.2 设计原则

当前代码已经按“独立编排”落地：

1. idle-dock 逻辑与 `setMinimized()` 分离
2. `setMinimized(nextMinimized)` 保持原有职责
3. idle-dock 只在外部读取最小化状态，并在必要时调用原始 `setMinimized(true/false)`

### 8.3 当前 idle-dock 状态

当前独立编排变量包括：

1. `idleDockTier`
2. `idleDockActive`
3. `idleDockSavedPosition`
4. `idleDockTriggeredMinimize`
5. `idleDockMinimizeObserver`
6. `idleDockContainerObserver`
7. `idleDockSyncFrame`

### 8.4 进入停靠的真实时序

当前代码时序是：

1. 监听 `neko:auto-goodbye:state-change`
2. 只有 tier 进入 `cat2` / `cat3` 才处理
3. 如果聊天框已最小化：
   - 保存当前球位置
   - 立即停靠到 return-ball 左侧
4. 如果聊天框未最小化：
   - 先调用原始 `setMinimized(true)`
   - 用 `MutationObserver` 观察最小化完成
   - 完成后再保存位置并停靠

### 8.5 退出停靠的真实时序

当前退出逻辑是：

1. tier 离开 `cat2` / `cat3` 时退出
2. 或收到任一 `*-return-click` 时退出
3. 先恢复停靠前保存的位置
4. 如果这次停靠是 idle-dock 主动触发最小化的，则再调用原始 `setMinimized(false)`

### 8.6 当前实现的边界约束

当前实现刻意保证：

1. 不在 `setMinimized` 内部塞 idle-dock 分支
2. 不新增 `window.reactChatWindowHost.setMinimized`
3. 不新增 `setIdlePresentation / clearIdlePresentation` 之类对外桥接
4. 不接管正常最小化 / 展开流程

---

## 九、文件与职责映射

当前主要文件分工如下：

1. [static/app-auto-goodbye.js](/Users/tonnodoubt/N.E.K.O/static/app-auto-goodbye.js:1)
   - auto-goodbye 控制器
   - 阻断判断
   - 计时、tier、事件派发
2. [static/app-interpage.js](/Users/tonnodoubt/N.E.K.O/static/app-interpage.js:1)
   - `/chat` 交互回传首页
3. [templates/index.html](/Users/tonnodoubt/N.E.K.O/templates/index.html:381)
   - 首页注入 auto-goodbye
4. [main_routers/pages_router.py](/Users/tonnodoubt/N.E.K.O/main_routers/pages_router.py:35)
   - auto-goodbye 与 idle 资源的版本跟踪
5. [static/avatar-ui-buttons.js](/Users/tonnodoubt/N.E.K.O/static/avatar-ui-buttons.js:182)
   - return DOM、拖拽、tier 视觉桥接
6. [static/app-ui.js](/Users/tonnodoubt/N.E.K.O/static/app-ui.js:1756)
   - goodbye / return UI 切换与 `handleReturnClick`
7. [static/app-react-chat-window.js](/Users/tonnodoubt/N.E.K.O/static/app-react-chat-window.js:1824)
   - React chat host 的 idle-dock 独立编排
8. [static/css/index.css](/Users/tonnodoubt/N.E.K.O/static/css/index.css:353)
   - CAT return 视觉样式与 idle-docked 样式
9. `static/assets/neko-idle/`
   - 当前占位资源

---

## 十、当前测试参考

这组功能当前对应的主要测试文件有：

1. `tests/unit/test_app_auto_goodbye_phase1.py`
2. `tests/unit/test_avatar_return_button_cat1_static.py`
3. `tests/unit/test_avatar_return_button_idle_tiers_static.py`
4. `tests/unit/test_react_chat_idle_dock_static.py`
5. `tests/unit/test_auto_goodbye_goodbye_return_contract.py`
6. `tests/unit/test_phase5_regression_boundary.py`

这些测试主要锁住：

1. auto-goodbye 只派发 `live2d-goodbye-click`
2. return 协议仍走现有 `*-return-click`
3. CAT1 / CAT2 / CAT3 资源与 tier 同步
4. CAT2 / CAT3 下拖拽和松手不重置 tier
5. hover click GIF 按自身时长播放，反复进入不重播开头
6. idle-dock 只限 CAT2 / CAT3
7. `setMinimized` 不被 idle-dock 污染
8. `/chat` 不运行首页控制器

---

## 十一、后续修改时必须继续遵守的约束

后续如果继续改这块，至少要继续守住：

1. auto-goodbye 仍复用现有 goodbye 业务底座
2. `CAT1 / CAT2 / CAT3` 仍只代表表现层，不引入新业务态
3. return 仍保留现有 ID、事件和 `handleReturnClick` 语义
4. idle-dock 仍保持独立编排，不污染正常最小化方法体
5. `/chat` 独立窗口不运行首页控制器
6. 网页端和 `/Users/tonnodoubt/N.E.K.O.-PC` 桌面端都要一起验

---

## 十二、明确剩余待办

当前还没最终收口的事项只有这些：

1. 把阈值从 `5s / 10s / 15s` 切回正式 `20min / 30min / 40min`
2. 替换当前占位猫图 / click 图 / gif 为正式资源
3. 对网页端与 `/Users/tonnodoubt/N.E.K.O.-PC` 桌面端做最终肉眼 UI 验收

如果后续没有新的产品变更，这三项之外不应再重新发明第二套 idle 业务语义。
