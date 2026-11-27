# 窗口判断与运行时门控

## 1. 多窗口标志和「谁来执行」

`window.__NEKO_MULTI_WINDOW__` 由桌面壳在页面脚本之前注入。pet 窗口（`index.html`）和 chat 窗口（`chat.html`）都带，所以它只说明「在 Electron 多窗口里」。

判「我是 chat 跟随窗」的现行写法（仓库里已有多份，新代码照抄同一个判据）：

```js
window.__NEKO_MULTI_WINDOW__ === true
    && /^\/chat(?:_full)?(?:\/|$)/.test(window.location.pathname || '')
```

现有使用点：`static/app/app-websocket.js` 的 `isSecondaryJukeboxControlSurface()`、`_isVisitChatFollowerWindow()`；`static/app/app-react-chat-window/visit-chat.js` 的 `isChatFollowerPath()` / `isUiWindow()`；`static/app/app-react-chat-window/message-bundle-actions-and-prompts.js` 里小游戏开窗那段；`static/app/app-chat-adapter.js`；`static/achievement_manager.js`。

为什么需要它：多窗口下，同一条 WebSocket 消息会被转发给 pet 和 chat 两个窗口，两边都会跑同一个处理函数。

- 不选执行者 → 两个窗口各开一个游戏窗口、各起一条音轨。
- 只看标志就让位 → pet 和 chat 都不执行，桌面端功能整个失效，网页端单窗口正常。

做法：

- 在处理函数里明确写出谁执行。常见分法：pet（以及网页单窗口）执行有副作用的动作，chat 跟随窗只收起 / 更新界面。
- 「谁执行」要在真正执行的那一刻判断，不要用入队前的快照（参见 `dispatchJukeboxControl` 的注释：排队期间独立窗口可能刚开或刚关）。
- 回归测试的写法参考 `tests/unit/test_mini_game_ws_launch_owner.py`：用 node `vm` 加载宿主脚本，分别跑浏览器（无标志）、Electron pet（有标志 + `/`）、Electron `/chat`、Electron `/chat_full` 四种情况，断言只有前两种执行。

## 2. Electron 下的 WebSocket（壳侧行为，主仓库这边据此写代码）

以下几条是壳的行为，在主仓库里核对不了，按「已知契约」对待；`static/app/app-websocket.js` 里关于「代理」「READY / CLOSED」「僵尸代理」的注释说的都是这套机制。

- 只有 pet 窗口持有到后端的真连接。chat 窗口里的 `WebSocket` 是壳提供的代理：发送经 pet 的连接转出去，收到的消息由 pet 转过来。所以后端每个角色只看到一条连接，chat 窗口不会去抢占它。
- 分析「某条广播为什么没到 chat 窗口」时，问题在转发这一段，不在后端。
- chat 窗口里「切角色」只是重建代理，不会让真连接重连。要让真连接跟着切，pet 窗口自己也得走切换流程。
- 代理在未连通状态下发送的消息会被丢掉，不排队。切换瞬间发出去的消息可能丢。
- 音频数据类消息不转发给 chat 窗口。chat 窗口里的代码不能依赖收到它们。
- 浏览器里直接开 `/chat` 没有这层代理，走的是真连接；同一个浏览器开多个标签页会真的互相抢连接。所以浏览器里测 `/chat` 的连接行为不能代表 Electron。
- `app-websocket.js` 的注释记载：full 聊天窗在独立的存储分区里，和主窗口不共享 `localStorage`，也听不到主窗口的同源广播。跨 pet / chat 的状态同步不要假设能靠 `storage` 事件或 `BroadcastChannel`。

## 3. 多窗口下的页面配置

`static/js/index.js` 的 `startPageConfigLoad()`：在多窗口且路径属于 `RESERVED_PAGE_PATHS`（`chat`、`chat_full` 等）时，页面配置不从 HTTP 取，而是等壳注入（`neko:config-injected` 事件 / `window.__nekoInjectedConfig`，可用 `window.__nekoRequestConfigInjection()` 催一次），5 秒没等到才回退到 HTTP 接口。

`main_routers/pages_router.py` 最后一条路由是 `/{lanlan_name}` 兜底。新增页面路由要写在它前面；如果新页面也会加载 `static/js/index.js`，还要把路径首段加进 `RESERVED_PAGE_PATHS`，否则会被当成角色名。

## 4. `chat.html` 被两种宿主共用

`templates/chat.html` 同时服务 `/chat` 和 `/chat_full`（`pages_router.py` 里两条路由只差 `initial_chat_surface_mode` / `initial_chat_host_kind`，写到 `body` 的 `data-initial-chat-surface-mode`、`data-chat-host-kind` 上）。这两个地址既被桌面壳加载成独立窗口，也能在浏览器里直接打开。

⚠️ `body` 上的 `electron-chat-window` 是模板静态写的，浏览器访问也有。后果：

- 用它当「仅 Electron 独立窗口」的门，会误伤浏览器里的 `/chat_full`。
- `static/app/app-react-chat-window/bootstrap-state-and-geometry.js` 的 `I.isElectronChatWindow()` 只看这个 class，浏览器里也返回 true。它的实际含义是「这是 `chat.html`」。

区分真 Electron 用运行时信号：

- CSS：`body.neko-electron-runtime`。它由 `chat.html` 的 body 内联脚本同步打上，依据是 `window.nekoChatWindow`、UA 里的 `Electron`、`process.versions.electron`。和 `index.html` 头部的 `__LANLAN_IS_ELECTRON_PET__` 是一对。
- JS：同文件里的 `isElectronChatRuntime()`，或组合好的 `I.isCompactOnlyElectronRuntimeChatHost()`。

另外，`/chat`（`data-chat-host-kind="compact"`）会把 `full` 强制降成 `compact`（`I.coerceChatSurfaceModeForHost`）：`/chat` 的承载窗是透明的紧凑窗，full 由 `/chat_full` 单独承载。

## 5. 手机模式：Electron 的窗口必须排除

`index.html` 的手机模式（黑背景、窄布局）主要由 CSS 的 `@media (max-width: 768px)` 驱动（`static/css/index.css`、`static/css/dark-mode.css`）。Electron 的 pet 窗口加载的就是 `index.html`，用户把它拖窄到 768px 以下就会误进手机模式。

现行排除机制：

- `index.html` 头部有一段同步脚本，检测到 Electron 信号就设 `window.__LANLAN_IS_ELECTRON_PET__` 并给 `<html>` 加 `lanlan-pet-mode`；`<body>` 开头再有一段给 `<body>` 加同一个 class（头部脚本执行时 body 还不存在）。必须同步、必须在样式表之前，否则窄窗口首帧会闪一下黑底。
- 媒体查询里的选择器用 `:where(body:not(.lanlan-pet-mode))` 前缀排除 pet 窗口。`:where()` 不增加特异性，原规则的优先级不变。新增手机规则照这个写法。
- chat 窗口靠 `body:not(.electron-chat-window)` 排除。
- JS 侧的统一谓词是 `window.isMobileWidth()`，定义在 `static/live2d/live2d-core.js`（`static/vrm/vrm-init.js` 有一份兜底；`static/common_ui.js` 的 `uiIsMobileWidth()` 和聊天宿主的 `I.isMobileWidth()` 也各自做了排除）。新代码调谓词，不要内联 `window.innerWidth <= 768`。
- `static/app/app-state.js` 的 `appUtils.isMobile()` 是按 UA 判的，不是按宽度，桌面 Electron 本来就不命中，两者别混用。

## 6. `index.html` 的点击穿透底

`static/css/index.css` 里 `html, body { pointer-events: none }`，为的是桌宠窗口点击能穿透到桌面。所有可交互的界面都要自己声明 `pointer-events: auto`。

踩过的坑：

- 症状：某个图标刚启动时可见但永远点不动，「请她离开再回来」之后又能点了。
- 根因：元素创建时设的是内联 `pointer-events: auto`；后来某个「恢复」函数为了清掉教程隐藏时留下的 `!important none`，对它调了 `style.removeProperty('pointer-events')`，把那份 `auto` 一起抹掉了，元素落回继承来的 `none`。
- 做法：给这类元素在样式表里补一条静态的 `pointer-events: auto` 兜底（现有例子：`index.css` 的 `[id$="-lock-icon"]`）。拖拽期间屏蔽、游戏态隐藏、教程隐藏都用 `!important none`，仍能压住兜底。
- 兜底和屏蔽列表要成对：`static/avatar/avatar-ui-drag.js` 里拖拽屏蔽锁图标用的也是 `[id$="-lock-icon"]` 后缀匹配，覆盖四种模型。按前缀逐个列会漏掉某一种模型。

怎么查：先看 `getComputedStyle(el).pointerEvents`，再看 `document.elementFromPoint(x, y)` 命中的是谁。`dispatchEvent` 发出的合成点击不受 `pointer-events: none` 影响，不能拿「合成点击触发了处理函数」当作可点的证据。

## 7. 静止时不留每帧空转的循环

桌面端挂机时 CPU 偏高，查下来主要是渲染进程里静止时仍在每帧跑的循环，不是后端。现行做法，新增循环时照着来：

- Live2D：`static/live2d/live2d-core.js` 的 `_startIdleFpsGovernor()` 定时探测有没有动作播放、说话、拖拽，没有就降到 `LIVE2D_IDLE_FPS`，有活动再升回配置帧率。`static/frame-pacing.js` 只在 Electron pet 窗口生效，配置帧率明显低于刷新率时改用定时器驱动。
- compact surface 的定位循环（`scheduleCompactMinimizeBallTracking`）：拖拽 / 缩放期间每帧同步，静止后再跑几帧就停，有变化时由调用方重新触发。
- `frontend/react-neko-chat/src/useFocusGlow.ts`：数值衰减到底后停掉 `requestAnimationFrame`，下次有新值再启动。呼吸效果是 CSS 关键帧，不需要 JS 每帧重写同一个值。

判断要不要常驻循环：只有「没有事件可订阅的连续状态」（时间流逝、模型屏幕位置、音频振幅）才需要轮询，而且要有停下来的条件。能用 `resize` / `ResizeObserver` / 自定义布局事件的，不要用每帧 `getBoundingClientRect`。

## 8. 桌面壳依赖主仓库的契约点

下面这些在主仓库里看起来只是普通的名字，但壳按名字使用它们。改名、删除、改语义之前先到壳仓库核对，并且两边一起改。

- **路由路径**：`/`、`/chat`、`/chat_full`、`/subtitle`，以及其它被做成独立窗口的页面。路径本身就是协议。
- **`chat.html` 的 body 属性**：`data-chat-host-kind`、`data-initial-chat-surface-mode`、`electron-chat-window`。
- **壳注入、主仓库读取的全局**（主仓库代码必须在它们不存在时照常工作）：`window.__NEKO_MULTI_WINDOW__`、`window.nekoChatWindow`、`window.nekoSubtitle`、`window.electronShell` / `electronDesktopCapturer` / `electronSpatialAudio`、`window.__NEKO_DESKTOP_RUNTIME__`、`window.nekoPetDrag`、`window.__nekoRequestConfigInjection`。
- **主仓库提供、壳会调用的桥**：`window.reactChatWindowHost`（`static/app/app-react-chat-window/resize-drag-and-api.js` 末尾导出，文件头注释写明它是给宿主代码和 IPC 适配层用的稳定接口，包括 `setChatSurfaceMode`）。
- **事件**：`neko:config-injected`、`neko:desktop-compact-layout-change`、`neko:compact-surface-layout-change`、`neko:compact-surface-drag-grab`，以及 compact 的 geometry 数据结构（见 `docs/design/compact-chat-mode-design.md` 的「Geometry 合同」）。
- **`static/` 路径**：壳通过 HTTP 路由加载页面，对 `static/` 内部怎么分目录不敏感；但页面路由和上面这些名字不行。
