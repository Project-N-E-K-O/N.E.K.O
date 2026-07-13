# 翻译字幕面板功能文档

本文记录当前翻译字幕面板的实际功能、状态字段、页面结构和桌面端桥接方式。后续新增功能或调整样式时，先用本文理解现有实现，再阅读对应代码确认细节。

## 代码位置

NEKO Web / 共享显示层：

1. `static/subtitle/subtitle-shared.js`：字幕设置状态、渲染状态、DOM 引用、面板交互、设置控件、拖拽缩放基础逻辑。
2. `static/subtitle/subtitle-window.js`：独立 `/subtitle` 窗口页面逻辑、桌面端交互桥接、译文渲染、独立设置窗口调用、穿透轮询。
3. `static/css/subtitle.css`：字幕面板、controls、设置层、resize handle、Web host、desktop host、settings window host 的样式。
4. `static/subtitle-settings.html`：桌面端独立设置窗口页面。
5. `static/subtitle/subtitle-settings-window.js`：桌面端独立设置窗口状态同步和设置变更传播。
6. `static/app/app-react-chat-window`：React 对话框工具按钮，包括展开状态里的翻译按钮开关、按钮高亮同步和桌面字幕窗口桥接。

NEKO-PC 桌面壳（同级仓库 `/N.E.K.O.-PC`）：

1. `src/preload-subtitle.js`：字幕窗口 preload，转发转写文本、状态同步、窗口控制、设置窗口 IPC。
2. `src/main.js`：字幕窗口显示/隐藏、设置窗口 IPC、字幕设置变更转发。
3. `src/window-manager.js`：字幕窗口和字幕设置窗口的 BrowserWindow 创建、定位、销毁。
4. `src/main/window-control-ipc.js`：通用窗口移动、尺寸、拖拽、缩放 IPC；字幕窗口使用其中的 bounds 和 resize 逻辑。
5. `src/preload-common.js`：字幕窗口关闭后的聊天入口开关同步。
6. `src/preload-chat-react.js`、`src/preload-chat-full.js`：独立对话窗口向页面注入 `window.nekoSubtitleWindow`，让展开对话框里的翻译按钮通过桌面 IPC 切换真实字幕功能。

## 功能定位

翻译字幕面板是独立的译文显示浮层。它只显示用户选择语言的翻译结果，不显示原文，不写入聊天历史。

聊天框继续负责聊天入口、原文短句、输入、工具轮盘和历史记录。字幕面板负责译文显示、译文浮层操作和字幕相关设置。

## 数据状态

`static/subtitle/subtitle-shared.js` 维护两类状态：设置状态和渲染状态。

设置状态字段：

1. `subtitleEnabled`：翻译字幕显示开关，默认 `false`。
2. `userLanguage`：目标语言，默认 `zh`。
3. `subtitleOpacity`：背景不透明度，默认 `95`。
4. `subtitlePanelBounds`：面板尺寸，默认 `{ width: 655, height: 109 }`。
5. `subtitlePanelPosition`：Web host 面板位置，默认 `null`。
6. `subtitlePanelLocked`：位置锁定状态，默认 `false`。
7. `subtitleInteractionPassthrough`：透明区域穿透，默认 `false`；当前可见控件和 `subtitlePanelLocked` 绑定，兼容旧存储时可只恢复穿透但不锁定面板。
8. `subtitleDanmakuMode`：弹幕模式开关，默认 `false`。
9. `subtitleFontSize`：字幕字号，默认 `26`，有效值为 `16`、`21`、`26`、`34`、`44`。
10. `subtitleColorScheme`：字幕配色，默认 `default`，有效值为 `default`、`red`、`orange`、`yellow`、`green`、`blue`、`indigo`、`violet`。
11. `uiLocale`：UI 文案语言，默认来自当前页面语言，兜底 `zh-CN`。

设置状态持久化到 `localStorage`：

1. `subtitleEnabled`
2. `userLanguage`
3. `subtitleOpacity`
4. `subtitlePanelBounds`
5. `subtitlePanelPosition`
6. `subtitlePanelLocked`
7. `subtitleInteractionPassthrough`
8. `subtitleDanmakuMode`
9. `subtitleFontSize`
10. `subtitleColorScheme`

渲染状态字段：

1. `text`
2. `visible`
3. `subtitleEnabled`
4. `userLanguage`
5. `uiLocale`
6. `subtitleOpacity`
7. `subtitlePanelBounds`
8. `subtitlePanelPosition`
9. `subtitlePanelLocked`
10. `subtitleInteractionPassthrough`
11. `subtitleDanmakuMode`
12. `subtitleFontSize`
13. `subtitleColorScheme`
14. `subtitlePanelState`

`subtitlePanelState` 的有效值为 `clean`、`controls`、`settings`。

## DOM 结构

共享逻辑通过 `getSubtitleRefs()` 查找以下元素：

1. `#subtitle-display`：面板根节点。
2. `#subtitle-scroll`：文本容器。
3. `#subtitle-text`：译文文本节点。
4. `#subtitle-panel-controls`：外层 controls 容器。
5. `#subtitle-lock-btn`：锁定 / 解锁按钮。
6. `#subtitle-settings-btn`：设置按钮。
7. `#subtitle-close-btn`：关闭字幕面板按钮。
8. `#subtitle-settings-panel`：页面内设置 panel 或独立设置窗口中的设置主体。
9. `.subtitle-settings-label`：设置项标签。
   当前标签结构为 `.subtitle-settings-label::before` 承载图标，`.subtitle-settings-label-text` 承载文字；文字颜色或渐变只能作用在内层文字节点，不能挂在整个 label 父级上。
10. `#subtitle-lang-select`：语言选择。
11. `#subtitle-opacity-slider`：不透明度 slider，实际控制字幕背景透明度。
12. `#subtitle-opacity-value`：不透明度数值显示。
13. `#subtitle-font-size-select`：字体选择。
14. `#subtitle-color-scheme-select`：字幕配色选择。
15. `#subtitle-danmaku-mode-btn`：弹幕模式开关，写入并传播 `subtitleDanmakuMode`。
16. `.subtitle-resize-edge`：面板 resize handle。

`static/subtitle-settings.html` 当前包含五个设置项 / 入口：

1. 语言：`#subtitle-lang-select`，选项为 `zh`、`en`、`ja`、`ko`、`ru`、`es`、`pt`。
2. 不透明度：`#subtitle-opacity-slider`，范围 `0` 到 `100`，实际控制字幕背景透明度。
3. 字体：`#subtitle-font-size-select`，选项为 `16`、`21`、`26`、`34`、`44`。
4. 配色：`#subtitle-color-scheme-select`，选项为默认和红、橙、黄、绿、蓝、靛、紫。
5. 弹幕：`#subtitle-danmaku-mode-btn`，用于开启 / 关闭弹幕模式。

当前实际模板不包含独立的 `#subtitle-passthrough-toggle`。透明区域穿透由锁定状态间接控制。

## 面板样式和尺寸

`applySubtitlePanelBounds(display, bounds, options)` 将面板尺寸写入：

1. `data-subtitle-panel-width`
2. `data-subtitle-panel-height`
3. `style.width`
4. `style.height`
5. CSS 变量 `--subtitle-panel-width`
6. CSS 变量 `--subtitle-panel-height`
7. CSS 变量 `--subtitle-content-max-height`
8. CSS 变量 `--subtitle-font-size`

面板可自由缩放，但会夹紧到最小 `228px x 40px`，确保右上角三个 controls 按钮和两处角框装饰不会被挤出边界，并在按钮上下保留舒适余量。Web host 的可用宽度或高度小于面板尺寸时，实际渲染尺寸会临时收缩到可用 viewport，避免面板溢出宿主；viewport 变化时会重新计算，因此宿主缩小后不会溢出、放大后也会恢复逻辑尺寸。持久化尺寸仍保留正常下限。桌面独立窗口在面板四周另保留 `6px` resize inset，因此原生窗口的最小尺寸为 `240px x 52px`。过小的历史持久化尺寸在读取时也会自动夹紧。

当前面板基础字号由 `subtitleFontSize` 决定，默认 `26px`。`applySubtitlePanelBounds()` 会写入 `style.fontSize`、`data-subtitle-font-size` 和 `--subtitle-font-size`。`#subtitle-text` 继承面板字号；Web host 在长文本溢出时可能给 `#subtitle-text.style.fontSize` 写入临时缩小值；独立字幕窗口收到译文后会清空 `#subtitle-text.style.fontSize`，避免文本节点保留独立 inline 字号。

面板右上角三个 controls 按钮使用 `--subtitle-control-scale` 做整体等比缩放。缩放基准为默认面板 `{ width: 655, height: 109 }`，最小不低于 `1`，最大不超过 `2`。因此面板缩小时按钮不继续变小；面板放大时按钮跟随放大，最高到默认大小的 200%。

默认配色沿用浅色黑字 / 暗色白字表现。红、橙、黄、绿、蓝、靛、紫通过 `data-subtitle-color-scheme` 切换字幕文字、占位文字和角标线颜色；浅色模式使用经典基础色，暗色模式使用同色相的高亮版本。设置写入 `localStorage.subtitleColorScheme` 后，共享层会通过 `storage` 事件同步其它窗口，因此无需刷新即可让独立字幕窗口、独立设置窗口和 Web host 看到变化。

独立设置窗口页面使用 `body.subtitle-settings-window-host`。当前内容按固定五行设置项排布，CSS 侧要求最小内容尺寸为 `300px x 188px`；这个尺寸只描述页面内容承载空间，不负责创建桌面窗口。

弹幕模式开关复用 `.subtitle-settings-switch` / `.subtitle-settings-track`。它是可交互 checkbox，业务上写入并传播 `subtitleDanmakuMode`。

背景透明度由 `applyBackgroundOpacity()` 写入 CSS 变量：

1. `--subtitle-panel-alpha`
2. `--subtitle-panel-soft-alpha`
3. `--subtitle-panel-soft-mid-alpha`
4. `--subtitle-panel-soft-edge-alpha`

主题颜色由 CSS 根据 `html[data-theme="dark"]` / `html.dark` 切换。文字颜色、描边和阴影由 CSS 变量控制。

## 交互状态

`initSubtitleUI(options)` 初始化面板交互。

Clean 状态：

1. `data-subtitle-panel-state="clean"`。
2. 面板只显示译文文本。
3. controls 处于隐藏状态。

Controls 状态：

1. `data-subtitle-panel-state="controls"`。
2. 鼠标进入、点击、focus 面板时进入该状态。
3. 鼠标离开后通过 `CONTROLS_HIDE_DELAY_MS = 600` 延迟回到 clean。

Settings 状态：

1. `data-subtitle-panel-state="settings"`。
2. 点击设置按钮后进入该状态。
3. Web host 使用页面内 `#subtitle-settings-panel`。
4. 桌面独立字幕窗口使用外部设置窗口。

Escape 行为：

1. 设置层打开时按 Escape 关闭设置层并回到 controls。
2. 设置层未打开时按 Escape 将面板状态切到 clean。

## 按钮行为

锁定按钮：

1. 点击后切换 `subtitlePanelLocked`。
2. 同步设置 `subtitleInteractionPassthrough` 为相同值。
3. 更新 `aria-pressed`。
4. 通过 `applyLockButtonIcon()` 切换锁住 / 解锁 SVG path。
5. 通过 `propagateSetting({ type: 'lock', value })` 向宿主传播；共享 UI 内部同时带有 `subtitlePanelLocked` 和 `subtitleInteractionPassthrough` patch。

设置按钮：

1. Web host 打开或关闭页面内设置 panel。
2. 桌面独立字幕窗口调用 `openExternalSettings()` / `closeExternalSettings()`。
3. 设置打开时面板状态为 `settings`。

关闭按钮：

1. 先关闭设置层。
2. 更新 `subtitleEnabled` 为 `false`。
3. 通过 `propagateSetting({ type: 'toggle', value: false })` 向宿主传播。
4. 没有宿主关闭桥时，直接隐藏本地面板并更新 render state。

## 设置项行为

语言：

1. 读取和写入 `userLanguage`。
2. 变更后传播 `type: 'language'`。
3. 可触发 `options.onLanguageChange(nextLanguage, nextState)`。

不透明度：

1. 读取和写入 `subtitleOpacity`。
2. 变更后传播 `type: 'opacity'`。
3. UI 数值显示为百分比。

字体：

1. 读取和写入 `subtitleFontSize`。
2. 变更后传播 `type: 'fontSize'`。
3. Web host 会重新测量当前译文，必要时临时缩小文本节点字号以适配面板。
4. 独立字幕窗口通过 state sync 实时更新面板字号。

配色：

1. 读取和写入 `subtitleColorScheme`。
2. 变更后传播 `type: 'colorScheme'`。
3. `applySettingsToUi()` 将当前值写入 `data-subtitle-color-scheme`。
4. 独立字幕窗口和独立设置窗口通过 state sync 实时更新配色选择。
5. 其它同源窗口通过 `localStorage` 的 `storage` 事件接收 `subtitleColorScheme` 变化，避免必须刷新后才变色。

弹幕：

1. 当前通过 `#subtitle-danmaku-mode-btn` 提供弹幕模式开关。
2. 控件是可交互 checkbox，外层使用 `.subtitle-settings-switch`，轨道使用 `.subtitle-settings-track`。
3. 切换后写入 `subtitleDanmakuMode` 并传播 `{ type: 'danmakuMode', value }`。
4. 桌面独立字幕窗口开启弹幕模式时会进入临时布局：锁定面板、开启穿透、背景透明度临时设为 `0`，并订阅头像 bounds，把字幕窗口移动到头像附近的弹幕布局；面板底边通常与头像顶部保持 `12px` 间距，模型缩放时不随面板高度向头像内部漂移；头像上方空间不足且下方可容纳面板时，面板降级到头像下方并保持同样间距。桌面原生窗口的 `6px` 外沿也必须包含在工作区钳制范围内。
5. 弹幕模式渲染译文时，通过共享层按每两个逗号 / 句号 / 问号 / 感叹号等标点为一组切段，生成多条 `.subtitle-danmaku-item` 从右向左滚动；同一轨道里的文字通过 `.subtitle-danmaku-lane` 队列和固定间距排列，避免互相重叠；原始 `#subtitle-text` 仍保留完整译文用于状态和兼容链路。
6. 关闭弹幕模式时恢复进入前的面板 bounds、锁定状态、穿透状态、不透明度和 native window bounds。

透明区域穿透：

1. 当前实际没有独立可见设置项。
2. 锁定按钮会同时写入 `subtitlePanelLocked` 和 `subtitleInteractionPassthrough`。
3. 共享逻辑不再保留旧版 `#subtitle-passthrough-toggle` 兼容分支；穿透只能由当前锁定链路间接控制。

## Web host 行为

Web host 使用 `initSubtitleUI({ host: 'web' })`。

Web 面板位置通过 `subtitlePanelPosition` 保存，位置对象包含 viewport 坐标信息。拖动时会更新 `subtitlePanelPosition`；窗口尺寸变化时会 clamp 已保存位置。

Web 面板 resize 使用 DOM resize handle。resize 完成后更新：

1. `subtitlePanelBounds`
2. `subtitlePanelPosition`
3. `localStorage`
4. render state

## React 对话框翻译按钮

React 对话框的翻译按钮由 `static/app/app-react-chat-window` 控制。普通右侧工具区和展开后的 overflow 菜单复用同一个 `translateEnabled` prop 和 `onTranslateToggle` 回调，因此它们必须表现一致。

按钮状态来源：

1. 初始 `translateEnabled` 优先读取 `window.appState.subtitleEnabled`。
2. 没有 appState 时读取 `localStorage.subtitleEnabled`。
3. 初始化后订阅 `window.nekoSubtitleShared.subscribeSettings()`；当 `subtitleEnabled` 变化时，更新 React props 并重新渲染按钮高亮。
4. 没有共享 store 时兜底监听 `neko-subtitle-settings-change`。

按钮点击行为：

1. 优先调用 `window.subtitleBridge.toggle()`，让当前页面执行完整的字幕开关副作用。
2. 如果 `subtitleBridge.toggle()` 不存在或抛错，兜底翻转 `appState` / `nekoSubtitleShared` / `localStorage`。
3. 得到 next enabled 后，React host 更新 `translateEnabled` 并重新渲染。
4. 如果宿主提供 `window.nekoSubtitleWindow.setEnabled(enabled)`，调用它把开关交给桌面主链路。
5. 只有没有 `setEnabled` 时，才兜底调用 `window.nekoSubtitleWindow.show()` / `hide()`。

展开状态下的关键语义：

1. `/chat` 独立对话窗口在多窗口模式下不是字幕翻译 owner，不应该自己发翻译请求。
2. 展开对话框里的翻译按钮不能只改本页按钮高亮，也不能只 show / hide 一个空字幕窗口。
3. 正确链路是把 `subtitleEnabled` toggle 交给 Pet 主窗口，由 Pet 的 `subtitleBridge` 更新真实翻译状态、render state 和独立 Subtitle 窗口可见性。
4. 因此按钮代表“翻译字幕功能开关”，不是临时 BrowserWindow 可见性。截图、热键、迁移等系统临时 hide 窗口不应单独改变按钮高亮。

## 独立字幕窗口行为

`static/subtitle/subtitle-window.js` 运行在独立 `/subtitle` 页面。

文本输入：

1. `preload-subtitle.js` 监听 `WS_CHANNELS.TRANSCRIPT`。
2. preload 将数据写入 `window.__nekoSubtitleLatestTranscript`。
3. preload 派发 `neko-ws-transcript`。
4. `subtitle-window.js` 只处理 `data.translated === true` 的 transcript。
5. 文本写入 `#subtitle-text.textContent`。

状态输入：

1. `preload-subtitle.js` 监听 `SUBTITLE_CHANNELS.STATE_SYNC`。
2. preload 写入 `window.__nekoSubtitleLatestState`。
3. preload 派发 `neko-subtitle-state-sync`。
4. `subtitle-window.js` 将 incoming data 映射为共享设置 patch。

实际可见性：

1. 独立字幕窗口的 BrowserWindow 由 NEKO-PC 按需创建 / 显示 / 隐藏。
2. Subtitle 页面内部的 `#subtitle-display.hidden` 由 state sync 或本地 UI 状态控制。
3. Pet preload 监听 `neko-subtitle-render-state`，根据 `subtitleEnabled && visible` 决定调用 `window.nekoSubtitleWindow.show()` 或 `hide()`。
4. 因此“功能开启但当前没有可显示译文”可能短暂表现为空面板或未显示窗口；这不等于用户关闭了翻译功能。

窗口尺寸：

1. 面板 bounds 来自 `subtitlePanelBounds`。
2. BrowserWindow 宽高为 panel bounds 加 `DESKTOP_WINDOW_EDGE_INSET * 2`。
3. `DESKTOP_WINDOW_EDGE_INSET = 6`。
4. `setSize(width, height, { panelBounds })` 由 preload 转发到 NEKO-PC `WINDOW_CONTROL_CHANNELS.SET_SIZE`。

文本字号：

1. 独立字幕窗口渲染译文时清空 `#subtitle-text.style.fontSize`。
2. 文本继承 `#subtitle-display` 的 `subtitleFontSize` 字号，默认 `26px`。
3. 面板尺寸变化不会给文本节点写入更小字号。
4. 收到 `fontSize` / `subtitleFontSize` state sync 后会实时更新字号。

## 桌面独立设置窗口

独立字幕窗口检测到 `window.nekoSubtitle.openSettings` 和 `window.nekoSubtitle.closeSettings` 后，设置 `windowInteractions: 'external'`。

打开设置：

1. `subtitle-window.js` 读取 `#subtitle-display.getBoundingClientRect()`。
2. 结合 `window.screenX` / `window.screenY` 生成 screen anchor。
3. 调用 `window.nekoSubtitle.openSettings({ state, anchor })`。
4. `preload-subtitle.js` 发送 `SUBTITLE_CHANNELS.OPEN_SETTINGS`。
5. NEKO-PC `main.js` 调用 `windowManager.showSubtitleSettingsWindow()`。

设置窗口：

1. BrowserWindow 加载 `/static/subtitle-settings.html`。
2. 默认宽高由 NEKO-PC `SUBTITLE_SETTINGS_WINDOW_WIDTH`、`SUBTITLE_SETTINGS_WINDOW_HEIGHT` 控制。
3. 当前桌面壳固定值为 `SUBTITLE_SETTINGS_WINDOW_WIDTH = 300`、`SUBTITLE_SETTINGS_WINDOW_HEIGHT = 188`，与主仓 `static/css/subtitle.css` 中 settings window host 的最小内容尺寸一致。
4. 位置由 `getSubtitleSettingsBounds(anchor)` 计算，优先放在字幕面板上方。
5. `sendSubtitleSettingsState(state)` 通过 `SUBTITLE_CHANNELS.STATE_SYNC` 同步设置状态。

更新设置窗口：

1. 字幕窗口设置状态变化后调用 `updateSettingsWindow(state)`。
2. preload 发送 `SUBTITLE_CHANNELS.SETTINGS_WINDOW_UPDATE`。
3. main 调用 `windowManager.sendSubtitleSettingsState(data)`。

关闭设置窗口：

1. 字幕窗口调用 `closeSettings()`。
2. preload 发送 `SUBTITLE_CHANNELS.CLOSE_SETTINGS`。
3. main 调用 `windowManager.hideSubtitleSettingsWindow()`。
4. 字幕窗口隐藏或关闭时，window-manager 同步关闭设置窗口。

## 桌面拖拽和缩放

独立字幕窗口启用 `attachDesktopWindowInteractions()`。

拖拽：

1. 面板未锁定时，非控件区域可开始拖拽。
2. renderer 调用 `window.nekoSubtitle.dragStart()`。
3. preload 发送 `WINDOW_CONTROL_CHANNELS.DRAG_START`。
4. 主进程根据光标位置移动 BrowserWindow。
5. 结束时 renderer 调用 `dragStop()`。

缩放：

1. `.subtitle-resize-edge` 提供 `data-resize-dir`。
2. renderer 在 mousedown / touchstart 时解析方向。
3. renderer 调用 `resizeStart(direction, { minWidth, minHeight, cursor })`。
4. 鼠标移动时调用 `resizeMove({ x, y })`。
5. 鼠标释放时调用 `resizeStop()`。
6. 主进程 `WINDOW_CONTROL_CHANNELS.RESIZE_START / RESIZE_MOVE / RESIZE_STOP` 负责实时更新 BrowserWindow bounds。
7. renderer 在 window resize 期间用 viewport 尺寸更新 CSS 变量。
8. resize 结束后写回 `subtitlePanelBounds` 并传播 `type: 'bounds'`。

缩放方向语义：

1. `e` / `w` 改变宽度。
2. `n` / `s` 改变高度。
3. `w` 缩放时右边界保持为起始右边界。
4. `n` 缩放时下边界保持为起始下边界。

## 桌面穿透

独立字幕窗口通过 `updateNativeInteractionPassthrough()` 轮询全局光标维护穿透状态。轮询采用就近调频：光标在面板附近时保持 16ms 响应节奏（与桌面聊天穿透一致），光标停在远处（字幕可见但用户在别处操作的常见空闲态）时退避到 96ms，避免 60Hz 空转打桥。

preload 暴露：

1. `enableInteraction()`：发送 `set-ignore-mouse-events false`。
2. `disableInteraction()`：发送 `set-ignore-mouse-events true, { forward: true }`。
3. `getCursorPoint()`：读取当前光标点。

穿透判定使用：

1. `subtitleInteractionPassthrough`
2. 面板是否可见
3. 当前是否拖拽 / 缩放
4. 设置层是否打开
5. 光标是否在文本或 controls 上

效果：

1. 可见文本和 controls 保持可交互。
2. 透明区域可以转发鼠标事件。
3. 拖拽、缩放、设置操作期间保持交互可用。

## 开关同步

字幕面板关闭按钮会传播：

```js
{ type: 'toggle', value: false }
```

桌面端转发链路：

1. `preload-subtitle.js` 通过 `SUBTITLE_CHANNELS.SETTINGS_CHANGE` 发给 main。
2. `main.js` 将所有字幕设置变更转发给 Pet。
3. 当 `data.type === 'toggle'` 时，main 同时转发给 chat 和 fullChat。
4. chat / fullChat preload 调用 `syncSubtitleToggleFromWindow(data.value)`。
5. `syncSubtitleToggleFromWindow()` 优先调用 `window.subtitleBridge.setSubtitleEnabled(nextEnabled)`。
6. 没有 subtitleBridge 时，写入 `window.nekoSubtitleShared.updateSettings({ subtitleEnabled })`。
7. 再兜底写入 `window.appState.subtitleEnabled` 和 `localStorage.subtitleEnabled`，并派发 `react-chat-window:set-view-props`。

展开对话框翻译按钮主动切换时的桌面端链路：

1. `app-react-chat-window` 点击翻译按钮后得到 next enabled。
2. `preload-chat-react.js` / `preload-chat-full.js` 暴露 `window.nekoSubtitleWindow.setEnabled(enabled)`。
3. `setEnabled()` 发送 `SUBTITLE_CHANNELS.SETTINGS_CHANGE`，payload 为 `{ type: 'toggle', value: enabled }`。
4. main 按同一条 `SETTINGS_CHANGE` 转发链路交给 Pet。
5. Pet 调用真实 owner 的 `subtitleBridge.setSubtitleEnabled(enabled)`。
6. Pet 的 render state 再决定独立 Subtitle BrowserWindow 的 show / hide。
7. chat / fullChat 收到 main 回传的 toggle 后也会刷新本地按钮状态，避免多个展开窗口状态分叉。

## 字幕设置变更类型

共享 UI 当前会传播以下变更类型：

1. `toggle`：`subtitleEnabled`
2. `language`：`userLanguage`
3. `opacity`：`subtitleOpacity`
4. `bounds`：`subtitlePanelBounds`
5. `lock`：`subtitlePanelLocked`
6. `fontSize`：`subtitleFontSize`
7. `colorScheme`：`subtitleColorScheme`
8. `danmakuMode`：`subtitleDanmakuMode`
9. `interactionPassthrough`：`subtitleInteractionPassthrough`，当前实际可见模板不提供独立控件。

设置窗口向宿主传播时只发送 `{ type, value }`。完整状态由各窗口各自通过共享设置维护，并通过 state sync 刷新。

## 功能验收点

1. 打开翻译显示后，字幕面板出现并只显示译文。
2. 点击关闭后，字幕面板隐藏，聊天工具轮盘翻译状态同步关闭。
3. `#subtitle-text` 不显示原文。
4. hover / click / focus 能显示 controls。
5. 鼠标离开后 controls 延迟隐藏。
6. 锁按钮能切换锁住 / 解锁图标和 `aria-pressed`。
7. 设置按钮能打开设置层。
8. 设置层包含语言、不透明度、字体、配色和弹幕模式五个控件。
9. 设置层修改会更新共享设置并传播对应 `{ type, value }`。
10. 面板拖拽后位置持久化。
11. 面板缩放后 `subtitlePanelBounds` 持久化。
12. 独立字幕窗口设置层显示在独立 BrowserWindow 中。
13. 桌面缩放期间字幕窗口 bounds 实时变化，结束后面板 bounds 写回。
14. 桌面穿透开启时，透明区域不阻塞底层点击，controls 和 resize handle 仍可交互；当前穿透开启由锁定状态绑定触发。
15. 字幕基础字号默认为 `26px`，译文文本继承面板字号；Web host 长文本可临时缩小文本节点字号。
16. 字体修改后，Web host、独立字幕窗口和独立设置窗口状态保持同步。
17. 配色修改后，Web host、独立字幕窗口和独立设置窗口状态保持同步；跨窗口变化不需要刷新。
18. 弹幕模式开关可交互，开启后会临时切换为头像附近弹幕布局，关闭后恢复进入前布局。
19. 展开对话框里的翻译按钮开启 / 关闭时，按钮高亮、`subtitleEnabled`、Pet 主翻译状态和真实独立字幕窗口开关保持一致。
20. 字幕面板右上角关闭按钮关闭翻译时，展开对话框里的翻译按钮同步熄灭。
21. 系统临时隐藏 Subtitle BrowserWindow 不应被误当成用户关闭翻译功能。
22. NEKO 和 NEKO-PC 的状态同步不影响 compact 聊天框消息、历史和输入。
