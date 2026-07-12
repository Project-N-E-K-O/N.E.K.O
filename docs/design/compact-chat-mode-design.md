# 首页紧凑聊天框功能与维护指导

> 本文是 `chat_min` 分支首页紧凑聊天框的主文档。
> 它必须自足：后续维护紧凑聊天框时，不能依赖其它准备删除的 notes 作为事实来源。
> 如果本文与当前代码、测试或真实运行结果冲突，以可复现证据和当前代码为准，并先更新本文再继续实施。

## 文档定位

本文覆盖当前分支已经落地的紧凑聊天框长期事实和维护边界，包括：

1. `compact / minimized` 两态聊天形态（活跃形态；被复活的 `full` 是冻结 legacy、与之严格隔离，见「已确认事实」第 2 条与 `FullChatSurface.tsx` 文件头，不在本文范围）。
2. `default / options / input` 紧凑态内部三态。
3. 紧凑 surface、最小化 ball、选项层、工具转轮、内联历史、历史气泡拖拽与发送链路。
4. Web、独立 `/chat`、NEKO-PC 桌面壳三端的样式、geometry、命中、bounds 和拖拽边界。
5. 近期 `chat_min` 分支对紧凑语音显字、历史拖拽、桌面桥接和玻璃背景的收口经验。

本文不记录临时试错过程，也不把“未来设想”写成已完成事实。若其它旧文档仍存在但与本文冲突，按本文和当前代码处理。

## 核心目标

首页紧凑聊天框不是缩小版完整聊天窗口，而是首页角色构图里的底部伴随式交互器。

目标：

1. 让首页交流焦点回到 YUI / 猫娘模型，而不是大面积聊天面板。
2. 用低信息密度保留聊天、选项、输入、附件、工具、历史选择和历史拖拽发送能力。
3. 默认优先展示当前轮短句与推荐选项，输入作为用户主动展开能力。
4. 历史、导出、拖拽发送都围绕同一个 compact surface island，不新增业务协议分叉。
5. Web、独立 `/chat` 和 NEKO-PC 桌面壳的用户可见结果尽量一致；桌面端可通过 Electron 原生窗口、bounds、shape、pointer passthrough 和外部 ball window 适配。
6. NEKO 提供消息、选项、附件、工具和发送业务语义；NEKO-PC 是外壳和窗口协调层，不承载新的聊天业务协议。

## 非目标

1. 不重写消息 schema、聊天协议、历史存储或后端会话系统。
2. 不把首页 compact 直接推广为所有页面和所有窗口的默认聊天形态。
3. 不恢复旧 `#chat-container` 作为实现依据。
4. 不把字幕系统改造成 compact 主文本源。
5. 不把 GalGame 大面板换皮后当成 compact 选项层。
6. 不创建教程专属 compact UI。
7. 不让 NEKO-PC 的窗口限制反向定义网页端产品目标。
8. 不把历史图片拖出到外部应用或系统保存链路作为当前已实现目标；当前完成的是历史内容拖到角色后发送给当前角色。

## 当前分支历史摘要

以下提交是维护本文时需要知道的当前分支事实来源：

1. `85e9b57f style(chat): refine compact glass surface`
   - 统一 compact surface frame 的磨砂玻璃背景、背景模糊、浅色/暗色文字可读性。
2. `7a3590cc fix(chat): preserve compact speech turn tails`
   - 紧凑语音显示要保留当前/最新流式尾部，不能被同一助手回合里更旧的消息 id 覆盖。
3. `e9ce3e38 fix(chat): stabilize compact history drops`
   - 历史拖拽发送要保持 sessionId 防串扰，并避免 avatar interaction deferral 把恢复后的附件错误带进历史文本发送。
4. `2d01d58a fix(chat): stabilize compact history drag rebase`
   - 历史拖拽期间源气泡和临时气泡必须跟随窗口 / 历史列表 rebase，不能因新消息或桌面 carrier 变化断链。
5. `f6ae38c1 feat(chat): expose compact history drag bridge state`
   - 页面向桌面壳暴露 compact history drag 状态，供 NEKO-PC 扩窗、rebase、drop target 和 passthrough 使用。
6. `a6aee670 feat(chat): add compact history drag delivery`
   - 历史文本拖到角色后发送给当前角色；消息 schema、App、Panel、静态发送桥和去重测试都有对应改动。
7. `8325ed8b` / `6d6a693f`
   - 收口 compact interaction、工具恢复、命中、history geometry、测试和样式稳定性。
8. `002407de refine compact history controls`
   - 历史显隐改为常驻 handle 控制；工具转轮里的历史/导出按钮改为控制历史操作栏。
9. `e7a8269 stabilize compact resize carrier bounds`
   - 桌面端 compact resize 期间锁住 carrier 纵向 bounds，避免历史 extra island 在首次 resize 时触发窗口纵向跳动。

## 当前真实代码链路

后续修改必须基于当前真实生效链路，不按历史印象修改。

### NEKO React Chat

主要文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
3. `frontend/react-neko-chat/src/message-schema.ts`
4. `frontend/react-neko-chat/src/styles.css`

已确认事实：

1. 当前聊天 UI 以 React chat 为准；旧 `#chat-container` 只作为兼容 DOM 存在。
2. 宿主形态是 `chatSurfaceMode: 'full' | 'compact' | 'minimized'`。`full` 是被复活的**冻结 legacy** 完整聊天窗口，由 `App.tsx` 顶层无 hooks 的 dispatcher 路由到自包含的 `FullChatSurface.tsx`（删除前那版 App 的冻结快照），与本文覆盖的 `compact / minimized` **严格代码隔离**：两子树互斥挂载，hooks/state 不共享，full 不再迭代——本文只描述活跃的 compact/minimized，full 见 `FullChatSurface.tsx` 文件头。`full` 不进 `CHAT_SURFACE_MODE_SEQUENCE`（compact↔minimized 的 cycle），只由显式 `setChatSurfaceMode('full')`（如 NEKO-PC 托盘切换）进入。
3. compact 内部状态是 `compactChatState: 'default' | 'options' | 'input'`。
4. `effectiveCompactChatState` 会在存在 ChoicePrompt / GalGame options 时把 compact 推到 `options`，不需要业务层另造状态。
5. compact 主体 DOM 已统一为：
   - `.compact-chat-surface-shell`
   - `.compact-chat-surface-frame`
   - `data-compact-geometry-owner="surface"`
   - `data-compact-geometry-item="capsule" | "input" | "resizeHandle" | "toolFan" | "history" | "historyHandle" | "choice"`
   - `data-compact-geometry-part="capsuleBody" | "inputBody"`
   - `data-compact-drag-surface="true"` 声明 compact 对话框本体 surface；整体拖拽只指这个本体，不包含历史、工具轮盘、选项层等浮层。
   - `data-compact-no-drag="true"` 声明 textarea、工具按钮、resize、历史、选项等真实控件和浮层排除拖拽。
   - 工具轮盘 toggle / fan 原点仍是 `data-compact-no-drag`（宿主命中判定不自动起拖），但 App.tsx 在原点按下并移动超阈值时额外派发 `neko:compact-surface-drag-grab`（带按下点 client/screen 坐标），让宿主以该点为锚启动本体拖拽——使轮盘中心兼作「按住拖动文本框」把手；点按仍展开/关闭轮盘、悬停展开保留、轮盘边缘拖动仍旋转。拖动后补发的 click 用独立的 origin suppress 标志吞掉（不能复用 wheel suppress，轮盘关闭 effect 会清它）。Wayland 走原生 app-region 拖拽，事件式起拖不适用。
6. 早期 `.compact-chat-capsule-shell` / `.compact-chat-input-shell` 已不是当前主体事实，后续文档和实现不要再按这两个旧类名设计。
7. `.compact-chat-surface-frame` 是同一个 54px 高的本体：`default/options` 内放 capsule button，`input` 内放 textarea 和右侧工具/发送按钮。
8. 旧蓝线拖拽手柄 `.compact-chat-drag-handle` / `data-compact-drag-handle="true"` 已删除，不应恢复；对话框本体拖拽走 `data-compact-drag-surface` / `data-compact-no-drag`。
9. 左右缩放手柄是 `.compact-chat-resize-handle-left/right`，通过 `neko:compact-surface-resize-request` 与宿主同步宽度。
10. 工具转轮通过 portal 挂到 `document.body`，并以 `data-compact-geometry-item="toolFan"` 进入 geometry。
11. 历史层由 `CompactExportHistoryPanel` 挂载到 `app-shell` 内，锚点是 `.compact-export-history-anchor`，并以 `data-compact-geometry-item="history"` 进入 geometry。
12. 历史显隐由常驻 `.compact-history-visibility-handle` 控制，使用 `data-compact-geometry-item="historyHandle"` 进入 geometry。
13. 历史关闭时，`CompactExportHistoryPanel` 只在 closing 动画期间短暂保留挂载；`COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS` 到期后必须卸载，避免关闭态继续接收新 `messages` 并闪现历史气泡。
14. ChoicePrompt 和 GalGame options 共享 compact choice layer；ChoicePrompt 优先，GalGame 在无 ChoicePrompt 时显示。

### NEKO 宿主与静态桥

主要文件：

1. `static/app/app-react-chat-window.js`
2. `static/app-buttons.js`
3. `templates/index.html`
4. `templates/chat.html`
5. `static/css/index.css`

已确认事实：

1. `templates/index.html` 和 `templates/chat.html` 都加载 `/static/react/neko-chat/neko-chat-window.css` 和 `neko-chat-window.iife.js`。
2. 因此 compact surface 的 React CSS 在首页、独立 `/chat` 和 NEKO-PC 承载页上是同一份构建产物。
3. 改 `frontend/react-neko-chat/src/*` 后必须运行 `bash build_frontend.sh`，确保 `static/react/neko-chat/neko-chat-window.css` 已同步。
4. `static/app/app-react-chat-window.js` 负责：
   - `compact ↔ minimized` 形态切换。
   - compact surface 位置和宽度持久化。
   - `--compact-surface-left/top/width/height` 和 `--desktop-compact-surface-*` CSS 变量同步。
   - surface geometry 收集、union、hit rect、native rect 输出。
   - 最小化 ball 的独立定位和 geometry。
   - resize session、desktop resize active 和 layout-change 事件。
   - 监听 `neko:compact-surface-drag-grab`（来自 React 工具轮盘原点拖拽），非 Electron 时以事件坐标为锚启动 compact surface 本体拖拽（复用既有 startDrag/全局 mousemove/mouseup 与落点 click 守卫）。Electron 由 `preload-chat-react.js` 监听同一事件改走原生窗口拖拽。
5. `static/app-buttons.js` 是发送桥之一。compact history 文本发送必须带清晰 session / request 语义，不能让已有 composer 附件在 deferred send 中被误带上。
6. 语音模式 / `composerHidden` 下的 history drop 只保留前端拖拽、命中和收束动效；真实发送必须在 `sendCompactHistoryDropPayload` 边界跳过，不能通过改 React 拖拽 phase 或样式来伪装。
7. `static/music_ui.js` 的音乐播放器在 compact 模式下优先挂到常驻 `.compact-music-player-mount#music-player-mount`；历史关闭或卸载不能把播放器挪回 composer fallback，但播放器视觉显隐必须跟随历史打开、closing、closed 状态，也不能被通用 `#music-player-mount` 样式撑成超过 compact surface 的横向尺寸；音量弹层展开/收起时必须刷新 compact geometry，避免浮出播放器原生矩形的滑块看得见但不可点。

### NEKO-PC 桌面壳

主要文件：

1. `../N.E.K.O.-PC/src/preload-chat-react.js`
2. `../N.E.K.O.-PC/src/preload-pet.js`
3. `../N.E.K.O.-PC/src/desktop-compact-layout.js`
4. `../N.E.K.O.-PC/src/main.js`
5. `../N.E.K.O.-PC/src/main/window-host-ipc.js`
6. `../N.E.K.O.-PC/src/main/top-coordinator.js`

已确认事实：

1. NEKO-PC 消费 NEKO 页面 geometry，不应另算一套产品规则。
2. pet preload 提供 avatar screen bounds，chat preload 订阅后下发：
   - `window.__nekoDesktopAvatarBounds`
   - `window.__nekoDesktopCompactLayout`
   - `window.__nekoDesktopCompactExternalBall`
3. 桌面 compact surface 的 BrowserWindow bounds 由页面 surface geometry、history drag carrier bounds 和 workArea 共同派生。
4. 最小化 ball 按外部 ball window 思路承载，不应和 surface 绑在同一个大透明窗口里。
5. Native Wayland 下 compact 对话框本体拖拽需要让 `[data-compact-drag-surface="true"]` 走 `-webkit-app-region: drag`，并用 `[data-compact-no-drag="true"]` 排除真实控件和浮层；不能强行走全局 cursor polling。
6. 桌面历史拖拽已有桥接状态：
   - `window.__nekoDesktopCompactHistoryDragState`
   - `window.__nekoDesktopCompactHistoryPointerPassthrough`
   - `neko:compact-history-drag-state-change`
   - `neko:compact-history-drag-rebase`
   - `neko:compact-history-drag-desktop-target-change`
7. 桌面历史拖拽时，preload 会扩展 carrier window bounds、rebase 页面拖拽 rect，并根据 avatar bounds 判断是否 over target。
8. 历史空白区域需要 pointer passthrough；拖拽期间必须关闭 passthrough，避免拖拽链路丢事件。
9. Electron 透明窗口里 CSS 透明不等于点击穿透；必须同时考虑 BrowserWindow bounds、setShape/input region、页面 `pointer-events` 和 history passthrough。

## 两态聊天形态

首页聊天框只有一条连续形态链：

1. `compact`
   - 紧凑聊天框（默认）。
   - 承担当前轮预览、选项、输入、工具、内联历史、历史选择和历史拖拽发送。
2. `minimized`
   - 最小化小球。
   - 承担最轻入口和恢复链路。

规则：

1. 两者共享同一套消息、发送、附件、选项、教程和恢复语义。
2. 两者区别是视觉密度、承载面积和原生窗口 bounds，不是业务协议分叉。
3. 切换形态不能清空会话、重置选项或破坏输入状态恢复。
4. compact surface 的用户拖动位置只影响 surface，不影响 ball。
5. 从 compact 切到 minimized 时，桌面端必须同步 native ball 窗口；从 minimized 恢复 compact 时，必须走 compact carrier bounds 而非旧 full 面板尺寸。

> 复活的 `full`（冻结 legacy 完整聊天窗口）不在上述活跃形态链内，由顶层 dispatcher 路由到自包含的 `FullChatSurface.tsx`，与 compact/minimized 严格隔离；它的最小化走独立的「左下角呼吸灯球」支路（见下方「形态切换与本地测试」）。

## 形态切换与本地测试（full / compact / minimized）

`chatSurfaceMode` 有三态：`full`（完整聊天窗口）/ `compact`（悬浮对话条，默认活跃形态）/ `minimized`（折叠球）。

### 默认形态来源

宿主 `static/app/app-react-chat-window.js` 的 `getDefaultChatSurfaceMode()`（用户无持久化偏好时）：

- **Web / 浏览器** → `full`。
- **Electron 桌面壳** → `compact`：chat.html（electron chat body class）与 index.html 宠物窗（`window.__LANLAN_IS_ELECTRON_PET__`）都识别为 compact。
- **显式覆盖**：`window.__NEKO_CHAT_DEFAULT_COMPACT__ = true/false` 优先于上面的运行时识别，强制 compact / full。

用户的显式选择持久化在 `localStorage['neko.reactChatWindow.chatSurfaceMode']`（值只会是 `'full'` 或 `'compact'`；`minimized` 不持久化，恢复时回到 `lastRestorableChatSurfaceMode`）。

### 本地切换 / 测试配方（浏览器控制台，硬刷新生效）

```js
// 进 full
localStorage.setItem('neko.reactChatWindow.chatSurfaceMode','full'); location.reload();
// 进 compact
localStorage.setItem('neko.reactChatWindow.chatSurfaceMode','compact'); location.reload();
// 看「默认」形态（清掉显式偏好）
localStorage.removeItem('neko.reactChatWindow.chatSurfaceMode'); location.reload();
// 在浏览器里模拟 Electron 的 compact 默认
window.__NEKO_CHAT_DEFAULT_COMPACT__ = true;
localStorage.removeItem('neko.reactChatWindow.chatSurfaceMode'); location.reload();
```

### 各形态自测点

- **full**：渲染完整历史列表 + 完整 composer（底部工具条：导入图片 / 截图 / GalGame / 翻译 / 点歌 / 表情工具 + 发送圆钮；窗口变窄时工具折叠进溢出菜单 `⋯`）。点最小化 → 折向**左下角的蓝色呼吸灯球**（不贴角色、不折出屏幕、不走 idle dock）；点球展开 → **优先恢复上次拖拽/缩放后的记忆位置，仅在无记忆时居中**（不再回左中）。full 是冻结快照，行为对齐删除前的 full。
- **compact**：悬浮对话条 / 字幕胶囊 / 输入态 / 工具轮盘 / 内联历史。点最小化 → **毛线球就地折叠**到自身底左锚点附近（不再强制贴角色/猫）。注：CAT2/CAT3 视觉层级（idle tier）下 compact 仍会自动贴猫的 idle dock，那是层级自动触发的特性，不是手动点最小化的行为。
- **minimized**：折叠球；点球恢复回 `lastRestorableChatSurfaceMode`（full 回 full、compact 回 compact）。

### 三端必测（react-neko-chat 改动通用）

`index.html` 宽屏 / `index.html` 窄宽（<768px 纯 CSS 手机版）/ `chat.html`（Electron，Chromium fork，部分 Web API 行为与浏览器不同）。

## Compact 内部三态

`chatSurfaceMode === 'compact'` 时，内部三态如下。

### `default`

1. 显示 `.compact-chat-surface-frame` 胶囊和当前短句。
2. 历史层是否显示由独立 history visibility state 控制；初次启动默认显示历史，但历史不属于胶囊本体高度。
3. 不展开 textarea。
4. 点击胶囊进入 `input`。
5. 语音模式 / `composerHidden` 下，点击胶囊不能请求或恢复 `input`，compact 只保留当前文字展示和已打开的历史显示。

### `options`

1. 选项层显示在 compact surface 上方或下方。
2. 底部胶囊仍作为同一交互器锚点存在。
3. ChoicePrompt 优先于 GalGame options。
4. 下方空间不足时选项显示到上方；上下都不足时受控压缩并内部滚动。

### `input`

1. `.compact-chat-surface-frame` 内切换为 textarea + 右侧按钮。
2. 空输入且无附件时，右侧按钮是工具入口。
3. 有文本或附件时，右侧按钮是发送。
4. 输入态高度受控，不允许被 composer 撑成独立大面板。
5. blur、发送、工具关闭后必须能自然回到展示态。

## Compact Surface 视觉合同

当前 surface 本体是 `.compact-chat-surface-frame`，不是外层窗口 shell。

规则：

1. 背景、边框、磨砂和文字可读性只改 `.compact-chat-surface-frame` 及其子层，不要把背景加到 `#react-chat-window-shell` 或整个透明窗口外壳。
2. 当前玻璃背景使用：
   - `::before` 承载半透明渐变面层。
   - `::after` 承载轻量顶部雾面高光。
   - `backdrop-filter: blur(18px) saturate(1.22) brightness(1.08)`。
   - 浅色与暗色模式各自定义 `--compact-chat-surface-bg`、`--compact-chat-surface-edge`、`--compact-chat-surface-glow`。
3. 这层背景是为了可读性，不是凸起按钮；避免强底部内阴影、过重外投影和强高光造成“胶囊按钮凸起”。
4. 文字层必须在未过滤层，保持清晰。不要把文本放进 blur/goo/filter 层。
5. 文字可读性优先靠稳定半透明底板和适度文字颜色，不靠过度加粗或硬阴影。
6. frame 保持 `height/min-height/max-height: 54px`；扩展能力应通过历史层、选项层、工具层或桌面 bounds，不让本体长高成完整面板。

## Compact Surface Island

Surface island 包含：

1. `.compact-chat-surface-frame` 本体，同时也是 compact 对话框本体拖拽 surface。
2. 左右 resize 手柄。
3. ChoicePrompt / GalGame options。
4. 工具转轮。
5. 内联历史 / 导出历史层。
6. 历史拖拽视觉层。

旧蓝线拖拽手柄不再属于 surface island；compact 对话框本体拖拽由 `.compact-chat-surface-frame` 声明承担。

定位：

1. 未保存位置时，默认基于模型 bounds 和安全区计算，处于模型可见区域偏下方。
2. 有用户保存位置时，优先使用保存位置并 clamp。
3. 默认位置只看聊天框本体，不把选项、历史、工具转轮或拖拽视觉层计入初始锚点。
4. 输入态、选项打开、工具打开、历史打开、右侧展开栏打开，都不能改变用户选择的 surface anchor。
5. 如果需要扩展可视/命中区域，应扩 native/window bounds 或 geometry，不要让 surface 自身跳位。

命中：

1. 只有可见本体、输入、选项、工具、历史滚动区、历史控件、拖拽层和 resize 手柄可命中。
2. 透明包裹层必须穿透。
3. 关闭态、空态、未加载态不能留下透明但吃事件的大矩形。
4. 历史气泡之间、气泡左右留白等非对话透明区应尽量 passthrough，尤其是桌面端。

层级：

1. surface 应稳定显示在模型视觉层上方。
2. ChoicePrompt / GalGame 选项应在历史层上方。
3. 工具转轮在输入器上方。
4. 历史拖拽视觉层在 history/source 之上，但不应污染普通命中。
5. 旧蓝线不再参与层级；本体拖拽命中不能扩大成大透明面。

## Compact Ball Island

Ball island 包含最小化小球视觉与点击区域。

规则：

1. ball 基于模型 bounds 位于模型左侧。
2. ball 使用 viewport/workArea clamp，不能出屏。
3. 没有模型 bounds 时才使用 fallback，并视为降级路径。
4. ball 不随 compact surface 本体拖拽移动。
5. ball 不读取 compact surface localStorage。
6. ball 不参与 surface bounds 计算。
7. 桌面端优先由独立 ball window 承载，不和 surface 之间生成大透明命中区域。

## Geometry 合同

Compact Interaction Geometry 是紧凑态的根合同。所有可见、可点、可滚动、可拖拽的 compact 区域都必须能被 geometry 解释。

### Geometry Item

每个 compact 交互区域至少需要表达：

1. `owner: surface | ball`
2. `kind / item`
3. `visualRect`
4. `hitRect`
5. `nativeRect`
6. `interactive`

当前常见 item：

1. `capsule`
2. `input`
3. `choice`
4. `history`
5. `historyHandle`
6. `toolFan`
7. `resizeHandle`
8. `ball`

规则：

1. `surface` union 只包含 surface 相关区域。
2. `ball` rect 只包含小球。
3. surface 和 ball 之间不能通过一个大透明矩形相连。
4. 子组件允许视觉浮出父 DOM，但浮出的可见区域必须注册进 geometry。
5. 新增 compact 浮层时，同步补 DOM 身份、geometry item、hit 策略、native bounds 和验证项。
6. `resizeHandle` 这类 aria-hidden 控件只有在 collector 明确允许时才能进入 geometry；旧 `dragHandle` 不再进入 geometry。

### 页面 Geometry 来源

1. 页面真实 DOM rect 是 geometry 的事实来源。
2. `static/app/app-react-chat-window.js` 聚合 compact DOM、avatar bounds、Electron override，并输出：
   - `surfaceItems`
   - `surfaceUnion`
   - `surfaceHitRects`
   - `surfaceNativeRects`
   - `ballRect`
   - `externalBall`
3. React 组件用稳定 `data-compact-geometry-owner` 和 `data-compact-geometry-item` 暴露身份。
4. NEKO-PC preload 只消费页面 geometry 或同名同义的过渡 selector，不能另算产品规则。

### Electron Native Region

1. 桌面端 BrowserWindow bounds 只能覆盖真实需要显示 / 命中的 surface union。
2. setShape/input region 应从 geometry hit/native rect 派生。
3. `setIgnoreMouseEvents` 只能作为整窗 fallback 或 pointer passthrough 手段，不适合作为多区域命中的唯一方案。
4. native region 只能解决点击区域，不能承载视觉；小球远离 surface 时必须有真实视觉承载。
5. geometry 更新必须 hash/debounce，避免文字显字、模型呼吸或鼠标移动造成高频 native region 更新。

## 输入态合同

1. input 与 capsule 使用同一个 `.compact-chat-surface-frame`，通过 `data-compact-chat-state` 和 `data-compact-geometry-item` 区分语义。
2. textarea 只在 input 态渲染，不把 full composer 搬进 compact。
3. `.composer-input` 高度固定在 compact frame 内，长文本内部滚动。
4. 附件预览不能把 compact 输入态撑成 full composer。
5. 工具转轮通过 portal 浮出，不参与 input 本体高度测量。
6. 工具转轮打开时仍属于 surface geometry。
7. 空输入且无附件时右侧按钮打开工具；有文本或附件时右侧按钮发送。
8. 输入态 blur/collapse 不能被工具层、按钮 pointerdown 或发送状态卡死。
9. 语音模式 / `composerHidden` 下禁止进入 compact input；不得暴露 textarea、工具转轮、GalGame 或其它会触发文本/道具交互的入口。

## 选项层合同

1. `choicePrompt` 优先于 GalGame options。
2. ChoicePrompt 和 GalGame options 都属于 Compact Surface Island。
3. 选项层从 surface 上方或下方显示，不能塞回胶囊内部。
4. placement 只能在 `above` / `below` 之间选择真实可见位置。
5. 下方空间不足时优先显示到上方，不通过把聊天框弹走规避。
6. 上下都不足时，选项层受控压缩并内部滚动。
7. 选项层关闭后不能保留透明命中区域。
8. 选项层必须进入 geometry 和桌面 native bounds，否则 Electron 下会被裁切。
9. 历史打开时，选项层可以盖在历史层上方，不参与历史层重排。

## 内联历史与导出合同

Compact 历史默认在初次启动时显示。历史列表本身由常驻展开/收起 handle 控制；工具转轮中的历史/导出入口不再直接开关历史列表，而是开关历史下方的操作栏。

当前已落地事实：

1. 历史层由 `CompactExportHistoryPanel` 实现。
2. 历史锚点是 `.compact-export-history-anchor`，属于 `data-compact-geometry-owner="surface"`。
3. 历史位于 compact surface 上方，宽度基于 `--compact-export-surface-width` 和比例计算。
4. 最新消息在最下方，靠近聊天框。
5. 历史区域有最大高度，超出后内部滚动。
6. 打开历史后，如果用户继续聊天，应自动保持或恢复到底部。
7. 没有 `neko.reactChatWindow.compactExportHistoryOpen` 持久化记录时，历史默认打开；用户显式收起后持久化为 `false`。
8. 常驻 `.compact-history-visibility-handle` 只控制历史列表显隐；它关闭历史时不清除操作栏打开状态。
9. 工具转轮历史/导出按钮控制操作栏显示；如果历史关闭时点击该按钮，应先打开历史并显示操作栏。
10. 历史关闭后可以播放 closing 动画；动画结束后历史面板必须卸载。关闭期间新增的文字/语音消息不得进入历史面板 DOM，也不得在历史区域短暂闪现；重新打开历史时再按最新 `messages` 完整渲染。
11. closing 期间历史气泡、操作栏和预览控件都不进入 history hit region，不保留按钮语义、键盘焦点或透明命中区。
12. 操作栏显示期间进入选择模式：气泡点击 / 键盘 Enter / Space 可以选中或取消选中历史消息。
13. 操作栏隐藏时退出选择模式：必须清空当前选中项，并禁止继续通过点击或键盘选择；拖拽源识别和拖拽发送不受这个选择模式限制。
14. 操作栏包含选择和导出动作，如计数、全选、取消/清空、反选、导出预览等；操作栏自身进入 history hit region。
15. 选择状态、导出预览和操作栏显示状态由 React state 管理；操作栏状态可以跨历史显隐保留，但只在历史实际打开时算作可见。
16. 音乐播放器有独立 `.compact-music-player-mount#music-player-mount`，它与历史消息面板分离并作为 `musicPlayer` 几何项进入 compact surface；历史关闭/卸载后播放器必须继续停留在该独立挂载点，但视觉上要随历史一起收起和展开；横向尺寸必须限制在 compact surface 宽度内；历史记录底部必须为播放器高度和阴影预留间距，不能与播放器重叠；音量滑块展开到播放器外侧时要触发 geometry refresh。
17. 预览关闭时要清理 stale export error 和必要 preview lifecycle 状态，避免重新打开显示旧错误。
18. 历史透明区域不能长期遮挡后方；可见气泡、按钮、预览控件和必要滚动区域可命中，气泡间透明区应尽量穿透。
19. GalGame / ChoicePrompt 出现时，选项层在历史层上方。

## 历史气泡拖拽与发送合同

当前分支已实现历史文本/气泡拖到角色后发送给当前角色，不实现“图片拖出到外部应用保存”。

行为：

1. 拖拽源是历史消息气泡或可拖拽内容块。
2. 拖拽开始后源气泡内容隐藏/弱化，但原处占位不塌陷。
3. 拖出的临时气泡应基本保持原气泡样式；形变集中在源头、连接和边缘拉扯，不把文本层滤糊。
4. 橡皮泥/QQ 气泡参考的核心是：
   - 原点小圆/软块随距离变化。
   - 中间连接是闭合 path / gooey 辅助融合。
   - 文本内容在未过滤层保持清晰。
   - 长气泡只取靠拖动方向的一段圆角端作为连接锚点，不让整个长矩形参与胶带计算。
5. 拖到角色/模型有效目标上时，源处不塌陷地进入发送动画并原处消失。
6. 未拖到有效目标上时，回弹并恢复源气泡。
7. 语音模式 / `composerHidden` 下，拖拽视觉、命中反馈和发送收束动画保持不变，但宿主发送边界必须阻断实际发送。

实现事实：

1. `CompactExportHistoryPanel.tsx` 维护 activeDrag、sessionId、origin rect、drag visual rect、connection rect、elastic path、over target 等状态。
2. 页面发出 `neko:compact-history-drag-state-change` 供 NEKO-PC 桌面壳消费。
3. NEKO-PC 可能在拖拽期间扩展 BrowserWindow carrier bounds，并通过 `neko:compact-history-drag-rebase` 让页面拖拽 rect 跟随窗口 rebase。
4. NEKO-PC 通过 avatar bounds 判断 desktopOverAvatar，并通过 `neko:compact-history-drag-desktop-target-change` 回传给页面。
5. drop delivery 必须携带 sessionId；事件缺失 sessionId 或 sessionId 不匹配时不能 mutate active drag。
6. 文本发送必须 snapshot 意图，不能让拖拽期间临时恢复的 composer 附件混入历史文本发送。
7. 语音模式下的历史拖拽不应在 React 拖拽层改动 phase 或动效；只在 `static/app-buttons.js` 的 `sendCompactHistoryDropPayload` 真实发送边界返回成功并跳过 `sendTextPayload`。

维护注意：

1. 不要把拖拽效果拆成明显的几个独立图形；连接必须贴边/贴角并柔和融合。
2. 不要让原气泡完整显示在原处；原处应是源头软块/占位效果。
3. 不要让临时气泡变成和原气泡完全不同的样式。
4. 不要让小圆点在长气泡左右大范围跳动；锚点应受角色方向和气泡角色约束。
5. 拖拽时如果有新消息导致历史列表重排，连接点必须随源气泡真实位置 rebase。
6. 桌面端拖拽时要特别注意卡顿：carrier bounds、pointer passthrough、drop target 判断和 native region 更新都要避免高频无效刷新。

## 当前文字与语音显示合同

Compact 当前文字是“当前轮轻提示”，不是完整历史记录、字幕或完整转录。

要求：

1. 默认从 React `messages` 提取最近可预览内容。
2. assistant streaming 时可以显示当前流式消息并做本地显字。
3. TTS / speech playback 可以作为显字节奏参考，但不是文本事实源。
4. 当 TTS 禁用或播放不可用时，compact 不能停在空白；必须按 fallback reveal 显示文本。
5. 同一 compact assistant turn 合并多个连续 assistant 消息时，preview id 要保持在当前/最新流式尾部或真正回合 id 上，不能被反向循环里的旧消息 id 覆盖。
6. 主动搭话 / 语音态必须显示当前正在说的文本，不能把上一轮已完成 assistant 回复当成当前内容。
7. 非 streaming 场景必须截断，避免重新长成历史聊天框。
8. 清理 `[play_music:...]` 等控制指令和多余空白。
9. 若未来引入“正在说到哪一句”的事实驱动显示，必须单独设计稳定、可回退、跨端一致的信号源。

## 三端显示关系

三端指：

1. NEKO 首页。
2. NEKO 独立 `/chat` 页面。
3. NEKO-PC 桌面壳承载的 React chat 页面。

当前事实：

1. 首页和 `/chat` 都加载同一份 `/static/react/neko-chat/neko-chat-window.css`。
2. NEKO-PC 不维护独立 React CSS 副本，而是承载 NEKO 页面并注入窗口/桌面协调逻辑。
3. 因此 compact frame 视觉样式改动应同时影响三端。
4. 三端最终视觉仍需要实际打开验证；代码链路一致不等于运行时截断、透明窗口、DPI、Wayland/Windows/macOS 组合都已验收。

## 桌面端适配原则

1. 桌面端 surface 和 ball 的产品语义与网页端一致。
2. 桌面端可以用独立 ball window、BrowserWindow bounds、setShape/input region 和 pointer passthrough 实现命中与裁切。
3. 桌面端不能因为原生窗口实现限制而把 ball 重新绑回 surface。
4. 桌面端拖拽保存的是 surface anchor，不是 ball anchor。
5. 桌面端 compact resize / relayout 只影响 compact carrier，不再维护 full 模式窗口快照。
6. 从 minimized 恢复 compact 时，必须恢复 compact bounds、shape、ignore-mouse-events、external ball 和 resizable 状态。
7. Windows 展开 fallback 需要真实 resizable style toggle，不能把 `setResizable(false)` 到 `setResizable(false)` 当成 cache busting。
8. Native Wayland compact 对话框本体拖拽应保留原生 drag 策略，不走不可用的全局 cursor/window polling。
9. ReactChat 紧凑窗口必须保持在模型上方，并由 window manager / top coordinator 维护层级。

## 修改指导

### 修改前

1. 先跑 `git status --short`。
2. 明确本轮只改目标文件；未跟踪 `.agent/notes` 默认不动。
3. 判断改动属于：
   - React 组件结构。
   - CSS 视觉 / 命中 / 层级 / 高度。
   - Web 宿主状态 / geometry。
   - NEKO-PC preload / native window。
   - 后端消息 / 工具协议。
4. 能在前端解决的，不先改后端。
5. 能复用现有消息、选项、附件、工具回调的，不新增协议。
6. 涉及桌面端时，同时对照网页端实际表现，不允许只修 PC 表面症状。

### 修改中

1. 新增 compact 可见区域时，同步补：
   - DOM 身份。
   - geometry item。
   - CSS pointer-events。
   - Electron native bounds / shape / passthrough 验证。
2. 修改 surface 定位时，确认：
   - 用户保存位置优先。
   - 默认位置只看聊天框本体。
   - 不读取 ball 位置。
   - 不被选项、历史、工具、拖拽层撑跑。
3. 修改 ball 时，确认：
   - 只看模型 bounds。
   - 不读 surface position。
   - 不被 compact surface 本体拖拽影响。
4. 修改选项层时，确认：
   - 不塞回胶囊。
   - 下方不足能转到上方。
   - 不让聊天框为选项弹走。
5. 修改输入态时，确认：
   - 高度受控。
   - 发送和工具入口语义不混。
   - 输入态能回到展示态。
6. 修改历史层时，确认：
   - 历史透明区不污染命中。
   - 气泡点击、多选、滚动、拖拽意图区分清楚。
   - 选项层盖在历史上方时 native bounds 不裁切。
7. 修改历史拖拽时，确认：
   - sessionId 严格匹配。
   - source rebase 正确。
   - 桌面 carrier bounds 不高频抖动。
   - drop target 与 avatar bounds 一致。
8. 修改桌面端时，确认：
   - 页面 geometry 与 preload 消费一致。
   - BrowserWindow bounds 没有包住无用透明区域。
   - setShape/input region 没有漏掉实际可点击浮层。
   - Wayland / Windows / macOS 分支语义没有互相破坏。

### 修改后

如果改了 `frontend/react-neko-chat/src/*` 或前端宿主链路：

1. 运行 `bash build_frontend.sh`。
2. 确认构建成功并同步到 `static/react/neko-chat/*`。
3. 至少跑与改动范围匹配的静态测试或 typecheck；若已有无关失败，要说明失败项和是否本次引入。

如果改了 NEKO-PC：

1. 做桌面端真实启动检查。
2. 检查 `compact ↔ minimized` 两态。
3. 检查拖拽、resize、选项、输入、工具、历史、历史拖拽、层级和点击穿透。
4. 对照网页端目标表现确认一致。

## 验收清单

基础形态：

1. `compact ↔ minimized` 切换稳定。
2. compact 初次启动默认展示历史；用户显式收起历史后，下次按持久化记录恢复收起。
3. compact 当前文字在下方 surface 内，不出现上方独立说话框。
4. minimized ball 位于模型左侧，且不随 surface 拖拽。

Surface：

1. 用户拖动 surface 后位置被保存。
2. 未保存位置时，默认在模型可见区域偏下。
3. surface 不被模型压住。
4. 打开工具、选项、历史或右侧展开栏时，surface anchor 不抖动。
5. 左右 resize 不突破 workArea，也不污染 legacy full bounds 存储。
6. 玻璃背景在浅色/暗色模式下都能读清文字，且不像凸起按钮。

命中：

1. compact 周围透明区域不挡后方内容点击。
2. 可见胶囊、输入、选项、工具、历史、拖拽层和 resize 手柄都能稳定点击。
3. 选项/历史关闭后不留透明命中区。
4. 桌面端 BrowserWindow bounds / setShape / passthrough 与页面 geometry 一致。

选项：

1. ChoicePrompt 优先级高于 GalGame options。
2. 选项不会在模型头部多出一份。
3. 下方空间不足时，选项显示到聊天框上方。
4. 选项不会被 Electron 原生窗口裁切。

输入：

1. 输入态不会被 composer 撑成独立大面板。
2. 长文本内部滚动。
3. 空输入右侧按钮打开工具转轮。
4. 有文本或附件时右侧按钮发送。
5. 输入态能自然回到展示态。
6. 语音模式 / `composerHidden` 下点击 compact 胶囊不请求 `input`。
7. 语音模式 / `composerHidden` 下不显示 textarea、工具转轮或 GalGame 入口。

历史：

1. 历史列表初次默认显示；常驻展开/收起 handle 可以显示或隐藏历史列表。
2. 历史最新消息在下方并可自动回到底部。
3. 工具转轮历史/导出按钮显示或隐藏操作栏，不直接切换历史列表。
4. 操作栏显示时可以点击/键盘选中历史消息；操作栏隐藏时清空已选消息并禁止继续选择。
5. 操作栏隐藏后再次打开，选择、全选、反选、清空、导出预览可用。
6. 历史列表收起再展开时，操作栏显示状态可保留，但按钮高亮只反映当前实际可见状态。
7. 历史透明区不遮挡后方。
8. 历史关闭动画结束后，历史面板卸载；关闭期间继续发生文字/语音对话时，历史区域不出现新气泡闪现。
9. 历史重新展开后，关闭期间产生的新消息会按最新 `messages` 正常出现在历史中。
10. 播放中的音乐栏在 compact 模式下停留在独立播放器挂载点；历史打开时显示，历史 closing / closed 时同步收起且不再命中；历史打开、关闭或卸载都不能把它挪回 composer，横向宽度不能突破 compact surface，历史记录不能贴住或覆盖播放器；音量滑块展开后可以点击和拖拽。
11. 预览关闭不会保留旧 error。

历史拖拽：

1. 源气泡占位不塌陷，内容按设计隐藏/弱化。
2. 临时气泡样式接近原气泡。
3. 橡皮泥连接不明显断裂，不糊文字。
4. 未拖到角色时回弹恢复。
5. 非语音模式下，拖到角色时发送给当前角色，且不混入 composer 现有附件。
6. 语音模式 / `composerHidden` 下，拖到角色时保留命中与收束动效，但不实际调用发送。
7. 桌面端拖拽不因窗口 rebase、pointer passthrough 或 avatar target 判断造成卡顿/断链。

桌面端：

1. surface 和 ball 独立承载。
2. 拖拽 surface 不牵动 ball。
3. 模型移动不会强行覆盖用户保存的 surface 位置。
4. compact window 在模型上方。
5. 从 minimized 恢复 compact 后窗口 bounds、shape、resizable 和 ball 状态恢复正确。
6. Native Wayland 对话框本体拖拽仍使用可工作的原生拖拽路径。

## 禁止方案

1. 禁止用 `HEAD` 覆盖当前工作区紧凑态改动。
2. 禁止恢复 `.compact-chat-drag-handle` 或 `data-compact-drag-handle="true"` 作为 compact 主拖拽入口；同时禁止误删历史显隐按钮和桌面壳折叠球链路。
3. 禁止把 compact 背景加到 `#react-chat-window-shell` 这类外层透明窗口壳上。
4. 禁止把 ball 固定到视口左下角当作模型左侧定位。
5. 禁止让 compact surface 的持久化位置影响 ball 位置。
6. 禁止让 ball 和 surface 共用一个大透明矩形作为桌面端最终方案。
7. 禁止用全局粗暴 `pointer-events: none` 破坏输入、选项、工具、历史、历史显隐按钮或 ball。
8. 禁止只给 textarea 加固定高度就宣称解决输入态撑高。
9. 禁止只提高局部 `z-index` 就宣称解决模型上方层级问题。
10. 禁止把 GalGame / ChoicePrompt 选项塞回胶囊内部来绕过裁切。
11. 禁止选项或历史关闭后仍保留透明命中区域。
12. 禁止为未来功能预留透明但吃事件的大空区域。
13. 禁止让历史滚动误触发 compact surface 本体拖拽。
14. 禁止让历史、选项或工具层参与 ball 定位。
15. 禁止让视觉浮层依赖未登记的 `overflow: visible` 逃逸父容器。
16. 禁止把 NEKO-PC 的实现限制反向改成网页端产品目标。
17. 禁止把历史图片拖出保存链路写成当前已实现或必做目标。
18. 禁止在历史拖拽发送中忽略 sessionId 或复用可能混入附件的普通发送路径。

## 当前优先级

后续紧凑态相关改动按以下顺序判断优先级：

1. 先保护 compact ↔ minimized 两态和 compact 内部三态的状态语义。
2. 再保护 surface / ball 独立、geometry、命中和桌面 bounds。
3. 再保护选项、输入、工具这些当前核心交互。
4. 再保护内联历史 / 导出历史 / 历史拖拽发送。
5. 最后再做更多游戏化增强和更复杂动画。

原因：紧凑态的根本风险不是某个按钮的位置，而是角色附近交互器的几何、命中、层级和状态边界。基础稳定后，历史、导出、拖拽和表现层动画才不会继续堆成补丁。
