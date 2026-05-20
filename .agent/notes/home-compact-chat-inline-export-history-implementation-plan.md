# 首页紧凑态内联导出历史层实施方案

> 本文只承载 `home-compact-chat-inline-export-history-design.md` 的实际实施步骤、修改顺序、检查项和回归方式。
> 功能目标、状态规则、交互约束、geometry 合同、桌面端要求、禁止方案和参考方案仍以 `home-compact-chat-inline-export-history-design.md` 为准。
> 若本文与设计文档、当前代码、测试或真实运行结果冲突，以设计文档、当前代码和可复现证据为准。

## 实施总原则

1. NEKO 负责 React UI、消息状态、导出能力和网页端 geometry。
2. NEKO-PC 只是桌面前端外壳，只消费 NEKO 页面给出的 geometry、bounds 和 hit region，不复制消息选择、导出格式化、Markdown / Image 构建或发送语义。
3. 先让网页端前端结构、交互状态、滚动、选择、层级和 geometry 合同基本确定，再完善导出能力；不能先为了导出能力或桌面壳补偿一个还没稳定的页面结构。
4. 第一阶段只做内联历史展示、选择、inline 导出预览和导出能力复用；图片、表情包、整条气泡拖拽全部留到后续阶段。
5. 所有位置计算只看紧凑聊天框本体 `capsule` / `input`；history、preview、choice、toolFan 都是 extra island，只扩展可见范围和命中范围，不参与聊天框保存位置和基础 anchor。
6. 每一步做完必须检查对应入口，确认没有污染 full 导出窗口、普通聊天、紧凑输入、GalGame / ChoicePrompt、最小化小球和桌面端拖动链路。
7. 本文中的“后端完善”不是新增 Python 后端接口；第一阶段只完善 NEKO 现有前端宿主和 `static/app-chat-export.js` 的无窗口导出能力。
8. 网页端通过只算前置基线，不算最终达标；本功能的主要风险在 NEKO-PC 透明窗口、bounds、setShape/native hit、保存位置、模型遮挡和跨窗口层级。
9. 每个会影响 geometry、层级、滚动、pointer 命中或尺寸的前端步骤，都必须同步写清楚它在桌面端的消费方式；不能把“网页端看起来正常”当作桌面端自然成立。
10. History / preview 的宽高必须从紧凑聊天框本体尺寸变量派生，不能写死为当前胶囊宽度；后续紧凑聊天框支持用户拉长 / 拉宽时，上方历史对话必须随聊天框等比例放大缩小，并继续受 viewport / workArea 上限保护。
11. 不实现拖拽整个历史区域上下移动。历史区域只提供内部滚动、滚动条和气泡选择；后续拖拽只允许作用在图片 / 表情包 / 整条气泡内容上。
12. History 内部气泡、滚动区和预览区也必须随容器比例缩放；不要只让外层 history 变宽，内部气泡仍被固定 px 上限锁住。

## 改造前代码事实

这些事实来自本文制定时的 NEKO / NEKO-PC 代码。实施前如果代码已经变化，先重新核对，不要按本文旧事实硬套。

1. React 聊天 UI 位于 `frontend/react-neko-chat/`，构建产物是 `neko-chat-window.iife.js`。
2. 改造前紧凑态导出按钮在 `frontend/react-neko-chat/src/App.tsx` 的 `.compact-input-tool-item-export`，点击仍走 `onExportConversationClick`；阶段 2 后应改为 compact 本地 toggle，不再复用旧导出入口。
3. `static/app-react-chat-window.js` 的 `handleExportConversationClick()` 当前只调用 `window.appChatExport.open()`，这会打开原 full 导出预览窗口。
4. `static/app-chat-export.js` 当前只公开：
   - `window.appChatExport.open`
   - `window.appChatExport.close`
   具体 entries、format、build、copy、download 能力仍在模块内部。
5. 当前 `MessageList.tsx` 不适合直接作为 inline history：
   - 有 `MAX_DISPLAY_MESSAGES = 50` 裁剪。
   - 有自己的自动贴底和 `ResizeObserver`。
   - 这些行为会破坏完整历史导出和独立滚动锚定。
6. 当前 `MessageBubble.tsx` 内部的 `MessageBlockView` 没有导出。inline history 如果要显示 text / image / link / status / buttonGroup，必须先抽共享渲染能力，不能复制一套 block 解析。
7. 当前 `ChatMessage.status` 只有：
   - `sending`
   - `sent`
   - `failed`
   - `streaming`
   不能在第一版实现里硬写 `pending` / `retrying`。
8. 当前 compact geometry 在 `static/app-react-chat-window.js` 中扫描 `[data-compact-geometry-owner="surface"]`。
9. 当前 geometry 对普通 item 使用元素 rect 同时作为 `nativeRect` 和 `hitRect`；`toolFan` 已有子命中特判，`choice` 有打开态过滤和桌面端位置校正。
10. 当前 body portal 结构里，geometry collector 对不在 React root 内的 `compact-input-tool-fan` 和 `compact-chat-choice-anchor` 做了例外；history 如果也使用 body portal，必须显式纳入 collector 允许范围。
11. 当前 NEKO-PC `src/preload-chat-react.js` 会把页面 `surfaceItems` 转成 screen rect。
12. 当前 NEKO-PC 只把 `capsule` / `input` 作为 surface anchor，`dragHandle` 属于 base surface kind 但不保存为 anchor，其他 item 只扩展 bounds / hit region。
13. 当前 NEKO-PC 对 `choice` 有空间不足时上/下切换的特殊处理；history / preview 不能复用这套重新定位逻辑，否则会再次引入聊天框跳动和选项位置异常。
14. 当前 i18n 文件位于 `static/locales/{en,es,ja,ko,pt,ru,zh-CN,zh-TW}.json`，新增用户可见文案必须 8 个 locale 同步。
15. 当前设计文档明确“不新增后端接口”；如果后续确实需要 Python 后端或协议改动，必须先另写设计并确认，不混入本实施方案第一阶段。

## 修改顺序

按下面顺序实施。除非当前代码事实变化，否则不要跳步。

1. 前端渲染合同和消息 block 共享。
2. React compact inline history 状态与基础 UI。
3. History UI、滚动锚定、气泡选择和 inline preview 前端壳。
4. 网页端 geometry 精确命中，并完成桌面端风险预检查。
5. NEKO-PC 桌面壳消费新增 geometry。
6. 前端行为测试、桌面端契约测试和 i18n key 初步补齐。
7. 导出能力无窗口化和宿主能力完善。
8. Inline preview 接入真实导出能力。
9. 构建、真实运行验证和收口。
10. 后续阶段再做历史拖拽。

## 第 0 步：实施前基线确认

目标：

1. 确认当前工作区差异，保护用户已有修改。
2. 确认当前代码仍符合“当前代码事实”。
3. 确认本轮只改 inline history 相关文件，不扩展到无关聊天链路。

必须检查：

1. `git status --short`
2. `rg -n "onExportConversationClick|compact-input-tool-item-export|handleExportConversationClick|appChatExport" frontend/react-neko-chat/src static/app-react-chat-window.js static/app-chat-export.js`
3. `rg -n "MAX_DISPLAY_MESSAGES|MessageBlockView|status: z.enum|data-compact-geometry-owner|collectCompactSurfaceGeometryItems" frontend/react-neko-chat/src static/app-react-chat-window.js`
4. `rg -n "surfaceItems|isDesktopCompactSurfaceAnchorKind|item.kind === 'choice'|hitRects|setShape" /Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js`

完成标准：

1. 已确认 full 导出窗口、compact 导出按钮、消息渲染、geometry、NEKO-PC 入口各自的真实位置。
2. 如果事实与本文不一致，先更新本文或重新制定方案，不直接编码。

## 第 1 步：抽共享消息渲染能力

修改范围：

1. `frontend/react-neko-chat/src/MessageBubble.tsx`
2. 可新增 `MessageBlockView.tsx` 或等价共享组件。
3. 必要时更新 `MessageList.tsx` 引用。

目标：

1. 把当前 `MessageBubble.tsx` 内部的 block 渲染能力抽成共享组件或共享函数。
2. Full message list 与 compact history 都使用同一套 text / image / link / status / buttonGroup 语义。
3. 不直接复用 `MessageList.tsx` 作为 history 列表，避免继承 50 条裁剪和现有贴底逻辑。
4. 不在 history 里用字符串拼接或只取 text block 的方式假装渲染消息。

检查：

1. `MessageList` 现有测试通过。
2. 普通 full 聊天气泡样式和按钮动作不变。
3. 新共享 block renderer 能覆盖当前 `MessageBlock` 全部类型。
4. `buttonGroup` 和 `message.actions` 仍能调用原 `onMessageAction`，不会被 history 气泡点击选择误触发。

## 第 2 步：实现 React compact inline history 基础状态

修改范围：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/message-schema.ts` 仅在需要新增 prop 时修改。
3. `frontend/react-neko-chat/src/App.test.tsx`

目标：

1. 新增 compact 内部状态：
   - `compactExportHistoryOpen`
   - `compactExportPreviewOpen`
   - `compactExportSelectedIds`
   - `compactExportAutoScrollToBottom`
   阶段 2 只新增已经有入口或派生值消费的状态。
   - `compactExportPointerIntent` 等 pointer 临时状态留到第 3 步滚动 / 点击 / 拖拽识别真实接入时添加。
   - compact inline export options 留到 inline preview 或真实导出能力接入时添加。
2. 修改紧凑工具轮盘导出按钮：
   - `chatSurfaceMode === 'compact'` 时 toggle inline history。
   - 非 compact 时继续调用 `onExportConversationClick`，保持 full 导出窗口行为。
3. 导出按钮变为 compact 开关后，必须参照现有 GalGame / 翻译按钮补齐开关态：
   - `is-active`
   - `aria-pressed`
   - `data-compact-tool-active`
4. 点击导出按钮时仍遵守现有 `compactFanCloseOnAction` 收起工具轮盘。
5. Compact 无消息时点击“历史对话”仍打开 inline history，并在 history 内显示空状态提示；不调用旧导出入口，不弹旧导出提示。Full 导出入口的空对话提示仍由原导出模块处理。
6. 切换到 full / minimized、组件卸载时关闭 history 和 preview，并恢复导出按钮未选中态；消息清空时如果 history 已打开，保持 history 打开并切换为空状态，同时清空选择和关闭 preview。
7. History 打开期间不改变 `compactChatState`，不把 input 撑高，不关闭下方输入。
8. 暴露到 DOM 的 `data-compact-export-*` 必须按 `chatSurfaceMode` gating；非 compact 下不能残留 selected count、auto scroll 或 open state。
9. `pointer intent` 和 compact inline export options 只在后续 UI / preview 真实消费时添加；阶段 2 不要用未消费状态假装完成。

检查：

1. Compact 下导出按钮第一次点击打开 history，第二次点击关闭。
2. Full 下导出按钮仍打开原导出窗口。
3. Tool fan 点击导出后会收起，不残留不可点击扇形区域。
4. History 打开时，紧凑输入框仍可进入 input、输入、发送。
5. History 打开/关闭不影响最小化小球状态。
6. 切换 full / minimized 后 history 和 preview 都关闭。
7. “历史对话”按钮在 open / close / 无消息 / 消息清空 / 离开 compact 后的 `is-active`、`aria-pressed`、`data-compact-tool-active` 与实际 history 状态一致。
8. History 打开/关闭不触发 `onCompactChatStateChange`，`data-compact-chat-state` 保持原值。
9. 非 compact 表面上的 `data-compact-export-selected-count` 为 `0`，`data-compact-export-auto-scroll` 为 `false`。

## 第 3 步：实现 history UI、滚动锚定和气泡选择

修改范围：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/styles.css`
3. 必要时新增局部组件文件，例如 `CompactExportHistoryPanel.tsx`。

结构要求：

1. History panel 是 compact surface extra island，建议使用 body portal 或与 compact choice / toolFan 等价的独立层。
2. 面板位于紧凑聊天框本体上方。
3. 背景透明。
4. 内部包含：
   - 历史消息滚动区。
   - 历史选择操作区。
   - inline 导出预览区。
   - 轻量选择计数 / 空选择提示。
5. 历史选择操作区只显示：
   - 全选。
   - 取消。
   - 反选。
   - 导出。
6. 最新消息位于历史列表最下方，靠近紧凑聊天框。
7. 长历史只在 history 内部滚动，不推动聊天框本体。

选择规则：

1. 点击整条可选气泡切换选中。
2. 选中态使用明确视觉反馈，推荐“主题色描边 + 小 check 角标 + 未选中轻微降级”。
3. 选中态不能改变气泡尺寸，避免 geometry 抖动。
4. `sending` 或缺少稳定 id 的消息不可选。
5. `sent` / `failed` / `streaming` 是否可选取决于 id 是否稳定。
6. 已选消息被删除时，从 `compactExportSelectedIds` 移除。
7. Streaming 更新同一条消息时，保持选中状态，不生成重复气泡。

滚动规则：

1. 打开 history 默认滚到底部。
2. 用户距离底部在阈值内时，新消息和 streaming token 自动贴底。
3. 用户主动向上滚动后，关闭自动贴底，后续 streaming 不抢回滚动。
4. 用户手动回到底部或主动触发会产生新消息的操作后，恢复自动贴底。
   - 必须覆盖文字发送、仅附件 / 图片发送、截图附件发送、GalGame 选项、ChoicePrompt 选项。
   - 不能只在 `submitDraft()` 的文字路径里恢复贴底；所有会进入现有发送链路或产生用户选择消息的入口都要调用同一个恢复函数。
5. 滚动区自身处理 wheel / touchpad / touch move，不透传成聊天框拖动、蓝线拖动或模型交互。
6. 历史区域不提供整体拖拽移动；纵向 pointer move 只能触发内部滚动或取消本次点击选择。

Pointer intent 规则：

1. pointer down 记录起点和目标消息 id。
2. 位移低于点击阈值且没有滚动时，pointer up 才触发选择。
3. 位移超过阈值或发生滚动后，本次 pointer interaction 判定为 scroll / cancelled，不在 pointer up 补触发选择；第一阶段不要把它升级成拖拽。
4. 点击气泡内部链接、按钮、图片预览等交互元素时，不触发整条气泡选择。
5. 第一阶段不启用任何历史内容拖拽能力，只预留 wrapper 和状态入口。
6. 不预留“拖拽 history panel 本体上下移动”的状态入口；后续也不应新增该能力，避免与点击选择、内部滚动和气泡拖拽冲突。

检查：

1. assistant 在左，user 在右。
2. 长历史超过 50 条仍可访问和选择。
3. 全选 / 取消 / 反选遵守 `MAX_EXPORT_SELECTION`。
4. 取消只清空选择，不关闭 history。
5. 输入态不会因为 history 打开而被撑高。
6. 滚动 history 时不触发紧凑聊天框拖拽。
7. 点击气泡内部按钮不会切换整条选择。
8. Streaming 中主动向上滚动不会被下一帧 token 拉回底部。

### 第 3 步收口状态

收口结论：第 3 步已经完成 React 侧基础 UI、滚动锚定和气泡选择闭环，可以进入 geometry / 桌面端消费和后续 inline preview 真实导出能力阶段。

已落地内容：

1. 新增 `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`，承载 compact inline history 面板、滚动区、选择操作区和 inline preview 占位区。
2. History 面板使用当前 `messages` 实时渲染，不复用 `MessageList`，因此不继承 50 条裁剪。
3. 历史消息左右方向已按角色处理：assistant / tool / system 在左，user 在右。
4. 气泡渲染复用共享消息 block 渲染能力，不用临时字符串拼接替代 text / image / link / buttonGroup 等语义。
5. 选择交互已接入：
   - 点击可选气泡切换选择。
   - `sending` 或缺少稳定 id 的消息不可选。
   - 全选 / 取消 / 反选共用同一份 `compactExportSelectedIds`。
   - 全选 / 反选遵守 `COMPACT_EXPORT_SELECTION_LIMIT = 100`。
   - 选中态使用描边、小 check 和未选中轻微降级，且不改变气泡尺寸。
6. Pointer intent 已接入：
   - pointer down 记录消息 id 和起点。
   - 位移超过阈值或发生滚动后取消本次点击选择。
   - 气泡内部按钮、链接、图片等交互目标不会触发整条选择。
   - 第一阶段不启用历史内容拖拽，也不保留拖拽整个 history panel 的入口。
7. 滚动锚定已接入：
   - 打开 history 默认贴底。
   - 用户在底部附近时，新消息 / streaming 更新继续贴底。
   - 用户主动向上滚动后关闭自动贴底，streaming 不抢回滚动。
   - 用户手动回到底部或主动触发会产生消息的操作后恢复贴底；当前已覆盖文字发送、GalGame 选项和 ChoicePrompt 选项，后续接入附件 / 图片 / 截图真实发送入口时必须复用同一个恢复函数。
8. 无消息 / 消息清空场景已修正：
   - 点击“历史对话”会打开 history 并显示 `chat.exportEmpty` 空状态。
   - 计数显示 `0/0`。
   - 全选 / 取消 / 反选 / 导出按钮保留但禁用。
   - 不调用旧导出入口，不弹旧导出 toast。
9. GalGame / ChoicePrompt 让位规则已按设计收敛：
   - 只有选项实际位于 history 上方时，history 视觉淡化并禁用交互。
   - 选项位于下方时，history 保持正常显示和交互。
10. History 高度、宽度、气泡最大宽度和滚动区域已按紧凑聊天框尺寸变量派生，不使用固定 px 锁死，给后续紧凑框拉长 / 拉宽留出伸缩空间。
11. 右侧工具轮盘按钮文案已从旧 full 导出语义收敛为 compact 专用 `chat.compactExportHistory`，中文为“历史对话”。

已验证：

1. `npm --prefix frontend/react-neko-chat run typecheck`
2. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact export history|compact export action"`
3. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "opens compact export history with an empty state|toggles compact export history"`
4. 8 个 locale JSON 解析通过。
5. `git diff --check`
6. `bash build_frontend.sh`

阶段 3 边界：

1. 第 3 步只收口 React UI、选择、滚动和基础视觉层，不宣称真实导出能力完成。
2. Inline preview 当前仍是前端壳 / 占位，真实 Markdown / Image 构建、复制、下载接入留到第 8 / 第 9 步。
3. Desktop 透明窗口 bounds / native hit / setShape 的最终真实运行验收属于第 4 / 第 5 / 第 10 步；第 3 步只能保证 React DOM 和 CSS 为后续 geometry 提供稳定结构。
4. 图片 / 表情包 / 整条气泡拖拽仍属于后续阶段，不能混进第一阶段基础功能。

## 第 4 步：实现 inline 导出预览前端壳

修改范围：

1. `frontend/react-neko-chat/src/App.tsx` 或新增 `CompactExportPreview` 组件。
2. `frontend/react-neko-chat/src/styles.css`

目标：

1. 点击 history 操作区的“导出”后，在同一个透明 history layer 内展开 inline 导出预览。
2. 不打开新页面，不打开 full 导出窗口。
3. Inline preview 和 history 共用同一个 `compactExportSelectedIds`。
4. Inline preview 打开后进入预览模式，直接替换原历史消息滚动区；历史选择操作区的“全选 / 取消 / 反选 / 导出”不再显示，避免选择模式和预览模式同时抢空间。
5. 关闭 / 返回 preview 后回到历史选择模式，并保留原有选择状态。
6. Preview 内容仍基于同一份 `compactExportSelectedIds` 派生；消息删除或状态变化时，preview 同步更新。
7. 先确定 preview 的前端布局、状态、空选择提示、格式区占位和命中范围，不在这一步强行完善导出构建能力。
8. 空选择时显示提示，并禁用复制 / 下载等最终动作，不默认导出全部。
9. Preview 先通过清晰的 adapter 接口调用导出能力；能力可以暂时不可用，但接口形状要稳定，后续第 7 步再接真实实现。
10. 历史选择操作区提供小三角折叠 / 展开；折叠只隐藏“计数 / 全选 / 取消 / 反选 / 导出”内容，不改变选择状态、history open 状态或 preview 状态。

检查：

1. 选中 1 条消息后打开 preview，预览只包含该消息。
2. Preview 打开后历史列表与历史选择操作区被 preview 替换，不再显示“全选 / 取消 / 反选 / 导出”。
3. 空选择时 preview 不默认导出全部。
4. Preview 展开 / 收起不改变 history 选择状态。
5. Full 导出窗口不受 compact inline preview 前端壳改动影响。
6. 复制、下载等最终动作在真实导出能力接入前必须禁用或显示能力不可用提示，不能假成功。
7. 历史选择操作区小三角能折叠并恢复操作区内容；展开态小三角朝下，折叠态小三角朝上；折叠状态下呈现为左右两段短横线夹一个小三角，不保留原胶囊栏的大面积背景，不制造大面积透明遮挡。

### 第 4 步收口状态

收口结论：第 4 步已经完成 inline 导出预览的 React 前端壳，并完成与设计文档的目标一致性检查，可以继续进入 geometry 精确命中与桌面端消费阶段。真实 Markdown / Image 构建、复制、下载能力仍留到第 8 / 第 9 步接入。

已落地内容：

1. 点击 history 操作区的“导出”后，在同一个透明 history layer 内展开 `.compact-export-preview-region`，不打开新页面、不打开 full 导出窗口。
2. Preview 与 history 共用 `compactExportSelectedIds`：
   - 选中 1 条消息后打开 preview，只显示该消息。
   - Preview 打开后进入预览模式，原历史列表区域由 preview 替换。
   - 如果选中的消息被删除或选择集合被清空，preview 显示空选择提示。
3. Preview 打开时隐藏历史选择操作区，不再显示“全选 / 取消 / 反选 / 导出”。
4. Preview 已有明确返回入口；返回只关闭 `compactExportPreviewOpen`，不清空已选消息。
5. Preview 前端壳包含：
   - 标题、计数和关闭按钮。
   - Markdown / Image 格式占位。
   - 当前选中消息的轻量气泡预览。
   - 空选择提示。
   - 复制 / 导出最终动作占位。
6. 复制 / 导出最终动作在真实能力接入前保持禁用，不假成功、不默认导出全部。
7. Preview 样式沿用透明轻量面板，不复用 full 导出窗口的大白面板。
8. 历史选择操作区的小三角折叠 / 展开已经落地：
   - 展开态小三角朝下，表示可收起。
   - 折叠态小三角朝上，表示可展开。
   - 折叠后只保留左右两段短线和中心三角，不保留原胶囊栏的大面积背景。
   - 折叠不清空选择、不关闭 history、不改变 preview 状态。
9. 已同步修正设计文档中的旧冲突描述：
   - Compact 无消息时点击“历史对话”打开 inline history 空状态，不走旧导出入口、不弹旧导出提示。
   - 消息清空时如果 history 已打开，保持空状态，清空选择并关闭 preview。

已验证：

1. `npm run typecheck`（目录：`frontend/react-neko-chat`）
2. `npm test -- --run App.test.tsx`（目录：`frontend/react-neko-chat`，84 tests passed）
3. `git diff --check`
4. `bash build_frontend.sh`

阶段 4 边界：

1. 第 4 步不接入真实导出构建能力。
2. 第 4 步不修改 `static/app-chat-export.js`。
3. 第 4 步不新增 Python 后端接口。
4. 第 4 步不处理桌面端 setShape / bounds 最终验收；preview 的 native hit 和 bounds 消费留到第 5 / 第 6 步。

## 第 5 步：网页端 geometry 精确命中

修改范围：

1. `static/app-react-chat-window.js`
2. React history / preview DOM 上必要的 `data-*` attribute。
3. `frontend/react-neko-chat/src/styles.css`

目标：

1. History 打开时输出 `kind: 'history'` 的 compact geometry item。
2. Inline preview 展开时可以：
   - 合并进 history item 的真实 rect；或
   - 输出 `kind: 'preview'` 的 extra item。
   两种方式都必须保证不参与 base anchor。
3. History 关闭时完全移除 geometry item。
4. 透明 wrapper 不进入 native hit region。
5. 只有可见气泡、滚动区和按钮区进入 hit region。
6. 如果 history 使用 body portal，collector 必须像 `compact-input-tool-fan` / `compact-chat-choice-anchor` 一样允许它被收集。
7. 如果 history 外层 rect 与内部可交互区不一致，先扩展 collector 支持显式 hit rect 或子 item，不用整块 wrapper rect 充当 hitRect。
8. History 的 geometry 采集必须消费实际渲染后的 DOM rect；不要在 NEKO-PC 里复刻 history 尺寸公式。尺寸公式只属于 NEKO React/CSS，桌面端只消费页面提供的 rect。
9. ChoicePrompt / GalGame 选项打开时：
   - choice item 层级高于 history / preview。
   - 只有选项 placement 在上方并覆盖 history / preview 时，history / preview 才进入视觉让位和不可点击状态。
   - 选项 placement 在下方时，history / preview 保持正常显示和交互。
   - 不发生重排，不推动聊天框。
10. 同步产出 NEKO-PC 可直接消费的 geometry 合同；网页端 collector 的输出必须能表达桌面端需要的 window bounds 和 native hit，不留给 preload 猜测。

建议数据约定：

1. `data-compact-geometry-item="history"`
2. `data-compact-geometry-owner="surface"`
3. `data-compact-geometry-hit-scope="children"` 或等价机制，用于声明 hitRect 来自子节点。
4. 可交互子节点使用明确 attribute，例如 `data-compact-hit-region="true"`。
5. 非可见外层 `pointer-events: none`，可见气泡 / 滚动区 / 按钮 `pointer-events: auto`。

检查：

1. `window.__nekoGetCompactInteractionGeometry()` 打开 history 时能看到 history。
2. 关闭 history 后 geometry 中没有 history / preview。
3. Hit rect 不包含透明外层空白。
4. 选中态视觉变化不改变 nativeRect / hitRect 尺寸。
5. ChoicePrompt / GalGame 选项位于上方时，choice 可点，history 不抢点击；选项位于下方时，history 保持正常交互。
6. ChoicePrompt 关闭后，history 不抖动、不推动聊天框本体。
7. 在 geometry snapshot 中确认 `capsule` / `input` 仍是唯一 surface anchor 候选；history / preview 只能作为 extra item。
8. 手动把 history 移到靠近底部、靠近模型、靠近屏幕边缘的形态，确认输出 rect 不要求 preload 重新定位聊天框本体。

### 第 5 步收口状态

收口结论：第 5 步已经完成网页端 compact geometry 合同收敛，并完成与设计目标的全量复查。History / preview 的真实尺寸和命中区域由 NEKO 页面实际 DOM 输出，后续 NEKO-PC 只消费 geometry，不需要复刻 history 尺寸公式。

已落地内容：

1. `CompactExportHistoryPanel` 外层 history 声明：
   - `data-compact-geometry-owner="surface"`
   - `data-compact-geometry-item="history"`
   - `data-compact-geometry-hit-scope="children"`
2. History 的真实可命中子区域声明：
   - 历史滚动区：`data-compact-hit-region="true"` / `data-compact-hit-region-kind="scroll"`
   - 历史选择操作区：`data-compact-hit-region="true"` / `data-compact-hit-region-kind="controls"`
   - Inline preview：`data-compact-hit-region="true"` / `data-compact-hit-region-kind="preview"`
3. `static/app-react-chat-window.js` 的 collector 已优先读取 `[data-compact-hit-region="true"]`：
   - `history:native` 只输出 native union，`hitRect: null`，避免透明外层吃点击。
   - scroll / controls / preview 子 item 输出真实 DOM rect；只有 `pointer-events !== none` 时进入 `hitRect`。
   - ChoicePrompt / GalGame 选项位于上方时，history / preview 通过 CSS `pointer-events: none` 进入视觉让位，不抢点击；选项位于下方时不让位。
4. Collector 仍允许 body portal 中的 `.compact-export-history-anchor` 被采集，和 toolFan / choice portal 一致。
5. `getCompactSurfaceBaseRect()` 的 base anchor 候选仍只包含 `input` / `capsule`；history / preview 不参与聊天框本体锚点计算。
6. Geometry item 语义已收敛为 composite：
   - `history:native` 用于窗口 bounds union。
   - `history:native` 不进入 hit，不吃点击。
   - scroll / controls / preview 子区域才进入真实 hit。
7. 设计文档中的 geometry 规则已同步修正：
   - 无消息空状态打开时也输出 history 真实可见区域，不退回旧导出入口。
   - `interactive` 不再简单写成 `true`，而是 composite item 语义，避免后续桌面端把 native union 误当整块 hit region。

已复查场景：

1. History 关闭时 DOM 不存在，collector 不输出 history / preview。
2. History 打开且无消息时，输出空状态对应的真实可见区域。
3. History 打开且有消息时，scroll / controls 分别作为真实 hit region。
4. 操作区折叠时，controls hit region 缩到折叠后的实际 DOM rect，不保留原胶囊栏的大面积透明 hit。
5. Inline preview 打开时，preview 替换 history list 和 controls，并作为 `preview` hit region 输出。
6. ChoicePrompt / GalGame 位于上方时，history / preview 进入 `under-choice-prompt` 让位态且不抢点击；位于下方时保持正常交互。
7. 选中态、check 角标、glow 和 preview 内容变化不改变 base anchor 计算。
8. `capsule` / `input` 仍是唯一 surface base anchor 候选；history / preview 只能作为 extra geometry item。

已验证：

1. `node --check static/app-react-chat-window.js`
2. `npm run typecheck`（目录：`frontend/react-neko-chat`）
3. `npm test -- --run App.test.tsx`（目录：`frontend/react-neko-chat`，84 tests passed）
4. `git diff --check`
5. `bash build_frontend.sh`

阶段 5 边界：

1. 第 5 步只完成网页端 geometry 输出合同。
2. 第 5 步不修改 NEKO-PC 的 `preload-chat-react.js`。
3. 第 5 步不做桌面端 setShape / bounds 最终消费验收；该部分进入第 6 步。
4. 第 5 步不接入真实 Markdown / Image 导出构建能力。

## 第 6 步：NEKO-PC 桌面壳消费新增 geometry

修改范围：

1. `/Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js`
2. 仅在必要时修改 NEKO-PC 侧透明窗口 shape / bounds 消费逻辑。

目标：

1. NEKO-PC 消费页面新增的 `history` / `preview` geometry item。
2. `history` / `preview` 只扩展 compact window bounds 和 native hit region。
3. `history` / `preview` 不参与 `isDesktopCompactSurfaceAnchorKind`。
4. `history` / `preview` 不参与保存用户拖动位置。
5. `history` / `preview` 不推动最小化小球。
6. 不把 `choice` 的空间不足重定位逻辑套到 history / preview 上。
7. Streaming 高频更新时，如果 history 尺寸没有真实变化，不应触发窗口 bounds 高频抖动。
8. 打开 history / preview 时，窗口可以扩展到包含可见内容，但 base surface 的目标位置必须保持用户原先选择的位置。
9. 历史层或预览层如果超出工作区，第一阶段优先裁剪/限制 history 自身高度或内部滚动，不通过移动聊天框本体解决。

桌面端需要特别确认：

1. `getDesktopCompactGeometryScreenItems()` 能读取新增 item。
2. `buildDesktopCompactWindowLayout()` 中 base anchor 仍只来自 `capsule` / `input`。
3. extra item 的 `nativeRect` 进入窗口 bounds union。
4. extra item 的 `hitRect` 进入 shape / native hit。
5. `applyDesktopCompactNativeRegion()` 的 setShape rect 只包含真实命中区域。
6. `saveDesktopCompactSurfacePosition()` 不会因为 history / preview 打开而写入新位置。
7. `compactChoicePlacement` 仍只服务 choice，不服务 history / preview。

检查：

1. 桌面端 history / preview 不被模型或窗口边界裁切。
2. 桌面端透明空白区域可点击后方内容。
3. 打开/关闭 history 不改变用户保存的紧凑聊天框位置。
4. 打开/关闭 preview 不改变用户保存的紧凑聊天框位置。
5. GalGame / ChoicePrompt 选项盖在 history / preview 上方且可点。
6. History 滚动和 streaming 更新不会让桌面端窗口抖动。
7. 最小化小球位置不受 history / preview 影响。
8. 拖动紧凑聊天框后松手，history / preview 不把聊天框吸回模型头部或其它自动位置。
9. 打开右侧工具轮盘、history、preview、choice 的任意组合时，聊天框本体不因为 extra item bounds 变化而跳动。

### 第 6 步收口状态

收口结论：第 6 步已经完成 NEKO-PC 对 compact inline history / preview geometry 合同的桌面端消费修正。桌面壳继续只消费 NEKO 页面输出的 geometry，不复制 history 尺寸、导出选择、导出格式化或消息业务。

已落地内容：

1. `src/preload-chat-react.js` 的 `buildDesktopCompactWindowLayout()` 已保持 extra item 的显式命中语义：
   - `nativeRect` 进入 compact window bounds union。
   - `hitRect` 只在页面 geometry 明确提供时进入 setShape / native hit。
   - `hitRect: null` 不再被回填成 `nativeRect`。
2. `history:native` 这类 composite native union item 可以扩展窗口 bounds，但不会把透明外层变成可点击遮罩。
3. `history` / `preview` 仍然不属于 `isDesktopCompactSurfaceAnchorKind()`：
   - 聊天框本体 anchor 仍只看 `capsule` / `input`。
   - 用户拖动后保存的位置仍通过 `getDesktopCompactBaseSurfaceScreenRect()` 读取本体 surface。
   - history / preview 打开、关闭或尺寸变化不会写入新的本体位置。
4. `choice` 的空间不足上 / 下重定位逻辑仍只对 `item.kind === 'choice'` 生效，不会套到 history / preview。
5. 新增 `test/compact-history-geometry-contract.test.js` 锁定桌面端契约：
   - history / preview 不进入 anchor kind。
   - extra item 的 `nativeRect` 与显式 `hitRect` 分离。
   - 旧的 `hitRect || nativeRect` 回填不会回归。
   - 拖拽保存位置不引用 history / preview。
   - 通过抽取当前 `preload-chat-react.js` 中的真实 layout 相关函数，模拟网页端输出的 `surfaceItems`，验证 history / preview 的 bounds、hit、surface anchor 和 choice 重定位关系。
6. 桌面端实际显示修正：
   - `chat.html` 的 `<body>` 带 `electron-chat-window subtitle-web-host`。
   - 桌面端 compact history 不再留在 React root / `.chat-window` 内部，而是和 toolFan / ChoicePrompt 一样通过 body portal 挂到 `document.body`。
   - 这样 history 首帧不会被紧凑聊天窗口内部 `overflow: hidden` / 小窗口 bounds 裁切，geometry collector 也能像其它 interaction island 一样采集它。
   - 网页端不带 `electron-chat-window`，仍保持原挂载方式，避免污染网页表现。

真实模拟覆盖：

1. 网页端打开 history 后输出：
   - `history:native`：进入 window bounds union，不进入 hit。
   - `history:scroll` / `history:controls`：进入 native hit / setShape。
   模拟结果确认透明 history 外层点位不在 hitRects 内，滚动区和操作区点位在 hitRects 内。
2. Inline preview 展开后输出 preview hit region：
   - window bounds 随 preview 扩展。
   - 聊天框本体 surface 的屏幕坐标保持不变。
   - preview 可交互区域进入 hitRects，preview / history 透明边缘不吃点击。
3. ChoicePrompt / GalGame 选项空间不足时：
   - 只有 `choice` 被移动到上方。
   - `history` 保持网页端提供的位置。
   - choice 原本越界位置不再可点，移动后的 choice 可点。
   - history 透明 native 区域仍不进入 hit。

已验证：

1. `node --check src/preload-chat-react.js`（目录：`/Users/tonnodoubt/N.E.K.O.-PC`）
2. `node --test test/compact-history-geometry-contract.test.js`（目录：`/Users/tonnodoubt/N.E.K.O.-PC`，5 tests passed）
3. `git diff --check`（目录：`/Users/tonnodoubt/N.E.K.O.-PC`）
4. `node --check static/app-react-chat-window.js`（目录：`/Users/tonnodoubt/N.E.K.O`）
5. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact export history|compact export action|desktop compact options"`（12 tests passed）
6. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact export history|desktop body portal"`（11 tests passed）
7. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx`（85 tests passed）
8. `npm --prefix frontend/react-neko-chat run typecheck`
9. `bash build_frontend.sh`
10. `git diff --check`（目录：`/Users/tonnodoubt/N.E.K.O`）

补充测试结果：

1. 已尝试执行 `node --test test/*.test.js`（目录：`/Users/tonnodoubt/N.E.K.O.-PC`）。
2. 该全量桌面端测试当前存在 3 个与本轮 compact geometry 无关的既有失败，集中在 `test/storage-window-display-contract.test.js`：
   - `NEKO web hook remains host-optional and backend-authoritative when available`
   - `retained recommended root cleanup stays backend-owned and selective`
   - `storage selection emphasizes recommended location before current path`
3. 新增的 compact history geometry contract 测试在该全量运行中也通过；上述 storage 测试不属于第 6 步修改范围，本阶段不夹带修复。

阶段 6 边界：

1. 第 6 步只修正 NEKO-PC 对新增 geometry 的消费契约，不修改 NEKO 页面 UI、导出业务或后端协议。
2. 第 6 步不接入真实 Markdown / Image 导出构建能力。
3. 第 6 步的桌面端实际窗口交互仍需要在最终全流程验收中配合 NEKO-PC 真实启动确认，包括窗口不抖、透明区域穿透、ChoicePrompt 覆盖和用户拖动位置保持。

## 第 7 步：前端行为测试、桌面端契约测试和 i18n key 初步补齐

修改范围：

1. `frontend/react-neko-chat/src/App.test.tsx`
2. 必要时新增 component test。
3. `static/locales/en.json`
4. `static/locales/es.json`
5. `static/locales/ja.json`
6. `static/locales/ko.json`
7. `static/locales/pt.json`
8. `static/locales/ru.json`
9. `static/locales/zh-CN.json`
10. `static/locales/zh-TW.json`

目标：

1. 在真实导出能力完善前，先用测试锁定前端行为和状态合同。
2. 新增用户可见文案先补齐 8 个 locale，避免后续导出能力接入时夹带硬编码文案。
3. 测试重点放在 compact UI 行为、选择集合、滚动意图、preview 壳、ChoicePrompt 覆盖、geometry attribute 和桌面端契约。

测试要求：

1. Compact 导出按钮 toggle history。
2. Full 导出按钮仍调用 `onExportConversationClick`。
3. Compact 无消息时打开带空状态的 history，不调用旧导出入口，不弹旧导出提示。
4. Compact “历史对话”按钮作为 toggle 时，`is-active`、`aria-pressed`、`data-compact-tool-active` 与 open state 同步。
5. History open 不改变 `compactChatState`，不关闭 compact input，不触发 `onCompactChatStateChange`。
6. 非 compact 表面不暴露 compact 专属派生状态。
7. 气泡点击选择 / 取消选择。
8. 全选 / 取消 / 反选。
9. Inline preview 前端壳与选择集合同步。
10. 超过 50 条消息 history 不裁剪。
11. ChoicePrompt / GalGame 选项位于上方时 history 视觉让位并不抢点击；选项位于下方时 history 不让位。
12. Pointer intent 区分点击、滚动、拖拽阈值。
13. History / preview 不参与 compact base anchor 的数据标记。
14. 如果有可行的 NEKO-PC 单元/脚本测试，覆盖 `history` / `preview` extra item 只扩展 bounds / hit rect、不参与保存位置、不复用 choice 位置重排。

检查：

1. `npm --prefix frontend/react-neko-chat test`
2. 8 个 locale JSON 可解析。
3. 新增 key 在 8 个 locale 中一致。
4. 至少用 geometry snapshot 或脚本验证桌面端消费合同；不能只凭网页端 DOM 测试宣称桌面端安全。

## 第 8 步：导出能力无窗口化和宿主能力完善

修改范围：

1. `static/app-chat-export.js`
2. `static/app-react-chat-window.js` 仅在需要把能力透给 React host 时修改。

目标：

1. 保留 `window.appChatExport.open()` 和 `window.appChatExport.close()` 的现有行为。
2. 从现有 full 导出窗口内部拆出可被 compact inline 调用的纯能力。
3. Compact inline 不调用 `openExportPreviewWindow()`，只调用无窗口能力。
4. `MAX_EXPORT_SELECTION = 100` 的限制继续由导出能力统一维护，compact 的全选 / 反选不能绕过。
5. 不新增 Python 后端接口，不新增 NEKO-PC 业务桥接。
6. 不改变 full preview window 的 DOM、选择逻辑和下载行为。

建议公开能力：

1. `getMessages()`
   - 继续从 `window.reactChatWindowHost.getState().messages` 读取。
2. `buildEntries(messages, selectedIds?)`
   - 复用当前 full preview 的 entry 结构。
   - selectedIds 为空时只返回空选择结果，不自动解释为全量导出。
3. `getFormats()`
4. `getImageStyles()`
5. `getImageFormats()`
6. `buildDocument(entries, options)`
   - 复用当前 Markdown / Image 构建逻辑。
7. `copy(entries, options)`
8. `download(entries, options)`
9. `getMaxSelection()`
10. `translate(key, fallback, vars?)` 或复用现有翻译函数的安全包装。

检查：

1. Full 模式点击原导出入口仍打开 `neko-chat-export-preview` 窗口。
2. Full 预览里的复选框、格式、复制、Open In Window、下载仍可用。
3. 新无窗口 API 生成的 entries 与 full 预览选中同一组消息时一致。
4. 无窗口 API 不创建 DOM、不打开窗口、不写 compact UI 状态。
5. `MAX_EXPORT_SELECTION` 在 full 和 compact 能力入口都一致。

## 第 9 步：Inline preview 接入真实导出能力

修改范围：

1. `frontend/react-neko-chat/src/App.tsx` 或 `CompactExportPreview`。
2. `static/app-chat-export.js` 能力接口调用层。
3. 必要时更新相关测试。

目标：

1. 把第 4 步确定的 preview 前端壳接到第 7 步提供的真实无窗口导出能力。
2. Markdown / Image 预览与 full 导出能力一致。
3. 复制和下载调用 `app-chat-export.js` 无窗口能力。
4. 空选择继续禁用最终动作，不默认导出全部。
5. 真实导出失败时在 inline preview 内显示失败状态或 toast，不关闭 history，不丢选择。

检查：

1. 选中 1 条消息后打开 preview，预览只包含该消息。
2. Preview 打开后再选 / 取消消息，预览同步变化。
3. Markdown / Image 预览与 full 导出能力一致。
4. 复制和下载可用。
5. Full 导出窗口不受 compact inline preview 改动影响。

## 第 10 步：构建、真实运行验证和收口

最终检查：

1. `git diff --check`
2. `npm --prefix frontend/react-neko-chat test`
3. `bash build_frontend.sh`
4. 对照总验证清单做网页端和桌面端真实运行验证；桌面端验证优先级高于网页端视觉自检。

如果只改文档，不运行构建；如果改了 `frontend/react-neko-chat/src` 或会影响构建产物，必须运行 `bash build_frontend.sh`。

网页端验证：

1. 打开首页，切到 compact。
2. 进入 input，打开工具轮盘。
3. 点击导出按钮，确认 history 出现在紧凑聊天框上方。
4. 再次点击导出按钮，确认 history 关闭。
5. 发送新消息，确认 history 打开期间实时追加。
6. 触发 assistant streaming，确认在底部时贴底，向上滚动后不抢回。
7. 选择几条消息，点击“导出”，确认 inline preview 展开并同步选择。
8. 触发 GalGame / ChoicePrompt，确认选项盖在 history / preview 上方。

桌面端验证：

1. 启动 NEKO-PC。
2. 切到 compact。
3. 拖动紧凑聊天框到不同位置，包括靠近屏幕底部。
4. 打开 history，确认聊天框不跳、不跟模型重新吸附。
5. 打开 preview，确认聊天框不跳、不保存错误位置。
6. 在 history 外透明区域点击，确认后方内容可交互。
7. 在 history 内滚动、选择、点击按钮，确认不会漏到下方。
8. 打开 GalGame / ChoicePrompt，确认选项可点且不被 history 挡住。
9. 最小化 / 恢复，确认小球和聊天框仍按原紧凑态规则工作。
10. 打开/关闭 history、preview、右侧工具轮盘和 ChoicePrompt 的组合，确认窗口不抖、不跳、不写入错误保存位置。
11. 模型移动或模型 bounds 更新时，已由用户拖动确定的紧凑聊天框位置不被 history / preview 重新吸附。

完成标准：

1. 网页端和桌面端用户可见行为一致。
2. NEKO-PC 没有业务逻辑复制。
3. Full 导出窗口行为不变。
4. 普通聊天、紧凑输入、工具轮盘、GalGame / ChoicePrompt、最小化小球、拖动位置保存均未被污染。

## 后续阶段：历史内容拖拽

只有前面基础功能稳定并完成真实运行验证后，才实施本阶段。

目标：

1. 图片 / 表情包可拖动到外部保存或拖到猫娘模型身上发送。
2. 整条气泡可拖到猫娘模型上，作为重新发送这条历史内容。
3. 拖动整条气泡时，历史列表中原位置临时折叠，上下记录相邻。
4. 松手在猫娘模型范围内时，播放发送动画并走现有发送链路。
5. 松手不在猫娘模型范围内时，播放弹回动画。
6. 无论发送或弹回，气泡最终恢复到原历史位置。
7. 不真实删除消息，不改变历史数据本身。

实施前置：

1. 每条 history message 已有 animation wrapper。
2. Geometry 已支持临时 drag item 或临时扩大 bounds。
3. 已有 pet/avatar drop target 可被检测或桥接。
4. 已明确现有图片 / 附件 / 文本发送链路。

拖拽约束：

1. 不使用 `display: none` 或真实删除消息模拟抽走。
2. 使用 FLIP / spring / max-height collapse 等方式表达空间合拢和撑开。
3. 位移超过阈值并离开列表约 150ms 后才启动合拢动效。
4. 合拢 / 弹回 / 发送动效期间锁定历史滚动写入，避免 `scrollTop` 与动画争夺。
5. NEKO-PC 在明确 `bubbleDrag` 期间可以临时扩大 bounds，动效结束后必须恢复。
6. 跨窗口拖出图片时不能暴露内部本地路径、系统用户名、token、缓存路径或不可公开 URL。

## 总验证清单

必须验证：

1. Compact 下导出按钮开关 inline history。
2. Full 下导出按钮仍打开原导出预览窗口。
3. Compact 无消息时不调用旧导出入口、不弹旧导出提示；点击“历史对话”打开带空状态的 inline history，Full 导出空提示仍由原导出模块处理。
4. Compact “历史对话”按钮的选中 / 非选中态与 history open state 一致，并参照现有 toggle 工具按钮的 DOM / ARIA 语义。
5. History 打开不改变 compact input / default / options 的基础状态语义，不污染紧凑聊天框本体 anchor。
6. 非 compact 表面不残留 compact inline export 的 open、selected、auto-scroll 等派生状态。
7. History 消息左右方向正确。
8. 最新消息在最下方并靠近紧凑聊天框。
9. 长历史内部滚动，超过 50 条不裁剪。
10. 点击气泡能选择 / 取消选择。
11. 选中态明确且不改变布局尺寸。
12. 全选 / 取消 / 反选与气泡点击共用同一份选择集合。
13. 取消只清空选择，不关闭 history。
14. 点击“导出”展开 inline preview，不打开新窗口。
15. Inline preview 与 history 选择集合实时一致。
13. Preview 收起后选择状态保留。
14. 空选择不默认导出全部。
15. Markdown / Image 导出能力与 full 预览一致。
16. History 打开期间继续聊天会实时更新。
17. Streaming 更新复用同一条气泡，不产生重复气泡。
18. 用户在底部时自动贴底。
19. 用户向上滚动后 streaming 不抢滚动。
20. 用户重新到底部或主动触发会产生新消息的操作后恢复贴底；覆盖文字、附件 / 图片、截图附件、GalGame 和 ChoicePrompt，不只覆盖打字发送。
21. `sending` 或缺少稳定 id 的消息不可选。
22. 当前 schema 下不引入 `pending` / `retrying` 分支。
23. History 打开期间下方紧凑输入仍可输入、发送和切换工具。
24. Pointer intent 能区分点击、滚动和拖拽阈值。
25. 滚动 history 不触发聊天框拖动、蓝线拖动或模型交互。
26. 气泡内部链接、按钮、图片预览不误触发整条选择。
27. History 外透明 wrapper 不吃事件。
28. Native hit region 只包含真实可交互区域。
29. ChoicePrompt / GalGame 选项位于上方时盖在 history / preview 上方；位于下方时不遮挡 history。
30. ChoicePrompt / GalGame 选项位于上方时 history / preview 视觉让位且不可点；位于下方时保持正常显示和交互。
31. ChoicePrompt 关闭后 history / preview 不抖动。
32. 桌面端 history / preview 不被裁切。
33. 桌面端透明区域不挡点击。
34. History / preview 不参与 base surface anchor。
35. History / preview 不改变用户保存的紧凑聊天框位置。
36. History / preview 不影响最小化小球位置。
37. History 滚动和 streaming 更新不会让桌面端 bounds 高频抖动。
38. NEKO-PC 没有复制导出选择、格式化、文件构建或发送业务。
39. 新增用户可见文案同步 8 个 locale。
40. `git diff --check`、React 测试、`bash build_frontend.sh` 通过。
