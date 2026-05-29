# 猫娘空闲状态分层 - 代码参考

> 产品和行为口径见 [cat-idle-states-feature.md](./cat-idle-states-feature.md)。
> 本文档以当前代码为准，给实现、排查和 review 使用。

## 一、总实现结论

当前实现已经收敛为：

1. 不新增独立 idle 业务状态机。
2. 空闲到点后自动复用现有 goodbye。
3. `CAT1 / CAT2 / CAT3` 只表示 goodbye 后的 return 入口视觉 tier。
4. return 仍走原有 `*-return-click` 和 `handleReturnClick`。
5. 首页网页端和桌面 Electron 聊天窗都接入 `CAT2 / CAT3` 停靠。

当前仍是联调阈值：

```text
AUTO_GOODBYE_MS = 5s
CAT2_MS        = 10s
CAT3_MS        = 15s
```

## 二、主要文件

| 文件 | 职责 |
|------|------|
| [static/app-auto-goodbye.js](/Users/tonnodoubt/N.E.K.O/static/app-auto-goodbye.js) | idle 计时、阻断判断、goodbye 触发、visual tier 派发 |
| [static/app-ui.js](/Users/tonnodoubt/N.E.K.O/static/app-ui.js) | return-ball 显示/隐藏/拖拽、桌面 return-ball 状态广播 |
| [static/app-interpage.js](/Users/tonnodoubt/N.E.K.O/static/app-interpage.js) | `/chat` 交互回传、`idle_return_ball_state` 跨窗口转发 |
| [static/app-react-chat-window.js](/Users/tonnodoubt/N.E.K.O/static/app-react-chat-window.js) | 首页 React chat idle-dock、桌面 Electron idle-dock 消费端 |
| [static/avatar-ui-buttons.js](/Users/tonnodoubt/N.E.K.O/static/avatar-ui-buttons.js) | return DOM、tier 视觉同步、GIF hover 播放控制 |
| [static/css/index.css](/Users/tonnodoubt/N.E.K.O/static/css/index.css) | return 猫形象、网页端 idle-docked 样式 |
| [templates/index.html](/Users/tonnodoubt/N.E.K.O/templates/index.html) | 首页注入 auto-goodbye |
| [main_routers/pages_router.py](/Users/tonnodoubt/N.E.K.O/main_routers/pages_router.py) | `static_asset_version` 跟踪 idle GIF 资源 |
| `/Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js` | 桌面聊天窗折叠/展开桥接 |
| `/Users/tonnodoubt/N.E.K.O.-PC/src/main/window-control-ipc.js` | BrowserWindow collapse / expand IPC |
| `static/assets/neko-idle/` | `CAT1 / CAT2 / CAT3` 默认态与点击态 GIF |

## 三、auto-goodbye 控制器

主入口：[static/app-auto-goodbye.js](/Users/tonnodoubt/N.E.K.O/static/app-auto-goodbye.js)

### 3.1 运行范围

控制器只在首页运行：

1. `templates/index.html` 注入。
2. `/chat` 不注入控制器。
3. 控制器内部也用 pathname 限定 `/` 或 `/index.html`。
4. 启动前等待 storage startup barrier。

### 3.2 状态和公开接口

当前 `window.nekoAutoGoodbye` 暴露：

1. `noteUserInteraction(source)`
2. `hasBlockingActiveWork()`
3. `hasActiveConversationState()`
4. `hasActiveSystemExecutionState()`
5. `getIdleBlockReasons()`
6. `tryAutoGoodbye(reason)`
7. `setVisualTier(tier, meta)`
8. `clearTimers(reason)`
9. `getState()`

关键状态包括：

1. `lastInteractionAt`
2. `autoGoodbyeTriggered`
3. `visualTier`
4. `idleSuppressed`
5. `idleSuppressionReasons`
6. `conversationGraceUntil`

### 3.3 tick 顺序

当前 tick 先判断是否已经 goodbye，再处理 idle suppression：

```text
ensureInfrastructurePrimed()
goodbyeActive = isGoodbyeActive()
idleSuppressed = syncIdleSuppressionState('tick')

if goodbyeActive:
  syncVisualTierFromCurrentState('tick-goodbye')
  return

if idleSuppressed:
  return

if elapsed >= AUTO_GOODBYE_MS:
  tryAutoGoodbye('idle-threshold')
```

这样可以保证：桌面端仍有录音、播放等 blocker 时，已经进入 goodbye 的猫不会卡在 `CAT1`，仍会推进到 `CAT2 / CAT3`。

### 3.4 阻断条件

会阻止“进入 auto-goodbye”的条件包括：

1. `window._agentTaskMap` 中存在 queued / running 任务。
2. 真实对话状态：录音、语音启动 pending、播放中、assistant turn 未结束、conversation grace。
3. 真实执行状态：模式切换、角色切换、游戏 route、游戏语音 STT、手动屏幕共享、拖拽。
4. 教程或接管态：home tutorial active、interaction locked、`body.yui-taking-over`。

当前不再作为 blocker：

1. `voiceChatActive`
2. `isTextSessionActive`
3. `isMicStarting`
4. 只是子窗口打开
5. 只是主页失焦
6. 只是静态前台 UI 可见

### 3.5 goodbye 后的交互

`noteUserInteraction()` 在 `isGoodbyeActive()` 且 source 不是 `return-click` 时直接返回。

因此：

1. goodbye 后 pointer / key / touch / wheel 不刷新 idle 基线。
2. 拖拽 suppression 进入和解除不刷新 idle 基线。
3. return-click 才清 tier 并刷新基线。

## 四、视觉 tier 和 return 入口

### 4.1 return DOM 协议

继续复用原 return DOM：

| 类型 | Live2D | VRM | MMD |
|------|--------|-----|-----|
| 容器 | `live2d-return-button-container` | `vrm-return-button-container` | `mmd-return-button-container` |
| 按钮 | `live2d-btn-return` | `vrm-btn-return` | `mmd-btn-return` |
| 事件 | `live2d-return-click` | `vrm-return-click` | `mmd-return-click` |

这些 ID 和事件是兼容协议，后续不要随意重命名。

### 4.2 tier 同步

`avatar-ui-buttons.js` 负责：

1. 读取 `window.nekoAutoGoodbye.getState().visualTier`。
2. 给 return container / button / art 写入 `data-neko-idle-tier`。
3. 监听 `neko:auto-goodbye:state-change`。
4. 在 tier 变化时同步资源和过渡效果。

### 4.3 GIF 播放

当前资源路径：

| tier | 默认态 | 点击态 |
|------|--------|--------|
| `cat1` | `/static/assets/neko-idle/cat-idle-cat1.gif` | `/static/assets/neko-idle/cat-idle-cat1-click.gif` |
| `cat2` | `/static/assets/neko-idle/cat-idle-cat2.gif` | `/static/assets/neko-idle/cat-idle-cat2-click.gif` |
| `cat3` | `/static/assets/neko-idle/cat-idle-cat3.gif` | `/static/assets/neko-idle/cat-idle-cat3-click.gif` |

资源维护口径：

1. 只有 `CAT1 / CAT2 / CAT3` 是当前代码契约。
2. 新增或替换这些 GIF 时，要确保 [main_routers/pages_router.py](/Users/tonnodoubt/N.E.K.O/main_routers/pages_router.py) 的 `static_asset_version` 跟踪列表仍覆盖对应文件。
3. 如果目录中出现未接入的实验资源，例如 `cat-idle-cat4-*`，在代码、版本跟踪和测试更新前不属于当前功能合同。

实现规则：

1. tier 切换使用 overlay 做旧图淡出、新图淡入。
2. hover 使用 click GIF。
3. mouseleave 后按 GIF 帧延迟总和等待一轮播放完成再恢复默认 GIF。
4. GIF duration 按 URL 缓存。
5. 同一 click GIF 播放中不重复设置 `src`。
6. token / timer 防止旧恢复逻辑覆盖新 hover 或新 tier。

## 五、return-ball 状态广播

主文件：[static/app-ui.js](/Users/tonnodoubt/N.E.K.O/static/app-ui.js)

当前 `app-ui.js` 在 return-ball 显示、隐藏、tier 切换、viewport resize、拖拽时发布：

```text
action: idle_return_ball_state
source: pet-window
reason: return-ball-show / return-ball-revealed / return-ball-hide / visual-tier / viewport-resize / return-ball-dragging / return-ball-drag-end
visible: boolean
tier: none | cat1 | cat2 | cat3
screenRect: { left, top, width, height, right, bottom } | null
lanlan_name
timestamp
```

重要约束：

1. 只有非 `electron-chat-window` 页面允许发布。
2. Electron 聊天窗只消费，不发布，避免它自己的 resize 反向广播“不可见”。
3. 拖拽时使用 pointer 的 `screenX/screenY` 生成临时 `screenRect`，让桌面聊天窗跟随拖动过程，而不是等松手后跳到最终位置。

跨窗口转发在 [static/app-interpage.js](/Users/tonnodoubt/N.E.K.O/static/app-interpage.js)：

1. BroadcastChannel 收到 `idle_return_ball_state`。
2. 按 `lanlan_name` 过滤。
3. 派发本地 `neko:idle-return-ball-state`。

## 六、首页 React chat idle-dock

主文件：[static/app-react-chat-window.js](/Users/tonnodoubt/N.E.K.O/static/app-react-chat-window.js)

### 6.1 作用范围

网页端首页 idle-dock 作用于：

1. 非 Electron 首页 React chat host。
2. `CAT2 / CAT3`。
3. 最小化球停靠到 return-ball 左侧。

不作用于：

1. `CAT1`。
2. `/chat` Electron 窗口。
3. goodbye / return 业务语义。

### 6.2 实现原则

当前仍保持独立编排：

1. `setMinimized(nextMinimized)` 不塞 idle-dock 分支。
2. idle-dock 外部调用原始 `setMinimized(true/false)`。
3. 用 `MutationObserver` 等最小化完成后再停靠。
4. 用 `requestAnimationFrame` 合并 return-ball 位置同步。

### 6.3 关键状态

网页端首页使用：

1. `idleDockTier`
2. `idleDockActive`
3. `idleDockSavedPosition`
4. `idleDockTriggeredMinimize`
5. `idleDockMinimizeObserver`
6. `idleDockContainerObserver`
7. `idleDockSyncFrame`

### 6.4 进入与退出

进入：

```text
neko:auto-goodbye:state-change
  -> tier 是 cat2 / cat3
  -> 如果已最小化：保存原球位置并停靠
  -> 如果未最小化：调用 setMinimized(true)，等待 class 变更后停靠
```

退出：

```text
tier 离开 cat2 / cat3
或收到 live2d/vrm/mmd-return-click
  -> 恢复停靠前球位置
  -> 如果是 idle-dock 主动最小化，则 setMinimized(false)
```

## 七、桌面 Electron chat idle-dock

主文件：

1. [static/app-react-chat-window.js](/Users/tonnodoubt/N.E.K.O/static/app-react-chat-window.js)
2. `/Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js`
3. `/Users/tonnodoubt/N.E.K.O.-PC/src/main/window-control-ipc.js`

### 7.1 桥接接口

PC preload 在 `window.nekoChatWindow` 上提供：

1. `isCollapsed()`
2. `idleDockCollapse()`
3. `idleDockExpand(savedBounds)`

`idleDockCollapse()` 会：

1. 如果忙或已经折叠，返回当前状态。
2. 保存展开 bounds。
3. 给 shell 加 `neko-e-collapsed`。
4. 调 `W.collapse()` 把 BrowserWindow 缩到折叠球尺寸。
5. 设置 `eMinimized = true`。

`W.collapse()` 对应 PC 主进程 `WINDOW_CONTROL_CHANNELS.COLLAPSE`，当前会把 BrowserWindow 收到 `COLLAPSED_ICON_SIZE = 84`。

`idleDockExpand(savedBounds)` 会：

1. 用传入 bounds 或本地保存 bounds 作为目标。
2. 移除 `neko-e-collapsed`。
3. 调 `W.expand(target)`。
4. 清掉保存的展开 bounds。

### 7.2 消费 `idle_return_ball_state`

Electron chat 窗口监听 `neko:idle-return-ball-state`：

1. `visible=true` 且 `tier=cat2/cat3` 且有 `screenRect`：进入或更新停靠。
2. 否则退出停靠。

进入时：

1. 保存当前 BrowserWindow bounds。
2. 如果还没折叠，先 `idleDockCollapse()`。
3. 折叠成功后设置 active。
4. 根据 return-ball `screenRect` 把折叠球放到猫左侧。

退出时：

1. 取消 retry。
2. 取消 rAF 定位。
3. 递增 generation，使 pending enter 失效。
4. 如果本次是 idle-dock 触发的折叠，调用 `idleDockExpand(savedBounds)`。
5. 否则恢复 saved bounds。

### 7.3 竞态保护

当前桌面端收口了三类竞态：

1. `electronIdleDockDesired + electronIdleDockGeneration`
   - 退出会递增 generation。
   - 旧的 pending enter / retry / collapse 完成后不会再设置 active。
2. `electronIdleDockPositionFrame + electronIdleDockPositionSeq`
   - 拖拽中定位按 rAF 合并。
   - 旧的异步 `getBounds/getWorkArea` 结果不能覆盖新坐标。
3. `entrySavedBounds`
   - 如果退出发生在 `idleDockCollapse()` 进行中，旧链路完成后仍能用本次保存的 bounds 尽力展开回滚。

这几项是为了保证：

1. 猫消失或点击回来时，不会再被过期 retry 折叠。
2. 拖拽过程中桌面聊天窗不会因为旧坐标回跳。
3. 聊天窗不会刚折叠就被自己的 resize 状态展开。

## 八、测试参考

当前相关测试：

| 测试 | 覆盖重点 |
|------|----------|
| `tests/unit/test_app_auto_goodbye_phase1.py` | 控制器启动、阻断、tier 推进、goodbye 后拖拽不重置、interpage 转发 |
| `tests/unit/test_avatar_return_button_cat1_static.py` | CAT1 return 入口静态契约 |
| `tests/unit/test_avatar_return_button_idle_tiers_static.py` | CAT2/CAT3、GIF hover、拖拽不重置 |
| `tests/unit/test_react_chat_idle_dock_static.py` | 网页 idle-dock、Electron return-ball bridge、竞态保护字符串契约 |
| `tests/unit/test_auto_goodbye_goodbye_return_contract.py` | goodbye / return 语义不被改写 |
| `tests/unit/test_phase5_regression_boundary.py` | 边界回归 |
| `/Users/tonnodoubt/N.E.K.O.-PC/test/react-chat-idle-dock-contract.test.js` | preload 桌面折叠桥接 |
| `/Users/tonnodoubt/N.E.K.O.-PC/test/pet-hidden-return-ball-contract.test.js` | 桌面隐藏宠物命中区域契约 |

常用验证命令：

```bash
node --check static/app-react-chat-window.js
node --check static/app-ui.js
node --check static/app-interpage.js
node --check static/app-auto-goodbye.js
uv run python -m pytest tests/unit/test_react_chat_idle_dock_static.py tests/unit/test_app_auto_goodbye_phase1.py tests/unit/test_avatar_return_button_cat1_static.py tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_auto_goodbye_goodbye_return_contract.py tests/unit/test_phase5_regression_boundary.py -q
```

桌面仓库：

```bash
cd /Users/tonnodoubt/N.E.K.O.-PC
node --check src/preload-chat-react.js
node --test test/react-chat-idle-dock-contract.test.js test/pet-hidden-return-ball-contract.test.js
```

## 九、后续修改约束

后续继续改这块时必须保持：

1. auto-goodbye 仍复用现有 goodbye 底座。
2. `CAT1 / CAT2 / CAT3` 仍只是视觉层。
3. return DOM ID 和 `*-return-click` 事件继续兼容。
4. `/chat` 不运行首页 auto-goodbye 控制器。
5. Electron 聊天窗只消费 return-ball 状态，不发布。
6. 网页端和桌面端 idle-dock 都要考虑 CAT2/CAT3、拖拽、退出、return-click。
7. 不把 idle-dock 分支塞回 `setMinimized()` 正常逻辑。

## 十、剩余待办

当前剩余待办：

1. 阈值从 `5s / 10s / 15s` 切回 `20min / 30min / 40min`。
2. 替换正式 GIF 资源。
3. 做网页端和桌面端肉眼验收，重点看：
   - CAT1 -> CAT2 -> CAT3 过渡
   - hover click GIF 播放完整
   - CAT2/CAT3 聊天窗停靠
   - 桌面端折叠后停靠
   - 拖拽猫时桌面聊天窗跟随
   - 点击回来后聊天窗恢复
