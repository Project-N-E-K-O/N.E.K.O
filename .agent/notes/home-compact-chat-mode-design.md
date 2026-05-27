# 首页紧凑聊天框功能与设计指导

> 本文是首页紧凑聊天框的主文档。  
> 它定义紧凑态聊天框的功能目标、设计边界、真实代码链路、交互几何合同、桌面端适配原则、后续修改指导和验收标准。  
> 若本文与当前代码、测试或真实运行结果冲突，以可复现证据和当前代码为准，并先更新本文再继续实施。

## 文档定位

本文覆盖首页紧凑聊天框相关的长期设计与约束，包括：

1. `full / compact / minimized` 三层聊天形态。
2. `default / options / input` 紧凑态内部三态。
3. 紧凑聊天框、最小化小球、GalGame / ChoicePrompt 选项、工具转轮、后续历史导出层之间的关系。
4. Web 与 NEKO-PC 桌面壳的 geometry、命中、bounds、层级和拖拽边界。

本文不记录一次性补丁过程。具体新增功能若需要更细设计，另建功能文档；当前内联历史 / 导出历史层以 `home-compact-chat-inline-export-history-design.md` 为补充文档。

## 核心目标

首页紧凑聊天框不是“缩小版聊天窗口”，而是首页半身角色构图里的底部伴随式交互器。

目标：

1. 让首页交流焦点回到 YUI / 猫娘模型，而不是左下角大输入框。
2. 用更低信息密度保留现有聊天、选项、输入、附件和工具能力。
3. 默认优先展示当前轮短句与推荐选项，降低用户立刻打字的压力。
4. 输入、选项、工具、历史导出和最小化入口都围绕同一套角色附近交互语义组织。
5. 网页端和桌面端的用户可见表现一致；桌面端可通过 Electron 原生窗口、setShape、独立小球窗口等方式适配。
6. NEKO 作为后端继续提供消息、选项、附件、工具回调等既有数据；NEKO-PC 是前端外壳，不应承载新的业务协议。

## 非目标

1. 不重写消息 schema、聊天协议、历史存储或后端会话系统。
2. 不把首页紧凑态直接推广为所有页面和所有窗口的默认聊天形态。
3. 不把字幕系统改造成紧凑聊天框的主文本源。
4. 不恢复旧 `#chat-container` 作为首页紧凑态实现依据。
5. 不把 GalGame 大面板视觉换皮后当作紧凑态。
6. 不创建教程专属紧凑聊天框。
7. 不为桌面端写一套与网页端目标表现不一致的独立产品逻辑。
8. 不把内联历史 / 导出历史变成紧凑态默认常驻主面板。

## 当前真实代码链路

后续修改必须基于当前真实生效链路，不按历史印象修改。

### NEKO 网页端

主要位置：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
3. `frontend/react-neko-chat/src/MessageBlockView.tsx`
4. `frontend/react-neko-chat/src/styles.css`
5. `static/app-react-chat-window.js`
6. `static/app-chat-export.js`
7. `static/css/index.css`

已确认事实：

1. 当前聊天 UI 以 React chat 为准；旧 `#chat-container` 只作为兼容 DOM 存在。
2. 宿主状态使用：
   - `chatSurfaceMode: 'full' | 'compact' | 'minimized'`
   - `compactChatState: 'default' | 'options' | 'input'`
3. 最小化按钮按 `full -> compact -> minimized -> full` 循环，不是二元开关。
4. 紧凑态渲染已经走独立分支：
   - `chat-body-compact-surface`
   - `compact-chat-stage`
   - `compact-chat-surface-shell`
   - `compact-chat-surface-frame`
   - `data-compact-chat-state="default|options|input"`
   - `data-compact-geometry-item="capsule|input|dragHandle|resizeHandle"`
5. 当前文字来源是 React `messages`，由 `getCompactMessagePreview(messages)` 提取紧凑预览。
6. 当前没有正式的 TTS / 字幕逐句事实桥；音频播放状态只可作为显字开闸和限速参考，不能当成文本事实源。
7. GalGame options 和 ChoicePrompt 复用原选项语义；紧凑态选项层通过 `compactChoiceLayerNode` 独立挂载，不应塞回胶囊内部。
8. 紧凑输入态复用 composer 的发送、附件、禁用、工具回调语义，但由紧凑态壳层限制高度和浮层。
9. 紧凑输入态工具转轮当前作为 `compact-chat-surface-shell` 内部浮层渲染，并带 `data-compact-geometry-item="toolFan"`；宿主 geometry 会为 tool fan 输出 native rect 和可点击子 item。
10. 蓝线拖拽通过 `data-compact-drag-handle="true"` 标记，必须保留。
11. 紧凑聊天框本体支持左右 resize handle；宽度状态由 React 控制，并通过 CSS 变量 / 桌面 layout 参与 history 尺寸和 geometry。
12. 紧凑态内联历史 / 导出层已接入 React：`CompactExportHistoryPanel` 直接消费 `messages`，用 `MessageBlockView` 复用现有 block 渲染，并通过 `window.appChatExport` 的 compact inline API 复用导出能力。
13. `static/app-react-chat-window.js` 已负责紧凑 surface、小球、geometry snapshot、composite hit region 和 CSS 变量同步。

### NEKO-PC 桌面壳

主要位置：

1. `../N.E.K.O.-PC/src/preload-chat-react.js`
2. `../N.E.K.O.-PC/src/preload-pet.js`
3. `../N.E.K.O.-PC/src/main.js`
4. `../N.E.K.O.-PC/src/main/window-host-ipc.js`
5. `../N.E.K.O.-PC/src/main/top-coordinator.js`

已确认事实：

1. pet preload 能提供模型 / avatar screen bounds，并通过主进程转发给 ReactChat。
2. chat preload 会订阅 avatar bounds，并把桌面 compact layout 下发到页面：
   - `window.__nekoDesktopAvatarBounds`
   - `window.__nekoDesktopCompactLayout`
   - `window.__nekoDesktopCompactExternalBall`
3. 桌面端 compact surface 的 BrowserWindow bounds 由页面 geometry 和 workArea 派生。
4. 桌面端小球按外部 ball window 思路承载，不应再和 surface 绑定在同一个大透明窗口里。
5. 桌面端蓝线拖拽移动的是 ReactChat 原生窗口，再把最终 surface 位置保存为桌面 compact surface position。
6. Electron 透明窗口里，CSS 透明不等于点击穿透；必须同时考虑 BrowserWindow bounds、setShape/input region 和页面 `pointer-events`。
7. `setShape` / input region 能力应优先复用项目已有管线，不能另造一套透明窗口命中系统。
8. 窗口层级必须复用现有 window manager / top coordinator，不在页面脚本里发明新的 always-on-top 方案。

## 产品结构

### 三层聊天形态

首页聊天框只有一条连续形态链：

1. `full`：完整聊天框，承担完整历史和完整工具区域。
2. `compact`：紧凑聊天框，承担当前轮互动、选项、输入、必要工具和按需历史导出层。
3. `minimized`：最小化小球，承担最轻入口和恢复链路。

规则：

1. 三者共享同一套消息、发送、附件、选项、教程和恢复语义。
2. 三者区别是视觉密度和承载面积，不是业务协议分叉。
3. 切换形态不能清空会话、重置选项或破坏输入状态恢复。
4. compact surface 的用户拖动位置只影响紧凑聊天框，不影响小球。

### 紧凑态三态

`chatSurfaceMode === 'compact'` 内部再分：

1. `default`
   - 显示底部胶囊和当前短句。
   - 不显示完整历史。
   - 不展开输入框。
2. `options`
   - 在底部交互器上方显示 GalGame options 或 ChoicePrompt。
   - 底部胶囊仍作为同一交互器的锚点存在。
3. `input`
   - 展开紧凑输入框。
   - 支持文本、附件、发送、工具转轮。
   - 高度受控，超出后内部滚动。

原则：

1. 默认态是基础落点。
2. 有推荐回复或 ChoicePrompt 时，选项态优先于输入态。
3. 输入态是用户主动打开的补充能力。
4. 不长期同时展开选项态和输入态。
5. 不恢复上方独立说话框与下方交互框并存的双层文字结构。

## 交互岛结构

紧凑态由两个独立交互岛组成，不是一个大透明面板。

### Compact Surface Island

内容：

1. 紧凑聊天框本体：`compact-chat-surface-frame`，通过 `data-compact-geometry-item="capsule|input"` 表达胶囊 / 输入语义。
2. 紧凑输入框。
3. GalGame / ChoicePrompt 选项层。
4. 工具转轮。
5. 内联历史 / 导出历史层。
6. 蓝线拖拽手柄。
7. 左右 resize handle。

定位：

1. 默认基于模型 bounds 和安全区计算，处于模型可见区域偏下方。
2. 若存在用户保存的 compact surface 拖动位置，优先使用该位置。
3. 未保存位置时，默认位置应按模型底部向上约 `1/5` 的视觉关系计算，只看聊天框本体，不把 GalGame 选项、历史层或工具转轮计入默认锚点。
4. surface 拖拽只移动 surface island。

命中：

1. 只有可见的胶囊、输入框、选项、工具、历史滚动区和蓝线可命中。
2. 透明包裹层必须穿透。
3. 关闭态、空态、未加载态不能留下透明但吃事件的大矩形。

层级：

1. surface 应稳定显示在模型视觉层上方。
2. ChoicePrompt 和 GalGame 选项在历史层上方。
3. 工具转轮在输入器上方。
4. 蓝线在 surface 本体上方，但不能扩大命中面。

### Compact Ball Island

内容：

1. 最小化小球视觉。
2. 小球点击区域。

定位：

1. 基于模型 bounds 位于模型左侧。
2. 使用 viewport/workArea clamp，不能出屏。
3. 没有模型 bounds 时才使用 fallback，并视为降级路径。

行为：

1. 不随蓝线拖拽移动。
2. 不读取 compact surface localStorage。
3. 不参与 surface bounds 计算。
4. 桌面端优先由独立 ball window 承载，不和 surface 之间生成大透明命中区域。

## 几何合同

Compact Interaction Geometry 是紧凑态的根合同。所有可见、可点、可滚动、可拖拽的紧凑态区域都必须能被 geometry 解释。

### Geometry Item

每个紧凑态交互区域至少需要表达：

1. `owner: surface | ball`
2. `kind: capsule | input | choice | history | toolFan | dragHandle | resizeHandle | ball`
3. `visualRect`
4. `hitRect`
5. `nativeRect`
6. `interactive`

规则：

1. `surface` union 只包含 surface 相关区域。
2. `ball` rect 只包含小球。
3. surface 和 ball 之间不能通过一个大透明矩形相连。
4. 子组件允许视觉浮出父 DOM，但浮出的可见区域必须注册进 geometry。
5. 每次新增 compact 浮层，都必须同步补 geometry item、hit 策略和验证项。

### 页面 Geometry 来源

1. 页面真实 DOM rect 是 geometry 的事实来源。
2. `static/app-react-chat-window.js` 聚合 compact DOM、avatar bounds、Electron override，并输出：
   - `surfaceItems`
   - `surfaceUnion`
   - `surfaceHitRects`
   - `surfaceNativeRects`
   - `ballRect`
   - `externalBall`
3. React 组件需要用稳定 `data-compact-geometry-owner` 和 `data-compact-geometry-item` 暴露身份。
4. 对 history 这类透明外层 + 内部可点击区域，React 侧用 `data-compact-geometry-hit-scope="children"` 和 `data-compact-hit-region="true"` 暴露 composite geometry；宿主输出 `history:native` 和子 hit rect。
5. NEKO-PC preload 只消费页面 geometry 或同名同义的过渡 selector，不能另算一套产品规则。

### Electron Native Region

1. 桌面端 BrowserWindow bounds 只能覆盖真实需要显示 / 命中的 surface union。
2. setShape/input region 应从 geometry 的 hit rect 派生。
3. `setIgnoreMouseEvents` 只能作为整窗 fallback，不适合作为多区域命中的主方案。
4. native region 只能解决点击区域，不能承载视觉；小球远离 surface 时必须有真实视觉承载。
5. geometry 更新必须 hash/debounce，避免文字显字、模型呼吸或鼠标移动造成高频 native region 更新。

## 定位合同

### 小球定位

小球定位必须拆清三层：

1. 读取模型 bounds。
2. 从模型 bounds 计算小球 placement。
3. 返回最终小球 target。

要求：

1. 网页端读取当前可见模型 manager 的 `getModelScreenBounds()`。
2. 桌面端读取 `window.__nekoDesktopAvatarBounds` 或由外部 ball window 直接消费 screen rect。
3. 小球公式保持简单可验证：
   - `left = bounds.left - ballSize - gap`
   - `top = bounds.top + bounds.height * verticalRatio - ballSize / 2`
   - 最后 clamp 到 viewport/workArea。
4. 小球 target 不读取 surface position。
5. 小球 target 不读取蓝线拖拽状态。

### Surface 定位

要求：

1. 有用户保存位置时，使用保存位置并 clamp。
2. 没有保存位置时，基于模型 bounds 默认放在模型可见区域偏下方。
3. 默认位置只看聊天框本体的宽高，不把选项、历史或工具转轮计入初始锚点。
4. surface 跟随模型需要有脱离阈值：模型小范围构图偏移可带动 surface，模型过度靠边或近景时 surface 优先留在安全区。
5. 输入态、高度变化、选项打开、工具打开不得让 surface 自动跳到另一个业务位置。
6. 打开右侧展开栏或工具层不能导致 surface 向上抖动；若需要扩窗，只扩 native bounds，不改变用户选择的 surface anchor。

## 输入态合同

1. 当前代码已经统一到 `.compact-chat-surface-shell` 包裹 `.compact-chat-surface-frame`；紧凑框本体语义仍以 `data-compact-geometry-item="capsule|input"` 为准，不以 class 名推断。
2. `capsule` / `input` 是同一类 base surface anchor；后续若重构 DOM 或 class，需要同步 React、CSS、geometry collector、NEKO-PC preload 和测试。
3. `.composer-input` 允许在上限内增长，超出后内部滚动。
4. 附件预览不能把紧凑输入态撑成 full composer。
5. 工具转轮作为 surface 内部浮层浮出，不参与 input 本体高度测量。
6. 工具转轮打开时仍属于 surface geometry。
7. 空输入且无附件时，右侧按钮是工具入口；有文本或附件时，右侧按钮是发送。
8. 进入输入态后必须能回到展示态，不能因为 focus/blur 或工具层状态卡死。

## 选项层合同

1. `choicePrompt` 优先于 GalGame options。
2. GalGame options 和 ChoicePrompt 都属于 Compact Surface Island。
3. 选项层从底部交互器上方或下方显示，但不能塞回胶囊内部。
4. 选项层 placement 只能在 `above` / `below` 之间选择真实可见位置。
5. 下方空间不足时，应优先把选项显示到聊天框上方；不得通过把整个聊天框弹走来规避空间不足。
6. 上下都不足时，选项层应受控压缩并内部滚动。
7. 选项层关闭后不能保留透明命中区域。
8. 选项层必须进入 geometry 和桌面 native bounds，否则 Electron 下会被裁切。
9. 内联历史打开时，选项层可以盖在历史层上方，不参与历史层重排。

## 历史 / 导出入口合同

默认 compact 不展示历史；历史只通过明确入口按需打开。

内联历史功能以 `home-compact-chat-inline-export-history-design.md` 为准，并遵守：

1. 历史区域属于 Compact Surface Island。
2. 历史区域位于紧凑聊天框上方。
3. 最新消息在最下方，靠近聊天框。
4. 历史区域有最大高度，超出后内部滚动。
5. 打开历史时，如果用户继续聊天，历史内容需要实时更新。
6. 历史选择状态与导出预览共享。
7. GalGame / ChoicePrompt 选项出现时盖在历史上方。
8. 历史滚动、气泡点击、多选和后续拖拽必须有明确意图区分。
9. 历史拖拽发送模型属于后续阶段功能；当前基础历史 / 导出层只做打开、选择、预览、复制 / 下载和 geometry。
10. 历史区域关闭或为空时，不允许留下透明但吃事件的大占位。
11. 后续需要把“历史记录中的非对话透明区域”纳入穿透目标：气泡、按钮、预览控件和必要滚动命中可以吃事件；气泡之间、气泡左右留白等非对话透明区不应长期作为 native hit region 遮挡后方。

## 当前文字显示合同

紧凑态当前文字是“当前轮轻提示”，不是历史记录、字幕或完整转录。

要求：

1. 默认从 React `messages` 链路提取最近可预览内容。
2. assistant streaming 时可以显示当前流式消息并做本地显字。
3. 非 streaming 场景必须有限截断，避免重新长成历史聊天框。
4. 清理 `[play_music:...]` 等控制指令和多余空白。
5. 不新增未验证的 TTS/字幕桥接字段。
6. 若未来引入“正在说到哪一句”的事实驱动显示，必须单独设计稳定、可回退、跨端一致的信号源。

## 字幕与教程

1. 字幕是辅助层，不是紧凑聊天框主文本层。
2. 教程可以启用同一套 compact 模式，但不能生成教程专属 UI。
3. 教程结束、跳过、异常销毁后，聊天形态和字幕状态必须按原链路恢复。
4. 字幕样式优化复用现有全局字幕机制，不创建教程私有字幕系统。

## 桌面端适配原则

NEKO-PC 是前端外壳，不是业务后端。桌面端适配必须保持网页端目标表现一致。

要求：

1. 桌面端 surface 和 ball 的产品语义与网页端一致。
2. 桌面端可以用独立 ball window、BrowserWindow bounds、setShape/input region 实现命中和裁切。
3. 桌面端不能因为原生窗口实现限制而把小球重新绑回聊天框。
4. 桌面端拖拽保存的是 surface anchor，不是 ball anchor。
5. 桌面端 compact window resize / relayout 不能污染 full 模式窗口大小。
6. 从 compact 切回 full 或 minimized 时，必须恢复原窗口状态、shape、ignore-mouse-events 和 external ball。
7. `setBounds`、拖拽结束、形态切换后，ReactChat 紧凑窗口必须保持在模型上方。

## 修改指导

### 修改前

1. 先跑 `git status --short`。
2. 判断改动属于：
   - React 组件结构。
   - Web 宿主状态 / geometry。
   - CSS 命中 / 层级 / 高度。
   - NEKO-PC preload / native window。
   - 后端消息 / 工具协议。
3. 能在前端解决的，不先改后端。
4. 能复用现有消息、选项、附件、工具回调的，不新增协议。
5. 涉及桌面端时，同时对照网页端实际表现，不允许只修 PC 表面症状。

### 修改中

1. 新增 compact 可见区域时，同步补：
   - DOM 身份。
   - geometry item。
   - CSS pointer-events。
   - Electron native bounds / shape 验证。
2. 修改 surface 定位时，确认：
   - 用户保存位置优先。
   - 默认位置只看聊天框本体。
   - 不读取小球位置。
   - 不被选项、历史、工具层撑跑。
3. 修改小球时，确认：
   - 只看模型 bounds。
   - 不读 surface position。
   - 不被蓝线拖拽影响。
4. 修改选项层时，确认：
   - 不塞回胶囊。
   - 下方不足能转到上方。
   - 不让聊天框为选项弹走。
5. 修改输入态时，确认：
   - 高度受控。
   - 发送和工具入口语义不混。
   - 输入态能回到展示态。
6. 修改桌面端时，确认：
   - 页面 geometry 与 preload 消费一致。
   - BrowserWindow bounds 没有包住无用透明区域。
   - setShape/input region 没有漏掉实际可点击浮层。

### 修改后

如果改了 `frontend/react-neko-chat/src/*` 或前端宿主链路：

1. 运行 `bash build_frontend.sh`。
2. 确认 `static/react/neko-chat/*` 产物已更新。

如果改了 NEKO-PC：

1. 做桌面端真实启动检查。
2. 检查 compact/full/minimized 三态。
3. 检查拖拽、选项、输入、工具、层级和点击穿透。
4. 对照网页端目标表现确认一致。

## 验收清单

基础形态：

1. `full -> compact -> minimized -> full` 循环稳定。
2. compact 默认不展示完整历史。
3. compact 当前文字在下方胶囊 / 输入器内，不出现上方独立说话框。
4. minimized 小球位于模型左侧，且不随 surface 拖拽。

Surface：

1. 用户拖动 surface 后位置被保存。
2. 未保存位置时，默认在模型底部向上约 `1/5` 的可见区域。
3. surface 被模型压住的问题不再出现。
4. 打开工具、选项或右侧展开栏时，surface anchor 不抖动。

命中：

1. compact 周围透明区域不挡后方内容点击。
2. 可见胶囊、输入、选项、工具、历史和蓝线都能稳定点击。
3. 选项关闭后不留透明命中区。
4. 桌面端 BrowserWindow bounds / setShape 与页面 geometry 一致。

选项：

1. ChoicePrompt 优先级高于 GalGame options。
2. 选项不会在模型头部多出一份。
3. 下方空间不足时，选项显示到聊天框上方。
4. 选项不会被 Electron 原生窗口裁切。

输入：

1. 输入态不会被 composer 撑成 full 面板。
2. 长文本内部滚动。
3. 空输入右侧按钮打开工具转轮。
4. 有文本或附件时右侧按钮发送。
5. 输入态能自然回到展示态。

桌面端：

1. surface 和 ball 独立承载。
2. 拖拽 surface 不牵动 ball。
3. 模型移动不会强行覆盖用户保存的 surface 位置。
4. compact window 在模型上方。
5. 切回 full/minimized 后窗口 bounds、shape 和小球状态恢复正确。

## 禁止方案

1. 禁止用 `HEAD` 覆盖当前工作区紧凑态改动。
2. 禁止删除 `.compact-chat-drag-handle` 或 `data-compact-drag-handle="true"` 链路。
3. 禁止把小球固定到视口左下角当作模型左侧定位。
4. 禁止让 compact surface 的持久化位置影响小球位置。
5. 禁止让小球和聊天框共用一个大透明矩形作为最终桌面端方案。
6. 禁止用全局粗暴 `pointer-events: none` 破坏输入、选项、工具、蓝线或小球。
7. 禁止只给 textarea 加固定高度就宣称解决输入态撑高。
8. 禁止只提高局部 `z-index` 就宣称解决模型上方层级问题。
9. 禁止把 GalGame / ChoicePrompt 选项塞回胶囊内部来绕过裁切。
10. 禁止选项关闭后仍保留透明命中区域。
11. 禁止为未来历史对话预留透明但吃事件的大空区域。
12. 禁止让历史滚动误触发蓝线拖拽。
13. 禁止让历史、选项或工具层参与小球定位。
14. 禁止让视觉浮层依赖未登记的 `overflow: visible` 逃逸父容器。
15. 禁止把 NEKO-PC 的实现限制反向改成网页端产品目标。
16. 禁止把旧 `.compact-chat-capsule-shell` / `.compact-chat-input-shell` 当成当前真实 DOM 链路；当前真实本体是 `.compact-chat-surface-shell` / `.compact-chat-surface-frame`，但 anchor 语义仍只认 `capsule|input`。

## 当前优先级

后续紧凑态相关改动按以下顺序判断优先级：

1. 先保护三层形态和紧凑三态的状态语义。
2. 再保护 surface / ball 独立、geometry、命中和桌面 bounds。
3. 再保护选项、输入、工具这些当前核心交互。
4. 再稳固内联历史 / 导出历史的选择、预览、滚动、导出和 composite geometry。
5. 最后再做拖拽历史气泡、图片拖出、发送给模型等游戏化增强。

原因：紧凑态的根本问题不是某个按钮的位置，而是角色附近交互器的几何、命中、层级和状态边界必须稳定。只有这个基础稳定，后续历史、导出、拖拽和表现层动画才不会继续堆成补丁。
