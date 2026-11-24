# 帧率：空闲降帧与 Pet 窗口帧率总闸

涉及文件：`static/live2d/live2d-core.js`、`static/vrm/vrm-manager.js`、`static/mmd/mmd-core.js`、`static/frame-pacing.js`，以及三个后端各自的交互 / 光标跟随文件。

## 两层机制

**第一层：空闲治理（所有加载了形象的页面，除非页面豁免）**

- 每个后端有一个 300ms 的活动探测定时器（Live2D 是 `_startIdleFpsGovernor`）。有活动就 `boostInteractiveFPS()`（VRM / MMD 叫 `_boostInteractiveFPS()`）升回配置帧率，并安排 900ms 后衰减。
- 衰减后进入「定时器驱动」：停掉 rAF 驱动的循环，用 `setInterval` 按地板帧率手动推进一帧。地板是 `min(30, 配置帧率)`；配置为 0（不限帧）时地板仍是 30。
- 活动的定义在各后端的 `_hasRenderActivity()`：高于 idle 优先级的动作或表情切换、拖拽、最近 900ms 内指针坐标变过、口型同步（`window.appState.lipSyncActive`）、串门采集；VRM 还包括加载中（`_loadState` 是 `preparing` / `settling`）、相机刚变过等。
- 预览页用 `window.__NEKO_DISABLE_AVATAR_IDLE_THROTTLE__ = true` 豁免：只升不降。

**第二层：帧率总闸（只在 Electron Pet 窗口）**

- `static/frame-pacing.js` 只由 `templates/index.html` 加载，并且内部再判一次 `__LANLAN_IS_ELECTRON_PET__`。其它页面没有 `window.nekoFramePacing`，行为不变。
- 它用 rAF 时间戳间隔的中位数量显示器刷新率（加载完 1.5 秒后量，收到 `electron-display-changed` 重量）。量不出来（3 秒超时）就当「未知」，留在 rAF，并隔 5 秒重试，每块屏最多重试 2 次。卡顿只会让刷新率被低估：对 `activeTimerTickFps` 这是保守方向（更容易留在 rAF）；对 `rafCapFps` 会把上限抬高，由下面的 1.5 倍余量兜住。
- `activeTimerTickFps()`：配置帧率低于「刷新率 × 0.9」时返回配置帧率，否则返回 null。含义是活动态也用定时器驱动、只是周期换成配置帧率。60 帧配置在 60Hz 屏上留在 rAF（和垂直同步对齐更顺，换定时器也省不了什么），在 120 / 144Hz 屏上切定时器。
- `rafCapFps(fps)`：留在 rAF 时实际执行的软件上限。配置帧率 ≥「刷新率 × 0.9」（和 `activeTimerTickFps` 同一阈值，留在 rAF 就一定放宽，中间没有空档）时返回 `max(配置, round(刷新率 × 1.5))`，例如 60Hz 屏上是 90；其余情况原样返回。三个后端都经各自的 `_resolveRafCapFps` 用它：Live2D 写 `app.ticker` 和 Pet 下 `shared` / `system` 的 `maxFPS`（governor 每 300ms 按最新刷新率重判），MMD 算 `frameTime`，VRM 的 rAF 循环每帧重判（放宽时改走和 VMC 一样的累计截止时间，严格不超过放宽后的上限；原来非 VMC 分支 0.9 的间隔容差会让 90 的上限实际跑到 100）。
  - 为什么要放宽：PIXI 7.4.3 的 `Ticker.update` 先把帧间隔截成整数毫秒（`(t - _lastFrame) | 0`）再和 `1000 / maxFPS` 比，60Hz 屏配 60 时 16.6ms 截成 16 < 16.667，整帧跳过、下一帧双倍 delta；MMD 的节流按精确间隔比较并对齐时间格，比格点早到的 vsync 同样被跳过。刷新率测准时显示器本身就是上限，放宽不会让帧率变高。
  - 为什么不直接设 0：刷新率可能被低估（采样时 rAF 被降频、系统动态刷新率）。设 0 会让帧率一路跑到真实刷新率；放宽到 1.5 倍时，最多到「测得刷新率 × 1.5」。这时实际帧率仍可能超过配置值，属于有意的取舍。
  - 0.9 阈值的边界：设置里只有 30/45/60/0。配置 60、测得 61～66Hz，或配置 45、测得 46～50Hz 时也会放宽（例如 45 配 50Hz 屏会按 50fps 跑，超过配置；放宽前 PIXI 隔帧跳、实际约 25fps）。这是有意的取舍。
  - 换屏：`electron-display-changed` 一到就递增 epoch，新屏重测成功前 `rafCapFps` 不信旧刷新率、原样返回配置值；换屏前就开始的那轮采样即使结束在换屏后也不算数。`activeTimerTickFps` 没有这个判定，换屏后短时间内仍按旧值判断（只影响节奏，不会抬高上限）。
  - 非 Pet 页面不测刷新率，`rafCapFps` 原样返回，浏览器主页面的跳帧没有覆盖。
- 已经在定时器模式时，`_enterIdleTickMode(fps)` 只换周期，不经过 rAF。

## 为什么不能只调 maxFPS

设置里的「帧率」最初只写 `pixi_app.ticker.maxFPS`。量下来不听它的有四处：

- `PIXI.Ticker.shared`：模型的动作和物理 `autoUpdate` 挂在这上面。`Application` 默认 `sharedTicker: false`，所以它和 `app.ticker` 不是同一个实例。
- rAF 排程本身：PIXI 的 ticker 每帧无条件再请求 rAF，`maxFPS` 只是跳过回调体。
- MMD：目标帧率原先来自 `performanceMode`，没接设置里的滑块。
- 桌面壳的光标轮询派发的合成 `pointermove`。

现行实现对应的四条规则：

1. Pet 窗口下，Live2D 的 `_applyRafMaxFps` 把 `shared` 和 `system` 的 `maxFPS` 一并写了；非 Pet 页面只碰 `app.ticker`。
2. 定时器模式下由 `_startTimerTick` 手动 `update()` `app.ticker` 以及被扣住的 `shared` / `system`，三者 `maxFPS` 清 0，原值分开保存、分开恢复（`_idleTickSavedMaxFPS`、`_idleTickSavedSharedMaxFPS`、`_idleTickSavedSystemMaxFPS`）。
3. MMD 的目标帧率接到 `window.targetFrameRate`；没有这个值时按 `performanceMode` 回退，并把自己解析出的目标帧率传给 `activeTimerTickFps(fps)`，免得总闸按 60 算。
4. 页面侧只有坐标变了才记「指针活动」：Live2D 是 `live2d-interaction.js` 里的 `pointerMoved`，VRM 是 `vrm-cursor-follow.js` 和 `vrm-manager.js` 里的 `moved`，MMD 是 `mmd-cursor-follow.js` 里对 `_rawMouseX/Y` 的比较。

## 全局 ticker 的扣住与放开（Live2D）

`PIXI.Ticker.shared` / `system` 是全局单例。相关不变式都有测试钉着（`tests/frontend/pet_frame_pacing_gate.test.cjs`）：

- `initPIXI` 把 `ticker.stop` / `ticker.start` 包了一层：外部代码直接停、起 ticker 时先退出定时器模式。包装里有身份检查，PIXI 重建后旧 ticker 上残留的包装不能去动当前实例和全局 ticker。
- 外部 `stop()`（切到 VRM / MMD、`pauseRendering`）退出定时器模式时**不**把全局 ticker 拉起来：Live2D 都不渲染了，拉起来只会让 PIXI 的 rAF 链空跑。之后的 `start()` 再放开。
- 切角色的真实顺序是 stop → `removeModel` 里 start → 最后再 stop。最后这一下不在定时器模式里，所以 Pet 窗口下 `stop()` 无论此前是否在定时器模式都要扣住全局 ticker。
- ⚠️ 销毁路径必须无条件放开全局 ticker（`_stopIdleFpsGovernor` 里的 `_releaseGlobalTickers`，幂等）。漏了的话，重建之后模型的 `autoUpdate` 挂在一个被停掉的 `shared` 上，表现是**模型加载出来但不动**。
- 活动探测定时器在 `pixi_app` 被销毁后自己停；ticker 只是被暂停时不自停（很多恢复路径直接 `ticker.start()`，没人会重启它）。
- 别处的「确保在渲染」代码（`if (!ticker.started) ticker.start()`）会把 Live2D 拉回 rAF 模式，而且不会再有衰减定时器带它回去。探测定时器里有自愈分支：无活动、无待衰减时重新进入定时器模式。
- 纯浏览器标签页隐藏时定时器 tick 不渲染（对齐 rAF 的暂停语义）；Pet 窗口不受影响。
- 当前没有页面同时运行两个持有活跃 `pixi_app` 的 `Live2DManager`。将来如果出现，先进入空闲的那个会把另一个的 `shared` 驱动降到自己的地板，那时要把接管改成跨实例引用计数。

## VRM / MMD 的对应点

- VRM 定时器驱动时，弹簧骨物理隔帧有个下限：隔帧后的步长必须落在 50ms 防爆上限内，所以定时器频率低于 40 帧时每个 tick 做全量物理（`_isLowTickRate`）。隔帧和全量之间切换时，物理时间总和必须等于实际经过的时间；累计步长只喂弹簧骨，LookAt 和 MToon 材质每个 tick 只推进当前这一帧的时间差。
- VRM 加载失败时必须把 `_loadState` 复位成 `idle`。`_hasRenderActivity` 把 `preparing` / `settling` 当活动，卡在 `preparing` 会让本会话的节流永久失效，而且加载前画布已经置成透明，旧场景会在看不见的画布上满帧空转。
- 时间戳 0 是合法值（`performance.now()` 的起点），判断「最近有没有活动」不能用真值判断，用 `Number.isFinite`。
- 负数或非法的 `targetFrameRate` 一律按 60 处理，否则地板会算成负数、定时器周期变成秒级。

## 其它每帧循环

渲染后端进入定时器驱动后，这些循环都改成跟着走（`requestPacedFrame`）：麦克风音量监测和口型同步（`static/app/app-audio-capture.js`、`app-audio-playback.js`）、表演舞台的补间 / 漂浮 / 呼吸 / 姿态时间线（`static/avatar/avatar-performance-stage.js`）、反应气泡跟随、三个后端的拖拽回弹动画。新增每帧循环照这个写法：

```js
const pacing = window.nekoFramePacing;
if (pacing && typeof pacing.requestPacedFrame === 'function') {
    cancel = pacing.requestPacedFrame(step);   // 返回取消函数
} else {
    const id = requestAnimationFrame(step);
    cancel = () => cancelAnimationFrame(id);
}
```

静止挂机时真正每帧醒来的常驻循环应该只有渲染本身。其它 rAF 用法要么是一次性的延后，要么有会话期门控（只在说话、拖拽、气泡显示期间跑）。「轮询一个没有变更事件可订阅的连续状态」是唯一正当的常驻理由，这种也要在静止时降频。React 侧的例子：`frontend/react-neko-chat/src/useFocusGlow.ts` 在数值衰减到地板后停掉 rAF，下次有新值再启动——否则会每帧重写同一个 CSS 变量。

## 排查入口

- 「帧率调低了 CPU 没降」：Pet 窗口 DevTools 里看 `live2dManager._idleTickMode` 和 `live2dManager._idleTickFps`（VRM 是 `vrmManager`，MMD 是 `mmdManager.core`）。一直是 `false` 说明有东西在持续续命升帧，先查有没有新的「见到指针事件就升帧」的入口，再查有没有新加的裸 rAF 循环。`window.nekoFramePacing.getDisplayRefreshHz()` 返回 null 说明刷新率没量出来，会保守留在 rAF。
- 「切角色后模型不动」：查全局 ticker 是不是被扣着没放（`PIXI.Ticker.shared.started`）。
- 想看有多少活跃定时器：`localStorage.NEKO_DEBUG_HEALTH = 1` 后刷新，见 `static/debug-health.js`。
- 测试：`tests/frontend/pet_frame_pacing_gate.test.cjs`（整文件加载三个后端和 `frame-pacing.js`，只需要 `window` 桩，不需要真的 PIXI / THREE；假定时器加手动推 rAF），pytest 入口是 `tests/unit/test_pet_frame_pacing_gate.py`。
