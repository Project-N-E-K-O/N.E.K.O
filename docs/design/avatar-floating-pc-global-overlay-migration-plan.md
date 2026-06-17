# PC 全局透明教程 Overlay 迁移开发计划

本文定义七日新手教程在 N.E.K.O.-PC 上迁移到全局透明 overlay 的开发计划、接口边界和可行性检查。目标是让 PC 端与网页端拥有一致的导演语义：Ghost Cursor、高亮框和花瓣转场都在同一个跨窗口视觉层演出，不再依赖 Pet 窗口和独立聊天窗各自渲染一套效果后再“假装接力”。

## 目标

1. PC 端七日新手教程的 Ghost Cursor 能从聊天窗、Pet 主窗口、Agent HUD、插件窗口等目标之间连续移动。
2. 高亮框、圆形高亮和花瓣转场统一由 PC 全局透明 overlay 承载；skip 按钮仍保留在原业务窗口，由现有 Manager 处理。
3. 网页端继续使用当前 `static/tutorial/yui-guide/overlay.js` 的 DOM overlay，不引入 Electron 专属依赖。
4. 迁移期间保持每日 scene、台词、情绪动作和真实 UI 操作不变，只替换“视觉演出层”。

## 现有可复用基础

`N.E.K.O.-PC` 已有一套可复用的全局透明 overlay 能力：

| 现有能力 | 文件 | 可复用点 |
| --- | --- | --- |
| 透明置顶 overlay BrowserWindow | `src/avatar-tool-cursor-service.js` | 已按 display 创建 `transparent: true`、`frame: false`、`focusable: false`、`setIgnoreMouseEvents(true)`、`setAlwaysOnTop(true, 'screen-saver')` 的窗口。 |
| overlay preload 渲染 | `src/preload-avatar-tool-cursor-overlay.js` | 已能通过 IPC 接收状态并在透明窗口内绘制 cursor 图片。 |
| 多显示器管理 | `src/avatar-tool-cursor-service.js` | 已处理 display added/removed/metrics changed、每个显示器一层 overlay。 |
| 坐标基础 | `src/main/window-host-ipc.js` | 已有 `get-cursor-point` 和窗口 bounds 查询思路，可扩展到教程目标矩形上报。 |
| 主/聊天窗口管理 | `src/window-manager.js` 及现有主进程调用 | 可取得 Pet、Chat、AgentHUD 等 BrowserWindow bounds。 |

因此本方案可行性较高，工程重点不是性能，而是统一坐标协议、生命周期和旧 DOM overlay 的降级路径。

## 迁移原则

1. **PC 端全局 overlay 是视觉唯一来源**：教程进行中，PC 端 Ghost Cursor、高亮和花瓣由全局 overlay 渲染；Pet 页面和 Chat 页面只负责上报目标矩形、回传 cursor screen 锚点、执行真实点击/打开面板等业务操作。外置聊天窗不得直接向 PC 全局 overlay 推送 cursor show/move/hide，避免和首页 Director 抢同一个 cursor 状态。
2. **网页端不变**：非 Electron 环境继续用 `YuiGuideOverlay`，不加载 PC overlay bridge。
3. **scene 语义不变**：`static/tutorial/yui-guide/days/day1-home-guide.js` 至 `static/tutorial/yui-guide/days/day7-graduation-guide.js` 的 scene id、台词拆分、operation 不因 overlay 迁移而改写。
4. **坐标统一为 screen 坐标**：各窗口把目标 DOMRect 转成屏幕坐标后发给教程 overlay；overlay 内部再按当前 display bounds 转为本地坐标渲染。
5. **点击穿透默认开启**：全局 overlay 始终 `setIgnoreMouseEvents(true)`；真正需要拦截的 skip 仍由原窗口里的 skip 按钮或主进程统一入口处理，不让 overlay 截获普通输入。
6. **少量节点、少量 IPC**：每个 scene 只同步 primary、secondary、persistent、extra 中实际需要的矩形；动画在 overlay 窗口本地跑，不逐帧从业务窗口发位置。
7. **可见 cursor 不瞬移**：教程开始后 Ghost Cursor 保持可见直到收尾语音结束再消失；所有目标变化都必须带 `durationMs > 0` 并由 overlay 本地平滑过渡。`durationMs: 0` 只允许用于首次显示或同点 click/wobble 效果，后续跨段移动只复用可见锚点。
8. **教程 overlay 必须压过 Pet/模型按钮**：PC 全局透明教程 overlay 在创建、复用、状态更新和 active run 期间必须保持 `setAlwaysOnTop(true, 'screen-saver') + moveTop()`；reassert 间隔不得高于 160ms，否则 Pet/模型按钮窗口后续 `moveTop()` 会在长距离 cursor 移动期间把 Ghost Cursor 和高亮整体压到下层。教程 clear、skip、angry exit 或 stop 后必须停止这条轻量 reassert。
9. **教程期脸部跟踪 Ghost Cursor**：首页 `YuiGuideOverlay.getCursorPosition()` 是教程脸部跟踪的唯一 cursor 源。教程 look-at 性能会直接读取该点并驱动模型焦点；PC 全局 overlay 必须让这个 getter 在远端 Ghost Cursor 移动期间按 overlay 可见 cursor 的同一条 `cubic-bezier(.22,1,.36,1)` 曲线实时返回当前位置，避免模型脸部只跟踪起点/终点，或与屏幕上的 Ghost Cursor 产生缓动错位。

## 目标架构

```text
YuiGuideDirector
  ├─ 网页端：YuiGuideOverlay 直接渲染 DOM 高亮 / Ghost Cursor / 花瓣
  └─ PC 端：YuiGuidePcOverlayBridge
        ├─ 从 Pet/Chat/AgentHUD/插件窗口采集目标 rect
        ├─ 转换为 screen 坐标
        ├─ IPC 发送给 N.E.K.O.-PC 主进程
        └─ PC Global Tutorial Overlay BrowserWindow 渲染
```

### 新增/抽象模块建议

| 模块 | 所属项目 | 职责 |
| --- | --- | --- |
| `YuiGuidePcOverlayBridge` | `N.E.K.O/static/` | Director 的渲染适配层：提供 `setSpotlights()`、`moveCursor()`、`clickCursor()`、`hideCursor()`、`playPetalTransition()`。网页端不存在该 bridge 时自动回退 DOM overlay。 |
| `tutorial-global-overlay-service` | `N.E.K.O.-PC/src/` | 可基于 `avatar-tool-cursor-service.js` 抽出通用 overlay window 管理；承载教程 cursor、高亮、花瓣。 |
| `preload-tutorial-global-overlay.js` | `N.E.K.O.-PC/src/` | 在透明 overlay 中渲染 Ghost Cursor、高亮框、圆形高亮、花瓣层。 |
| `tutorial-overlay-ipc` | `N.E.K.O.-PC/src/ipc-channels.js` | 定义开始、更新、隐藏、销毁、目标矩形上报等 IPC channel。 |

## 数据协议

所有坐标统一使用屏幕坐标：

```ts
type TutorialOverlayRect = {
  id: string;
  kind: 'persistent' | 'primary' | 'secondary' | 'extra' | 'virtual';
  shape: 'rounded-rect' | 'circle';
  x: number;
  y: number;
  width: number;
  height: number;
  padding?: number;
};

type TutorialOverlayCommandEnvelope<T> = {
  tutorialRunId: string;
  sceneId: string;
  sequence: number;
  payload: T;
};

type TutorialOverlayCursorCommand =
  | { type: 'showAt'; x: number; y: number; durationMs?: number }
  | { type: 'moveTo'; x: number; y: number; durationMs: number; effect?: 'move' | 'wobble' | 'click' }
  | { type: 'hide' }
  | { type: 'click'; durationMs?: number };

type TutorialTargetRectReport = {
  targetId: string;
  rect: TutorialOverlayRect;
  sourceWindowId: number;
  devicePixelRatio: number;
  zoomFactor: number;
  visualViewport?: {
    offsetLeft: number;
    offsetTop: number;
    scale: number;
  };
};
```

所有发往 PC 主进程和全局 overlay 的命令都必须包在 `TutorialOverlayCommandEnvelope` 中。主进程只接受当前 active `tutorialRunId` 且 `sequence` 不小于当前已应用序号的命令；skip、destroy、页面刷新或新一轮教程启动时，旧 run 的异步 rect 回包和 cursor 命令必须被丢弃，避免旧高亮或旧 Ghost Cursor 复活。

### 坐标转换要求

1. 目标窗口上报 DOMRect 时，必须同时上报 `devicePixelRatio`、Electron `zoomFactor`、`visualViewport` offset/scale 和 `sourceWindowId`；主进程用 sender window 的 `contentBounds` 或可见内容区域校准，而不是只依赖外框 `bounds`。
2. 基础换算以内容区左上角为原点：`screenX = contentBounds.x + (rect.left + visualViewport.offsetLeft) * zoomFactor`，`screenY = contentBounds.y + (rect.top + visualViewport.offsetTop) * zoomFactor`。如果窗口或平台返回的 rect 已经是 CSS 像素，不能再乘 `devicePixelRatio`；Phase 0 必须用实测误差决定最终公式。
3. Pet、Chat、Agent HUD、插件窗口、记忆页都走同一套 `requestTutorialTargetRect(selectorOrSemanticId)` 协议，避免每个窗口各写一套坐标换算。
4. 多显示器时，主进程把 screen 坐标分发到对应 display overlay；跨屏移动时由 overlay service 在源/目标 display 间做连续隐藏/显示或分段移动。
5. 所有坐标实现必须通过像素级校准：overlay 高亮中心与目标 DOMRect 中心误差在 2px 以内才算通过；超过误差时不能进入正式迁移。
6. 跨窗口接力时，Chat/Pet/插件页都必须把上一个 Ghost Cursor screen 坐标作为下一段移动起点；若跨页过程中 cursor position 丢失，只能从最近一次可见锚点或真实目标中心恢复，并继续用平滑移动到新目标，不能用 0ms 改坐标。

## 七日教程迁移范围

| 天数 | 必须迁移到 PC 全局 overlay 的视觉元素 |
| --- | --- |
| Day 1 | 输入激活、聊天窗高亮、语音按钮、Agent/插件/设置/主动搭话、收尾花瓣。 |
| Day 2 | 聊天窗承接、屏幕分享按钮、收尾聊天窗、花瓣。 |
| Day 3 | 聊天工具区、Avatar 工具按钮、Galgame、收尾花瓣。 |
| Day 4 | 聊天窗、设置侧边栏、锁定/离开按钮、主动视觉开关、收尾花瓣。 |
| Day 5 | 角色设置侧边栏、模型/声音入口、记忆入口、收尾花瓣。 |
| Day 6 | Agent 按钮、Agent 面板/开关、插件入口、任务 HUD、收尾花瓣。 |
| Day 7 | 记忆入口、记忆列表/整理区域、聊天窗存储说明、毕业花瓣。 |

每日业务操作仍在原窗口执行，PC 全局 overlay 只承担视觉层。

## 开发计划

### 阶段 0：可行性验证

1. 在 `N.E.K.O.-PC` 复用 `avatar-tool-cursor-service.js` 的透明 overlay window 创建逻辑，做一个最小 demo：在 overlay 中画一个 cursor，从 Pet 窗口按钮移动到 Chat 窗口输入框。
2. 验证 Windows、macOS、Linux X11/Wayland 下 `setIgnoreMouseEvents(true)`、`setAlwaysOnTop(true, 'screen-saver')`、多显示器 bounds 是否稳定。
3. 验证透明 overlay 不影响现有截图隐藏 N.E.K.O 窗口逻辑；屏幕截图/屏幕分享前需要把教程 overlay 纳入 hide/restore 列表。
4. 验证坐标校准：至少采样 Pet 按钮、聊天输入框、设置侧边栏、Agent HUD 四类目标，overlay 高亮中心与 DOMRect 中心误差必须小于 2px。
5. 验证命令防串：启动教程、发送延迟 rect 回包、立即 skip，再确认旧 run 的高亮和 cursor 不会重新出现。

### 阶段 1：PC overlay service

1. 抽出或复用现有 overlay window 管理，新增教程 overlay channel。
2. overlay preload 先支持：显示/隐藏 cursor、moveTo、click、rounded rect spotlight、circle spotlight。
3. 加入本地动画时钟：cursor 移动和 spotlight 过渡在 overlay 进程内完成，业务窗口只发目标状态。
4. 所有 overlay 命令接入 `tutorialRunId`、`sceneId`、`sequence` 校验；skip/destroy 时主进程立即清空 active run。

### 阶段 2：跨窗口目标矩形采集

1. Pet 页面继续本地解析模型旁按钮、设置弹窗、Agent 面板等 rect。
2. Chat 窗口通过 preload 或 BroadcastChannel/IPC 上报聊天窗、输入框、Avatar 工具按钮、Galgame 按钮和菜单打开状态；Day 3 当前不采集三个道具按钮 rect，不恢复道具高亮。
3. Agent HUD、插件页、记忆页等独立窗口提供 `requestTutorialTargetRect(selectorOrSemanticId)` 能力。
4. 所有目标上报失败时，Director 不得回退到页面中心；必须跳过该高亮或使用明确的语义 fallback。
5. Phase 2 至少完成 Pet 与 Chat 两个窗口的目标采集后，才能让 Director 默认使用 PC bridge。

### 阶段 3：网页 Director 接入 bridge

1. `YuiGuideDirector` 增加 overlay renderer 适配层：优先使用 PC bridge，缺失时使用 DOM `YuiGuideOverlay`。
2. 保持现有 `playAvatarFloatingScene()` 时序不变，只把 `applyGuideHighlights()`、`moveAvatarFloatingCursor()`、`playAvatarFloatingPetalTransitionAtCue()` 的渲染目标切到 bridge。
3. 保留网页端原 DOM overlay 作为 fallback。
4. bridge 启用前必须先完成 renderer readiness handshake；未 ready 时继续 DOM overlay，不能出现“bridge 接管但目标为空”的空窗。

### 阶段 4：花瓣转场与收尾

1. 把现有花瓣 WebP/CSS 迁移到全局 overlay preload 中。
2. 收尾 cue 触发时，overlay 同一帧启动花瓣层、隐藏 cursor、清理 spotlight。
3. reduced motion 时在 overlay 内降级为短淡出/轻粒子。

### 阶段 5：全量回归

1. Day 1-7 在 PC 端逐日跑完整主线。
2. 网页端跑 Day 1-7，确认 fallback 未被破坏。
3. 验证 skip、angry exit、destroy、页面刷新、外置聊天窗关闭、跨显示器移动都能清理 overlay。

## 可行性检查清单

正式实现前必须完成以下检查，全部通过才进入阶段 1：

| 检查项 | 通过标准 | 当前判断 |
| --- | --- | --- |
| 透明 overlay window | 能创建全屏透明、置顶、点击穿透 BrowserWindow。 | 可行，`avatar-tool-cursor-service.js` 已具备基础。 |
| 多显示器 | 每个 display 都有 overlay，窗口 bounds 跟随 display metrics。 | 可行，现有 cursor service 已有 display 管理。 |
| Z-order 维持 | 教程 active run 存在时 overlay 以不高于 160ms 的间隔周期性轻量 `moveTop()`，防止同级 topmost 的 Pet/模型按钮窗口后续抢到最上层。 | 已纳入 PC tutorial overlay service；clear/stop 后停止。 |
| 跨窗口坐标 | Pet 与 Chat 窗口 DOMRect 能转换为统一 screen 坐标。 | 可行，主进程已有 BrowserWindow bounds 查询和 `get-cursor-point` 思路。 |
| 坐标精度 | Pet、Chat、设置、HUD 目标中心误差小于 2px。 | 需实测，必须在 Phase 0 完成。 |
| 命令防串 | skip/destroy/新 run 后，旧 run 异步命令不会恢复高亮或 cursor。 | 需新增 `tutorialRunId` 与 `sequence` 校验。 |
| 动画性能 | 10 分钟教程期间 CPU/GPU 无明显异常；cursor 移动不掉帧。 | 需实测，预期可控。 |
| 输入穿透 | overlay 不拦截用户点击；skip 仍由原窗口处理。 | 可行，使用 `setIgnoreMouseEvents(true)`。 |
| 截图/屏幕分享 | 教程 overlay 不被主动视觉/截图误捕获，或截图前可隐藏。 | 需接入现有 hide/restore N.E.K.O windows 流程。 |
| Wayland | overlay 置顶、透明、坐标与点击穿透可接受。 | 需实测，现有代码已有 Wayland 注释和兜底。 |
| 打包资源 | overlay preload、花瓣资源、highlight 图片能进入 PC 包。 | 需更新 `electron-builder`/copy 规则或使用现有 static 资源路径。 |

## 性能预算

| 项目 | 预算 |
| --- | --- |
| 常驻窗口 | 教程未运行时不创建，或创建后隐藏；运行中每个 display 最多 1 个 overlay。 |
| DOM 节点 | 常态 cursor 1 个，spotlight 1-6 个，花瓣层仅收尾时创建。 |
| IPC 频率 | scene 切换和目标更新时发送；cursor 动画不逐帧 IPC。 |
| 动画 | CSS transform/opacity 为主；不使用全屏 blur，不做实时截图，不引入 WebGL。 |
| 降级 | reduced motion 或低端设备下关闭粒子/拖尾，只保留 cursor 和简单高亮。 |

## 风险与降级

1. **跨显示器移动断裂**：先分段处理，源 display 隐藏、目标 display 显示；后续再做跨 display 连续轨迹。
2. **Wayland 置顶不稳定**：PC 全局 overlay 不可用时不再回退旧外置聊天窗 cursor，只保留教程文字、高光和业务操作；一旦 PC 全局 overlay 可用，外置聊天窗只能回传锚点，不能同时渲染第二套 cursor。
3. **截图误捕获 overlay**：截图/主动视觉前主进程隐藏教程 overlay，完成后恢复。
4. **目标 rect 上报失败**：跳过该 spotlight 或使用语义 fallback，不使用屏幕中心。
5. **旧命令复活高亮**：所有命令和 rect 回包必须携带 `tutorialRunId`/`sequence`，主进程丢弃旧 run 或过期序号。
6. **bridge 接管过早**：PC bridge readiness handshake 未完成时继续 DOM overlay，不让 Director 切到空 renderer。
7. **overlay 崩溃/未就绪**：Director 自动回退原 DOM overlay，教程不阻塞。

## 验收标准

1. PC 端 Day 1-7 任意从聊天窗切到 Pet 主窗口按钮，都能看到 Ghost Cursor 连续移动，不闪现到页面中心或目标按钮。
2. PC 端从 Pet 主窗口回聊天窗、Agent HUD、设置弹窗、插件页时，高亮和 cursor 都由同一全局 overlay 绘制。
3. 每天收尾花瓣和高亮/cursor 清理同帧发生，不出现高亮先消失、花瓣后出现的空档。
4. skip 按钮始终可用；skip 后全局 overlay 立即隐藏并销毁/清空状态。
5. 网页端 7 日教程视觉与现状一致，不因 PC bridge 缺失报错。
6. PC 端截图、屏幕分享、主动视觉不会把教程 overlay 当成用户屏幕内容长期捕获。
7. Day 3 不恢复三个道具按钮高亮；Avatar 工具阶段只保持 Avatar 工具按钮高亮，Galgame 阶段只保留一个圆形高亮。
8. skip/destroy/页面刷新后，即使旧窗口稍后返回 rect，也不会让全局 overlay 重新显示。
