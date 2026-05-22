# 首页紧凑态内联导出历史层实施方案

> 本文承载 `home-compact-chat-inline-export-history-design.md` 的实际实施、调整、桌面端对齐和验收方式。
> 本文的重点已经从“重新设计网页端”调整为：以网页端已经验收的展示效果、DOM 几何和交互逻辑为事实源，让 NEKO-PC 桌面端使用正确方法复现同一用户可见结果。
> 若本文与当前代码、测试或真实运行结果冲突，以当前代码、网页端真实表现、geometry snapshot 和可复现证据为准；先更新本文，再继续改代码。

## 文档定位

本文只回答三个问题：

1. 网页端已经实现的 inline history / preview / toolFan 表现应如何作为基线固化。
2. 桌面端应如何实际消费网页端输出，而不是复制网页端业务或样式公式。
3. 后续修复和验收应按什么顺序收口，避免再次把 history、preview、toolFan、choice 混进 compact base surface。

本文不重新定义功能目标。功能语义、交互规则、拖拽后续阶段和禁止方案仍以 `home-compact-chat-inline-export-history-design.md` 为准；UI 加宽、错落气泡、hover 工具轮盘等优化以 `home-compact-chat-inline-export-history-ui-optimization-design.md` 为准。

## 当前事实源

后续实施前必须先确认当前分支代码处于哪一种状态：

1. 如果当前分支已经包含网页端 inline history，则网页端真实 DOM、截图、Playwright/浏览器观察和 `window.__nekoGetCompactInteractionGeometry()` 是第一事实源。
2. 如果当前分支暂时没有 `CompactExportHistoryPanel` / `.compact-export-history-*` / `data-compact-export-*`，不要按旧失败实现重写；应先找到网页端已经验收版本或对应补丁，再按本文的合同恢复。
3. 旧实施里已经证明有问题的方案不能直接恢复，包括：
   - history / preview 外扩区域反向改写 base surface。
   - `history:native` 这种 bounds-only item 被回填成 hit rect。
   - NEKO-PC 用固定 CSS 或 selector 重新计算网页端 history 尺寸。
   - 输入态和展示态生成两套不等尺寸框体，导致切换抖动。
   - 打开 history / toolFan 改变保存位置、模型锚点或小球位置。

每次开始前先执行：

1. `git status --short`
2. `rg -n "CompactExportHistory|compact-export-history|compact-export-preview|data-compact-export" frontend/react-neko-chat/src static/app-react-chat-window.js`
3. `rg -n "data-compact-geometry-item|__nekoGetCompactInteractionGeometry|surfaceItems|hitRects" frontend/react-neko-chat/src static/app-react-chat-window.js ../N.E.K.O.-PC/src`

## 根合同

### 产品合同

1. NEKO 网页端是 UI、消息状态、选择状态、导出能力、DOM rect 和 compact geometry 的事实源。
2. NEKO-PC 是桌面外壳，只负责窗口 bounds、native hit region、avatar bounds、外部小球和窗口层级。
3. 桌面端最终用户可见效果必须与网页端一致；唯一允许差异是桌面端暂未实现的紧凑聊天框毛玻璃效果。
4. 桌面端不能实现消息选择、导出格式化、preview 尺寸公式、工具排序、hover 状态机或 history UI 业务。
5. 不新增 Python 后端接口，不修改消息 schema，不改变 full 导出窗口行为。

### Geometry 合同

1. `capsule` / `input` 是唯一 compact base surface anchor。
2. `dragHandle` 是 base hit item，但不决定 surface 尺寸语义。
3. `history` / `preview` / `choice` / `toolFan` 都是 extra island。
4. Extra island 可以扩展 BrowserWindow bounds 和真实 hit region，但不能参与：
   - base surface anchor。
   - 用户保存位置。
   - minimized ball 定位。
   - 默认模型底部向上约 `1/5` 的 surface 初始锚点。
5. `window.__nekoDesktopCompactLayout.surface` 只能表示 base surface，不能表示包含 history / preview / choice / toolFan 的 window union。
6. Bounds-only item 必须保持 `interactive: false` 和 `hitRect: null`；NEKO-PC 不能把缺省 hitRect 与显式 null 混为一谈。
7. 透明区域穿透以 geometry / native hit region 为准，不能只靠 CSS 透明或 `pointer-events` 自我安慰。

### 样式一致性合同

1. 网页端和桌面端必须使用同一套 React DOM、同一套构建后的 `neko-chat-window.css` / `neko-chat-window.iife.js` 和同一套状态语义。
2. NEKO-PC 只能注入桌面布局所需的 position / bounds / workArea / avatar bounds 信息；不能在 preload 或主进程里重写 history 宽度、高度、气泡样式、preview 样式、按钮排序、z-index 规则或选择态视觉。
3. 桌面端可接受的视觉差异只有当前明确的毛玻璃能力差异；除此之外，history 宽度、高度、滚动范围、preview 展开高度、气泡左右身份、选择描边 / check、controls 折叠、toolFan 位置和 ChoicePrompt / GalGame 覆盖关系都必须与网页端一致。
4. 桌面端 `electron-chat-window` 或类似 body class 只能用于平台必要适配，例如透明窗口、外部 ball 和可用的毛玻璃降级；不能让同一功能在桌面端变成另一套版式。
5. 外部 compact ball 属于 NEKO-PC 的窗口承载适配，用于避免 surface 与 ball 之间出现大透明命中区域；它不代表 NEKO 侧要新增一套不同的 compact 版式或业务状态。
6. 视觉尺寸比较以真实 DOM rect / screenshot 为准。不要用设计稿数值或 CSS 推断替代实际截图，尤其不要用固定 px 判定 history 是否“看起来够宽”。

### 防抖动合同

1. Extra island 的打开、关闭、滚动、streaming 文本增长、hover toolFan、choice above/below 都不能反向改写 base surface。
2. `setBounds` 只应响应真实 window union 变化；`setShape` 只应响应真实 hitRects 变化；页面 CSS 变量中的 `surface` 只应响应 base surface 变化。
3. Snapshot 去重必须覆盖会导致窗口可见变化的字段：
   - BrowserWindow `x/y/width/height`。
   - base surface `left/top/width/height`。
   - nativeRects hash。
   - hitRects hash。
   - `compactChoicePlacement`。
   - external ball bounds。
4. 如果只比较窗口 bounds 和 base surface，可能漏掉 hitRects 或 choice placement 变化；如果把 extra union 写进 base surface，又会引入 layout feedback loop。两者都不合格。
5. Geometry 采集和桌面 relayout 必须做 hash/debounce/RAF 合并。Streaming 文本每个 token 都触发 DOM 更新时，只有实际 rect 改变超过阈值才允许提交 native bounds / shape。
6. 关闭或切换状态时遇到 transient zero rect、未挂载 DOM、动画中间态时，应保持上一帧有效 base surface 或等待下一帧重新测量；不能把 0 rect 或半成品 rect 保存为用户位置。
7. 桌面端真实验收必须观察一段持续时间，而不是只看单帧截图。打开 history、preview、toolFan、choice 后至少观察数秒，确认 `setBounds` / `setShape` 不持续刷屏，窗口不肉眼抖动。

## 网页端基线固化

目标：先证明网页端已经实现的效果本身稳定、可测、可被桌面端消费。

需要确认的网页端表现：

1. compact 下“历史对话”按钮是开关，打开 / 关闭 inline history，不打开旧导出窗口。
2. full / non-compact 下保留 `window.appChatExport.open()` 的旧 full 导出窗口。
3. 无消息时 compact 仍打开 inline history 外壳，不调用旧导出空提示，也不显示顶部空状态文案 / 空 pill。
4. 历史消息最新在底部，打开时默认贴底。
5. 用户在底部时，新消息和 streaming 更新继续贴底。
6. 用户向上滚动后，streaming 更新不能强行拉回底部。
7. 点击气泡切换选择；链接、按钮、图片等内部控件不误触发整条气泡选择。
8. `sending` 或其他没有稳定 id 的消息默认不可选；当前 schema 只处理 `sending` / `sent` / `failed` / `streaming`，不要硬写不存在的 `pending` / `retrying`。
9. 点击“导出”在 inline panel 内展开 preview，不开新窗口。
10. preview 与 history 使用同一份选择集合。
11. 关闭 history 同时关闭 preview；清空消息时 history 可保持打开，但不显示空状态提示，同时清空选择、关闭 preview。
12. ChoicePrompt / GalGame 位于上方时覆盖 history；位于下方时不要求 history 视觉让位。
13. history / preview / toolFan 打开和关闭不改变 compact surface 本体位置。
14. history、preview、toolFan 和 choice 的过渡只允许影响 opacity / transform / filter 这类视觉属性；不得通过改变 base frame 尺寸、margin 或 anchor 位置制造展开效果。

网页端代码检查重点：

1. React 状态只表达 compact 专属 UI：
   - `compactExportHistoryOpen`
   - `compactExportPreviewOpen`
   - `compactExportSelectedIds`
   - `compactExportAutoScrollToBottom`
2. 导出能力从 `static/app-chat-export.js` 复用；React 不重新实现 Markdown / Image 构建、复制和下载。
3. 历史消息渲染复用现有 message block 能力，不用字符串拼接替代 text / image / link / status / buttonGroup。
4. `MessageList` 的 50 条裁剪和自动贴底逻辑不能直接复用为 inline history。
5. 新增用户可见文案同步 8 个 locale。

完成标准：

1. 网页端截图或浏览器观察确认上述表现。
2. `window.__nekoGetCompactInteractionGeometry()` 中能看到 history / preview / toolFan / choice 的真实 DOM rect。
3. `App.test.tsx` 覆盖 toggle、无消息打开且无空状态提示、选择、不可选消息、preview、滚动贴底、choice above/below。

## 网页端 Geometry 输出

目标：让桌面端不用猜尺寸和业务状态，只消费网页端真实输出。

建议输出结构：

1. `history:native`
   - `kind: 'history'`
   - `interactive: false`
   - `hitRect: null`
   - 只用于 BrowserWindow bounds union。
2. `history:scroll`
   - 只作为视觉滚动容器和内容裁剪容器。
   - 不输出 `data-compact-hit-region`，不进入桌面端 hitRects。
   - 空白滚动槽必须穿透；滚轮 / 触控板滚动只在指针位于真实可见气泡或控件上时由浏览器滚动链路处理。
3. `history:bubble:*`
   - 当前最终空白穿透方案的消息命中来源。
   - 气泡选择、链接、按钮、图片命中都应来自实际气泡或内部控件 rect。
4. `history:controls`
   - 全选 / 取消 / 反选 / 导出 / 折叠控件真实 hit。
5. `history:preview`
   - preview 展开后的可见区域与可交互控件。
6. `toolFan:native`
   - bounds-only reserve，不进入 hit。
7. `toolFan:button:*`
   - 每个真实可点击按钮自己的 hit rect。

规则：

1. history 关闭后，所有 history / preview item 必须从 geometry 中消失。
2. preview 展开时仍属于 history extra island，不参与 base anchor。
3. history 加宽、气泡错落、preview 展开后的 rect 必须来自最终 DOM 测量结果。
4. 禁止宿主按 CSS 变量、设计稿宽度或 fixed px 推导 history 尺寸。
5. `hitRect: null` 表示显式不可点击；缺省 `hitRect` 才允许按 nativeRect fallback。
6. 网页端不能继续用整块 scroll 区作为滚动 hit；透明空白穿透完成后，history 的 hit item 只能来自真实可见气泡、controls、preview region，以及后续明确设计过的“必要滚动命中区域”。
7. “必要滚动命中区域”不是整块透明 `.compact-export-history-scroll`。如果后续要保留空白处滚轮 / 滚动条拖拽，只能提供窄滚动条、可见滚动 affordance，或其它不会覆盖大面积透明空白的显式 hit item。
8. 当前网页端把 scroll 整块从 hit 中移除是为了满足透明区域点击穿透；这不是桌面端已实现问题，而是后续 NEKO-PC 接 setShape / hitRects 时必须保持的输入约束。

完成标准：

1. geometry snapshot 能区分 base anchor、bounds-only item 和真实 hit item。
2. history / preview 不进入 `surface` base anchor。
3. 透明空白没有被错误加入 hitRects；`history:scroll` 不应作为整块 scroll rect 出现在 hitRects 中。
4. 如果出现滚动命中 item，它必须是可解释的最小命中区域，并在 snapshot 中能和气泡 / controls / preview 区分。

## NEKO-PC 实际使用方法

目标：NEKO-PC 使用网页端 geometry 和已有桌面 compact 管线复现效果，不重写产品逻辑。

桌面端正确方法：

1. `preload-pet.js` 继续提供 avatar screen bounds。
2. `preload-chat-react.js` 读取页面 compact geometry，把 page rect 转成 screen rect。
3. `desktop-compact-layout.js` 只做分类、clamp、bounds union、choice relocation 和 hit rect 归一化。
4. BrowserWindow bounds 来自：
   - base surface native rect。
   - bounds-only extra native rect。
   - interactive extra native rect。
   - toolFan reserve rect。
5. setShape / native input region 来自 hitRects。
6. external compact ball 继续由独立 ball window 承载，位置只看 avatar bounds 和 workArea clamp。
7. 窗口层级继续走现有 window manager / top coordinator。
8. history 的透明滚动槽不能因为桌面端需要滚动而被整体加入 hitRects；桌面端只能消费 NEKO 输出的真实 hit item。
9. 如果 NEKO 后续输出必要滚动命中，NEKO-PC 只做坐标转换和 setShape 应用，不在桌面壳里猜测 scroll 高度、补全整块透明区域或放大为 history union。

桌面端禁止：

1. 在 NEKO-PC 写 history 宽度、高度、preview 高度、气泡错落或导出选择公式。
2. 让 history / preview / toolFan 参与 saved surface position。
3. 让 `history:native` 被纳入 hitRects。
4. 把整块 `.compact-export-history-scroll` 或 history union 当作点击 / 滚动 hitRect。
5. 为了修桌面裁切，把网页端 history 塞进 capsule/input。
6. 因为桌面透明窗口限制，反向修改网页端产品目标。
7. 用大透明 BrowserWindow 覆盖 surface 与 ball 之间的空白。

桌面端代码检查重点：

1. `classifyDesktopCompactItems()`：
   - `capsule` / `input` 是 anchor。
   - `dragHandle` 只属于 base hit。
   - `history` / `preview` / `choice` / `toolFan` 是 extra。
2. `normalizeDesktopCompactSurfaceItems()`：
   - 显式 `hitRect: null` 保持 null。
   - 只有缺省 hitRect 的 interactive item 才 fallback 到 nativeRect。
3. `buildDesktopCompactLayoutRects()`：
   - `surfaceUnion` 只来自 base anchor。
   - `nativeRects` 可包含 extra native。
   - `hitRects` 只包含真实 hit。
   - choice relocation 只处理 `choice`，不能移动 history / preview。
   - toolFan reserve 只用于稳定 window bounds，不能成为 hit rect，也不能改变 base surface。
4. `applyDesktopCompactLayoutToPage()`：
   - `window.__nekoDesktopCompactLayout.surface` 仍是 base surface。
   - `compactChoicePlacement` 只表达 choice 位置修正。
5. `applyDesktopCompactNativeRegion()`：
   - setShape 做 snapshot 去重。
   - history / toolFan 打开关闭不造成高频抖动。
   - history 透明槽不进入 shape；只有气泡、controls、preview 和显式必要滚动命中进入 shape。
6. `activateDesktopCompactWindow()` / relayout 调度：
   - 不把 extra island union 保存为 expanded/full 窗口 bounds。
   - 不把 transient rect 写入 compact surface localStorage。
   - 不因 hover、streaming、scrollTop 变化连续触发 setBounds。
   - 如果一次 setBounds 后页面需要重新测量，下一轮必须收敛到同一 base surface，而不是在两个窗口尺寸之间来回跳。

完成标准：

1. 桌面端 history / preview 可见且不被裁切。
2. 可点击区域能点击；透明空白不长期遮挡模型或页面。
3. 历史消息可在真实可交互区域内滚动；如果需要透明空白处滚动，必须先由 NEKO 输出最小必要滚动 hit item，不能由 NEKO-PC 把整块 scroll 加回 hit。
4. 打开 / 关闭 history、preview、toolFan 不改变保存的 compact surface 位置。
5. minimized ball 不随 surface 拖拽、不随 history 加宽移动。

## 实施顺序

### 阶段 0：证据对齐

目标：确认当前分支和网页端已验收效果是否一致。

检查：

1. `git status --short`
2. 网页端真实打开 compact，截图记录：
   - default。
   - input。
   - history 无消息打开状态。
   - 短历史。
   - 长历史。
   - selected / unselected。
   - preview。
   - choice above / below。
   - toolFan。
3. 在浏览器控制台记录 `window.__nekoGetCompactInteractionGeometry()`。

完成标准：

1. 能明确当前代码是“已包含网页端实现”还是“需要先恢复网页端实现”。
2. 后续桌面端只以这份网页端截图和 geometry snapshot 对齐。

### 阶段 1：网页端缺口修正

仅当当前分支缺少网页端已验收效果时执行。

要求：

1. 先恢复网页端已验收的 DOM、状态和样式，不重新发明 UI。
2. 先通过网页端验收，再进入 NEKO-PC。
3. 任何新文案同步 8 个 locale。
4. 修改 `frontend/react-neko-chat/src/*` 后运行 `bash build_frontend.sh`，确认 `static/react/neko-chat/*` 产物更新。

完成标准：

1. 网页端显示效果与已验收版本一致。
2. geometry snapshot 满足本文合同。
3. 网页端视觉和已验收版本一致后，才允许进入桌面端；桌面端问题不能通过改网页端目标样式来掩盖。

### 阶段 2：NEKO geometry 收口

目标：保证网页端输出能被桌面端稳定消费。

问题判定：

1. 打开 history 后紧凑聊天框偶发闪烁、位置晃动，优先按 geometry / desktop feedback 问题处理，不按焦点样式问题处理。
2. 焦点样式只影响键盘可见 focus ring，不应改变 DOM rect、BrowserWindow bounds、base surface anchor 或 saved position；如果出现位置抖动，先查 geometry 输出和 NEKO-PC 消费。
3. 阶段 2 的职责是让 NEKO 输出稳定的 base anchor、extra native rect 和真实 hit rect；不在阶段 2 里用桌面端专属样式或焦点逻辑遮盖抖动。

修改范围：

1. `frontend/react-neko-chat/src/*`
2. `static/app-react-chat-window.js`
3. `frontend/react-neko-chat/src/App.test.tsx`

完成标准：

1. base anchor、extra island、bounds-only、hit item 在测试里可区分。
2. `history:native` 不进入 hitRects。
3. toolFan 每个可见可点击按钮有自己的 hit rect。
4. 关闭状态不残留 history / preview geometry。
5. history / preview / toolFan / choice 的 DOM rect 在 default、input、streaming、hover 和 choice 切换中保持可解释，不出现 transient wrapper 大矩形误入 hitRects。
6. geometry snapshot 至少记录 base surface、nativeRects、hitRects、choice placement 和 external ball；不能只记录 `surfaceUnion`。
7. 打开 / 关闭 history 时，NEKO 输出的 base surface rect 不随 history native union 改变；history 只能作为 extra island 扩展 native bounds。
8. 上方历史层透明区域是已知后续收口项：不能长期作为 click / pointer hit，后续必须把可命中范围继续下沉到气泡、controls、preview 或显式最小必要滚动命中。

阶段 2 根因记录（2026-05-22）：

1. 打开 history 后紧凑聊天框偶发闪烁 / 位置晃动的已确认根因不是焦点样式，也不是 history 未接入桌面端，而是桌面端 compact BrowserWindow bounds 与 history CSS 尺寸形成反馈环：
   - NEKO-PC 根据 history DOM rect 扩大 BrowserWindow。
   - history 原 CSS 使用 `63vh` / `78vh` 作为高度上限。
   - Electron compact 下 `vh` 来自当前 BrowserWindow 高度。
   - BrowserWindow 高度变大后，history 的 `vh` 上限也变大，下一帧又输出新的 history rect，导致多帧 relayout / setBounds 收敛，肉眼表现为闪烁或晃动。
2. 阶段 2 修复方式：
   - NEKO 读取 `window.__nekoDesktopCompactLayout.workArea`，写入 `--compact-desktop-workarea-width` / `--compact-desktop-workarea-height`。
   - 桌面端 history 的高度和宽度上限使用稳定 workArea 变量，不再使用当前 BrowserWindow 的 `vh` / `vw` 作为收敛输入。
   - 网页端普通浏览器仍保留原 viewport 约束。
3. 回归要求：
   - 静态测试必须保证桌面端 history CSS block 不再含 `vh` / `vw`。
   - 真实桌面端仍需观察打开 history 后 `setBounds` 是否一到两帧内收敛，不能持续刷屏。

### 阶段 3：NEKO-PC 消费收口

目标：让桌面端实际复现网页端已经实现的效果。

修改范围：

1. `../N.E.K.O.-PC/src/desktop-compact-layout.js`
2. `../N.E.K.O.-PC/src/preload-chat-react.js`
3. `../N.E.K.O.-PC/test/desktop-compact-layout-contract.test.js`

完成标准：

1. 契约测试覆盖：
   - bounds-only history。
   - 显式 `hitRect: null` 与缺省 hitRect 的区别。
   - history / preview / toolFan 不参与 base anchor。
   - choice relocation 不影响 history。
   - input/default base surface invariant。
   - toolFan reserve 只扩 bounds，不进入 hit。
2. 桌面真实运行中：
   - history / preview 不裁切。
   - hit region 覆盖真实控件。
   - 透明区域不挡后方，尤其是 history 上方 / 气泡外透明区域点击应穿透。
   - 打开关闭不抖动。
   - 网页端与桌面端截图中 history 宽度、高度、preview 展开高度、气泡选择态、controls、toolFan 和 choice 覆盖关系一致。
   - 连续观察期间 setBounds / setShape 不重复提交相同或来回切换的 rect。

### 阶段 4：真实运行矩阵

必须同时检查网页端和桌面端：

1. compact default。
2. compact input。
3. history 无消息打开状态。
4. history 短历史。
5. history 长历史。
6. history streaming 更新。
7. history selected / unselected。
8. preview 展开 / 返回。
9. controls 展开 / 折叠。
10. toolFan hover / click / Escape。
11. toolFan 最左 / 最右淡出按钮。
12. ChoicePrompt above。
13. ChoicePrompt below。
14. 模型移动。
15. 用户拖动保存 compact 位置。
16. 无用户保存位置时默认模型底部向上约 `1/5`。
17. minimized ball 点击、显示、隐藏和恢复。
18. 桌面端和网页端同场景截图对照。
19. 同一场景持续观察至少数秒，记录 geometry / setBounds / setShape 是否收敛。

桌面端额外检查：

1. 打开 / 关闭 history 不抖动。
2. 打开 / 关闭 preview 不抖动。
3. 打开 / 关闭 toolFan 不抖动。
4. input/default 切换不抖动。
5. history 加宽后不改变 base surface 保存位置。
6. history / preview / toolFan 不被模型压住。
7. history 非对话透明区域不长期遮挡后方。
8. hover 打开 toolFan 后 pointer 从按钮移到轮盘不造成窗口 bounds 来回跳。
9. streaming 期间 history 文本增长不造成 BrowserWindow 连续抽动；只有高度真实变化时才允许有限次数扩窗。
10. ChoicePrompt 从 below/above 切换时只影响 choice item 和 placement，不推动 base surface 或 history 重排。
11. 切回 full / minimized 后恢复原窗口 bounds、shape、external ball 和 ignore-mouse 状态。

完成标准：

1. 除桌面端毛玻璃外，用户可见表现与网页端一致。
2. 收口说明必须附网页端截图 / geometry snapshot 和桌面端截图 / geometry snapshot。
3. 收口说明必须写明是否观察到 setBounds / setShape 抖动；若有抖动，不能标记为完成。

### 阶段 5：真实导出动作

目标：Inline preview 的复制 / 下载复用 `static/app-chat-export.js` 无窗口能力。

要求：

1. 选择状态来自 `compactExportSelectedIds`。
2. 无选中时禁用最终复制 / 下载动作，不默认导出全部。
3. Markdown / Image、图片样式、图片格式语义与 full 导出一致。
4. Compact preview 不出现 full 导出窗口的大白面板和 `Open In Window`。
5. Full 导出窗口仍可正常打开。

完成标准：

1. 网页端和桌面端复制 / 下载按钮都可点。
2. 不打开新窗口。
3. 不漏点到模型。

## UI 优化接入顺序

这些只在基础 history / preview / desktop geometry 稳定后执行：

1. History 按 compact 本体比例加宽，受 viewport / workArea 上限保护。
2. 历史气泡使用稳定 token 做轻微错落，不使用 `Math.random()`。
3. Hover-capable 设备上工具轮盘 hover / focus 展开，touch 保留 click。
4. Escape 关闭 toolFan。
5. 淡出按钮只要视觉可见且 action 可用，就必须可点击、可聚焦并进入 geometry hit。
6. 常用按钮排序使用静态稳定规则，不写使用统计、不写后端、不写跨会话偏好。

UI 优化完成标准以 `home-compact-chat-inline-export-history-ui-optimization-design.md` 为准。

## 后续阶段：历史内容拖拽

只有基础 history / preview / export 在网页端和桌面端都稳定后，才实施拖拽。

范围：

1. 图片 / 表情包拖到外部保存。
2. 图片 / 表情包拖到猫娘模型上作为发送信息。
3. 整条聊天气泡拖到猫娘模型上作为重新发送消息。

要求：

1. 拖拽时不真实删除消息数据。
2. 列表内用 animation wrapper 做视觉合拢和回弹。
3. 命中模型后播放发送给猫娘的小动画，然后恢复原位。
4. 未命中模型时弹回原位。
5. 跨窗口拖拽不能暴露本地绝对路径。
6. NEKO-PC 只桥接 drop / window bounds，不复制消息转换业务。

## 验证命令

NEKO：

1. `git status --short`
2. `git diff --check`
3. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx`
4. `npm --prefix frontend/react-neko-chat run typecheck`
5. `bash build_frontend.sh`
6. `node --check static/app-react-chat-window.js`

NEKO-PC：

1. `git -C /Users/tonnodoubt/N.E.K.O.-PC status --short`
2. `git -C /Users/tonnodoubt/N.E.K.O.-PC diff --check`
3. `node --test /Users/tonnodoubt/N.E.K.O.-PC/test/desktop-compact-layout-contract.test.js`

真实运行：

1. 网页端截图 / geometry snapshot。
2. 桌面端截图 / geometry snapshot。
3. 对照 `home-compact-chat-inline-export-history-design.md` 和 `home-compact-chat-inline-export-history-ui-optimization-design.md` 逐项收口。

## 收口说明模板

每次完成一轮实现或修复时，说明必须包含：

1. 本轮只改了哪些文件。
2. 网页端表现是否与已验收效果一致。
3. 桌面端是否只消费 geometry，是否没有复制业务逻辑。
4. base surface、history、preview、choice、toolFan 的 geometry snapshot 结论。
5. 跑过哪些测试和真实运行检查。
6. 未验证范围和剩余风险。
