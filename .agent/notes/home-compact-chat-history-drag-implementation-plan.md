# 首页紧凑态历史投递拖拽实施计划

> 本文描述“最后再做拖拽历史气泡、图片拖到当前猫娘角色、发送给当前猫娘角色”的实施方式。
> 参考主文档：`home-compact-chat-mode-design.md`。
> 参考历史层文档：`home-compact-chat-inline-export-history-design.md`。
> 已删除的 `home-compact-chat-inline-export-history-implementation-plan.md` 曾把“图片 / 表情包拖到外部保存”列为后续候选；当前计划不实施该项，只保留为未来观察项。
> 若本文与当前代码、测试或真实运行结果冲突，以当前代码和可复现证据为准，并先更新文档再继续实施。

## 阅读路径与门禁

本文按“边界 → 代码落点 → 可实施性 → 外部参考 / 验证取舍 → 阶段 → 风险 → 验证”的顺序组织。真正实施时不要只按阶段标题跳读，必须先过下面四个门禁：

1. 附件门禁：历史图片必须进入 `app-buttons.js` 的 pending attachment 源头，再同步到 React host；只调用 `setComposerAttachments()` 不算完成。
2. 桌面窗口门禁：只要要求拖拽视觉或 pointer 离开 ReactChat BrowserWindow，第一版就必须以临时 bounds 保持 renderer 可接收关键 pointer 事件；全局 cursor 只能辅助 hover / 诊断，不能单独替代 pointerup 和窗口承载。
3. 桌面命中门禁：NEKO 网页端 avatar hit helper 不能直接代表桌面端命中。桌面路径必须显式消费 `window.__nekoDesktopAvatarBounds` / `neko:desktop-avatar-bounds-change`，或由 NEKO-PC 回传 `desktopOverAvatar`。
4. 动效门禁：橡皮泥连接、原位消失、吸附和弹回都只能在拖拽识别、发送链路、取消恢复和桌面 bounds 已稳定后实现。

## 任务类型与边界

本功能是紧凑态历史层的后续游戏化增强，不是导出基础功能的一部分。

实施范围：

1. 历史图片 / 表情包 / 图片 block 可拖到当前猫娘模型上，作为图片信息发送。
2. 整条历史聊天气泡可拖到当前猫娘模型上，作为把该条历史内容重新发送给猫娘。
3. 拖拽失败、取消或未命中目标时，历史消息、选择状态和滚动位置恢复原样。
4. 网页端和 NEKO-PC 桌面端表现一致，桌面端只桥接 geometry、hit、drop 和窗口 bounds，不复制消息转换业务。

非目标：

1. 不新增后端消息 schema、历史存储或聊天协议。
2. 不新增“图片发送后端协议”或“重发历史消息后端协议”。
3. 不改变 full 模式导出窗口。
4. 不改变紧凑态历史导出选择 / 预览 / 复制 / 下载的基础语义。
5. 不支持拖拽整个历史区域上下移动；历史区域只允许内部滚动。
6. 不在 NEKO-PC 里解析消息 block、构建发送文本或复制导出业务。
7. 不把拖拽影子变成长期 geometry item，也不让桌面透明窗口长期扩大。
8. 不在 drag data 中暴露内部本地路径、token、系统用户名、后端缓存路径或不可公开 URL。
9. 当前不实施“图片 / 表情包拖出到外部应用或系统保存链路”。这类操作容易把许多本来不是保存的拖拽意图误导成保存，后续需要重新确认产品语义后再决定。

## 当前代码落点

### NEKO

需要优先基于这些当前真实入口实施：

1. `frontend/react-neko-chat/src/App.tsx`
   - 当前 composer 发送入口：`onComposerSubmit`。
   - 当前图片导入按钮入口：`onComposerImportImage`；它只触发导入动作，不接受历史图片 payload。
   - 当前 avatar bounds / hit helper：`getAvatarRangeHit`、`isPointerWithinAvatarRange` 相关链路；网页端可直接用于 drop 命中，桌面端需要显式接入桌面 avatar bounds 或 NEKO-PC 回传命中。
   - `emitAvatarInteraction` 当前服务头像工具交互，不应直接拿来承载历史气泡发送语义。
   - 当前 compact export 状态：`compactExportHistoryOpen`、`compactExportPreviewOpen`、`compactExportSelectedIds`、`compactExportAutoScrollToBottom`。
2. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
   - 当前历史层、气泡选择、滚动、preview 和 `pointerIntentRef` 的主落点。
   - 后续拖拽意图应在这里从现有 pointer intent 扩展，而不是绕过它另写一套 click / drag 判断。
3. `frontend/react-neko-chat/src/MessageBlockView.tsx`
   - 当前 message block 共享渲染入口。
   - 图片 / link / status / buttonGroup 等 block 的识别和拖拽 source 标记应围绕这个共享渲染结果补充，不重新写 block parser。
4. `frontend/react-neko-chat/src/styles.css`
   - 拖拽影子、橡皮泥式连接、猫娘 drop 高亮、弹回、原位消失和再出现动画的样式落点。
5. `static/app-react-chat-window.js`
   - 当前 React chat host、compact geometry collector、desktop bridge callback 和 composer callback 的落点。
   - 当前 public host API 已有 `setComposerAttachments()` 和 `setOnComposerSubmit()`；发送时 `handleComposerSubmit()` 只把 `{ text, requestId }` 传给 callback，但会依据 host 内部 `composerAttachments` 判断是否允许仅附件发送。
   - 若需要把临时 drag geometry / drag state 暴露给桌面壳，应从这里输出稳定事件或 payload。
6. `static/app-chat-export.js`
   - 只作为消息提取 / 图片来源理解的参考。
   - 不应把 full / compact 导出预览 DOM 搬进拖拽实现。

### NEKO-PC

NEKO-PC 修改必须在 `/Users/tonnodoubt/N.E.K.O.-PC` 当前分支单独完成，和 NEKO 修改分开提交。

需要优先基于这些当前真实入口实施：

1. `src/preload-chat-react.js`
   - 当前主要负责 ReactChat BrowserWindow 折叠 / 展开 / 拖动 / resize 的页面侧拦截，并通过 `window.nekoChatWindow` 调主进程窗口控制能力。
   - 当前没有独立的 compact history drag geometry 管线，也没有 `desktop-compact-layout.js` 这类布局模块；后续需要在现有窗口控制基础上新增“临时拖拽 bounds / rebase / restore”桥接。
   - 后续只消费 NEKO 页面输出的临时 drag state / geometry，不自行猜历史气泡范围、气泡宽度或连接曲线。
2. `src/preload-pet.js`
   - 提供模型 / avatar bounds、avatar range hit 和 avatar interaction 相关桥接。
   - 后续提供当前猫娘 drop target 的命中桥接，但不做消息内容转换。
3. `src/main.js`
   - 管理 ReactChat / Pet 窗口、bounds 同步、跨窗口事件转发。
4. `src/main/window-host-ipc.js`
   - 复用已有 `setShape`、input region、`setIgnoreMouseEvents`、`get-cursor-point` 和窗口 bounds helper 能力。
   - 不新增一套独立透明窗口命中系统。

## 可实施性结论

基于当前代码，本功能可实施，但不能按“直接给现有回调塞新字段”的方式做。

已具备：

1. 历史层已有稳定消息 id、气泡 DOM、pointer intent、selection、scroll 和 composite geometry。
2. `MessageBlockView` 已统一渲染 image / text / link / status / buttons，可作为 drag source 识别基础。
3. `App.tsx` 已有 avatar bounds / range hit helper，可用于网页端 drop 命中判断；桌面端需要额外接入桌面 avatar bounds 或 NEKO-PC drop 命中回传。
4. `static/app-react-chat-window.js` 已有 `setComposerAttachments()`、`handleComposerSubmit()`、compact geometry collector 和 host callback 管线。
5. `static/app-buttons.js` 已把 React host submit 绑定到 `sendTextPayload()`，并在发送时读取 pending attachment 源头生成用户图片消息。
6. 后端链路已经有 attachments 语义：`main_logic/core.py`、`main_logic/cross_server.py`、`brain/task_executor.py`、`brain/openclaw_adapter.py` 等会把图片附件作为用户消息 / OpenClaw 输入继续处理。
7. NEKO-PC 当前已有窗口 bounds 控制、通用 `setShape` / input region、全局 cursor point 和 Pet 侧 avatar range / compact zone 经验；但当前分支没有历史拖拽专用 compact geometry bridge，不能假设桌面端已经能消费 history drag visual / hit rect。

需要先补齐：

1. React history panel 到 App / host 的内部 drag event 边界，例如 `onCompactHistoryDragStart`、`onCompactHistoryDropToAvatar` 或等价更贴近代码风格的回调。
2. 图片投递到现有附件语义的桥接入口。当前 `onComposerImportImage` 不能接收历史图片 payload，`onComposerSubmit` schema 也没有 attachments 字段；`setComposerAttachments()` 只同步 React host 可见附件状态，不等于 `sendTextPayloadInternal()` 一定会消费附件。正式实现必须补一个进入 `app-buttons.js` pending attachment 源头的入口，再同步到 React host。
3. 图片 block 起手判断。当前 `.message-block-image` 被选择忽略逻辑排除，后续要拆成“点击不选中”和“拖动可进入 imageDrag”两个分支。
4. 后端送达验证。历史图片投递必须确认最终进入 `sendTextPayloadInternal()` 真实消费的 pending attachment 源头，并在后端消息里形成 `attachments`，不能只在 React 层显示一个附件预览。
5. 突破聊天框边界的承载方案。网页端可用 fixed / portal overlay 跨出 compact history DOM；桌面端第一版应临时扩大 ReactChat BrowserWindow bounds，否则视觉会被 BrowserWindow 边界裁掉；单独 overlay window 只作为后续备选。
6. 桌面临时 drag geometry。NEKO 页面必须输出临时 drag shadow / connection visual bounds、drag shadow hit bounds、drop point 和 phase；NEKO-PC 只消费这些 bounds，不自行猜连接曲线、气泡范围或气泡宽度。
7. 桌面重基坐标合同。如果 NEKO-PC 扩大 bounds 时改变 ReactChat BrowserWindow 的 `x/y`，NEKO 必须消费 `rebaseDelta`，或 NEKO-PC 采用不移动左上角的扩展策略。
8. no-setShape 降级。setShape 不可用的平台不能因为临时扩大透明窗口而长期遮挡模型或桌面；必须提前定义可接受的视觉降级。
9. reduced motion 与性能边界。gooey / blur 只作用临时连接 overlay，不能作用整个历史列表。

## 成功标准

网页端：

1. 点击历史气泡仍只切换选择状态。
2. 上下滚动历史不会触发选择、气泡拖拽或蓝线拖拽。
3. 从图片 / 表情包缩略图拖动超过阈值后进入图片拖拽，不触发整条气泡拖拽和选择。
4. 从文本或气泡空白区域拖动超过阈值后进入整条气泡拖拽，不触发选择。
5. 图片拖到当前猫娘模型上后，复用现有附件 / 图片发送链路发送给猫娘。
6. 整条气泡拖到当前猫娘模型上后，复用现有 composer / 附件发送链路发送给猫娘。
7. 未命中猫娘模型或取消拖拽时，不发送、不改变 `compactExportSelectedIds`、不改变 inline preview 选择状态。
8. 拖拽过程中历史消息本体不被真实删除；发送成功后也仍保留在历史层。
9. GalGame / ChoicePrompt 打开时，取消或结束当前历史拖拽，避免选项、历史拖拽、drop target 争焦点。

桌面端：

1. ReactChat 透明窗口 bounds 只在明确 `bubbleDrag` 或必要 drag shadow 阶段临时扩大。
2. 拖拽完成、弹回或发送动画结束后，ReactChat bounds 恢复到 compact geometry 真实范围。
3. setShape / native hit region / pointer passthrough 按平台现有能力覆盖可见拖拽影子和必要 drop 交互，但不把历史层外透明区域变成长期遮罩。
4. NEKO-PC 不复制消息转换、图片 payload 构建或导出逻辑。
5. bounds 扩展若改变窗口左上角，NEKO 页面视觉坐标通过 `rebaseDelta` 或等价机制保持连续，不出现拖拽影子跳位。
6. no-setShape 平台有明确降级：视觉可以不完全越界，但透明空白不能持续挡住模型、桌面或其他窗口。
7. 最小化小球、蓝线拖拽、compact surface anchor、full 模式和 minimized 模式不受影响。

## 实施阶段

实施总顺序必须固定为两层：

1. 先完成 NEKO，再完成 NEKO-PC。
2. 每一端内部先完成拖拽基础，再完成发送投递，最后完成拖拽效果和动画。

原因：

1. NEKO 持有消息、block、composer、附件和发送语义；NEKO-PC 只能消费 NEKO 暴露的 drag state / geometry / payload 结果。
2. 先把拖拽意图、source、payload 和取消恢复做稳，才能安全接发送。
3. 发送链路跑通后再做橡皮泥连接、吸附、弹回、原位消失 / 再出现和桌面临时 bounds，避免动画遮住业务问题。

## 外部经验与本项目取舍

参考成熟拖拽库和 Electron 官方能力后，本项目应采纳这些经验，而不是直接引入一套新拖拽框架。

1. dnd-kit 的 `DragOverlay` 思路适合本功能：拖拽预览脱离普通文档流、相对 viewport 定位，源元素可以留在滚动列表里，拖拽影子独立移动。这与“原气泡不塌陷、只显示层变化”一致。
2. dnd-kit 也明确建议滚动容器、跨容器移动、虚拟列表等场景使用 overlay 或 fixed 定位，否则拖拽物容易被 overflow、scroll 或 stacking context 限制。本项目紧凑历史层正是滚动容器，因此不应直接 transform 原气泡。
3. React DnD 的 custom drag layer 经验也说明：复杂预览应该作为独立 drag layer 渲染，drag source 只负责状态和 payload。对应到这里，`MessageBlockView` 和 history bubble 不应被改成动画主体，应该提供测量锚点和 source 信息。
4. Atlassian Pragmatic drag and drop 的设计指南强调显式拖拽把手、自定义 native preview 和 preview portal，这对“明确起手区域”和“预览层独立”有参考价值。
5. 浏览器原生 HTML Drag and Drop 适合跨页面 / 跨应用数据交换，但需要 `DataTransfer` 暴露字符串或文件数据。本项目当前不做外部保存，且全局已禁用图片原生 dragstart，所以应坚持自定义 pointer drag，不填充面向系统保存的 drag data。
6. MDN 的 `pointer-events: none` 只解决页面内命中穿透，不等于桌面透明窗口穿透。NEKO-PC 必须继续使用 native hit region、setShape、setIgnoreMouseEvents 或 bounds 策略，不能只靠 CSS。
7. Electron `BrowserWindow.setBounds()` 可以移动 / 调整窗口，但 Wayland 明确存在全局坐标和程序化 resize 限制；因此桌面端必须有降级路径。
8. Electron `setIgnoreMouseEvents(true)` 是整窗级忽略鼠标事件，不适合拿来做大范围、多区域、持续变化的拖拽命中。它只能作为 no-setShape 路径下的谨慎辅助。
9. Electron `setShape()` 能限制窗口绘制和交互区域，但只在 Windows / Linux 且仍是实验能力；本项目已有 Linux setShape / debounce 经验，历史拖拽必须沿用 hash / throttle / debounce，不随橡皮泥曲线逐帧刷新 native region。

本项目的落地取舍：

1. 不引入 dnd-kit / React DnD / Pragmatic drag and drop 作为第一版依赖，因为当前已有 pointer intent、compact geometry、host bridge 和桌面窗口管线，引入库会增加与现有选择 / 滚动 / 桌面形状的适配面。
2. 采用这些库的结构经验：source 留原位，drag overlay 独立渲染，viewport 坐标，source payload 与 visual layer 分离，拖拽起手区域明确。
3. 桌面端不把网页 overlay 当成万能解法。网页 overlay 只能突破 DOM 边界；要突破 BrowserWindow 边界，第一版应由 NEKO-PC 临时扩大窗口；单独 overlay BrowserWindow 只作为后续验证过影响面后的备选。

参考链接：

1. dnd-kit DragOverlay：`https://dndkit.com/legacy/api-documentation/draggable/drag-overlay/`
2. Atlassian Pragmatic drag and drop design guidelines：`https://design-system-docs-proxy.services.atlassian.com/components/pragmatic-drag-and-drop/design-guidelines/`
3. Electron BrowserWindow：`https://www.electronjs.org/docs/latest/api/browser-window`

## 验证后的实施取舍

已验证过 NEKO 网页端和 NEKO-PC 桌面壳的关键闭环。验证代码不保留为正式实现，本文只沉淀可复用结论、风险和拆分方式。

验证过的检查包括：

1. NEKO：`npm --prefix frontend/react-neko-chat run typecheck`
2. NEKO：`node --check static/app-buttons.js`
3. NEKO：`npm --prefix frontend/react-neko-chat test -- --run App.test.tsx`
4. NEKO-PC：`node --check src/preload-chat-react.js`
5. NEKO-PC：`node --check src/preload-pet.js`
6. NEKO-PC：`node --check src/main/window-host-ipc.js`
7. NEKO-PC：确认当前 `package.json` / 测试文件后，再运行对应 `node --test ...` 或项目脚本。

这些检查只证明方案方向可行，不代表可以直接合入验证性代码。正式实现仍必须按阶段拆分，并在每阶段重新验证。

### NEKO 取舍

1. 网页端基本闭环可行。`CompactExportHistoryPanel.tsx` 可以在现有 pointer intent 上扩展出 `imageDrag` / `bubbleDrag`，source 留在历史列表，drag preview 用 fixed portal 承载，原气泡只做显示变化。
2. 第一版优先复用 `MessageBlockView` 的现有 DOM 与 `.message-block-image`，通过当前气泡内图片节点序号映射回 `message.blocks` 的 image block。不要为了标记 source 给每个 block 外包新 wrapper，除非后续动效锚点证明确实需要。
3. 图片发送不能只改 `setComposerAttachments()`。历史图片必须进入 `app-buttons.js` 的 pending attachment 源头，再由 `syncPendingComposerAttachments()` 同步给 React host，最终走 `sendTextPayloadInternal()` 的图片规范化和发送链路。
4. 历史图片入口需要独立语义，例如 `addHistoryImageAttachmentToPendingList()` 或等价命名；入口内部可以复用现有 pending attachment DOM / normalization，但外部不应继续暴露“截图”语义。
5. 发送触发要复用既有 `source: 'react-chat-window'` 路径，以保留 React optimistic message / rollback 等行为；可以额外带 `compactHistoryDragSessionId` 标识来源，但不要把 source 改成新值后绕开既有分支。
6. `onComposerSubmit?.({ text })` 只是触发发送，不是发送成功 ack。正式视觉成功态不能只看 drop 命中；需要等待可确认的 submit / send 结果，或明确失败恢复策略。
7. 多图片附件需要 all-or-nothing 或明确的部分失败策略。若已加入 pending attachment 源头后失败，要能清理真实 pending DOM / buffer；当前 `removePendingAttachmentById()` 带动画延迟，必要时需要即时清理能力。
8. remote URL、blob URL、data URL 的生命周期和 CORS 风险不同，不能只用 data URL 通过测试就认定图片附件链路完成。
9. `CompactExportHistoryPanel.tsx` 会明显变重。正式实现应优先抽出 compact history drag helper / hook / drag layer 组件，避免历史导出面板同时承载选择、预览、导出、拖拽、动画和 bridge 事件。
10. 第一个正式 PR 不应包含最终橡皮泥视觉；先把 drag session、drop request、附件入口、发送回执和测试做稳，再合入效果层。

### NEKO-PC 取舍

1. 当前 NEKO-PC 分支没有 `desktop-compact-layout.js` 这类历史拖拽专用布局模块，也没有已成型的 history drag geometry bridge。正式实现第一步不是复用不存在的 compact layout 管线，而是在现有 `preload-chat-react.js`、`window-host-ipc.js` 和窗口控制能力上补一个最小桥接：消费 NEKO 页面输出的临时 drag geometry，临时调整 ReactChat BrowserWindow bounds，必要时回传 `rebaseDelta`，结束后恢复。
2. NEKO-PC 不解析消息 block、不构建发送 payload、不复制导出逻辑。它只消费 NEKO 页面输出的 `sessionId`、`phase`、`visual rect`、`hit rect`、`overAvatar` 等几何 / 状态信息。
3. fixed portal 可以突破紧凑历史 DOM / scroll 容器裁剪，但不能突破 Electron BrowserWindow。NEKO 页面必须输出 drag visual rect / hit rect / phase，由 NEKO-PC 消费后临时扩大 ReactChat BrowserWindow。
4. 橡皮泥连接层只进入 visual/native bounds，不进入 hit rect。connection rect 可以扩大窗口显示，但不能成为可点击 / 可拦截的大面积命中区。
5. 如果 drag visual union 迫使 ReactChat BrowserWindow 的 `x/y` 改变，页面内 fixed overlay 会产生坐标重基问题。正式 NEKO 需要消费 NEKO-PC 回传的 `rebaseDelta`，或 NEKO-PC 采用尽量只扩右/下、不移动左上角的窗口扩展策略。
6. setShape 可用的平台上，临时扩大窗口 + 小 hit rect 是较清晰的方案；no-setShape 平台只能整窗 `setIgnoreMouseEvents`，在 active drag 时如果保持接收 pointer，扩大后的透明区域可能短暂遮挡桌面 / 模型。正式实现必须有降级策略。
7. 现有 `get-cursor-point` 能提供全局位置，但不能单独解决全局 mouseup。若不临时扩大 ReactChat BrowserWindow，renderer 仍可能收不到 pointerup；第一版桌面拖到模型更适合先靠临时 bounds 把 pointer 留在 ReactChat 窗口内。
8. 当前 NEKO React `App.tsx` 的 avatar hit helper 主要读页面内模型 manager；桌面 ReactChat 实际依赖 `window.__nekoDesktopAvatarBounds` / `neko:desktop-avatar-bounds-change`。正式实现要么让 NEKO App 显式读取桌面 avatar bounds，要么让 NEKO-PC 通过 bridge 回传 `desktopOverAvatar`。

### 测试取舍

1. JSDOM 可以覆盖关键业务回归：气泡拖拽不切换选择、图片拖拽进入 pending attachment 源头、命中 avatar 后发送、session id 透传。
2. 当前 NEKO-PC 分支没有 `desktop-compact-layout-contract.test.js`。如果新增纯几何 helper，应新增类似 `compact-history-drag-bounds-contract.test.js` 的合同测试，覆盖 drag visual rect 并入临时 window bounds、drag hit rect 保持小范围、connection 不进入 hit rect、窗口 rebase delta 等桌面几何合同。
3. 动画质感、真实滚动竞争、BrowserWindow 离窗、平台 setShape / no-setShape 表现必须靠真实运行验证；不能只靠单测判定完成。

## 当前阶段三代码审查（2026-05-27）

本节基于临时分支 `/Users/tonnodoubt/N.E.K.O__compact-history-drag-temp` 的实际代码审查，目标是防止阶段三继续被旧假设或临时补丁污染。正式迁移仍要以当前目标分支代码为准重新核对。

### 阶段三重做基线

2026-05-27 的临时阶段三视觉尝试未达到设计目标，不能按“已完成”迁移。后续实施应从当前 `chat_min` 基线重新做效果层，只保留已验证的阶段一 / 阶段二拖拽与发送合同，避免把失败视觉补丁继续堆厚。

必须遵守的反例约束：

1. 不能出现“源头孤立小椭圆 + 右侧裸文字”的效果。拖出内容必须仍像原气泡，连接必须是从原气泡边缘被拉出的同一团软体视觉。
2. 原位不能继续完整显示原句；拖拽开始后原位只能保留布局槽位和源头软块 / 短胶块。
3. 临时气泡不能被明显 skew、翻转、斜切或变成另一种卡片。允许轻微 scale / 压缩，但主体必须接近原气泡。
4. 中间连接不能是独立椭圆、描边线或硬拼接。连接应是闭合胶带 path，由两条二次贝塞尔曲线构成，端点贴在源头软块和拖出气泡靠近方向的边缘上。
5. 长气泡不能把整个长矩形参与源头计算。源头只取靠拖动方向的一段圆角端 / 边缘软块；长文本只影响拖出气泡本体，不影响胶带源点宽度。
6. gooey / blur 只作用背景 blob、源头软块和连接胶带。文字、图片内容和按钮图标必须在未过滤层保持清晰。
7. 颜色必须从当前气泡样式继承或通过同一 palette 派生；源头、连接、拖出气泡不能有明显色差。
8. 拖拽过程中若有新消息进入、历史滚动或列表重排，连接锚点必须重新读取 live rect，不能继续连到旧位置。
9. 高频 pointer move 只能用 rAF 更新 DOM / SVG 属性；不能每帧走 React state 重渲染整层，尤其不能拖到模型右侧按钮时卡顿。
10. 每次声称阶段三完成前必须真实运行、截图或录屏对比短文本、长文本、图片、未命中弹回、命中发送、新消息进入时锚点跟随。只跑单测和 build 不算视觉完成。

推荐重新实施拆分：

1. 先只恢复清晰、稳定的拖拽预览：临时气泡完整显示为原气泡样式，原位隐藏内容并保留槽位，不做胶带。
2. 再增加 QQ sticky bubble 几何：源头软块、拖拽端锚点、两条二次贝塞尔闭合胶带，所有背景 blob 放在同一个 SVG/filter 层。
3. 再处理动态锚点：新消息、滚动、列表 reflow 时从 live DOM 读取源气泡 / 消息行 rect。
4. 最后加吸附、弹回、发送消失 / 再出现动画和性能调优。

当前已具备：

1. `CompactExportHistoryPanel.tsx` 已在现有 pointer intent 上扩展 `imageDrag` / `bubbleDrag`，拖拽层通过 `createPortal(document.body)` 使用 viewport 坐标渲染。
2. 原历史列表槽位被保留；拖拽时源气泡内容通过视觉层隐藏，不用 `display: none`、真实删除或折叠高度制造“拿走”效果。
3. `App.tsx` 已能构建 `CompactHistoryDropPayload`，并通过 `onCompactHistoryDrop` 进入 host。`static/app-react-chat-window.js` 与 `static/app-buttons.js` 已有历史拖拽投递入口，图片会进入 pending attachment 源头，再由现有发送链路送出。
4. 当前网页端阶段三结构已经从“HTML 气泡框 + 独立连接”调整为分层实现：SVG gooey 层同时绘制拖拽气泡背景 shell path 和连接 path，HTML 层只承载清晰内容；文字和消息内容不进入 filter 层，避免模糊。
5. 小圆 / 源头锚点已经改为基于 `.compact-export-history-message` 的 `sourceFrameRect`，不再按长文本气泡宽度决定左右位置。刷新不到 live rect 时保留上一帧 `sourceFrameRect`，不应退回用 `originRect` 宽度猜。
6. 当前左右侧映射来自实际显示反馈：user 气泡源头在右侧窄范围，assistant / tool / system 气泡源头在左侧窄范围。后续不要用“角色语义”直接推导左右，CSS 对齐或主题变化后必须用截图复核。

仍需收口：

1. 阶段三视觉仍是 NEKO 页面内部实现；还没有向 NEKO-PC 输出稳定的 `visualRect`、`hitRect`、`sourceFrameRect`、`phase`、`rebaseDelta` 等桌面桥接字段。
2. `CompactExportHistoryPanel.tsx` 已明显变重，视觉稳定后应拆出 drag geometry helper、drag layer 组件或 hook，避免选择、预览、导出、拖拽、发送和动画全部堆在一个组件里。
3. 当前 active drag 是 React 本地状态；桌面端需要稳定 `sessionId` / phase / timeout 合同，防止页面结束后 PC 壳仍保持临时 bounds。
4. 几何单测只能证明 path / rect 计算不崩，不能证明“好看”。阶段三必须保留真实运行截图或录屏级验证，尤其验证长文本、短文本、图片、上下左右 360 度拖动、滚动中新消息进入时的表现。
5. 小圆水平锚点必须继续基于消息行侧边的窄范围，而不是气泡内容宽度。这个约束是为了解决长对话导致源点乱跑的问题，不能因为 fallback 或重构被抹掉。

## 阶段 0：证据补齐

开始写代码前先确认当前真实链路：

1. 重新读 `CompactExportHistoryPanel.tsx` 的 message wrapper、bubble button、selection、scroll 和 `pointerIntentRef`。
2. 重新读 `MessageBlockView.tsx` 的 image / link / buttonGroup 渲染结构，确认哪些 DOM 节点可以成为 image drag source。
3. 重新读 `App.tsx` 的 composer payload、附件状态、图片导入和 avatar hit helper。
4. 重新读 `static/app-react-chat-window.js` 的 compact geometry collector 和 composer callback。
5. 重新读 NEKO-PC 的 `preload-chat-react.js`、`preload-pet.js`、`main.js`、`window-host-ipc.js`，确认当前 bounds / setShape / avatar bounds 同步事件名。
6. 确认现有图片发送链路是否支持“已有 URL / blob / data URL 作为附件发送”。如果只支持打开文件选择器，需要先设计一个复用现有附件语义的导入入口，而不是新增后端协议。
7. 暂不调研系统级图片拖出 / 保存链路；仅记录当前不做的理由和未来可能重新评估的触发条件。
8. 确认全局 `<img draggable="false">` 和 `dragstart.preventDefault()` 不影响自定义 pointer 拖拽；当前实现不能依赖浏览器原生图片 dragstart。

输出物：

1. 代码修改前的事实记录或短实施注释。
2. 明确现有发送链路可接受的 payload 形状。
3. 明确 NEKO-PC 需要消费的最小 drag state / geometry payload。
4. 明确历史图片进入 pending attachment 源头的具体函数，而不是只更新 React host 的附件展示状态。
5. 明确网页端 pointer 事件在桌面离窗后的可观测边界，不能把网页端本地 pointerup 当作 NEKO-PC 可用性的证据。
6. 明确桌面 avatar bounds / `desktopOverAvatar` / `rebaseDelta` 的事件来源和字段形状。

## 阶段 1：NEKO 拖拽基础

先只完成 NEKO 网页端的拖拽识别、drag source、payload 准备和失败恢复，不接发送动画，不改 NEKO-PC。

第一步是在 `CompactExportHistoryPanel.tsx` 中把现有一次性 `pointerIntentRef` 扩展成明确状态机。

建议状态：

1. `pending`：pointer down 后尚未决定。
2. `click`：移动未超过阈值，pointer up 可执行气泡选择。
3. `scroll`：纵向滚动意图明确，直到 pointer up 都不触发选择或拖拽。
4. `imageDrag`：从图片 / 表情包 block 起手并超过拖拽阈值。
5. `bubbleDrag`：从气泡文本或空白区域起手并超过拖拽阈值。
6. `cancelled`：从链接、按钮、preview 控件、滚动条、不可选择区域或被外部状态打断。

规则：

1. 图片 / 表情包拖拽优先于整条气泡拖拽。
2. 当前 `isSelectionIgnoredTarget()` 会把 `.message-block-image` 视为选择忽略区；实施图片拖拽时要把“忽略选择”和“允许 imageDrag 起手”拆开，不能继续让图片起手直接被取消。
3. 点击链接、按钮、导出 preview 控件、选择操作按钮、滚动条时，不进入整条气泡选择或拖拽。
4. 一旦进入 `scroll`、`imageDrag`、`bubbleDrag` 或 `cancelled`，pointer up 不再补触发 click 选择。
5. 选择状态不能因拖拽开始、失败或取消而改变。
6. 阈值不要写成产品文档常量；先用小而保守的内部常量，经过真实设备调试后再定。
7. 触摸板 / 鼠标 / pointer capture 行为要一起验证。

第二步补稳定锚点与拖拽状态。历史气泡需要为后续橡皮泥连接和原位稳定提供可测量锚点，但第一版优先复用现有 message 外层和 `MessageBlockView` DOM，不给每个 block 大面积增加新 wrapper；只有动效锚点或无损 source 标记确实需要时，才补最小结构。

实现原则：

1. 每条 message 外层保留稳定 `message.id` key。
2. 优先使用现有 message 外层测量高度、保留原位空间、提供连接锚点和恢复定位。
3. 拖拽时原位不塌陷，不能通过高度折叠、列表重排、`display: none` 或真实删除 DOM 来模拟气泡被拿走。
4. 拖拽影子使用独立 overlay，例如 `data-compact-drag-layer`，不作为长期 history geometry item。
5. active drag 状态只表达临时交互，例如：
   - `type: 'image' | 'bubble'`
   - `messageId`
   - `blockIndex`
   - `payload`
   - `originRect`
   - `pointerOffset`
   - `overAvatar`
   - `phase: 'dragging' | 'returning' | 'sending' | 'settling'`
6. 拖拽期间暂停历史自动贴底；动画完成后按原本 `compactExportAutoScrollToBottom` 语义恢复。
7. 拖拽期间若消息列表更新，恢复定位以 `message.id` / sortKey 为准，不依赖 DOM 位置序号。
8. 图片 block source 优先通过现有 `.message-block-image` 命中节点映射回 `message.blocks`，或让 `MessageBlockView` 暴露可选 source metadata；避免为所有 block 新增布局 wrapper。

第三步完成图片 block 的内部拖拽 source。当前只支持拖到当前猫娘模型，不支持拖出到外部保存。

实现原则：

1. 只允许把用户已经能在历史层看到的图片作为内部 drag source 拿起。
2. 这里的“拖出”仅指从历史气泡里把图片作为内部 drag source 拿起，不代表拖到外部应用或系统保存。
3. 图片 drag 使用自定义 pointer 状态和 overlay，不依赖浏览器原生 `dragstart`。
4. 拖拽预览先使用轻量缩略图，不做最终吸附 / 弹回动效。
5. 不填充面向外部应用的保存型 `DataTransfer` 数据。
6. 拖拽失败时，不能丢消息、不能改变选择、不能触发发送。

## 阶段 2：NEKO 发送投递

拖拽基础稳定后，再完成发送给当前猫娘角色。

这一阶段必须同时覆盖前端投递和后端送达，不允许只做 UI 效果。

第一步定义 payload 转换。

payload 转换留在 NEKO / React host，不下沉到 NEKO-PC。

图片 / 表情包 payload：

1. 从当前可见 image block 提取安全来源。
2. 允许的数据来源包括可公开 URL、blob URL、data URL 或由现有附件链路可接受的临时对象。
3. payload 只服务内部拖到当前猫娘后的附件 / 图片发送链路。
4. 不把内部绝对路径、鉴权 URL、token、用户名或缓存路径写入 drag data。
5. 当前不填充 `text/uri-list`、`text/html`、`text/plain` 等面向外部保存的 DataTransfer 数据；如未来恢复外部拖出，需另开设计并重新评估“误保存”问题。
6. 当前 `ComposerSubmitPayload` 只有 `text` 和 `requestId`；图片投递不能假装 `onComposerSubmit({ attachments })` 已存在。
7. 可实施方案应在 `app-buttons.js` / React host 边界补一个复用现有附件语义的内部入口，例如 `addHistoryImageAttachmentToPendingList()` 或等价命名，把历史图片安全加入 pending attachment 源头，再调用现有同步函数更新 React host 附件展示；不能只调用 `setComposerAttachments()`。
8. 这个入口只属于前端 host / React 适配层，不新增后端图片发送协议。
9. 发送时要复用 `static/app-buttons.js` 的 pending attachment 源头 → `syncPendingComposerAttachments()` → `sendTextPayloadInternal()` → `imageUrls` → 后端 attachments 链路；不能另写一条只在 React 内部成立的图片发送路径。
10. 对 data URL / blob URL / remote URL 要走与现有导入图片一致的规范化、压缩、失败 toast 和清理逻辑；不能把历史图片绕过 `normalizeAllPendingComposerAttachments()`。
11. 后端验收要确认附件进入 `main_logic/core.py` 的 pending images / attachments 处理，以及 OpenClaw / task executor 能继续收到图片输入。
12. 多附件加入 pending attachment 源头时必须有 all-or-nothing 或明确的部分失败策略；不能成功加入一部分后静默发送残缺 payload。

整条气泡 payload：

1. user 文本气泡：按普通用户文本重新发送。
2. assistant / 猫娘文本气泡：作为引用式文本或普通文本发送，具体格式要以现有 composer 接受形状为准。
3. 图片 / 表情包气泡：走现有图片 / 附件发送链路。
4. 混合内容气泡：按现有 message block 顺序转换成文本 + 附件 payload。
5. 不支持的 block 使用可理解文本 fallback，但不能丢弃可发送的图片或文本。
6. 不创建新的后端重发接口。

发送策略：

1. 默认语义是“拖到当前猫娘后立即发送”，而不是只填入 composer 等用户二次确认。
2. 立即发送必须复用既有 `source: 'react-chat-window'` 分支，以保留 React optimistic message、rollback、dedup 和现有错误处理；可以额外附带 `compactHistoryDragSessionId` 作为来源标识，但不要把 `source` 改成新的值绕开原分支。
3. 只有现有发送链路无法安全承接混合 payload 时，才临时退化为“投递到 composer / 附件区并给出明确状态”，且必须把这种退化写成可见状态和后续待办，不能假装已经完成发送。
4. 图片或混合内容立即发送前，必须确认 host `composerAttachments` 与真实消费方会把附件一起送出；否则先补 pending attachment 源头适配，不能只在 React 里构造不可消费的本地状态。

第二步完成网页端 drop 到当前猫娘。网页端先完成从历史层到当前模型 bounds 的投递。

实现原则：

1. 使用 `App.tsx` 现有 avatar bounds / range helper 判断当前指针是否在猫娘模型接收范围。
2. 只针对当前可见 / 当前激活的猫娘角色，不引入多角色选择逻辑。
3. 拖拽进入模型范围时显示轻量接收反馈，例如 avatar range highlight 或拖拽影子状态变化。
4. 松手命中模型后：
   - 图片 drag：调用现有图片 / 附件发送链路。
   - 气泡 drag：调用现有 composer / 附件发送链路。
5. 松手未命中模型时：
   - 不发送任何内容。
   - 标记为失败恢复；最终弹回表现留到拖拽效果阶段实现。
   - 原位 wrapper 保持原尺寸。
6. drop 成功后：
   - 标记为成功投递；最终吸附、原位消失和再出现表现留到拖拽效果阶段实现。
   - 原位布局不塌陷。
   - 新发送消息按现有消息流进入 `messages`，不能从历史里删除原消息。

第三步做后端链路验证。

验证点：

1. 纯文本历史气泡投递后，`sendTextPayload()` 收到正确文本，后端按普通用户消息处理。
2. 图片历史气泡投递后，pending attachment 源头中出现该图片，React host 附件状态同步更新，`sendTextPayloadInternal()` 生成 `imageUrls`，后端消息中出现 `attachments`。
3. 仅图片、文本 + 图片、assistant 引用文本三类投递都能进入同一会话上下文，不新建无关 session。
4. 后端失败、图片无效或附件规范化失败时，前端拖拽状态恢复，不产生“已送达”的视觉确认。

## 阶段 3：NEKO 拖拽效果

NEKO 的拖拽和发送都跑通后，最后补网页端拖拽效果。

动画是表现层，不能改变业务事实。

网页端边界策略：

1. 可以突破紧凑聊天框和历史层 DOM 边界显示，拖拽影子与橡皮泥连接层应使用 fixed / portal overlay 挂到 React chat 根层或 document body，而不是被历史滚动容器裁剪。
2. overlay 只突破视觉边界，不改变 compact surface anchor、history layout、scroll container 和 selection 状态。
3. overlay 默认 `pointer-events: none`；真正的 pointer 判定仍由拖拽状态机和 avatar bounds helper 完成。
4. overlay 坐标使用 viewport / client 坐标，避免受 history scroll、CSS transform 或 compact panel resize 影响。
5. 如果网页端嵌在桌面 ReactChat BrowserWindow 中，overlay 只能突破 DOM 边界，不能突破 BrowserWindow 边界；桌面端突破需要阶段 4-6 的窗口 bounds 配合。

规则：

1. 拖拽开始后出现跟随指针的拖拽影子，原气泡仍占据原来的列表位置。
2. 原位不塌陷、不折叠、不推动上下消息；历史列表高度和当前滚动位置保持稳定。
3. 拖拽影子和原气泡之间形成橡皮泥式连接，视觉上像一团柔软材料被拉开。
4. 连接随距离变化：
   - 距离近时连接较短较厚，贴近原气泡边缘。
   - 距离变远时连接逐渐变长变细，允许轻微曲线和弹性滞后。
   - 连接不能变成生硬直线，也不能细到闪烁或断裂。
5. 原气泡可以有较明显的被牵引形变，例如靠拖拽方向的一侧拉圆、压扁、局部拉伸、边缘高光流动或柔软回弹，但这些都只能是显示层变化。
6. 原气泡的布局槽位、真实 DOM 顺序、消息数据、hit rect 基础语义和滚动高度都不改变；即使视觉变化稍夸张，也不能让列表实际塌陷、跳动或重排。
7. 视觉风格可以比普通 UI 动效更有弹性和存在感，但必须保持美观、干净、可读，不做浮夸、怪异、黏糊糊或恐怖感的效果。
8. 未命中模型：
   - 拖拽影子沿连接弹回原位。
   - 橡皮泥连接回缩并消失。
   - 原气泡恢复普通状态。
9. 命中模型：
   - 拖拽影子向模型吸附或缩小。
   - 模型附近短暂接收高亮。
   - 发送链路触发。
   - 原位气泡做视觉消失，但布局槽位不塌陷。
   - 消失后在原位播放短小的再出现 / 复位动画，让历史气泡回到原处可见状态。
10. 原位消失只能用 opacity、scale、mask、clip-path、filter 或等价视觉层实现，不能用 `display: none`、真实删除 DOM 或折叠高度。
11. 动画过程中禁止自动滚动和恢复动画同时争夺 `scrollTop`。
12. 动画结束必须清理 active drag state、drag layer、橡皮泥连接层和临时 geometry。

### 阶段三视觉基准

阶段三不是给拖拽气泡外面叠一个装饰圈，也不是把三块 DOM 形状拼在一起。目标效果更接近 QQ 未读气泡拖拽的软连接，但主体是聊天气泡而不是纯圆点。

必须坚持的形态：

1. 原位气泡槽位不塌陷，但视觉上不能继续完整显示原句。拖拽时原位应退化成靠消息行侧边的小圆 / 短软块，作为被拉出的源头。
2. 拖出来的临时气泡基本沿用第二阶段的原气泡样式，包括背景、圆角、阴影、内容排版和图片比例。它可以轻微 scale、压缩、拉伸，但不能明显 skew、翻转或变成另一个样式。
3. 中间连接必须是一条闭合的橡皮泥 path，端点贴在源头小圆和拖拽气泡靠近拖动方向的边 / 角上；不能用椭圆、线段、多个圆点或互相断开的图形冒充连接。
4. 连接 path 采用两条二次 / 三次贝塞尔曲线组成，端点取两端形状的切点，控制点随距离和方向插值。距离越远，源头越小、连接颈部越细；距离近时连接更厚、更融合。
5. 长气泡不能把整个长矩形参与胶带计算。连接只绑定靠拖动方向的一小段圆角端 / 边缘区域，避免长文本导致源点乱跑、连接跨越整条气泡或出现突出的中段。
6. 方向是 360 度连续的，不是只支持左右 / 上下四个方向。锚点可以被限制在气泡靠近拖动方向的边缘范围内，但切点和控制点必须随实际角度连续变化。
7. 形变主要发生在源头小圆缩放、连接胶带变细和拖拽气泡靠连接一侧的局部边缘拉扯；拖拽气泡主体不做大幅翻转式形变。
8. 颜色必须统一。源头、连接和拖拽气泡背景使用同一套气泡颜色 / 阴影体系，只允许靠透明度、blur 和高光产生层次，不能出现明显色差。
9. Gooey filter 只用于软化连接和背景 blob 边缘。文字、图片、表情包内容必须在未过滤层渲染，保持清晰和比例正确。
10. 小圆水平位置只允许在消息行固定侧边的窄范围内滑动：玩家气泡 / 猫娘气泡以实际显示侧为准，避免长气泡宽度影响源点。后续如果 CSS 对齐变化，必须通过截图重新确认左右侧。

实现取舍：

1. 保留当前“SVG path 承载连接”的方向；拖拽气泡背景需要融合时，可在同一 SVG / gooey 组内增加 shell path，比 DOM 圆点堆叠和多块图形叠加更容易获得连续边缘。
2. 拖拽气泡外观继续复用现有 `MessageBlockView` 渲染结果；额外形变用外层 transform、clip-path / mask 或背景副本实现，不改内容层排版。
3. 如果需要更强的橡皮泥感，优先增强 source nub 半径曲线、连接宽度曲线、靠连接边缘的背景副本拉伸，而不是扭曲文字内容。
4. 运行时判断“好看”必须用长文本、短文本、纯图片、文本 + 图片、从 8 个方向拖动和有新消息进入这几类样例一起看；只看短气泡会误判完成。

### 阶段三网页端结构实施参考

本节沉淀 NEKO 网页端阶段三实际实现中可复用的结构经验，主要服务后续 NEKO-PC 桌面端移植。桌面端不得复制消息转换或重算 DOM 内容，但可以消费这些视觉 / 几何合同。

核心分层：

1. 源消息行层：
   - 原历史消息仍留在 `.compact-export-history-message` 文档流中，保持原高度、DOM 顺序、滚动位置和选择状态。
   - 拖拽开始后，源消息内容只做视觉隐藏 / 退化，不用 `display: none`、高度折叠、删除 DOM 或移动真实气泡。
   - 源头小圆 / 短软块的锚点来自消息行 `sourceFrameRect` 的固定侧边窄范围，而不是来自长气泡内容宽度；这样新消息进入、长文本换行或列表轻微重排时，源点不会横向乱跳。
   - 每帧优先读取 live `.compact-export-history-message` rect；读取失败时保留上一帧有效 `sourceFrameRect`，不能退回用 `originRect` 宽度猜。
2. SVG 背景 / 橡皮泥层：
   - 使用一个 fixed SVG overlay 覆盖 viewport，只绘制背景 blob：源头软块、连接胶带、拖拽气泡背景 shell。
   - `compact-history-drag-goo-group` 内同时放连接 path 和拖拽气泡 shell path，并使用同一组 `--compact-history-drag-surface-rgb` / edge / shadow 变量，保证源头、连接和气泡背景像同一团材料。
   - gooey filter 只作用在这个背景组上；不要包住 `MessageBlockView`、文字、图片、图标或按钮。
   - 连接 path 仍按 QQ sticky bubble 思路生成：源头椭圆切点 `s1/s2`、拖拽端圆角矩形切点 `d1/d2`、中间腰点 `waist`，用两条二次贝塞尔闭合，不用椭圆、描边线或 DOM 圆点假装连接。
   - 拖拽气泡背景 shell 不再是普通 HTML 圆角矩形，而是 SVG path。靠近源头的一侧和相邻边可以按方向局部弯曲，远离源头的一侧保持接近原气泡，避免出现“方框 + 连接组件”两块分离的感觉。
3. HTML 内容层：
   - fixed drag layer 只负责显示清晰内容，气泡内容继续复用 `MessageBlockView`。
   - `.compact-history-drag-bubble` 本身背景透明、阴影为空；真正的背景由 SVG shell path 提供。
   - `.compact-history-drag-bubble-content` 允许很轻微 scale，但原则上不 skew、不翻转、不斜切，图片 / 表情包保持 `object-fit: contain` 和原比例。
   - image drag 可以继续使用 `<img className="compact-history-drag-image">`，但图片预览的比例和裁切不能受 bubble shell 逻辑影响。

运行时更新策略：

1. `ActiveCompactHistoryDrag` 至少保存 `type`、`phase`、`messageId`、`payload`、`originRect`、`sourceFrameRect`、`originElement`、`pointerOffset`、`pointerClient`、`overDropTarget`。
2. `pointermove` 不应每帧触发 React 全量重渲染；先把最新 pointer 写入 ref，再用 `requestAnimationFrame` 执行 `applyCompactHistoryDragFrame()`。
3. 每帧 `apply` 时只做必要 DOM 写入：
   - 重新读取 live `originRect` / `sourceFrameRect`。
   - 给 drag layer / source message 写 CSS vars。
   - 通过 `pathRef.setAttribute('d', ...)` 更新连接 path 和 shell path。
   - 只有 `overDropTarget`、`phase`、session 结束等离散状态变化才走 React state。
4. 连接腰点可以保留上一帧曲线点并低通插值，形成轻微滞后；但这个滞后只影响视觉 path，不能影响真实 drop point 或发送判定。
5. 拖拽结束、取消、发送、history 关闭、ChoicePrompt / GalGame 打开、组件卸载时，必须清理 rAF、timer、source style vars、path ref 和 active drag state。

几何生成参考：

1. `getCompactHistorySourceNub()`：根据拖拽距离让源头软块逐渐缩小；bubble 类型源头可以是椭圆 / 短胶块，image 类型可以更接近圆形。
2. `getCompactHistorySourceNubAnchor()`：bubble 类型锚点限制在消息行侧边窄范围，user / assistant 的左右侧以实际显示为准；不要让长气泡宽度参与源头横向定位。
3. `getCompactHistoryRoundedRectJoin()`：拖拽端取靠近源头方向的圆角矩形切点；命中角落时按圆角求切点，命中边缘时按边缘切线展开，避免连接只连到中心。
4. `getCompactHistoryElasticGeometry()`：负责输出连接 path、shell path、pull、opacity 和下一帧腰点；它是网页端和桌面端 bridge 的几何事实来源。
5. `getCompactHistoryBubbleShellPath()`：只负责拖拽气泡背景 shell 的局部形变。它不应改变内容排版，不应把整条长气泡轮廓卷入连接计算，也不应产生额外独立凸起。

给 NEKO-PC 的移植合同：

1. NEKO-PC 不需要、也不应该重算 shell path 或连接 path；桌面端只需要托住这些视觉层的外接 rect，并保持窗口 / hit region / rebase 连续。
2. NEKO 页面后续输出桌面 bridge 时，应从网页端当前结构派生这些字段：
   - `sourceFrameRect`：源消息行 live rect，用于源头跟随。
   - `dragVisualRect`：HTML 内容层和 SVG shell path 的并集外接 rect。
   - `connectionVisualRect`：源头软块 + 连接胶带外接 rect。
   - `dragHitRect`：指针附近和拖拽气泡的小范围命中，不包含连接胶带的大面积外接矩形。
   - `phase`、`sessionId`、`seq`、`pointerClient`、`overTarget`。
3. 桌面端临时 `setBounds()` / `setShape()` 应消费 visual rect / hit rect 的稳定外接结果，不跟随 SVG path 每个控制点逐帧重算 native shape。
4. 如果 NEKO-PC 扩大 ReactChat BrowserWindow 并改变左上角，必须回传 `rebaseDelta`；NEKO 页面收到后统一平移 `pointerClient`、`originRect`、`sourceFrameRect`、visual rect 和动画目标点。
5. 桌面端验证时必须重点看三类错误：视觉被 BrowserWindow 裁掉、透明连接外接矩形吃事件、bounds rebase 后源头 / 连接 / 拖拽气泡跳位。

桌面端禁止从网页端误搬的点：

1. 不要把 gooey filter 或 blur 套到文字、图片和 `MessageBlockView` 内容层。
2. 不要用拖拽气泡内容宽度决定源头位置；长气泡只影响拖拽气泡本体，不影响消息行侧边源点。
3. 不要把 `connectionVisualRect` 当作 `hitRect`。
4. 不要每个 pointermove 都 `setBounds()` 或 `setShape()`；网页端可以 60fps 更新 SVG，桌面壳必须节流 / hash / debounce。
5. 不要让 PC 端根据 CSS class 猜消息内容、气泡角色、图片 payload 或连接方向；这些都由 NEKO 页面输出的状态和 rect 决定。

## 动画参考与实现取舍

已有类似实现通常分成两类：一类用 SVG / CSS filter 做 gooey 黏连，一类用 spring motion value 做拖拽跟随、滞后和回弹。本文只提炼方法，不要求照搬外部 UI 或新增依赖。

参考点：

1. Gooey / metaball 效果常用 SVG filter 的 `feGaussianBlur` 加 `feColorMatrix`。思路是先让形状边缘模糊互相“渗开”，再提高 alpha 对比度把边缘收紧成黏连形状。
2. MDN 的 SVG filter 和 CSS filter 文档说明了 `feGaussianBlur`、`feColorMatrix` 以及 CSS `filter: url(...)` 的基础能力；如果使用 SVG overlay，可以把连接层限制在拖拽临时区域内。
3. Motion React 的 drag 文档提供了 drag lifecycle、elastic constraints、spring / motion value 的思路；当前 `frontend/react-neko-chat/package.json` 未包含 motion / framer-motion / react-spring / gsap 等动画依赖，因此优先按项目现有 pointer state + `requestAnimationFrame` / CSS transition / SVG overlay 实现，不为这个效果优先引库。

推荐方案：

1. 用临时 overlay 承载三层视觉：
   - 原位源头小圆 / 短软块。
   - 跟随指针的拖拽影子。
   - 位于两者之间的橡皮泥连接层。
2. 连接层优先用 SVG path 或 Canvas 绘制，而不是用一串 DOM 圆点堆出来；第一版继续优先 SVG path，便于调试切点和外接 bounds。
3. SVG path 用 QQ 拖拽气泡的几何思路生成：源头小圆半径随距离缩小，拖拽端取靠近源头方向的边 / 角切点，两条贝塞尔闭合成胶带。
4. 连接不能只画到拖拽气泡中心，也不能在拖拽气泡边缘外留缝。胶带端点应略微压进拖拽气泡背景边缘，配合相同 fill 和轻微 blur 形成融合。
5. 如果使用 gooey filter，只作用于源头软块、连接 path 和拖拽气泡背景 shell 这类背景 blob，不作用于整条历史列表和内容层，避免性能和文字模糊问题。
6. 拖拽影子位置使用 pointer 原始坐标；连接曲线可以用低通滤波或 spring 值稍微滞后，制造橡皮泥拉伸感。
7. 回弹时不要重排列表；只把拖拽影子和连接层动画回源头锚点，然后清理 overlay。
8. 原气泡形变可以用伪元素、mask、clip-path、filter、局部 scale 或 overlay 副本实现；避免直接改会影响排版的 width、height、margin、position flow。
9. 成功投递时不要把原 DOM 删除；先让原位视觉层短暂缩小 / 淡出，再再出现，真实消息 DOM 和布局槽位保持稳定。
10. 需要支持 reduced motion：降级为无橡皮泥连接的轻量拖拽影子、命中高亮和淡入淡出。

参考链接：

1. CSS-Tricks Gooey Effect：`https://css-tricks.com/gooey-effect/`
2. MDN SVG `feGaussianBlur`：`https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Element/feGaussianBlur`
3. MDN CSS filter effects：`https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_filter_effects/Using_filter_effects`
4. Motion React drag：`https://motion.dev/docs/react-drag`

## 桌面端资料与当前实现约束

桌面端不能只把网页端动画搬过去。Electron 透明窗口的视觉、鼠标命中、窗口 bounds 和平台能力是四层不同问题，任何一层处理粗糙都会影响已有 compact history、蓝线拖拽、小球、模型交互和透明穿透。

外部资料结论：

1. Electron `app-region: drag` 会让矩形区域忽略普通 pointer 事件；按钮等交互区域必须显式 `app-region: no-drag`。历史拖拽不能复用蓝线拖拽区域，也不能把历史层放进 native drag region。
2. Electron `setIgnoreMouseEvents(true)` 是整窗级行为；macOS / Windows 可用 `{ forward: true }` 转发鼠标移动，但它仍不适合当多区域命中的主方案。
3. Electron `setBounds()`、`setPosition()`、`getBounds()` 在 Wayland 上有限制；不能把“每帧移动 / 扩大窗口”当作所有 Linux 桌面的可靠基础。
4. CSS `pointer-events: none` 只影响页面内命中；透明 BrowserWindow 本身仍可能挡住桌面或其他窗口，所以必须配合 native hit region / passthrough。

当前 NEKO-PC 已有实现经验：

1. `preload-chat-react.js` 当前主要接管 ReactChat 窗口折叠、展开、拖动、resize 和页面侧窗口控制调用；历史拖拽不能复用蓝线拖拽区域，也不能把 drag layer 变成 native drag region。
2. 当前分支没有 `desktop-compact-layout.js`、`nativeRects` / `hitRects` / `historyPassthroughRects` 这一套历史拖拽专用几何管线。后续若需要，应先补纯 rect union / sanitize helper 并配合同测试，而不是让 NEKO-PC 猜 DOM 结构。
3. `window-host-ipc.js` 已有 `applyWindowShape(win, rects)`、input region、`setWindowIgnoreMouseEvents`、`get-cursor-point` 和窗口 bounds helper。历史拖拽应复用这些底层能力，但不能每帧把橡皮泥曲线写进 native shape。
4. `preload-pet.js` 已有模型 / avatar range / compact zone 相关经验，桌面端 drop 命中应复用 Pet 侧 bounds / hit 结果或由 PC 回传 `desktopOverAvatar`，不要在 ReactChat 页面里猜桌面模型位置。
5. 现有 setShape / passthrough 经验的核心价值是节流、hash、debounce 和失败降级；历史拖拽也要把视觉外接 bounds、真实 hit rect 和透明穿透分开处理。

桌面实现方法：

1. NEKO 页面是唯一的视觉几何来源。它输出 `sessionId`、`phase`、`pointerClient`、`sourceFrameRect`、`dragVisualRect`、`connectionVisualRect`、`dragHitRect`、`overTarget`、`needsDesktopBounds` 和 reduced-motion 状态；NEKO-PC 只消费 rect / phase，不重算橡皮泥 path。
2. ReactChat host 把页面 client rect 转成 screen rect，再交给 NEKO-PC 桥接。PC 侧只做 rect union、边界裁剪、最小尺寸、平台能力判断和恢复快照。
3. 临时 window bounds 使用 `dragVisualRect + connectionVisualRect + 当前 ReactChat 必要可见区域` 的 union，但不能保存成 compact surface anchor，也不能改变用户窗口位置偏好。
4. hit region 使用更小的 `dragHitRect`、现有 controls 和必要 drop 接收区域；橡皮泥连接层默认只参与视觉 bounds，不参与 input region。
5. setShape 路径下，shape 更新应 hash / throttle / debounce；橡皮泥连接的每帧曲线不要每帧写入 setShape。必要时用连接的稳定外接 visual rect 扩大 window，用较少变化的 drag shadow hit rect 控制输入。
6. no-setShape 路径下，不能让临时透明大窗长期吃事件。可接受降级是：视觉限制在当前 ReactChat window 可控范围内，drop 命中通过桌面 avatar bounds / cursor point 完成，动画弱化但不遮挡模型和桌面。
7. 真正“移动到模型上”的桌面显示只有两种可靠承载：
   - 首选：临时扩大现有 ReactChat BrowserWindow 到 drag visual rect 与模型接收区域附近，动画结束后恢复。
   - 备选：后续若现有窗口扩展影响太大，再评估单独 drag overlay BrowserWindow；但这会新增窗口层级、同步和穿透复杂度，不能第一版默认采用。
8. 如果不扩大窗口也不建 overlay window，拖拽视觉无法突破 BrowserWindow 裁剪，只能在当前聊天窗口范围内显示；此时仍可用全局 cursor point + avatar bounds 做 drop 判定，但视觉到不了模型上。
9. 如果临时扩大 bounds 需要移动窗口左上角，NEKO-PC 必须回传 `rebaseDelta` 或等价字段，让 NEKO 页面修正 fixed overlay 坐标；优先采用不移动左上角、只扩右 / 下 / 必要侧边的策略，减少 overlay 跳位。
10. 桌面 drop 命中不能只依赖页面内模型 manager；ReactChat 页面必须消费桌面 avatar bounds，或由 NEKO-PC 直接回传 `desktopOverAvatar`。
11. 动画结束、取消、切换 full / minimized、关闭 history、打开 ChoicePrompt / GalGame 时，必须统一清理临时 window bounds、shape snapshot、passthrough 状态和 drag bridge state。

### 三个桌面难点的最终解法

这三个问题必须一起解，不能分开补丁式处理。正确架构是：NEKO 页面负责画和算，NEKO-PC 负责托住窗口、限定命中和恢复状态。

#### 1. 突破聊天框 / BrowserWindow 边界

网页端 `createPortal(document.body)` 只能突破历史层和紧凑聊天框 DOM 边界，不能突破 Electron BrowserWindow 裁剪。NEKO-PC 必须在 active drag 期间临时扩大 ReactChat BrowserWindow。

最终实现：

1. NEKO drag layer 每帧或节流后输出：
   - `sessionId`
   - `phase`
   - `dragVisualRect`
   - `connectionVisualRect`
   - `dragHitRect`
   - `sourceFrameRect`
   - `pointerClient`
   - `needsDesktopBounds`
2. `static/app-react-chat-window.js` 已有 compact geometry collector 的 `visualRect` / `hitRect` / `surfaceNativeRects` 思路，正式实现应复用这种结构，新增临时 `historyDragItems`，而不是直接把 drag DOM 塞进 base surface。
3. `preload-chat-react.js` 接收页面状态，转换 client rect 到 screen rect，再把 `dragVisualRect + connectionVisualRect + 当前 ReactChat 基础可见 rect` 做 union。
4. NEKO-PC 对 union 做 sanitize：
   - 最小宽高保护。
   - 限制在当前显示器 / virtual screen 可接受范围。
   - 坐标和尺寸四舍五入。
   - 与上一帧差异小于 1-2px 时不更新。
5. PC 侧优先保持 ReactChat BrowserWindow 左上角不动，只向右、下或必要侧边扩展。若必须移动 `x/y`，必须回传 `rebaseDelta`。
6. 不把临时 drag bounds 写入 compact surface anchor、窗口偏好、保存位置或最小化小球位置。
7. `setBounds()` 不得每个 pointermove 都调用。页面可以 60fps 画橡皮泥，PC bounds 更新应节流 / hash 后执行，例如 24-30fps 或 rect 变化超过阈值才更新。
8. 结束、取消、失焦、history 关闭、full/minimized 切换、ChoicePrompt/GalGame 打开、session 超时都必须恢复原 bounds。

为什么这样做：

1. Electron 官方 `setBounds()` 支持移动和调整窗口，但 Wayland 对程序化 resize / position 有限制。
2. NEKO-PC 当前 `window-control-ipc.js` 已经多处用 `setBounds()` 替代 `setPosition()` / `setSize()`，并记录了 Windows 透明窗口尺寸漂移、DWM 异步落地和自适应降频经验；历史拖拽应沿用这个经验，避免高频强刷。
3. 单独 overlay BrowserWindow 可以作为后续备选，但第一版不默认使用。它会新增窗口层级、焦点、穿透、坐标同步和销毁恢复问题。

#### 2. 透明区域不要吃事件

临时扩大窗口后，视觉区域会变大，但可交互区域不能跟着变成大矩形。尤其橡皮泥连接层不能吃事件。

最终实现：

1. NEKO 页面明确输出两类 rect：
   - `visualRect`：拖拽气泡、连接、小圆、投递高亮的完整外接区域，用于显示和临时 window bounds。
   - `hitRect`：真正需要接收 pointer 的小范围，通常只覆盖拖拽气泡 / 指针附近 / 必要控件。
2. `connectionVisualRect` 只进 visual union，不进 hit region。
3. `dragHitRect` 要明显小于 drag visual union，并可按拖拽气泡大小加 padding，例如气泡外扩 8-16px，而不是整条连接外接矩形。
4. setShape 可用时，NEKO-PC 把 `dragHitRect + 现有 compact controls hit rect + 必要 drop 接收 rect` 写入 shape / input region。
5. setShape 不可用时，不能让整窗长期 `setIgnoreMouseEvents(false)` 吃住透明区域。降级策略是：
   - 缩小或限制视觉越界范围。
   - 继续用桌面 cursor point + avatar bounds 判断 drop。
   - 必要时只在拖拽气泡附近短时保持可接收，其余透明区域 passthrough。
6. macOS / Windows 的 `{ forward: true }` 只能辅助 mouse move，不应作为主命中模型。Linux / Wayland / X11 要按当前项目已有 setShape / XShape / fallback 经验分支。
7. 临时 shape / passthrough 必须和 `sessionId` 绑定，任何结束路径都能恢复。PC 侧需要 timeout 自愈，页面丢消息也不能留下透明遮罩。

为什么这样做：

1. Electron 官方说明 `setShape(rects)` 会决定窗口可绘制和可交互区域，区域外不绘制也不接鼠标，并会穿透到后方窗口；但它是 Windows / Linux 实验能力，不能无降级依赖。
2. Electron 的 `setIgnoreMouseEvents(true)` 是整窗级穿透；即使带 `forward`，也主要解决 move 事件转发，不适合做多区域动态命中。
3. 当前 NEKO-PC `window-host-ipc.js` 已有 `applyWindowShape()`、`setWindowIgnoreMouseEvents()`、`get-cursor-point` 和 input region sanitize；历史拖拽应复用这些底层能力，而不是新写一套透明窗口命中系统。

#### 3. 坐标重基与拖拽不跳位

一旦临时扩大窗口改变了 BrowserWindow 的 `x/y`，页面内 fixed overlay 的 client 坐标就会重基。如果不处理，用户看到的就是拖拽气泡跳位、连接断开、小圆方向反。

最终实现：

1. PC 每次应用临时 bounds 后计算：
   - `oldWindowBounds`
   - `nextWindowBounds`
   - `rebaseDelta = { x: old.x - next.x, y: old.y - next.y }`
2. NEKO 页面收到 `rebaseDelta` 后，把 active drag 的 viewport 基准统一平移：
   - `pointerClient`
   - `originRect`
   - `sourceFrameRect`
   - `dragVisualRect`
   - `connectionVisualRect`
   - 弹回 / 吸附动画目标点
3. 页面所有 drag layer 坐标都只能来自同一个坐标系，不能一部分用旧 client，一部分用 screen，一部分用新 client。
4. 若 PC 侧采用“不动左上角，只扩右 / 下”的策略，`rebaseDelta` 应为 `{0,0}`；页面仍要支持非零 delta，作为多屏、左侧拖出、上方拖出或系统限制下的保险。
5. `sourceFrameRect` 必须优先使用 live message row rect；测不到时保留上一帧有效值，不能 fallback 到 `originRect` 或气泡宽度，否则长气泡会让小圆乱跳。
6. drop 命中使用同一坐标转换链路：页面 client point → host screen point → 桌面 avatar bounds，或 PC 回传 `desktopOverAvatar`。不能混用网页模型 manager 的 client bounds 和桌面 Pet window screen bounds。
7. 所有 bridge 消息带 `sessionId` 和递增 `seq`。旧 session 或旧 seq 到达时丢弃，避免异步 setBounds / IPC 回包把新拖拽拉回旧坐标。

为什么这样做：

1. 当前网页端拖拽层已经使用 viewport / client 坐标和 `createPortal(document.body)`；这是正确基础，但只在 BrowserWindow 不动时天然成立。
2. NEKO-PC 当前已有 `get-cursor-point` 返回窗口内坐标和 screen 坐标的经验；正式实现应把 screen/client 转换做成明确 helper，并用测试覆盖。
3. Windows 透明窗口和 Linux 桌面环境下 `setBounds()` 可能异步或受限，bridge 不能假设“发出去就立刻等于当前 bounds”。

### 桌面端最终实现合同

正式实现前，NEKO 和 NEKO-PC 必须先约定最小合同。字段名可按代码风格调整，但语义不能少。

NEKO 页面输出：

```ts
type CompactHistoryDesktopDragGeometry = {
  sessionId: string;
  seq: number;
  phase: 'dragging' | 'returning' | 'sending' | 'settling' | 'cancelled';
  dragType: 'image' | 'bubble';
  pointerClient: { x: number; y: number };
  sourceFrameRect: Rect;
  dragVisualRect: Rect;
  connectionVisualRect: Rect | null;
  dragHitRect: Rect;
  overTarget: boolean;
  needsDesktopBounds: boolean;
  reducedMotion?: boolean;
};
```

NEKO-PC 回传：

```ts
type CompactHistoryDesktopDragAck = {
  sessionId: string;
  seq: number;
  applied: boolean;
  desktopBoundsActive: boolean;
  desktopOverAvatar?: boolean;
  rebaseDelta?: { x: number; y: number };
  degradedReason?: 'no-set-shape' | 'wayland-bounds-limited' | 'window-hidden' | 'invalid-rect';
};
```

实现顺序：

1. NEKO：先输出 geometry，不接 PC 消费；用日志 / debug overlay 验证 rect 正确。
2. NEKO-PC：只消费 geometry 做临时 bounds，不改 hit region；验证视觉不被裁。
3. NEKO-PC：加入 hit region / passthrough，验证透明区域不吃事件。
4. NEKO：接收 `rebaseDelta`，验证左 / 右 / 上 / 下 / 斜向拖出都不跳。
5. NEKO-PC：接入桌面 avatar bounds / `desktopOverAvatar`，验证命中和实际模型位置一致。
6. NEKO：确认 drop 后仍走现有 `onCompactHistoryDrop` / `app-buttons.js` 发送链路，PC 不构建 payload。
7. 双端：补 session timeout、cancel、restore、full/minimized/ChoicePrompt/GalGame 打断恢复。

验收底线：

1. 桌面端能显示越出 ReactChat 原始边界的拖拽气泡和连接。
2. 拖拽连接视觉不被 PC 端重算，不出现网页端和桌面端两套样式。
3. 临时扩大窗口后，连接层不吃事件，模型和桌面不被透明大矩形长期遮挡。
4. bounds 变化不导致拖拽气泡、连接、小圆跳位或断开。
5. 松手命中模型只发送一次；未命中只弹回，不污染 pending attachments。
6. 任何异常路径都恢复 bounds、shape、passthrough 和 active session。

桌面端禁止：

1. 禁止让橡皮泥连接层本身变成大面积 hit rect。
2. 禁止把 drag visual union 保存到 compact surface position。
3. 禁止为追求动画连续而每帧 setBounds / setShape，除非真实运行验证证明该平台稳定且有节流。
4. 禁止让 ReactChat 临时透明大窗在动画结束后继续覆盖模型或桌面。
5. 禁止让历史拖拽复用蓝线 `data-compact-drag-handle` 或 `app-region: drag`。
6. 禁止 NEKO-PC 根据 CSS class 猜消息内容、气泡宽度或连接曲线；这些必须由 NEKO 页面输出 geometry / payload。
7. 禁止在未验证的情况下新增长期常驻透明 overlay window 来承载拖拽动画。

桌面端参考链接：

1. Electron BrowserWindow：`https://www.electronjs.org/docs/latest/api/browser-window`
2. Electron Custom Window Interactions：`https://www.electronjs.org/docs/latest/tutorial/custom-window-interactions`
3. MDN CSS pointer-events：`https://developer.mozilla.org/en-US/docs/Web/CSS/pointer-events`
4. Electron `BrowserWindow.setShape()`：`https://www.electronjs.org/docs/latest/api/browser-window#winsetshaperects`

## 阶段 4：NEKO-PC 拖拽基础

NEKO 全部完成并验证后，再开始 NEKO-PC。NEKO-PC 第一阶段只做桌面拖拽基础桥接，不做发送业务和最终动效。

NEKO-PC 只负责桌面壳必须知道的窗口与 hit 信息。

注意：这里的“NEKO 全部完成”指网页端 source / payload / 发送入口 / overlay 合同已经稳定，不代表桌面端拖到模型已经可验收。只要要求视觉或 pointer 真正离开 ReactChat BrowserWindow，NEKO-PC 的临时 bounds、drop result、桌面 avatar bounds 和必要的 cursor 辅助就是前置；不能等到最后动画阶段才发现 renderer 收不到 pointerup 或命中源不成立。

建议桥接内容：

1. 先在 NEKO-PC 当前真实文件上补最小桥接，而不是寻找不存在的历史拖拽布局模块：
   - `preload-chat-react.js` 负责页面侧接收 / 转发 drag geometry 和 bounds rebase。
   - `window-host-ipc.js` 或现有 window control IPC 负责临时 bounds / shape / passthrough 应用与恢复。
   - 如需新增纯几何 helper，只做 rect union、screen/client 转换、节流和 sanitize，并同步新增合同测试。
2. NEKO 页面输出 compact history drag state：
   - 当前是否 active drag。
   - drag 类型。
   - drag shadow rect。
   - drag connection visual rect。
   - drag hit rects。
   - drop target hover 状态。
   - 当前 overlay 坐标是否已应用 `rebaseDelta`。
   - 是否需要临时扩大 ReactChat bounds。
3. NEKO-PC `preload-chat-react.js` 消费该状态：
   - 临时把 drag shadow / connection visual rect 纳入 ReactChat window bounds。
   - 只把 drag hit rects / 必要 drop 区域纳入平台可用的 setShape、input region 或既有 pointer passthrough 策略。
   - 如果窗口左上角变化，向 NEKO 页面回传 `rebaseDelta` 或保持左上角不变。
   - 动画结束后恢复到页面 compact geometry 真实范围。
4. NEKO-PC `preload-pet.js` / `main.js` 继续提供当前 avatar bounds / hit：
   - 只返回命中结果或当前 bounds。
   - 必要时回传 `desktopOverAvatar`，避免 ReactChat 页面用网页模型 manager 误判桌面命中。
   - 不转换 message payload。
   - 不决定发送文本。
5. 跨窗口 pointerup / cancel / drop result 必须能回到 NEKO 页面 / React host，由 NEKO 执行 payload 转换和现有发送链路。
6. 如果 pointer 离开 ReactChat BrowserWindow 后 renderer 无法继续收到 `pointermove` / `pointerup`，第一选择是用临时 bounds 保持关键事件仍进入 renderer；全局 cursor point 或 drop result 只能作为补充回传，不能成为唯一的 pointerup 方案。

事件命名需要在代码实施前按当前 IPC 风格和 `src/ipc-channels.js` 最终确认；新增通道必须集中登记，不能在 preload / main 里散写字符串。可选方向：

1. `neko:compact-history-drag-state-change`
2. `neko:compact-history-drag-geometry-change`
3. `neko:compact-history-drop-result`

这些名字只是实施候选；不能在未对齐现有事件风格前直接当作既定协议。

### 阶段 4 当前收口

当前分支已完成 NEKO-PC 拖拽基础桥接的第一轮收口，范围仍限定在“桌面窗口承载 / hit / rebase / restore”，不包含阶段 5 的发送投递桥接，也不在 NEKO-PC 里解析消息 block 或构建发送 payload。

已落地的实际合同：

1. NEKO 页面继续由 `CompactExportHistoryPanel.tsx` 输出 `neko:compact-history-drag-state-change`，payload 包含 `sessionId`、`phase`、`dragType`、`dragVisualRect`、`connectionVisualRect`、`dragHitRect`、`overTarget`、`needsDesktopBounds` 和 `timestamp`。
2. NEKO-PC `src/preload-chat-react.js` 只在 `needsDesktopBounds === true`、compact 模式、非 minimized、ReactChat 未隐藏时消费该状态。
3. NEKO-PC active drag 期间临时把 drag visual / connection visual 纳入窗口承载 bounds，把更小的 drag hit rect 纳入 hit region；connection visual 不作为 hit rect。
4. NEKO-PC active drag 期间使用 session 级 carrier bounds 预热承载区域，避免拖出左 / 上边界时才首次扩大窗口造成首帧跳位。
5. 若临时 bounds 改变 ReactChat BrowserWindow 左上角，NEKO-PC 在 `setBounds()` 前向页面派发 `neko:compact-history-drag-rebase`，由 NEKO 页面把 drag shadow、connection、origin/source frame、pointer intent 和弹性曲线控制点整体平移到同一 client 坐标系。
6. NEKO 页面收到 rebase 后必须同时更新 ref、DOM style 和 React `activeDrag` state；否则 rerender 会把拖拽层短暂写回旧坐标，引发“刚拖出去抽一下”。
7. rebase 之后的同步 rerender 不再重复向桌面端发同一帧 bridge state，避免只因 seq 变化触发无意义 relayout。
8. drag 结束、取消、模式切换、layout cleanup 或 stale timeout 时，NEKO-PC 会清理临时 drag state、carrier bounds、passthrough 和 pending window bounds，并重新 relayout 恢复 compact geometry。

对既有功能的影响边界：

1. compact surface anchor 仍只来自 `capsule` / `input` 等 base surface；history drag shadow 和 connection 只在 active drag 期间参与临时 desktop bounds，不参与聊天框本体锚点。
2. 蓝线拖拽、surface resize、最小化小球、full 模式和 minimized 模式不读取 history drag carrier bounds。
3. setShape 可用时使用 page hit rect 收敛 native region；no-setShape 平台 active drag 期间会临时禁用 history passthrough 以保证 pointer 链路不断，异常时依赖 stale clear 恢复，仍需真实运行观察是否会短时遮挡桌面 / 模型。
4. NEKO-PC 的 debug 日志仍保留在 `src/preload-chat-react.js`，用于继续核对 bounds / rebase 链路；后续若真实运行稳定，应在提交前或阶段 6 收口时降噪。

本轮已验证：

1. NEKO：`npm.cmd --prefix frontend/react-neko-chat run typecheck`
2. NEKO：`npm.cmd --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact history drag"`
3. NEKO：`npm.cmd --prefix frontend/react-neko-chat run build`
4. NEKO：`git diff --check`
5. NEKO-PC：`node --check src/preload-chat-react.js`
6. NEKO-PC：`node --check src/preload-pet.js`
7. NEKO-PC：`node --check src/main/window-host-ipc.js`
8. NEKO-PC：`node --check src/desktop-compact-layout.js`
9. NEKO-PC：`npm.cmd run lint`，当前脚本为占位输出 `No linting configured`
10. NEKO-PC：`git diff --check`

尚未由本轮自动化完全证明：

1. 真实桌面运行时左 / 上 / 右 / 下四个方向离窗拖拽均无首帧抽搐。
2. no-setShape 平台的大 bounds 透明区域不会在 active drag 以外吃事件。
3. 阶段 5 的 desktop avatar hit / drop result / 发送投递桥接尚未实施。

## 阶段 5：NEKO-PC 发送投递桥接

NEKO-PC 拖拽基础稳定后，再接桌面端发送投递桥接。

规则：

1. NEKO-PC 只把当前 avatar bounds / hit / drop result 桥回 NEKO 页面。
2. 发送内容转换仍在 NEKO / React host 执行。
3. 图片拖到当前猫娘后，NEKO-PC 不能直接写后端协议，只能让 NEKO 复用图片 / 附件发送链路。
4. 整条气泡拖到当前猫娘后，NEKO-PC 不能构建文本，只能把 drop 命中和必要 payload 标识交回 NEKO。
5. 当前不做跨窗口 / 跨应用拖出图片到外部保存；NEKO-PC 不创建保存用临时文件，也不向外部应用暴露图片文件路径。

## 阶段 6：NEKO-PC 拖拽效果与 bounds 恢复

桌面端拖拽和发送都跑通后，最后补 NEKO-PC 拖拽效果、临时 bounds 和恢复。

规则：

1. ReactChat 透明窗口 bounds 只在明确 `bubbleDrag` 或必要 drag shadow 阶段临时扩大。
2. 临时 bounds 使用 visual rect；native hit 使用更小的 hit rect，不能把橡皮泥连接外接矩形直接当命中区域。
3. 拖拽完成、弹回或发送动画结束后，恢复到页面 compact geometry 真实范围。
4. 拖拽影子和猫娘 drop target 需要进入临时 hit / drag-over 判定，但不能扩大长期透明遮挡面。
5. setShape / setBounds 更新必须 hash / throttle / debounce，不能跟随橡皮泥曲线逐帧高频写入 native region。
6. 最终动画表现应沿用 NEKO 已完成的网页端时序，桌面端只补窗口裁切、hit region 和跨窗口同步。
7. 如果 bounds 扩展产生 `rebaseDelta`，拖拽影子、连接层和原位锚点必须在同一坐标系内连续，不能出现跳位。
8. no-setShape 平台按降级策略处理：优先保证不遮挡和不误吃事件，其次才追求完全越界视觉。
9. 动画结束必须清理桌面临时 bounds、临时 hit region、passthrough 状态和 drag bridge state。

## 未来观察项：外部图片拖出

旧实施计划曾把“图片 / 表情包拖到外部保存”列入后续阶段，但当前不实施。

暂停原因：

1. 外部拖出很容易把“拖到猫娘发送”“拖动查看”“调整交互焦点”等本来不是保存的操作误变成系统保存。
2. Electron / 浏览器标准拖拽通常需要面向外部应用填充 `DataTransfer` 或临时文件，这会引入路径、权限、生命周期和隐私风险。
3. 当前真正要完成的是“发送给当前猫娘角色”，外部保存不是核心闭环。

未来重新评估条件：

1. 用户明确要求图片 / 表情包可拖到系统或外部应用保存。
2. 已经证明内部拖到猫娘、整条气泡拖到猫娘、取消恢复和桌面 bounds 都稳定。
3. 能清楚区分外部保存意图和内部投递意图，不造成误保存。
4. 能保证不暴露内部路径、token、用户名、缓存路径或不可公开 URL。

## 影响面与风险清单

这些风险需要在实施前和每个阶段验收时逐项对照。

1. 历史选择被拖拽误触发。当前气泡 click / selection 依赖 pointer move 阈值，新增拖拽后必须保证 `scroll`、`imageDrag`、`bubbleDrag`、`cancelled` 状态在 pointer up 时不会补触发选择。
2. 历史滚动被识别成拖拽。紧凑历史层是滚动容器，滚轮、触摸板滚动、滚动条拖动都必须优先进入 scroll / cancelled；但不能简单用“纵向位移更大”取消拖拽，因为用户也可能把气泡向下拖到模型。正式判断要结合起手区域、pointer type、速度、距离和 scrollTop 是否真实变化。
3. 图片 block 起手被现有选择忽略逻辑吞掉。`.message-block-image` 现在会被 `isSelectionIgnoredTarget()` 排除，后续要拆成“点击不选择”和“拖动可拿起图片”，否则图片拖拽起不来。
4. 链接、按钮、复制 / 下载 / preview 控件被气泡拖拽抢走。所有 interactive target 要继续优先，不能因为 wrapper 加 pointer handler 就破坏现有按钮和链接。
5. 全局 `<img draggable="false">` 与 `dragstart.preventDefault()` 会阻止原生图片拖出，这是当前想要的；内部图片拖拽必须完全走 pointer state 和 overlay，不能偷偷依赖浏览器原生 DnD。
6. overlay 可能盖住输入框、工具扇、preview、ChoicePrompt 或 GalGame。drag overlay 应默认 `pointer-events: none`，并在 ChoicePrompt / GalGame 打开或 compact 模式切换时统一取消拖拽。
7. z-index 争夺。拖拽影子需要高于历史层和紧凑框，但不能长期高于系统级弹窗、ChoicePrompt、GalGame 或错误提示；结束后必须卸载或隐藏。
8. 自动贴底和拖拽恢复抢 `scrollTop`。拖拽期间要暂停或冻结相关自动滚动，动画结束后再按原 `compactExportAutoScrollToBottom` 语义恢复。
9. 消息列表更新导致 originRect 失效。拖拽过程中若新消息、流式更新、历史窗口关闭或 message id 不存在，必须取消并恢复，不用旧 DOM rect 继续投递。
10. 发送重复。pointer up、drop bridge、动画 completion 和桌面 IPC 都可能重复触发；必须有一次性 drop token / requestId / consumed flag，确保只发送一次。
11. 附件残留。图片投递如果先写入 pending attachment 源头再发送，失败、取消、中断、发送异常都要清理临时附件，不能污染用户下一次手动发送；只清理 React host `composerAttachments` 不够，还要清理 `app-buttons.js` 里真实 pending DOM / buffer。
12. 附件 URL 生命周期。历史图片可能是 blob / data / remote URL / 缓存 URL，实施前必须确认现有附件链路能消费；不能把内部路径、token 或不可公开 URL 传给 drag payload。
13. assistant 气泡重发语义不清。把猫娘回复拖回猫娘时，文本应作为用户输入或引用式文本，不能以 assistant role 直接塞回后端历史。
14. 后端成功与视觉成功不同步。只有前端投递进入现有 send 链路并得到可确认状态后，才播放“送达”确认；后端失败或附件规范化失败时应走弹回 / 失败恢复。
15. optimistic user message / dedup 被破坏。图片投递要经过 `sendTextPayloadInternal()` 的既有 optimistic 消息和 dedup 路径，不能另造一条只在 React 里出现的假消息。
16. 桌面临时扩大窗口遮挡模型或桌面。visual rect 只用于拖拽阶段，hit rect 要更小；结束、取消、模式切换、窗口失焦都要恢复 bounds、shape 和 passthrough。
17. setBounds / setShape 高频抖动。橡皮泥连接每帧变形不能直接驱动 native window region；桌面端只消费节流后的外接 visual rect 和稳定 hit rect。
18. bounds 重基导致视觉跳位。ReactChat BrowserWindow 如果因 visual union 改变 `x/y`，fixed overlay 的 client 坐标会重基；必须使用 `rebaseDelta` 或避免移动左上角。
19. 桌面 avatar bounds 不一致。网页端模型 manager、桌面 Pet 窗口和 ReactChat BrowserWindow 可能不在同一坐标系；桌面 drop 命中必须使用统一 screen/client 转换，不能混用。
20. Wayland / 平台能力差异。Wayland 可能拿不到真实全局窗口坐标或无法可靠调整 bounds；必须允许视觉不越界但 drop 判定仍可用的降级。
21. no-setShape 透明空白吃事件。扩大 ReactChat BrowserWindow 后，如果没有可靠 shape 或 passthrough，透明区域会挡住猫娘、桌面和其他 NEKO-PC 操作；降级时优先缩小视觉范围。
22. reduced motion 与性能。gooey / blur / filter 只作用临时 overlay，小屏、低性能和 `prefers-reduced-motion` 下要降级为普通 shadow + 简短 transform。
23. 内存泄漏与监听残留。pointer capture、window pointermove / pointerup、animation frame、IPC bridge、geometry observer 都必须在 finish / cancel / unmount 时释放。
24. 测试只覆盖 React 本地状态不够。图片投递必须测试 pending attachment 源头、host `composerAttachments` 同步、`sendTextPayloadInternal()`、后端 attachments 归一化和 OpenClaw / task executor 输入。
25. 桌面离窗事件丢失。网页端 fixed / portal overlay 在 BrowserWindow 内有效，但 pointer 离开窗口后可能无法收到最终 pointerup；正式桌面实现必须通过 NEKO-PC 验证离窗拖动、取消和 drop result。
26. 桌面 bridge 状态分叉。NEKO 页面、`preload-chat-react.js` 和 `main.js` 若各自维护 active drag 状态，容易出现页面已结束但窗口仍扩大的残留；必须以 session id / phase / timeout 统一收敛。
27. 源头小圆回退污染。小圆水平锚点必须来自消息行固定侧边窄范围和最后有效 `sourceFrameRect`，不能在 DOM 暂时测不到时退回按气泡内容宽度计算，否则长消息会再次导致源点乱跳、左右反转或连接消失。

保护策略：

1. 每个阶段都保留 feature flag 或内部开关，先让 NEKO 网页端跑通，再打开 NEKO-PC 桥接。
2. `drop` 处理函数必须是幂等的：同一个 drag session 只能从 `dragging` 进入一次 `sending` 或 `returning`。
3. 视觉层和业务层分开验收：没有发送成功时不能因为动画结束而假装成功，发送成功后也不能真实删除历史消息。
4. 新增桌面 bridge state 必须有超时自愈，例如 active drag 超过合理时长或页面失联时，NEKO-PC 自动恢复窗口 bounds / shape / passthrough。
5. 首版桌面动效宁可降低越界范围，也不能牺牲现有紧凑框输入、历史选择、蓝线拖拽、小球、ChoicePrompt 和 GalGame。

## 几何与命中要求

1. 历史层仍使用 `data-compact-geometry-hit-scope="children"` 和 `data-compact-hit-region="true"` 的 composite 思路。
2. 气泡、图片缩略图、controls、preview 控件和必要滚动命中可以吃事件。
3. 气泡之间、气泡左右留白、历史层外透明区域不应成为长期 native hit region。
4. 拖拽影子和橡皮泥连接都是临时 visual item；只在 active drag 时参与临时 bounds，不参与 base surface anchor。
5. drag hit rect 必须比 drag visual rect 更小；连接层默认不作为 hit rect。
6. `capsule` / `input` 仍是 surface anchor；history、preview、drag shadow、drag connection 都不能改变聊天框本体默认锚点。
7. 蓝线拖拽 `data-compact-drag-handle="true"` 不受历史拖拽影响。
8. 最小化小球不读取 surface drag、history drag 或 temporary drag bounds。
9. ChoicePrompt / GalGame 打开时优先级高于历史拖拽；必要时取消历史拖拽。

## 测试计划

### NEKO 单元 / 组件测试

优先补在 `frontend/react-neko-chat/src/App.test.tsx` 或相关 panel 测试中：

1. 短点击气泡切换选择。
2. 明显滚动不切换选择。
3. pointer 被判定为 `imageDrag` 后不切换选择。
4. pointer 被判定为 `bubbleDrag` 后不切换选择。
5. 拖拽失败后 `compactExportSelectedIds` 不变。
6. 已选中气泡也可拖动，拖动不取消选中。
7. 图片 block 起手优先进入 image drag，不进入 bubble drag。
8. 链接、buttonGroup、preview 控件、controls 起手不触发 bubble drag。
9. 图片内部 drag payload 不包含内部路径、token 或不可公开 URL。
10. 图片 drag 不依赖浏览器原生 `dragstart`，全局 `<img draggable="false">` 仍保持有效。
11. 气泡 payload 转换使用现有 composer / attachment payload 形状。
12. 附件投递测试要覆盖 host `composerAttachments` 与 submit callback 的真实衔接，不能只断言 React 本地状态。
13. `compactExportPreviewOpen` 展开时不被拖拽选择逻辑污染。
14. 非 compact 模式下不暴露 active history drag DOM / state。
15. 网页端 drag overlay 使用 viewport 坐标突破 history scroll 容器边界，但不改变 history layout 和 scrollTop。
16. 图片投递测试要验证历史图片进入 `app-buttons.js` pending attachment 源头，并由同步函数反映到 React host 附件状态。
17. 重复 pointerup / drop result / animation completion 不会造成重复发送。
18. 桌面 avatar bounds 或 `desktopOverAvatar` 回传存在时，drop 命中使用桌面结果；不存在时才使用网页端 helper。
19. 收到 `rebaseDelta` 后，drag shadow / connection / origin anchor 坐标保持连续。

### NEKO 运行时验证

1. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact"`
2. `npm --prefix frontend/react-neko-chat run typecheck`
3. `bash build_frontend.sh`
4. `node --check static/app-react-chat-window.js`
5. 使用浏览器或 Playwright 验证：
   - 打开 compact history。
   - 拖图片到猫娘。
   - 拖整条气泡到猫娘。
   - 拖拽影子和连接层能突破历史层 / 紧凑聊天框 DOM 边界。
   - 拖拽失败弹回。
   - 滚动历史不误选。
   - 选择 / 预览 / 导出仍正常。
6. 后端链路验证：
   - 文本投递进入普通用户消息。
   - 图片投递进入 pending attachment 源头，并在后端消息中形成 attachments。
   - 附件失败时前端不显示成功投递。

### NEKO-PC 验证

修改 NEKO-PC 后，在 `/Users/tonnodoubt/N.E.K.O.-PC` 当前分支单独验证：

1. 检查项目当前 `package.json` 或脚本后运行对应 lint / test / typecheck。
2. 至少做语法检查或等价构建检查：
   - `node --check src/preload-chat-react.js`
   - `node --check src/preload-pet.js`
   - `node --check src/main/window-host-ipc.js`
   - 若新增 IPC 通道，检查 `src/ipc-channels.js`。
3. 如果新增纯几何 helper，新增并运行对应 contract test；当前分支没有 `desktop-compact-layout-contract.test.js`，不要把它当成现成测试入口。
4. 桌面真实运行验证：
   - compact history 可滚动、可选择。
   - 图片拖到当前猫娘可发送。
   - 整条气泡拖到当前猫娘可发送。
   - 拖拽视觉可以在支持的桌面路径上突破 ReactChat 原始边界移动到模型附近。
   - 不支持临时 bounds 的平台按文档降级，drop 判定仍可用但视觉不越界。
   - bounds 扩展若移动窗口左上角，`rebaseDelta` 生效，拖拽影子不跳位。
   - `desktopOverAvatar` 或桌面 avatar bounds 回传后，模型命中与实际桌面位置一致。
   - 未命中目标弹回。
   - 临时扩大 bounds 后恢复。
   - setShape 可用时 hit rect 小于 visual rect，connection 不吃事件。
   - no-setShape 降级时透明空白不长期遮挡后方。
   - 最小化小球、蓝线拖拽、full 模式、minimized 模式不受影响。

## 分支与提交边界

1. NEKO 修改只在 `/Users/tonnodoubt/N.E.K.O` 当前分支进行。
2. NEKO-PC 修改只在 `/Users/tonnodoubt/N.E.K.O.-PC` 当前分支进行。
3. 两个项目分别检查、分别提交，不把一个项目的修改混到另一个项目提交里。
4. `.agent/notes` 文档用于任务交接和实施参考，默认不进入普通功能提交，除非用户明确要求。
5. 每次实施前先看对应项目 `git status --short`，保留用户已有修改。

## 禁止事项清单

1. 禁止改旧 `#chat-container` 当作当前紧凑聊天框主实现。
2. 禁止绕过 `MessageBlockView` 重写一套 history block 渲染。
3. 禁止绕过现有 composer / attachment / send 链路直接新增后端协议。
4. 禁止拖拽失败后改变选择集合、preview 选择状态或聊天消息。
5. 禁止真实删除历史消息来模拟“拿走气泡”。
6. 禁止用 `display: none`、高度折叠或列表重排做原位抽走动画。
7. 禁止拖拽完成后让气泡永久停留在猫娘附近。
8. 禁止在当前阶段实现图片 / 表情包拖出到外部应用或系统保存链路。
9. 禁止让任何 drag data 暴露内部本地路径、token、用户名、缓存路径或不可公开 URL。
10. 禁止让桌面端临时扩大 ReactChat bounds 后不恢复。
11. 禁止让 history drag shadow 参与 compact surface base anchor。
12. 禁止让透明大矩形长期吃事件。
13. 禁止在 NEKO-PC 复制消息 payload 转换、导出构建或发送业务。
14. 禁止让历史滚动误触发蓝线拖拽。
15. 禁止让 ChoicePrompt / GalGame、历史拖拽和猫娘 drop target 同时争夺焦点。
