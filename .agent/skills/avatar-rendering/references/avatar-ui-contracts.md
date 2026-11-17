# 形象周边 UI：点击穿透、弹窗、字幕穿透、姿态时间线、猫形态门控

## 点击穿透底和 pointer-events

`static/css/index.css` 里 `html, body { pointer-events: none }`。这是桌宠点击穿透的基础：整页默认不吃鼠标，每个可交互元素自己声明 `pointer-events: auto`。由此带来一类反复出现的问题。

**症状**：某个按钮或图标看得见，但点不动；有时「请她离开再回来」之后又能点了。

**根因**：隐藏逻辑（告别、教程准备阶段、拖拽屏蔽、游戏态）用 inline 的 `pointer-events: none !important` 压住元素；恢复逻辑用 `style.removeProperty('pointer-events')` 清掉它。如果元素原本的 `auto` 也是 inline 写的，这一下会一起抹掉，元素就掉回继承来的 `none`。

**现行做法**：

- 锁图标的可点状态由 CSS 兜底：`[id$="-lock-icon"] { pointer-events: auto; }`，一条覆盖 live2d / vrm / mmd / pngtuber 四种。要屏蔽它的地方都用 `!important none`，仍然压得住。
- 与之对偶，`static/avatar/avatar-ui-drag.js` 的拖拽屏蔽列表对锁图标也按 id 后缀匹配（`body.neko-model-dragging [id$="-lock-icon"]`）。按前缀逐个列会漏掉某一种形象，那种形象拖拽时锁仍然可点，会粘住鼠标。
- 新加一个「静止时应该可点」的元素：把 `auto` 写在样式表里，不要只靠创建时的 inline 样式。
- 告别时 `static/app/app-ui/surface-floating-controls.js` 给各形象的弹窗加 inline `pointer-events: none !important`，由「请她回来」的路径成对清除；`AvatarPopupMixin` 的打开分支里也有一次 `removeProperty` 作为自愈。

**怎么查**：

- 先量 `getComputedStyle(el).pointerEvents`，再用 `document.elementFromPoint(x, y)` 看命中的是谁。这类问题多数不是层叠遮挡。
- ⚠️ `el.dispatchEvent(new MouseEvent('click'))` 这种合成点击会**绕过** `pointer-events: none`，处理函数照样触发。不能用它证明「能点」。
- 窄窗口下锁图标的默认位置可能落在底部的紧凑聊天条上，造成假的遮挡。排查时把窗口拉宽或把模型挪开。

## 弹窗：统一走 AvatarPopupMixin

`static/avatar/avatar-ui-popup.js` 定义 `AvatarPopupMixin`，`static/avatar/avatar-ui-popup-config.js` 把它装到 `Live2DManager`、`VRMManager`、`MMDManager` 的原型上，`static/pngtuber-core.js` 装到 `PNGTuberManager` 上。`showPopup` / `closePopupById` / `closeAllPopupsExcept` / `closeAllPopups` 都来自它；`static/avatar/avatar-ui-drag.js` 里不再有 Live2D 自己的版本。

- 以前 Live2D 有一套独立实现，和 mixin 在隐藏定时器存在哪里、怎么判断可见、生命周期事件上都分叉了。四种形象的弹窗行为要改，改 mixin，不要在某个后端里单独加分支。
- **教程中不能关设置弹窗**的守卫在 mixin 里（`isTutorialGuardedSettingsClose`：`window.isInTutorial === true` 且是 `settings`），「再点一次切换关闭」和 `closePopupById` 两条路径共用，四种形象同时生效。教程代码里调「关设置面板」在教程进行中被挡住是预期行为。
- 设置弹窗打开时同步复选框状态，用 `createSettingsPopupShowSync(prefix)` 这个工厂，Live2D 和 VRM 共用。MMD 没接。
- 设置和 Agent 弹窗旁边会展开侧面板，定位时预留 320px 宽度；**手机宽度下不预留**（`isMobileWidth()` 为真时传 0）。无条件预留的话，手机布局里按钮在左上，弹窗会被误判成该往左弹，整个推出屏幕左缘。
- `live2d-model.js` 和 `surface-floating-controls.js` 里清 `_popupTimers` 的代码是旧实现的残留，带存在性判断，无害。
- ⚠️ 麦克风和屏幕按钮旁的小三角图标，`transform` 有两套语义在抢：`positionPopup`（`static/avatar/avatar-popup-common.js`）把它当「弹出方向」指示，右弹 0 度、左弹 180 度；`updateSeparatePopupTriggerIcon`（`static/avatar/avatar-ui-buttons/methods-state-and-cleanup.js`）把它当「是否展开」指示，展开 180 度。重新定位在显示动画的 rAF 链之后跑时，两者会互相覆盖。这是既有行为。要改这个图标的表现，这两处一起看。

## 字幕窗口的点击穿透：只能轮询

`static/subtitle/subtitle-window.js`。字幕窗口处于点击穿透状态时，要知道光标是不是移到了可交互区域上，好把穿透关掉。

- ⚠️ **不能改成事件驱动。** 窗口穿透期间，Electron 转发过来的 DOM 鼠标事件不可靠，`mousemove` / `pointermove` 收不到或不全。所以页面通过桥（`window.nekoSubtitle.getBounds()` 和 `getCursorPoint()`）轮询全局光标位置，自己做命中判断，再调 `enableInteraction()` / `disableInteraction()`。这四个方法是主仓库和桌面壳之间的契约。
- 轮询节奏按光标远近调整：光标在窗口外扩 64px 以内时 16ms 一次；离开后按 16 → 32 → 64 → 96ms 逐级放慢，停在 96ms。刚离开的光标最可能马上回来，所以不是一步跳到最慢。
- 拖拽和缩放期间（面板带 `dragging` / `resizing` 类）`shouldUseNativePassthrough()` 返回 false，轮询整个停掉。所以改轮询节奏不影响拖拽和缩放的手感。
- 已知的边角：光标从远处极快甩进来并立刻点击，有可能在穿透关掉之前点穿一次（上界是 96ms 加 64px）。这是接受的。
- 改这几个常量或拆出来的函数名，要同步 `tests/frontend/test_subtitle_incremental.py` 里的 `test_subtitle_window_passthrough_poll_matches_desktop_chat_latency`，它直接断言源码字面量。这个文件用真实浏览器跑，CI 不跑，要本地跑。

## 姿态时间线的心跳（`runPoseTimeline`）

`static/avatar/avatar-performance-stage.js`。姿态时间线（教程里的苏醒动作等）每帧往模型参数里写值，有两个可能的驱动者：

- **覆盖回调**：`manager.setTemporaryPoseOverride(source, cb)` 注册的回调，在 `installMouthOverride` 包装的 `coreModel.update` 里、渲染前最后一步被调。这是首选，写入不会被同一帧里的视线、眨眼、常驻表情盖掉。
- **舞台自己的帧循环**：兜底。

规则和各自的理由：

- ⚠️ **注册成功不等于在被驱动。** `setTemporaryPoseOverride` 只是往 Map 里存一项。`coreModel.update` 的包装可能还没装，也可能因为异常自卸载了（之后 100ms 才重装）。所以帧循环要监督：覆盖回调在推进，就只做「到时结束、模型切换则取消」；没在推进，就自己写参数。
- **两者不能同时写。** 交接淡出阶段两边各混合一次，实际权重会变成 `1 - (1 - w)^2`，偏离设计的曲线。
- **判断「没在推进」用时间阈值（150ms），不数帧。** 高刷新率屏幕配 30 帧的模型 ticker 时，一个模型帧之间有四个以上的 rAF，数帧会误判成停滞。
- **心跳起点初始化成「已经停滞」。** 否则包装失活时前 150ms 没人写参数，时长不超过 150ms 的短时间线只播了初始帧就被判定播完。回调第一次推进之前由帧循环驱动，推进之后让位。
- **不要把兜底窗口收窄到「剩余时长」。** 剩余时长小于模型帧间隔时，健康的回调会被误判停滞，尾段又变成双驱动。这条评审建议以前驳回过。
- 结束函数可能是从覆盖回调里触发的，它会取消掉待执行的帧回调，所以必须直接唤醒等待中的 promise，否则 promise 永远不结束。
- 帧循环通过 `scheduleStageFrame` 排帧，跟随帧率总闸。

这套「双驱动加心跳」的每个参数都有反例支撑，不要按单条评审意见轻易改。包装失活的竞态在浏览器里构造不出确定的复现；验证要在 Node 里加载真实文件，配虚拟时钟、可控的 rAF 和模型 ticker，覆盖：包装失活时的短时间线和长时间线、高刷新率下回调存活、中途失活、模型切换、后端不支持覆盖回调。

## 猫形态下的两处门控

收起成猫之后的行为由 `docs/design/cat-idle-*.md` 描述，这里只记两条读代码不容易看出理由的：

- 「猫的位置表现是否忙碌」有两个判定函数，不要合并（`static/avatar/avatar-ui-buttons/idle-assets-and-question.js`）：
  - `_isNekoIdleCat1PositionPresentationBusy`：移动中、没落定，**或者落定在紧凑聊天条的顶边**，都算忙。给桌面窗口互动用，那里把顶边当作「已占用」。
  - `_isNekoCatMindCat1PositionBusy`：只有移动中、没落定才算忙。给 Cat Mind 的硬门控用。落定在顶边是休息位，社交提醒的反应就是为这个位置做的；如果这里也算忙，猫一停到顶边 Cat Mind 就全哑了。
- Cat Mind 的「动作连播保护」读的是 `clock.lastConfirmedActionStartedAt`，只在动作真正开始时写。不能复用 `lastActionStartedAt`：那个字段在进场时被设成计时锚点，别的时长阈值依赖它。
- Cat Mind 的降频（连播保护、悬停去重、走路时让路）和小幅移动的距离是有意的设计，维持现状。
