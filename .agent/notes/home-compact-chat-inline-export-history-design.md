# 首页紧凑态内联导出历史层设计

> 本文记录“紧凑态导出对话按钮”的当前实现、设计目标和后续改造边界。
> 若本文与当前代码、测试或真实运行结果冲突，以当前代码和可复现证据为准。  
> 本文不替代 `home-compact-chat-mode-design.md`；它只补充紧凑态导出历史层。

## 目标

紧凑聊天框里的“导出对话 / 历史对话”按钮在 compact 下是内联历史层开关；在 full 下仍保留原导出窗口入口。

目标效果：

1. 在紧凑态点击“导出对话”按钮，打开紧凑聊天框上方的透明历史导出层。
2. 再次点击同一个按钮，关闭该历史导出层。
3. 不打开新页面、不打开独立导出窗口、不使用 full 导出预览页的大白面板。
4. 历史消息以原聊天框语义展示：
   - 猫娘 / assistant 在左侧。
   - 用户在右侧。
   - 背景透明，使用轻量气泡。
5. 最新消息显示在历史列表最下方，最靠近紧凑聊天框本体。
6. 历史消息超过最大高度后，在历史区域内部滚动。
7. 原导出页的勾选框改为点击聊天气泡选择消息。
8. 历史导出层打开期间，如果用户继续聊天，上方历史对话必须跟随当前 `messages` 实时更新。
9. 所有历史对话拖拽能力都在基础功能完成后再实施，包括图片、表情包和整条聊天气泡。
10. 后续拖拽阶段中：
   - 图片 / 表情包可拖到外部保存 / 拷贝，或拖到猫娘模型身上作为图片信息发送。
   - 整条历史聊天气泡可拖到猫娘模型上，作为把这条对话重新发送给猫娘的交互。
11. 历史区域底部只保留四个主操作：
   - 全选。
   - 取消。
   - 反选。
   - 导出。
12. 点击“导出”后，在当前紧凑历史层内展开导出预览界面；导出预览界面的选择状态必须与历史气泡选择状态完全一致。
13. GalGame / ChoicePrompt 选项打开且实际位于紧凑聊天框上方时，直接盖在历史导出层上方，不推动历史层和聊天框重排；选项位于聊天框下方时，不要求历史层视觉让位。
14. 桌面端最终用户可见表现必须和网页端一致，包括 history 高度、滚动范围、preview 展开高度、选项覆盖关系、按钮状态、选择态和交互顺序；唯一例外是桌面端暂未实现的紧凑聊天框毛玻璃效果。

## 非目标

1. 不重写消息 schema。
2. 不新增后端接口。
3. 不改变 full 模式的导出预览窗口。
4. 不把历史记录变成紧凑态默认常驻区域。
5. 不让历史导出层参与最小化小球定位。
6. 不让历史导出层参与紧凑聊天框本体锚点计算。
7. 不把 GalGame / ChoicePrompt 选项塞回胶囊内部。
8. 不把任何历史对话拖拽能力放进第一阶段基础功能；图片、表情包、整条气泡拖拽都属于基础功能完成后的后续阶段。
9. 不新增一套图片发送后端协议；图片拖到猫娘身上时必须复用现有图片导入 / 附件 / 发送链路。
10. 不新增一套“重发历史消息”的后端协议；整条气泡投递到猫娘时必须复用现有文本 / 图片 / 附件发送链路。
11. 不支持拖拽整个历史对话区域上下移动。历史区域的位置由紧凑聊天框本体和 geometry 合同决定；用户只能通过内部滚动查看更早 / 更新消息。

## 历史链路与当前分流

本文创建时，紧凑工具扇形菜单里的导出按钮仍会通过 `onExportConversationClick` 走 `static/app-chat-export.js` 的 `open()`，打开 `neko-chat-export-preview` 新窗口。

当前代码已经分流：

1. React 侧紧凑工具扇形菜单仍有导出 / 历史按钮：
   - `frontend/react-neko-chat/src/App.tsx`
   - `.compact-input-tool-item-export`
2. `chatSurfaceMode !== 'compact'` 时，按钮继续调用 `onExportConversationClick`，保留 full 模式导出窗口。
3. `chatSurfaceMode === 'compact'` 时，按钮 toggle React 内部 `compactExportHistoryOpen`，渲染 `CompactExportHistoryPanel`，不打开新页面。
4. 导出能力仍由 `static/app-chat-export.js` 提供：
   - `getCompactInlineOptions()`
   - `buildCompactInlinePreview()`
   - `copyCompactInlineSelection()`
   - `downloadCompactInlineSelection()`
5. 导出模块读取消息的来源仍是 React host：
   - `window.reactChatWindowHost.getState().messages`
6. Full preview 的 `Open In Window` 只保留在 full 导出窗口；compact inline preview 不显示该入口。

## 当前代码对齐点

以下是进入实现前必须遵守的当前代码事实。若未来代码变化，以新代码和测试为准。

1. 聊天 UI 只有一份 React 实现：
   - `frontend/react-neko-chat/` 构建 `neko-chat-window.iife.js`。
   - `/` 和 `/chat` 都挂载同一套 React chat window。
   - 不能去恢复或扩展旧 `#chat-container` 纯 DOM 聊天链路。
2. 当前 `ChatMessage.status` 只定义：
   - `sending`
   - `sent`
   - `failed`
   - `streaming`
   - 文档里提到的 `pending` / `retrying` 只能作为未来可能状态；第一版不能在代码里硬写 schema 不存在的状态分支。
3. 当前 `MessageList` 有两个不适合直接复用到 inline history 的行为：
   - 只显示最近 `MAX_DISPLAY_MESSAGES = 50` 条。
   - 使用自身的自动贴底和 `ResizeObserver` 逻辑。
   - Inline history 需要完整导出历史和独立滚动锚定策略，因此不能直接复用 `MessageList` 作为历史列表；可以复用消息块 / 气泡渲染思路，但不能继承 50 条裁剪和现有贴底逻辑。
4. 当前消息 block 渲染已经抽到 `MessageBlockView.tsx`：
   - Inline history 和 inline preview fallback 都使用 `MessageBlockView` 渲染 text / image / link / status / buttonGroup。
   - 后续不要在 history 里重新写一套 block 解析和渲染，也不要退回临时字符串拼接。
5. 当前导出模块 `static/app-chat-export.js`：
   - 从 `window.reactChatWindowHost.getState().messages` 读取消息。
   - 当前 `MAX_EXPORT_SELECTION = 100`。
   - 当前 selection 以 `ChatMessage.id` 为 key。
   - 现有 full 预览里有复选框、格式控制、复制、Open In Window、下载按钮。
   - Compact inline 通过 `buildCompactInlinePreview` / `copyCompactInlineSelection` / `downloadCompactInlineSelection` 复用导出能力，不能把 full preview DOM 和复选框搬进紧凑层。
6. 当前 compact geometry 采集在 `static/app-react-chat-window.js`：
   - 扫描 `[data-compact-geometry-owner="surface"]`。
   - 当前普通 item 会把元素 `getBoundingClientRect()` 同时作为 `nativeRect` 和 `hitRect`。
   - 当前已经支持 `data-compact-geometry-hit-scope="children"` 的 composite item：history 外层输出 `history:native`，气泡 / controls / preview 等子节点用 `data-compact-hit-region="true"` 输出真实 hit rect。
   - 后续若要进一步细化滚动条、气泡空白、拖拽影子或 drop target，仍必须由 NEKO 页面输出更细 hit rect 或等价 payload，不能让 NEKO-PC 猜。
7. 当前 NEKO-PC 桌面壳：
   - 只消费页面提供的 compact geometry。
   - 用 `surfaceItems` 的 base item 锚定聊天框本体位置。
   - 非 base item 会进入 bounds / hit region，但不能参与聊天框本体 anchor。
   - 新增 `history` / `preview` / 后续临时 drag item 时必须保持这一边界。
   - 桌面壳不能为了补齐视觉差异复制网页端业务或重写 history / preview 尺寸公式；跨端一致应通过 NEKO 页面输出真实 geometry、NEKO-PC 正确消费 geometry 达成。
8. 当前 i18n 已有导出相关 key：
   - `chat.compactExportHistory`
   - `chat.compactExportControlsCollapse`
   - `chat.compactExportControlsExpand`
   - `chat.exportSelectAll`
   - `chat.exportSelectNone`
   - `chat.exportSelectInvert`
   - `chat.exportSelectionCount`
   - `chat.exportPreviewTitle`
   - `chat.exportSelectionEmpty`
   - `chat.exportFormatLabel`
   - `chat.exportFormatMarkdown`
   - `chat.exportFormatImage`
   - `chat.exportImageStyleNeko`
   - `chat.exportImageStyleOriginal`
   - `chat.exportImageStylePoster`
   - `chat.exportImageStyleLyrics`
   - `chat.exportImageFormatPng`
   - `chat.exportImageFormatJpeg`
   - `chat.exportImageFormatWebp`
   - `chat.exportPreviewLoading`
   - `chat.exportPreviewFailed`
   - `chat.copyToClipboard`
   - 新增用户可见文案必须同步 8 个 locale；不要只写中文或英文 fallback。

## 可参考方案与 NEKO 落点

这些参考只提炼成熟交互原则，不照搬外部产品 UI。

1. 聊天历史列表应消费当前消息状态，而不是打开时复制一次静态快照。
   - 可参考点：成熟聊天 SDK 的 message list 通常直接从当前 channel / message state 渲染。
   - NEKO 落点：inline history panel 打开后继续订阅当前 `messages`；新增消息、streaming 更新、失败、删除都反映到同一历史层。
2. 最新消息在底部，但滚动权要尊重用户。
   - 可参考点：聊天列表通常采用 bottom anchored scroll；用户位于底部时自动跟随，新消息到达时继续贴底；用户已经向上看旧消息时不强行抢滚动。
   - NEKO 落点：打开历史层默认滚到底部；如果用户在底部，继续聊天时自动贴底；如果用户向上滚动，则保持当前位置。
3. 消息更新应基于稳定 id，而不是 DOM 位置。
   - 可参考点：streaming message、状态变化、reaction 等都应更新同一条 message item。
   - NEKO 落点：assistant streaming 在原气泡内更新，不生成重复气泡；选中状态以 message id 保存，消息删除时才移除选中 id。
4. 紧凑空间的多选更适合“气泡级选择”，不适合常驻复选框。
   - 可参考点：列表选择模式强调选中项要明确，但控件不必常驻挤占内容区。
   - NEKO 落点：点击气泡选择；选中态用强调色细描边 + 小 check 角标，避免把 full 导出页的复选框搬进紧凑层。
5. 导出能力要和导出窗口 UI 解耦。
   - 可参考点：导出历史是数据处理能力，不应绑定到某一个预览窗口。
   - NEKO 落点：`app-chat-export.js` 拆出无窗口 entries / format / copy / download 能力；full 继续用原导出窗口，compact inline 先显示轻量历史选择区，点击“导出”后再展开 inline 导出预览。
6. 拖拽要分清 source、preview、destination 和 drop result。
   - 可参考点：成熟 drag and drop 交互会给出明确预览和目标反馈，且失败时恢复原状态。
   - NEKO 落点：历史拖拽全部放到后续阶段；拖动图片或整条气泡时，历史消息本体不被真实删除，松手后按 id / sortKey 恢复原位。
7. 桌面端透明窗口要把视觉层、命中层、窗口 bounds 分开处理。
   - 可参考点：普通 Web z-index 不能解决 Electron 透明窗口裁切和 native hit region。
   - NEKO 落点：history 必须进入 compact geometry 和 native hit region；透明空白不吃事件；history 不参与聊天框本体 anchor，也不影响小球。

## 目标结构

### Compact Export History Panel

该面板属于 Compact Surface Island。

内容：

1. 历史消息滚动区。
2. 历史选择操作区。
3. Inline 导出预览区。
4. 选中消息计数或轻量状态提示。

历史选择操作区只包含：

1. 全选。
2. 取消。
3. 反选。
4. 导出。
5. 一个用于折叠 / 展开该操作区的小三角。

Inline 导出预览区只在点击“导出”后展开，承载原导出预览页中真正需要执行导出的格式、样式、复制、下载等能力。

文案规则：

1. “取消”沿用现有 `chat.exportSelectNone` 语义，即清空选择。
2. 不新增中文硬编码按钮文案；需要新增 key 时同步 8 个 locale。
3. 若使用现有导出 key，必须确认 compact inline 场景下含义仍成立，避免 full preview 文案泄漏到紧凑层。

位置：

1. 位于紧凑聊天框本体上方。
2. 背景透明。
3. 最大高度受控。
4. 不预留空透明区域。
5. 历史层宽高必须跟随紧凑聊天框本体尺寸变量等比例变化；当前紧凑聊天框已有左右 resize width，history 不能使用固定 430px、510px 等硬编码尺寸锁死。
6. 尺寸放大仍必须受 viewport / workArea 上限约束，避免桌面端透明窗口超出屏幕或挤压 GalGame / ChoicePrompt 选项层。
7. 历史气泡自身也必须使用容器比例宽度，不能保留固定 px 最大宽度导致外层变宽但气泡不跟随。

层级：

1. 在聊天框本体上方。
2. 在 GalGame / ChoicePrompt 选项下方。
3. 在模型上方。
4. 不压过选项层。

命中：

1. 只有可见气泡、必要滚动命中、按钮和 inline 预览控件吃事件。
2. 气泡点击用于选择 / 取消选择消息。
3. 面板外透明区域穿透。
4. 关闭时完全移出 geometry，不留下 hit rect。
5. 后续需要进一步细化历史列表内部穿透：气泡之间、气泡左右留白、空白透明区域不应作为长期 click / pointer native hit region；滚动能力应优先由滚动条、滚轮 / 触控板事件和真实内容区处理，不能用整块透明 scroll wrapper 覆盖后方交互。

## 状态设计

Compact 专属状态只表达导出历史层、inline 导出预览、选中集合、滚动贴底和预览格式，不把 full 导出窗口状态塞进 compact UI。

当前状态：

1. React 侧：
   - `compactExportHistoryOpen: boolean`
   - `compactExportPreviewOpen: boolean`
   - `compactExportSelectedIds: Set<string>`
   - `compactExportAutoScrollToBottom: boolean`
   - `controlsCollapsed`
   - `exportFormat`
   - `imageStyle`
   - `imageFormat`
   - `pendingAction`
   - `previewState`
   - `pointerIntentRef`
2. `compactExportHistoryOpen` 当前由 React 读写 `localStorage` key `neko.reactChatWindow.compactExportHistoryOpen`；导出模块不持久化 compact UI 开关。
3. `static/app-chat-export.js` 只提供 windowless export bridge 和 full preview window 能力。

状态规则：

1. `compactExportHistoryOpen` 不替代 `compactChatState`。
2. `compactChatState` 仍只表达 `default | options | input`。
3. 导出层可以与 `default` 共存。
4. 导出层可以与 `input` 共存，但不能撑高 input 本体。
5. 点击紧凑工具菜单里的“导出对话”按钮：
   - 如果当前不是 compact，保留现有 `appChatExport.open()` 行为。
   - 如果当前是 compact，则 toggle `compactExportHistoryOpen`。
6. 工具扇形菜单打开时点击导出，先按现有 `compactFanCloseOnAction` 收起工具菜单，再打开历史导出层。
7. “导出对话”按钮在 compact 下是开关控件，不是一次性 action。打开 history 时必须和现有 GalGame / 翻译开关一致地提供：
   - `is-active`
   - `aria-pressed`
   - `data-compact-tool-active`
   history 关闭时恢复未选中态；离开 compact 时 DOM 暴露为关闭 / 空语义；无消息或消息清空但 history 仍打开时，按钮保持打开态。
8. 切换到 full / minimized 时，compact history DOM 和 geometry 必须关闭 / 移除；当前 React 代码会清空 preview、选择和自动贴底状态，但会保留持久化的 `compactExportHistoryOpen`，回到 compact 时可按存储状态重新打开。
9. 消息清空后如果历史导出层已打开，应保持历史层打开，但不显示空状态文案 / 空 pill；同时清空选择并关闭 inline 导出预览。
10. Compact 无消息时点击“历史对话”仍打开 inline history 外壳：不调用旧导出入口、不弹旧导出提示、不显示“暂时没有可以导出的对话”这类顶部空提示；Full 导出入口的空对话提示仍由原导出模块处理。
11. 历史层打开期间，`messages` 变化必须驱动历史层更新；不能只在打开瞬间截取一次快照。
12. 新增消息、更新 streaming 消息、消息失败状态变化、消息删除都应反映到历史层。
13. 已选中消息如果被删除，应从 `compactExportSelectedIds` 移除；已选中消息如果只是内容更新，应保持选中状态。
14. 历史层打开期间，用户仍然可以在下方紧凑聊天框正常输入和发送；历史层不能把聊天框锁成只读，也不能因为消息更新自动关闭。
15. `compactExportPreviewOpen` 只表达 inline 导出预览是否展开；它不创建新窗口。
16. 点击历史选择操作区的“导出”：
   - 展开 `compactExportPreviewOpen`。
   - 进入预览模式后，preview 替换原历史消息显示区域；历史选择操作区的“全选 / 取消 / 反选 / 导出”隐藏，避免选择模式和预览模式同时显示。
   - 如果没有选中消息，预览区显示空选择提示，并禁用复制 / 导出文件等最终动作。
   - 不自动把未选择状态解释为导出全部，避免用户误操作。
17. Inline 导出预览使用同一份 `compactExportSelectedIds`；如果消息删除、状态更新或后续 preview 内提供选择调整，预览内容必须同步变化并回写同一份集合。
18. 点击“取消”只清空 `compactExportSelectedIds`，不关闭历史层；如果 inline 导出预览已展开，应同步变为空选择状态或提示需要重新选择。
19. 关闭历史层时同时关闭 inline 导出预览。
20. `compactExportAutoScrollToBottom` 只表达历史滚动区是否允许自动贴底；用户主动向上滚动后必须关闭，不能被 streaming 高频更新重新抢回。
21. `pointerIntentRef` 只用于一次 pointer interaction 的临时意图判定，不持久化到业务状态。
22. Compact 专属派生状态不能泄漏到 full / minimized 表面。向 DOM、geometry、NEKO-PC 或测试暴露的 `data-compact-export-*` 在非 compact 下必须回到关闭 / 空 / false 语义。
23. 不要提前添加没有 UI 或交互消费点的空状态文案 / 空 pill。`pointer intent` 和导出 options 应在 history UI、滚动、选择或 preview 真实接入时落地，避免“状态存在但无人消费”的伪完成。

## 数据与导出能力复用

不应在 React 里重新写一套导出文本提取和文件生成逻辑。

当前已经从 `static/app-chat-export.js` 暴露无窗口能力，供 compact inline 面板调用。

当前能力边界：

1. `getCompactInlineOptions()`
   - 返回 Markdown / Image、图片样式、图片格式和导出模块忙碌状态。
2. `buildCompactInlinePreview(options)`
   - 按 `messageIds` 临时套用选择，复用现有 entry / Markdown / Image 构建，返回 document 或 image object URL。
3. `copyCompactInlineSelection(options)`
   - 按当前 compact 选择和格式复用现有文本 / 图片复制能力。
4. `downloadCompactInlineSelection(options)`
   - 按当前 compact 选择和格式复用现有下载能力。
5. `getReactMessages()`、`buildExportDocument()`、`copyTextToClipboard()`、`copyImageToClipboard()`、`downloadExportFile()` 等仍是导出模块内部能力，compact inline 不应复制。

需要保留：

1. full 模式继续调用 `appChatExport.open()`。
2. compact 模式先打开 inline history panel；点击 panel 内的“导出”后，通过 `buildCompactInlinePreview()` 构建内联预览，再通过 copy / download compact API 执行最终动作。
3. 现有 preview window 的 `Open In Window` 按钮只保留在 full 导出窗口中。
4. compact inline 面板不显示 `Open In Window`。
5. compact inline 导出预览不维护第二份选择状态；它只读取和回写 `compactExportSelectedIds`。
6. 渲染消息 block 时共享 `MessageBlockView`；禁止在导出历史层重写一套 text/image/link/status/buttonGroup 解析。

## UI 规则

历史消息：

1. 使用透明背景。
2. assistant / 猫娘消息左侧对齐。
3. user 消息右侧对齐。
4. 最新消息排在列表底部，靠近紧凑聊天框；打开历史导出层时默认滚动到最底部。
5. 用户向上滚动查看旧消息时保持用户当前位置；如果用户已经在底部，新消息追加后可以继续贴底。
6. 历史层打开期间继续聊天时，新增用户消息和 assistant 回复要实时追加到列表底部。
7. assistant streaming 消息应在原气泡内更新，不反复新增重复气泡。
8. 如果用户停留在底部，streaming 更新和新消息追加时保持贴底，最新内容继续接近聊天框。
9. 如果用户已经向上滚动查看旧消息，不强制把滚动位置拉回底部；可在后续实现轻量“有新消息”提示，但第一版不强制需要。
10. 用户正在输入但尚未发送的草稿不进入历史层；发送成功进入 `messages` 后再追加。
11. 发送失败或重试状态如果当前消息体系已有状态表达，历史层同步显示对应状态；如果当前消息体系没有状态表达，不额外发明新状态。
12. 文本气泡轻量、紧凑。
13. 单条消息内容过长时，气泡内部换行；历史区整体滚动。
14. 图片、表情包、附件等视觉内容应在气泡中显示可预览内容；无法渲染的类型才退化为导出模块已有的文本占位。
15. 第一阶段不实现任何历史内容拖拽；所有图片、表情包、整条气泡拖拽都进入后续阶段。
16. 历史列表不能沿用当前 `MessageList` 的 50 条展示裁剪；导出历史层应以导出模块可见的完整消息集合为数据源。
17. 如果后续出于性能考虑做虚拟列表或分段加载，也必须保证“导出选择集合”仍可覆盖完整消息集合，而不是只覆盖当前 DOM 中的可见消息。

消息选择：

1. 原导出预览页的复选框不出现在紧凑历史层中。
2. 点击消息气泡本体切换选中状态。
3. 选中状态必须有明确反馈，不能只依赖细微颜色变化。
4. 选中消息进入 `compactExportSelectedIds`。
5. 再次点击同一气泡取消选择。
6. 点击链接、按钮、图片预览等内部交互元素时，不应误触发整条气泡选择。
7. 键盘和辅助功能路径仍要能选择消息；至少需要 `aria-pressed` 或等价选中语义。
8. 进入 `compactExportSelectedIds` 的 key 必须稳定；不能使用会在发送成功、重试、后端确认后被替换的临时 id。
9. 当前代码只禁用 `sending` 消息；带稳定 `id` 的 `sent`、`failed`、`streaming` 消息都可选择。
10. 如果后续确认 NEKO 消息体系存在稳定 `clientMessageId` / `localId`，可用该稳定 key 作为选择 key；否则继续以 `ChatMessage.id` 为准。
11. 按当前 schema，实际要处理的是 `sending`、`sent`、`failed`、`streaming`；不要在实现里硬写 schema 不存在的 `pending` / `retrying`，除非先扩展 schema 和测试。
12. 如果后续实证发现 `streaming` 完成后会换 id，应按不稳定 key 处理，禁用选择直到进入稳定状态。

选中态视觉候选：

1. **推荐方案：强调色细描边 + 角标 check**
   - 气泡外沿出现 1-2px 蓝色或主题强调色描边，第一版可采用约 1.5px 的主题色描边。
   - 气泡靠外侧上角出现小圆形 check 角标，角标可用 `scale(0)` 到 `scale(1)` 的短 transition 弹出。
   - 优点是明确、成熟、不会显著扩大气泡面积。
   - 适合多选导出这种需要准确确认选中项的场景。
2. **柔光方案：轻微上浮 + 外发光**
   - 选中气泡轻微 `translateY(-1px)`，外圈出现柔和蓝白 glow。
   - 优点是好看、贴近透明紧凑层的视觉。
   - 缺点是只靠 glow 不够明确，应搭配小 check 或选中计数。
3. **淡出未选中方案：选中项保持正常，未选中项降低不透明度**
   - 进入多选后，未选中气泡降到 0.55-0.7 opacity。
   - 优点是批量选择时一眼能看出集合。
   - 缺点是会削弱历史可读性，不建议作为默认第一版，只适合选择数量较多时的增强态。
4. **边缘选择条方案：气泡外侧出现短竖条或小折角**
   - 不改变气泡内部颜色，仅在气泡外侧加短条。
   - 优点是轻。
   - 缺点是对透明背景和左右气泡同时存在时不如 check 明确。

第一版建议采用“强调色细描边 + 小 check 角标”，必要时叠加极轻微 glow；不要采用大面积实底变色，避免历史层变重。

多选增强态：

1. 当 `compactExportSelectedIds.size > 0` 时，可以对未选中气泡做非常轻微的视觉降级，例如 `opacity: 0.85`。
2. 不建议第一版默认对正文气泡使用明显 blur；如果后续尝试 `blur(0.5px)`，必须以不影响历史阅读为前提。
3. 未选中降级不能改变气泡布局尺寸，也不能改变 geometry rect。

## 历史交互意图判定

历史对话区域同时承载点击选择、上下滚动查看历史，以及后续阶段的图片 / 表情包 / 整条气泡内容拖拽。它不承载“拖拽整个历史区域上下移动”的功能。这里必须有统一的 pointer intent 判定，不能让 click、scroll、drag 三套事件各自抢执行。

基础原则：

1. 第一阶段只执行点击选择和上下滚动，不执行历史内容拖拽。
2. 后续拖拽阶段开启后，也必须先判定用户意图，再进入拖拽；不能因为轻微手抖把点击选择误判成拖拽。
3. 滚动历史的优先级高于气泡选择；用户明显上下翻动时，不触发气泡选中 / 取消选中。
4. 图片 / 表情包拖拽、整条气泡拖拽、点击选择三者只能有一个最终结果。
5. 一旦某次 pointer interaction 被判定为滚动或拖拽，pointerup 时不能再补触发 click 选择。
6. 选择状态、预览选择状态和滚动位置不能因为一次失败拖拽被意外改变。
7. 任何阶段都不把纵向拖动历史区域解释为移动 history panel；纵向拖动只能是内部滚动或一次被取消的 pointer interaction。

建议判定状态：

1. `pending`：pointer down 后尚未确定意图。
2. `click`：移动距离和按压时长都在点击范围内，pointer up 时切换选择。
3. `scroll`：主要移动方向为纵向，或滚动容器已经产生 scrollTop 变化；该状态只滚动内部列表，不移动历史区域。
4. `imageDrag`：后续阶段中，从图片 / 表情包 block 起手并超过拖拽阈值。
5. `bubbleDrag`：后续阶段中，从气泡文本 / 空白区域起手并超过拖拽阈值。
6. `cancelled`：GalGame / ChoicePrompt 打开、窗口失焦、pointer cancel、ESC 或其他高优先级交互打断。

点击选择判定：

1. 起点必须在可选择的气泡主体上。
2. 起点如果是链接、按钮、图片预览、导出预览控件、滚动条等内部交互元素，不触发整条气泡选择。
3. pointer down 到 pointer up 之间，如果累计位移超过点击阈值，不触发选择。
4. 如果历史滚动容器的 `scrollTop` 在本次 pointer interaction 中发生变化，不触发选择。
5. 如果进入后续拖拽状态，不触发选择。
6. 键盘选择不走 pointer 判定，但结果仍写入同一份 `compactExportSelectedIds`。
7. 如果用户按住历史区域上下拖动但未产生有效滚动，也只能取消本次点击选择；不能进入“移动历史区域”。

上下翻动 / 滚动判定：

1. wheel、trackpad、触摸板自然滚动只作用于历史消息滚动区。
2. 滚动事件不能冒泡成蓝线拖拽、聊天框拖动或气泡选择。
3. touch / pointer 拖动时，如果纵向位移明显大于横向位移，并且滚动区可滚动，则进入 `scroll`。
4. 进入 `scroll` 后，直到 pointer up 都保持滚动语义，不再切回点击或拖拽。
5. 滚动到顶部 / 底部后，如果还有剩余滚动量，也不能把这次操作转交给聊天框拖动；应保持在历史层内部消化或自然结束。
6. 用户滚动查看旧消息时，实时新增消息不强制拉回底部；只有用户本来就在底部附近时才自动贴底。
7. 自动贴底必须有明确阈值。第一版建议使用距离底部约 30px 作为 near-bottom 判断，具体值可按真实设备微调。
8. `scrollTop + clientHeight < scrollHeight - threshold` 时，立即把 `compactExportAutoScrollToBottom` 置为 `false`。
9. 只有用户手动回到底部附近，或用户主动触发会产生新消息的操作时，才重新打开 `compactExportAutoScrollToBottom`。
   - 主动触发包括文字发送、仅附件 / 图片发送、截图附件发送、GalGame 选项、ChoicePrompt 选项，以及后续复用现有发送链路的历史内容投递。
   - 不要只把“输入框文字发送”当作恢复贴底入口。
10. Assistant streaming 高频更新只能在 `compactExportAutoScrollToBottom === true` 时贴底；不能在用户向上滚动后每帧把滚动条拉回底部。
11. 当前普通 `MessageList` 使用约 60px near-bottom 阈值；inline history 可以单独选择 30px 左右阈值，但必须通过真实滚轮、触摸板和 streaming 场景调试确认。

后续拖拽判定：

1. 历史内容拖拽功能未进入实施阶段前，所有超过点击阈值的气泡移动只可能是滚动或取消，不能产生拖拽影子。
2. 后续开启图片 / 表情包拖拽后，从图片 / 表情包 block 起手，超过拖拽阈值才进入 `imageDrag`。
3. 后续开启整条气泡拖拽后，从气泡文本或空白区域起手，超过拖拽阈值才进入 `bubbleDrag`。
4. 如果起手点在图片 / 表情包 block 内，图片 / 表情包拖拽优先于整条气泡拖拽。
5. 拖拽阈值需要大于普通点击手抖范围，并且要兼容触摸板和鼠标；具体数值应在真实设备上调试后确定，不在文档里写死。
6. 进入拖拽后，历史列表中的视觉折叠、拖拽影子、drop target 判定都由拖拽链路接管；选择集合不因拖拽开始或失败而改变。
7. 拖拽结束未命中猫娘模型或外部有效目标时，只做弹回 / 恢复，不触发选择，不改变导出预览选择状态。

桌面端补充：

1. NEKO-PC 的 native hit region 必须覆盖历史滚动区、气泡、按钮和 inline 预览控件，但不应把历史层外的透明区域变成可交互遮罩。
2. 历史滚动区内的 wheel / pointer scroll 要留在 ReactChat 窗口内处理，不能透传给下方模型，也不能触发外层拖动。
3. 后续拖拽阶段中，只有进入明确 drag 状态后，才允许 pet/avatar 层参与 drop target 判定。
4. 如果 Electron / 系统级拖拽与 React pointer 事件冲突，以“不误触发选择、不丢失历史、不扩大透明遮挡”为优先级。

后续历史拖拽能力：

所有历史对话拖拽都属于后续阶段，必须在基础功能完成后再实施。基础功能包括：内联历史层、气泡选择、导出操作、geometry、桌面端窗口 bounds / native hit region、GalGame / ChoicePrompt 覆盖关系。

图片与表情包拖拽：

1. 可拖动目标：
   - 图片 block。
   - 表情包 / sticker block。
   - 导出模块能解析出原始 URL、blob 或 data URL 的图片附件。
2. 拖拽开始时使用标准 drag data：
   - `text/uri-list`：可公开访问或可本地解析的图片 URL。
   - `text/html`：带图片来源的最小 HTML 片段。
   - `text/plain`：图片描述或 URL fallback。
   - 自定义类型：例如 `application/x-neko-chat-image`，用于 NEKO 内部识别。
3. 拖拽预览应使用图片缩略图，而不是整条气泡；预览上可附一个很小的来源标记，帮助用户理解拖的是图片。
4. 拖到系统 / 外部应用：
   - 优先让浏览器 / Electron 默认图片拖拽能力暴露 URL 或文件数据。
   - 如果当前图片只有内部 blob / data URL，需提供可下载文件名和 MIME 信息；无法跨应用保存时，应给出明确 fallback，例如点击图片后的保存动作。
   - 桌面端如需生成可拖出的临时文件，应使用无敏感路径特征的临时文件名，或通过内存 / base64 管道投递；不能暴露用户本地绝对路径。
5. 拖到猫娘模型：
   - pet/avatar 层需要成为 drop target。
   - drop 后走现有图片导入 / 附件 / 发送链路，等价于用户把该图片作为输入发送给猫娘。
   - 不直接把图片写进后端新接口。
   - drop 成功后给轻量反馈，例如模型附近短暂高亮或聊天框出现附件发送状态。
6. 拖拽与选择的冲突处理：
   - 点击气泡是选择。
   - 从图片缩略图拖动超过阈值后进入拖拽，不触发选择。
   - 拖拽结束如果没有有效 drop，不改变选择状态。
7. 安全与权限：
   - 只允许拖出用户已经能在历史层看到的图片。
   - 不把鉴权 token、内部本地路径或不可暴露的源信息塞进 `text/plain`。
   - 不把后端缓存路径、系统用户名、项目目录、记忆目录等信息暴露给外部应用。
   - 跨窗口 / 跨应用拖拽失败时不能丢消息或修改聊天状态。

整条聊天气泡投递：

1. 这是后续阶段功能，必须在以下基础能力稳定后再实施：
   - 紧凑态内联历史层打开 / 关闭。
   - 气泡点击选择。
   - 图片 / 表情包拖拽。
   - history geometry、Electron bounds、native hit region。
   - 猫娘模型 drop target。
2. 目标语义：
   - 用户拖动整条聊天气泡到猫娘模型上。
   - 松手在猫娘模型范围内时，把该条历史对话作为发送信息投递给猫娘。
   - 松手不在猫娘模型范围内时，不发送，只回到原位。
3. 拖动时序：
   - 用户按住并拖动整条聊天气泡。
   - 气泡从历史列表中被“拿走”，跟随指针移动。
   - 历史列表中该条消息临时消失，上下两条记录自然变为相邻。
   - 这个过程应流畅连续，不能看起来像先删除再突然生成拖拽影子。
4. 命中猫娘模型后的松手结果：
   - 播放“发给猫娘”的小动画，例如气泡轻微缩小、朝模型方向吸附、模型附近短暂接收高亮。
   - 通过现有发送链路把内容送出。
   - 动画结束后，原历史气泡恢复到自己的原位置。
   - 历史列表上下相邻的记录重新分开。
5. 未命中猫娘模型时的松手结果：
   - 不发送任何内容。
   - 拖拽气泡播放弹回动画，回到自己原本的位置。
   - 原历史气泡恢复到自己的原位置。
   - 历史列表上下相邻的记录重新分开。
6. 原位恢复规则：
   - 历史记录本体不被真正删除。
   - 拖动过程中的“消失”只是视觉占位折叠。
   - 发送成功也不从历史层移除该条记录。
   - 恢复位置必须按消息 id 回到原排序点，不能用当前 DOM 顺序猜。
   - 第一阶段的 DOM 结构应为每条消息预留动画容器 wrapper，用于后续控制高度折叠、弹回撑开和恢复特效；该 wrapper 在第一阶段不能引入拖拽行为。
7. 与选择的冲突处理：
   - 短点击气泡仍是选择 / 取消选择。
   - 长按或拖动超过阈值后进入整条气泡拖拽。
   - 进入拖拽后不切换选择状态。
   - 已选中气泡也可以拖动；拖动不改变其选中状态。
8. 与图片拖拽的冲突处理：
   - 从图片缩略图开始拖动时，优先触发图片 / 表情包拖拽。
   - 从气泡空白或文本区域开始拖动时，触发整条气泡拖拽。
   - 若一条消息只有图片，图片本体优先作为图片拖拽；需要整条气泡拖拽时可使用气泡边缘或后续专用拖拽区域。
9. 发送内容映射：
   - user 文本气泡：作为用户文本重新发送。
   - assistant / 猫娘文本气泡：作为引用式文本或普通文本发送，具体文案需沿用现有发送链路可接受的格式。
   - 图片 / 表情包气泡：走现有图片 / 附件发送链路。
   - 混合内容气泡：按现有消息 block 转成可发送 payload；不支持的 block 必须给出可理解 fallback。
10. 动画和 geometry：
   - 拖拽影子属于临时交互层，不作为长期 history geometry item。
   - history item 的 native rect 在拖动过程中仍应覆盖列表剩余可交互区域。
   - 拖拽影子和猫娘 drop target 需要进入临时 hit / drag-over 判定，但不能扩大长期透明遮挡面。
   - 拖拽过程中如果 GalGame / ChoicePrompt 选项打开，优先取消或结束气泡拖拽；不能让选项、历史拖拽、drop target 三者争焦点。
   - 气泡从列表中被“拿走”时不能用 `display: none` 或真正删除 DOM；应通过动画容器的高度、margin、opacity 或 FLIP / spring 方式表现空间合拢。
   - 正式启动合拢动效前需要 intent lock。建议在拖拽超过阈值且指针离开列表区域约 150ms 后，再让原位空间开始合拢，避免手抖导致列表反复横跳。
   - 合拢 / 弹回 / 成功恢复动效期间，应临时锁定历史滚动区滚动更新，例如暂停自动贴底并避免浏览器因 `scrollHeight` 改变强行改写 `scrollTop`。
   - 未命中模型时，拖拽影子弹回，同时原位动画容器从折叠状态恢复到真实高度；命中模型时，发送特效结束后也要先撑开原位，再恢复气泡可见状态。
11. 桌面端要求：
   - NEKO-PC 只桥接拖拽数据、drop 命中和现有发送链路。
   - 不在 NEKO-PC 复制消息转换业务。
   - 拖拽影子不能导致 ReactChat 原生窗口长期扩大成大透明区域。
   - 进入明确 `bubbleDrag` 后，桌面端可以临时扩大透明窗口 bounds，以容纳拖拽影子和弹回路径；动效结束后必须收回到 geometry 真实范围。
   - 松手后的发送 / 弹回动画必须在同一条视觉链路内完成，不要出现气泡消失后瞬移恢复。

历史选择操作区：

1. 位于历史列表下方。
2. 与历史层同属一个透明 surface。
3. 包含：
   - 全选。
   - 取消。
   - 反选。
   - 导出。
4. “取消”表示清空当前选中消息，不表示关闭历史层。
5. “导出”表示展开 inline 导出预览，不直接打开新页面或 full 导出窗口。
6. Markdown / Image、图片样式、图片格式、复制到剪贴板、导出文件等细节按钮不出现在历史选择操作区，只在 inline 导出预览展开后显示。
7. 按钮排列要紧凑，不能变成 full 导出预览页的大控制面板。
8. 选中状态要清楚，但不要使用大面积实底卡片。
9. 操作区可以通过小三角折叠；展开态小三角朝下，折叠态小三角朝上；折叠后视觉应变成左右两段短横线夹一个小三角，不保留原胶囊栏的大面积背景，不清空选择、不关闭历史层、不影响历史列表滚动。
10. 小三角只控制历史选择操作区自身显隐；inline 导出预览展开时仍按预览规则替换历史区并隐藏整个历史选择操作区。

Inline 导出预览区：

1. 点击历史选择操作区的“导出”后展开。
2. 位于当前紧凑历史层内，不打开新页面、不打开独立窗口。
3. 展开后替换原历史消息显示区域；历史选择操作区隐藏，不同时显示“全选 / 取消 / 反选 / 导出”。
4. 复用原导出预览页的导出能力，但不复用 full 导出页的大白面板视觉。
5. 显示内容必须基于 `compactExportSelectedIds` 对应的消息集合。
6. 如果选中消息删除、状态变化或后续预览区允许调整选择，预览内容必须同步更新并回写同一份 `compactExportSelectedIds`，不能维护第二套选择状态。
7. 预览区可承载：
   - Markdown / Image。
   - 图片样式。
   - 图片格式。
   - 复制到剪贴板。
   - 导出文件。
8. 预览区需要有明确返回 / 收起方式；收起只关闭 `compactExportPreviewOpen`，不清空选中消息，并回到历史选择模式。
9. 如果选中消息被删除，预览内容同步移除对应消息；选中集合为空时，预览区显示空选择提示。

滚动：

1. 历史消息区有最大高度。
2. 超出后内部滚动。
3. 最新消息在底部，默认贴近紧凑聊天框。
4. 历史选择操作区可以固定在历史层底部，或自然排列在滚动区下方；第一版优先选择实现更稳定的方案。
5. 如果历史选择操作区固定在底部，它应位于消息滚动区下方，不能挡住最后一条消息。
6. 滚动不能触发蓝线拖拽。
7. 历史层打开期间收到新消息时：
   - 若滚动位置在底部附近，自动跟随到底部。
   - 若用户已向上滚动，则保持当前位置，不打断阅读。
   - streaming 文本增长时也遵守同一规则。
8. Inline 导出预览展开后，如果预览高度导致历史区空间不足，优先压缩历史消息滚动区高度，不移动聊天框本体位置。
9. Near-bottom 判断必须使用显式阈值，第一版建议约 30px；不能用“只要有新消息就 scrollIntoView”。
10. Streaming 更新频率较高时，应合并或节流滚动贴底操作，避免每个 token 都触发布局和滚动写入。
11. 用户主动滚离底部后，直到用户回到底部或主动发送新消息前，历史区不能自动抢滚动。

## Geometry 合同

面板必须纳入 compact geometry。当前代码已经通过 history composite item 接入。

当前 item：

1. `kind: history`
2. `owner: surface`
3. `interactive: composite`：`history:native` 只用于窗口 bounds union，不进入 hit；气泡、controls、preview 控件和必要滚动命中子区域才进入真实 hit。
4. `nativeRect`: 历史层真实可见区域、历史选择操作区、inline 导出预览区的 union。
5. `hitRect`: 气泡、历史选择操作区、inline 导出预览区、滚动条 / 必要滚动区域的真实命中区域。
6. 当前没有单独输出 `measuredRect` 字段；宿主直接读取真实 DOM rect 作为 native / hit 来源。后续如增加显式 payload，仍必须以 React 实际测量为准，禁止宿主按 CSS 设想值猜测。

规则：

1. history item 只在 `compactExportHistoryOpen === true` 时存在；无消息时仍输出 history 打开后的真实可见区域和 controls / preview 所需 geometry，但不输出空状态文案、空 pill 或空状态 hit region，不能退回旧导出提示或让桌面壳猜测空层尺寸。
2. history item 关闭后必须从 `surfaceItems` 移除。
3. history item 可以扩大 Electron compact window bounds；inline 导出预览展开时也只能通过 history item 扩展 bounds。
4. history item 不能参与聊天框本体 anchor 计算。
5. 聊天框本体 anchor 只看 `data-compact-geometry-item="capsule|input"` 的可见本体，不看 history / preview / toolFan / choice；当前真实 DOM 是 `.compact-chat-surface-shell` 包裹 `.compact-chat-surface-frame`，但 anchor 语义仍以 `capsule|input` 为准。
6. GalGame / ChoicePrompt 的 `choice` item z-order 高于 `history`。
7. 选项位于聊天框上方时直接覆盖 history，不让 history 推动选项或聊天框重排；选项位于下方时，history 不需要进入让位态。
8. 后续阶段中，图片拖拽的拖拽预览不作为长期 geometry item；只有实际可见的图片缩略图和 drop target 进入命中计算。
9. 选中态描边、check 角标或 glow 不应改变 history item 的布局尺寸。
10. 整条气泡拖拽的拖拽影子属于后续阶段临时 item，不参与聊天框本体 anchor，也不能长期扩大 Electron window bounds。
11. React 外层包裹容器如果只是布局辅助，必须 `pointer-events: none`；只有气泡、滚动区、历史选择操作区、inline 预览控件等真实可交互元素 `pointer-events: auto`。
12. 当前 geometry 采集会把普通 item 的元素 rect 同时作为 `nativeRect` 和 `hitRect`；history 已使用 `data-compact-geometry-hit-scope="children"`，不能退回只给外层打 `data-compact-geometry-owner="surface"` 的实现。
13. 宿主 geometry 只能使用真实可见和真实可交互 rect；不能因为 wrapper 使用 `height: 100%`、`max-height` 或透明占位，就把整块透明区域纳入 native hit region。
14. 历史层高度动态变化时，宿主必须重新读取实际 DOM rect 并同步 geometry；关闭或收起后要同步移除对应 rect。
15. 如果需要多个局部命中区域，应输出多个 history 子 item 或显式 `hitRect` 列表；不要把整个 history panel union 当成唯一 hitRect。
16. 用于调试或测试的 `data-compact-export-*` 也要遵守 compact gating；非 compact 表面不能残留会误导桌面壳或后续 geometry 采集的 compact 状态。
17. 当前代码没有把 `.compact-export-history-scroll` 整块注册为 hit region；气泡、controls、preview 等子节点才输出 hit rect，透明列表空白更接近穿透目标。后续若要让滚动条 / 必要滚动区域单独吃事件，必须继续以子 hit rect 或等价 payload 表达，不能退回整块透明 scroll wrapper。

## 与 GalGame / ChoicePrompt 的关系

这是本设计的关键点。

1. GalGame / ChoicePrompt 选项仍是更高优先级交互。
2. 选项层打开时：
   - 如果 placement 在上方，则显示在历史导出层上方。
   - 如果 placement 在下方，则不遮挡历史导出层，也不要求历史导出层视觉让位。
   - 可点击。
   - 不被历史导出层挡住。
   - 不被 inline 导出预览挡住。
   - 不推动历史导出层重排。
   - 不推动聊天框本体重新定位。
3. 历史导出层被盖住的部分不接收点击。
4. 选项关闭后，历史导出层保持原位置。
5. 如果空间不足，优先压缩历史导出层高度，而不是挤压选项层。
6. 选项层打开时，历史层可以继续保留滚动位置和选中状态，但不能抢焦点。
7. Inline 导出预览展开时也遵守同一规则：选项层高于预览，预览不能挡住或推动 GalGame / ChoicePrompt。
8. ChoicePrompt / GalGame 选项位于上方并覆盖历史层时，历史层应进入视觉让位状态，不重排、不改位置，但降低视觉干扰；选项位于下方时，历史层保持正常显示和交互。
9. 视觉让位状态建议给历史面板加类似 `under-choice-prompt` 的类：
   - `pointer-events: none`，让选项拥有唯一交互焦点。
   - 降低 opacity，例如约 0.25-0.4。
   - 可加轻微 blur，但必须保证退出选项后无残留样式。
   - transition 只影响 opacity / filter，不影响布局尺寸和 geometry anchor。

## 桌面端要求

桌面端必须遵守 NEKO-PC 是前端外壳、NEKO 是后端的边界。

1. 不新增后端协议。
2. 不在 NEKO-PC 里复制导出业务逻辑。
3. NEKO-PC 只消费页面 geometry 和现有导出能力。
4. Electron compact window bounds 需要包含 history item 的真实 rect。
5. setShape / native hit region 需要包含 history 的可交互区域。
6. history 关闭后，native hit region 也要移除。
7. history 打开和关闭不能影响最小化小球位置。
8. history 打开和关闭不能改变用户保存的紧凑聊天框位置。
9. history 的 native hit region 必须来自 React 实际测量的可交互 rect，不能使用透明 wrapper 的 CSS 尺寸猜测。
10. 历史滚动和 streaming 更新不能导致 Electron bounds 高频抖动；bounds 更新应跟随真实尺寸变化并做必要合并。
10.1. 后续要支持 history 内部非对话透明区穿透时，NEKO-PC 仍不能自行猜测气泡范围；必须由 NEKO 页面输出更细的 history 子 hit rect 或等价 payload。
11. 后续阶段中，图片拖到猫娘模型身上的 drop target 在桌面端应由 pet/avatar 层暴露，NEKO-PC 只桥接 drop 数据和现有发送链路。
12. 后续阶段中，跨窗口拖拽图片时，NEKO-PC 不能把内部路径或鉴权信息暴露给外部应用；如需临时文件，使用无敏感路径特征的临时文件名或内存传输。
13. 后续阶段中，整条气泡投递到猫娘模型时，NEKO-PC 仍只负责桥接 drop 事件；消息内容转换和发送语义必须留在 NEKO / React host 现有链路。
14. 后续阶段中，进入明确 `bubbleDrag` 后可临时扩大 ReactChat 透明窗口 bounds 来容纳拖拽影子；拖拽完成、弹回或发送特效结束后必须恢复到 geometry 真实范围。
15. NEKO-PC 不应复制导出选择、格式化、Markdown / Image 构建或消息转换逻辑；这些都属于 NEKO / React host / `app-chat-export.js` 能力。
16. 桌面端与网页端允许窗口、bounds、native hit 处理不同，但用户可见的历史打开、选择、预览、导出、ChoicePrompt 覆盖关系必须一致。

## 实施状态

基础内联历史 / 导出层已经落在当前代码中。后续实施时以本文、`home-compact-chat-mode-design.md`、当前代码和测试为准；历史实施计划文档不再作为必读入口。

当前基础能力包括：

1. Compact 下导出按钮 toggle inline history。
2. Full 下导出按钮继续打开原导出窗口。
3. History 直接消费 React `messages`，不继承 `MessageList` 的 50 条裁剪。
4. 气泡点击选择、全选、取消、反选。
5. Inline preview、复制、下载。
6. Composite geometry、ChoicePrompt / GalGame 上方覆盖时的视觉让位和点击让位。

尚未实施且仍属后续阶段：

1. 图片 / 表情包拖出或拖到猫娘模型。
2. 整条历史气泡拖到猫娘模型。
3. 拖拽影子、投递动画、跨窗口临时 bounds 扩展和恢复。

## 禁止方案

1. 禁止 compact 导出继续打开新页面。
2. 禁止把 full 导出预览窗口的大白面板直接塞到紧凑态。
3. 禁止在 React 里重写一套与 `app-chat-export.js` 不一致的导出文本提取。
4. 禁止让 history 参与聊天框本体位置锚点。
5. 禁止让 history 影响小球定位。
6. 禁止给 history 预留透明但吃事件的大区域。
7. 禁止让 history 挡住 GalGame / ChoicePrompt 选项。
8. 禁止为 compact inline 导出新增后端接口。
9. 禁止污染 full 模式导出窗口。
10. 禁止用单纯 `z-index` 宣称解决 Electron 裁切和命中问题。
11. 禁止继续用复选框作为紧凑历史层的主要选择交互。
12. 禁止让选中态只靠非常细微的透明度变化表达。
13. 禁止在历史选择操作区继续铺开 Markdown / Image / 图片样式 / 图片格式 / 复制 / 下载等细节按钮；这些只能进入 inline 导出预览。
14. 禁止 inline 导出预览维护第二份选择状态；它必须与历史气泡共用 `compactExportSelectedIds`。
15. 禁止“取消”关闭历史层；它只清空选中消息。
16. 禁止把任何历史内容拖拽能力混入第一阶段基础功能；图片、表情包、整条气泡拖拽都必须在基础功能稳定后单独实施。
17. 禁止上下翻动历史时触发气泡选择。
18. 禁止历史滚动透传成聊天框拖动、蓝线拖动或下方模型交互。
19. 禁止在 pointer up 阶段对已经判定为滚动或拖拽的操作补触发 click 选择。
20. 禁止拖拽失败后改变 `compactExportSelectedIds` 或 inline 导出预览选择状态。
21. 禁止 streaming 更新在用户主动向上滚动后继续强制贴底。
22. 禁止选择缺少稳定 key 的 `sending` 或其他未确认状态消息。
23. 禁止在当前 schema 未扩展前硬编码 `pending` / `retrying` 状态分支。
24. 禁止让 inline history 继承当前 `MessageList` 的 50 条展示裁剪。
25. 禁止透明 wrapper、`height: 100%` 或 `max-height` 占位进入 native hit region。
26. 禁止只靠 CSS `pointer-events` 声称解决桌面端 native hit；geometry collector 必须输出真实命中区域。
27. 禁止在 history 里复制一套与当前聊天气泡不一致的 message block 渲染逻辑。
28. 禁止 ChoicePrompt / GalGame 选项位于上方并覆盖 history / preview 时，history / preview 继续抢点击或保持同等视觉权重。
29. 禁止后续整条气泡拖拽用 `display: none` 或真实删除消息来模拟抽走效果。
30. 禁止拖拽合拢 / 弹回动效期间让浏览器自动滚动写入和动画同时争夺 `scrollTop`。
31. 禁止桌面端临时扩大拖拽 bounds 后不收回。
32. 禁止图片拖到猫娘身上时绕过现有发送/附件链路直接写新协议。
33. 禁止在 drag data 中暴露内部本地路径、token、系统用户名、后端缓存路径或不可公开 URL。
34. 禁止拖动整条气泡时真实删除历史消息；拖走只是视觉折叠。
35. 禁止松手后让气泡永久停留在猫娘附近；无论发送或弹回，都必须恢复到历史原位。
36. 禁止用当前 DOM 顺序猜测恢复位置；必须按消息 id / sortKey 回到原排序点。
37. 禁止整条气泡拖拽绕过现有发送链路。

## 参考方案

这些资料只作为交互方案参考，最终仍以当前 NEKO 代码和设计边界为准。

1. Virtuoso Message List scroll modifier：`https://virtuoso.dev/message-list/scroll-modifier/`。可参考 bottom anchored scroll、append message 和用户滚动位置保留策略。
2. Virtuoso Message List send messages tutorial：`https://virtuoso.dev/message-list/tutorial/send-messages/`。可参考发送消息后列表如何继续跟随底部。
3. Stream Chat React `MessageList`：`https://getstream.io/chat/docs/sdk/react/components/core-components/message_list/`。可参考消息列表消费当前 message state、通过自定义渲染扩展 UI 的方式。
4. Discord Social SDK chat history guidelines：`https://docs.discord.com/developers/discord-social-sdk/design-guidelines/chat-history`。可参考进入会话时优先显示最近上下文，而不是让用户先面对完整长历史。
5. Material Design selection pattern：`https://m1.material.io/patterns/selection.html`。其核心是 item selection 要让用户明确知道哪些项目已被选中；本设计采用“强调色描边 + check 角标”作为第一版推荐。
6. Apple Human Interface Guidelines drag and drop：`https://developer.apple.com/design/human-interface-guidelines/drag-and-drop`。其重点是拖拽目标和反馈要让用户能预测结果；本设计把图片 / 表情包 / 整条气泡拖拽统一放到后续阶段，再按该原则设计拖拽预览和投递反馈。
7. MDN `DataTransfer.setData()`：`https://developer.mozilla.org/en-US/docs/Web/API/DataTransfer/setData` 与 MDN drag operations：`https://developer.mozilla.org/docs/Web/API/HTML_Drag_and_Drop_API/Drag_operations`。它们是 Web 拖拽数据和自定义 drag image 的标准基础；本设计要求后续历史拖拽实现时再接入 URL / HTML / plain fallback 和 NEKO 内部自定义类型。
8. Slack workspace export：`https://slack.com/help/articles/201658943-Export-your-workspace-data`。可参考“导出是数据能力，展示和下载只是不同入口”的拆分思路；NEKO compact inline 不应复用 full 导出窗口 UI。
