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
13. 每一步完成后都要按网页端实际表现对照桌面端表现；除桌面端暂未实现的紧凑聊天框毛玻璃效果外，history、preview、choice、toolFan、输入态、展示态、滚动、选择、命中和窗口稳定性都必须达到同一用户可见结果。
14. 如果桌面端表现和网页端不一致，优先检查结构合同和 geometry 消费；不要先用桌面端专属视觉补丁把不一致遮住。

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
16. 当前 NEKO-PC 的高危点在 `getDesktopCompactGeometryScreenItems()` 和 `buildDesktopCompactWindowLayout()`：
   - `item.hitRect` 缺失时会回填 `nativeRect`。
   - `adjustedExtra` 也存在 `hitRect || nativeRect` 回填。
   - 对 `history:native` 这类 composite native union item，必须保持 `hitRect: null`，否则透明历史外壳会变成大面积可点击遮罩。
17. 当前 history 在 React root / `.app-shell` 内挂载，不是 body portal。若要改成 body portal，必须同时证明：
   - 网页端显示不变。
   - NEKO collector 能采集到 body portal history。
   - 桌面端输入态 / 展示态 / preview / choice 组合不抖。
   没有这些证据时，不要把 body portal 当成默认修复。
18. 桌面端历史层高度不能由 NEKO-PC 复刻 CSS 公式或按 workArea 猜；NEKO 页面必须输出真实 DOM rect，NEKO-PC 只能消费。

## 当前静态代码设计检查结论

本节来自对当前 NEKO / NEKO-PC 代码的静态检查，只能作为第 6 步真实运行和模拟验证的输入；不能替代桌面端启动截图、geometry snapshot 和实际交互验收。

已检查文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
3. `frontend/react-neko-chat/src/styles.css`
4. `static/app-react-chat-window.js`
5. `/Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js`

静态结论：

1. NEKO 侧当前结构基本符合“history 是 extra island”的方向：
   - `CompactExportHistoryPanel` 输出 `data-compact-geometry-item="history"`。
   - 外层有 `data-compact-geometry-hit-scope="children"`。
   - scroll / controls / preview 子区域有 `data-compact-hit-region="true"`。
   - history 当前挂在 React root 内，不是 body portal。
2. NEKO 侧仍有需要在第 7 步确认和可能加固的点：
   - 长历史的贴底 / 顶部可达必须确认不能被内容布局方式破坏。
   - history / preview 高度要在 input、default、choice、toolFan 组合下保持同一比例策略。
   - choice 位于下方时不得触发 history 让位。
3. NEKO-PC 侧当前高危点明确存在：
   - `getDesktopCompactGeometryScreenItems()` 中 `item.hitRect` 缺失时会回填 `nativeRect`。
   - `buildDesktopCompactWindowLayout()` 的 extra item 返回中仍存在 `hitRect || nativeRect`。
   - 对 `history:native` 这类 bounds-only item，这两处都可能把透明外壳变成 native hit region。
4. NEKO-PC 侧当前结构表达仍偏混合：
   - baseItems / extraItems 已经有初步区分。
   - 但 extra native / extra hit、bounds-only / hit-capable、choice 特殊重定位之间还没有形成足够明确的中间结构。
   - 第 8 步如果继续补条件，很容易再次出现“右侧展开按钮抖动”“preview 被压缩”“history 透明区吃点击”的补丁叠补丁问题。
5. 当前右侧展开按钮 / toolFan 是 body portal extra island，history 是 root 内 extra island，choice 是 body portal extra island；第 6 步必须真实模拟这些不同挂载方式在同一桌面布局算法里的组合，而不是只测 history 单独打开。
6. NEKO-PC 与 NEKO 页面之间存在 layout feedback loop：
   - NEKO-PC 计算 `window.__nekoDesktopCompactLayout` 并触发 `neko:desktop-compact-layout-change`。
   - `static/app-react-chat-window.js` 会把 compact surface 回写到 `--compact-surface-left/top/width/height`。
   - history、choice、toolFan 等 extra island 又依赖这些 CSS 变量生成新的 DOM rect。
   - preload 下一帧再采集 geometry 并计算 setBounds / setShape。
   - 如果 base surface、extra island、window bounds、hitRects 没有分层去重，就会形成“setBounds -> CSS 变量 -> geometry -> setBounds”的往返抖动。

必须据此处理的代码设计问题：

1. 在 NEKO-PC 中明确保存 bounds-only 和 hit-capable item 的差异，不让 `hitRect: null` 丢失。
2. 在 NEKO-PC 中明确 base surface 与 extra island 的差异，不让 history / preview / toolFan / choice 改变保存位置。
3. 在 NEKO 中确保 history / preview 的 DOM rect 是桌面端可消费的真实尺寸，不把视觉尺寸藏在无法采集的 wrapper 或被 overflow 裁切的层里。
4. 在 NEKO-PC 与 NEKO 页面之间切断 extra island 对 base surface 的反馈：extra 只能跟随 base delta，不能反向改变 `--compact-surface-*` 或 saved position。
5. 对每个结构调整都要同时验证网页端和桌面端；不能只凭单端测试通过就收口。

## 第 6 步以后实施方案总设计

第 6 步以后不再按“发现一个视觉问题补一个条件”的方式推进，而是按 geometry 合同和桌面窗口结构重做闭环。目标是让 NEKO 页面继续作为唯一 UI / 状态 / 尺寸真相，NEKO-PC 只负责把页面输出的真实 geometry 转成透明窗口 bounds、native hit region 和桌面层级。

### 总体执行顺序

1. 先做第 6 步诊断：采集网页端和桌面端同场景 geometry snapshot，确认问题落在 NEKO 输出、NEKO-PC 消费，还是二者合同不一致。
2. 再做第 7 步 NEKO 合同加固：确保 history / preview / controls / choice / toolFan 的 DOM rect、hit rect 和状态标记真实、稳定、可采集。
3. 再做第 8 步 NEKO-PC 消费重构：把 base surface、extra island、bounds-only、hit-capable、choice relocation 拆成明确中间结构。
4. 再做第 9 步测试收口：网页端行为测试和 NEKO-PC 契约测试一起覆盖，不允许只测网页端。
5. 最后才进入第 10 / 11 步导出能力无窗口化和 inline preview 接真实导出能力。

### NEKO 侧设计

1. `CompactExportHistoryPanel` 继续作为 compact surface extra island，不默认改 body portal。
   - 当前 history 挂在 React root 内，`collectCompactSurfaceGeometryItems()` 已能采集 root 内 history。
   - toolFan / choice 是 body portal，是已有例外；history 是否需要 portal 必须由第 6 步真实证据证明，不能先假定。
2. history 的外层只表达可见 native union，不表达点击命中。
   - `history:native` 必须保持 `interactive: false`、`hitRect: null`。
   - `history:scroll`、`history:controls`、`history:preview` 才是真实可点击区域。
3. history / preview 尺寸由网页端 CSS 和真实 DOM rect 决定。
   - NEKO-PC 不能复制高度公式。
   - NEKO-PC 不能注入 `--compact-history-slot-height` 这类反向尺寸变量。
   - 后续紧凑聊天框变长 / 变宽时，history 跟随 compact 本体尺寸比例变化，仍由 NEKO 输出真实 rect。
4. ChoicePrompt 的关系只在 NEKO 侧表达清楚：
   - choice 位于上方时，history 视觉让位并禁用 history 交互。
   - choice 位于下方时，history 不淡化、不禁用、不重排。
   - NEKO-PC 只消费 choice 自己的 rect 和 placement，不把 choice 逻辑套给 history。
5. history 的 empty、short、long、streaming、selected、preview-open、controls-collapsed 状态都要输出同一类 geometry 合同；不能某个状态退回旧导出 toast、隐藏 native rect 或只剩不可见 wrapper。

### NEKO-PC 侧设计

NEKO-PC 需要把当前混在一起的 compact layout 逻辑整理成下面这条清晰管线。可以仍放在 `src/preload-chat-react.js`，但函数职责必须拆开。

1. `readDesktopCompactPageGeometry()`
   - 只读取 `window.__nekoGetCompactInteractionGeometry()`。
   - 不在这里推导 history 高度、preview 高度或业务状态。
2. `normalizeDesktopCompactSurfaceItems(windowBounds)`
   - 把 page rect 转 screen rect。
   - 保留 `id`、`kind`、`interactive`、`hitRegionKind`、`nativeScreenRect`、`hitScreenRect`。
   - 显式 `hitRect: null` 必须保留为 `hitScreenRect: null`，不能回填 native rect。
   - 必须用 `Object.prototype.hasOwnProperty.call(item, 'hitRect')` 或等价方式区分“页面显式给了 `hitRect: null`”和“旧普通 item 没有 `hitRect` 字段”。不能只写 `item.hitRect ? ... : nativeRect`。
   - 只有普通 item 没有 composite 语义且 `interactive !== false` 时，才允许用 native rect 作为 fallback hit rect。
3. `classifyDesktopCompactItems(items)`
   - `baseAnchorItems`: 只允许 `kind === 'capsule' || kind === 'input'`。
   - `baseHitItems`: capsule / input / dragHandle。
   - `extraBoundsItems`: history:native、history children、preview、choice、toolFan 等所有 extra native rect。
   - `extraHitItems`: 只有真实 hit rect 的 extra item。
   - `choiceItems`: 只包含 choice。
   - `boundsOnlyItems`: 例如 `history:native`。
4. `resolveDesktopCompactBaseSurface(...)`
   - 只用 base anchor 或 fallback 计算聊天框本体位置。
   - 只用 base surface 读写 saved position。
   - 用户拖动保存位置后，history / preview / toolFan / choice 不能覆盖这个位置。
5. `translateDesktopCompactExtraIslands(...)`
   - base surface 被 clamped 或恢复到 saved position 时，extra island 只按同一个 delta 平移。
   - extra island 不反过来推动 base surface。
6. `resolveDesktopCompactChoicePlacement(...)`
   - 只处理 choice。
   - 只决定 choice above / below 和 choice 自己的 rect。
   - 不处理 history / preview 空间不足，不移动聊天框本体。
7. `buildDesktopCompactNativeBounds(...)`
   - window bounds 由 base native rect + extra native rect union 得出。
   - history:native 可以进入 bounds，保证不裁切。
   - bounds 变化要做 snapshot 去重，避免打开右侧展开按钮 / toolFan 后 setBounds 往返抖动。
8. `buildDesktopCompactHitRects(...)`
   - setShape / hit region 只来自 base hit rect + extra hit rect。
   - bounds-only item 永远不进 hitRects。
   - 如果没有 hitRects，才使用 1px 安全占位，不能用整个 window bounds。
9. `applyDesktopCompactLayout(...)`
   - 只在 windowBounds、surface、hitRects、choicePlacement 的 snapshot 真实变化时提交。
   - 不把 extra union 写入 `window.__nekoDesktopCompactLayout.surface`。
   - 不让 relayout 事件把网页端 CSS 尺寸重新算成桌面端猜测尺寸。
   - `window.__nekoDesktopCompactLayout.surface` 只能来自 base surface，不能来自 window union；页面侧 `--compact-surface-*` 只能反映聊天框本体，不反映 history / preview / toolFan / choice 的外扩区域。

### 必须解决的结构问题

1. 透明区域挡点击：根因是 bounds-only item 被回填成 hit rect。修复点在 normalize / build hitRects，不在 CSS 上扩大或缩小透明容器。
2. history / preview 被压缩：根因通常是桌面端没有消费 NEKO 输出的真实 native rect，或把窗口 bounds / surface rect 混为一个尺寸。修复点在 bounds union 和 surface 分离，不在 NEKO-PC 写高度公式。
3. 打开右侧展开按钮或 toolFan 抖动：根因通常是 extra island 参与 base anchor、保存位置或反复触发 bounds / layout 互相校正。修复点在 item 分类、snapshot 去重和 saved position 输入。
4. 拖动后回到模型头部：根因通常是 saved position 读取了 window union 或 avatar bounds 覆盖了用户保存位置。修复点是 `saveDesktopCompactSurfacePosition()` 只接收 base surface rect。
5. ChoicePrompt 表现不一致：根因通常是 choice relocation 和 history layout 相互污染。修复点是 choice 独立分支，只改 choice rect 和 `compactChoicePlacement`。
6. 展开按钮 / preview / history 打开后窗口剧烈抖动：更深层根因通常是 feedback loop 没有稳定：
   - NEKO-PC setBounds 改变 window bounds。
   - 页面 layout change 回写 `--compact-surface-*`。
   - extra island 依赖 CSS 变量产生新 rect。
   - preload 再把新 rect union 后 setBounds。
   修复点不是加延迟，而是让 `window.__nekoDesktopCompactLayout.surface` 永远只表示 base surface，并让 setBounds / setShape snapshot 区分 native bounds、hitRects、choicePlacement 和 base surface。

### 真实模拟与验收设计

第 6 步必须拿同一套场景分别看网页端和桌面端，不再用“网页端看着正常”推断桌面端。

1. 每个场景先采集 NEKO 输出：
   - `window.__nekoGetCompactInteractionGeometry()`
   - `surfaceItems`
   - history / preview / choice / toolFan 的 `nativeRect`、`hitRect`、`interactive`、`hitRegionKind`
2. 再把同一份 `surfaceItems` 喂给 NEKO-PC 的纯布局函数或等价脚本模拟：
   - 输出 base surface。
   - 输出 window bounds。
   - 输出 native rect union。
   - 输出 hitRects。
   - 输出 saved position 是否会变化。
   - 当前 `preload-chat-react.js` 是闭包式脚本，不是天然可 import 的模块；如果测试无法直接调用内部函数，先把 layout 计算抽成可测试纯函数，或在 NEKO-PC 内建立等价 fixture runner。不要因为函数不方便测就跳过模拟。
   - 输出下一帧页面将收到的 `window.__nekoDesktopCompactLayout.surface`，确认它与 base surface 一致，不包含 history / preview / toolFan / choice 外扩。
3. 最后真实启动 NEKO-PC 截图验证：
   - 展示态 history。
   - 输入态 history。
   - 长历史。
   - 无消息空状态。
   - preview 展开。
   - toolFan / 右侧展开按钮打开。
   - choice above。
   - choice below。
4. 判定标准：
   - 除桌面端暂未实现的紧凑聊天框毛玻璃外，用户可见结果必须和网页端一致。
   - history 高度、preview 高度、滚动范围、选择态、按钮状态、choice 覆盖关系都要一致。
   - 透明空白区域必须能点到后方。
   - history / preview / toolFan / choice 打开关闭不改变用户保存的聊天框位置。
   - 打开右侧展开按钮不触发窗口剧烈抖动。

### 代码设计验收

实施后要能从代码结构上直接看出下面事实，而不是靠注释解释：

1. 哪些 item 是 base surface。
2. 哪些 item 只扩展 window bounds。
3. 哪些 item 可以进入 native hit region。
4. 哪些 item 会参与 saved position。
5. 哪些逻辑只对 choice 生效。
6. 哪些逻辑只对 toolFan / 右侧展开按钮生效。
7. history / preview 的宽高没有出现在 NEKO-PC 的硬编码公式里。
8. `hitRect: null` 不会在任何后续阶段被 `|| nativeRect` 吃掉。
9. 测试或模拟里能直接覆盖 “显式 `hitRect: null`” 与 “缺省 `hitRect` 字段” 两种输入；这两者在 NEKO-PC 里必须得到不同结果。

## 进一步重构方案

这部分用于指导第 6 / 第 8 步之后的实际重构。目标不是重写整个桌面端，而是在不污染其他窗口链路的前提下，把 compact 桌面布局从补丁式条件判断改成可测试、可验证、可回退的 geometry 消费管线。

### 重构边界

只重构这些内容：

1. NEKO-PC compact geometry 读取、分类、bounds、hitRects、saved position 和 setBounds / setShape 提交。
2. NEKO 页面 compact history / preview / choice / toolFan 的 geometry 输出合同和必要 DOM 标记。
3. 为这些合同增加 fixture / 单元测试 / 真实运行验证。
4. NEKO-PC compact 窗口层级保持在模型窗口上方，history / preview 不能被模型压住；最小化小球仍按独立窗口规则显示，不和聊天框本体重新耦合。

不重构这些内容：

1. 不重写 full 导出窗口。
2. 不重写 React 聊天主流程、消息 schema、附件发送、GalGame 业务或模型窗口。
3. 不在 NEKO-PC 复制消息选择、导出格式化、Markdown / Image 构建或发送业务。
4. 不把 history / preview 做成独立桌面窗口。
5. 不把 body portal 当作默认修复；只有第 6 步证明 root 内无法满足桌面 bounds，才进入 portal 备选方案。

### 重构分段

与第 6 步之后的对应关系：

1. R0 属于第 6 步诊断，只收集 fixture 和证据，不改正式逻辑。
2. NEKO 页面 geometry 加固属于第 7 步，必须先于 NEKO-PC 正式消费重构完成。
3. R1 / R2 / R3 属于第 8 步 NEKO-PC 重构。
4. R4 属于第 8 / 第 9 / 第 12 步的测试和真实运行闭环。
5. 第 10 / 第 11 步导出能力接入不能提前绕过第 6-9 步的桌面 geometry 闭环。

#### R0：冻结基线和收集 fixture

先不改正式逻辑，只收集同一组场景的 geometry 输入 / 输出。

必须产出：

1. NEKO 页面 `surfaceItems` fixture：
   - history 关闭。
   - history 打开。
   - history 无消息。
   - history 长消息。
   - preview 展开。
   - toolFan / 右侧展开按钮打开。
   - choice above。
   - choice below。
   - history + preview + toolFan。
2. NEKO-PC 当前输出 fixture：
   - windowBounds。
   - surface。
   - nativeRects。
   - hitRects。
   - compactChoicePlacement。
3. 页面 CSS 变量记录：
   - `--compact-surface-left`
   - `--compact-surface-top`
   - `--compact-surface-width`
   - `--compact-surface-height`

完成标准：

1. 能用 fixture 复现当前问题或证明当前代码会产生风险输出。
2. fixture 不提交用户隐私内容；消息文本可脱敏或用人工构造数据。
3. 没有正式逻辑改动。

#### R1：抽出 NEKO-PC compact layout 纯计算层

把 `preload-chat-react.js` 中和 compact layout 相关的纯计算拆成可测试 helper。可以先保留在同文件内，但必须形成明确输入 / 输出，不依赖 DOM、ipc、localStorage 或 Electron API。

建议纯函数：

1. `normalizeDesktopCompactSurfaceItems(items, windowBounds)`
2. `classifyDesktopCompactItems(items)`
3. `resolveDesktopCompactBaseSurface(classified, context)`
4. `translateDesktopCompactItems(classified, dx, dy)`
5. `resolveDesktopCompactChoicePlacement(classified, context)`
6. `buildDesktopCompactNativeBounds(classified, context)`
7. `buildDesktopCompactHitRects(classified)`
8. `buildDesktopCompactLayoutSnapshot(layout)`

输入只能包括：

1. 页面 geometry。
2. 当前 window bounds。
3. workArea。
4. avatar bounds。
5. 已保存的 base surface position。

输出必须包括：

1. `baseSurfaceScreenRect`
2. `windowBounds`
3. `nativeRects`
4. `hitRects`
5. `compactChoicePlacement`
6. `layoutSurfaceForPage`
7. `shouldSaveSurfacePosition`

完成标准：

1. 纯计算层里没有 `W.setBounds`、`ipcRenderer.send`、DOM query、localStorage 写入。
2. `history:native.hitRect === null` 在 normalize 后仍为 null。
3. `layoutSurfaceForPage` 只来自 base surface，不来自 window union。

#### R2：用 adapter 接回现有 preload 生命周期

保留现有 Electron 调用时序，但把核心计算换成 R1 的纯函数输出。

接入规则：

1. `activateDesktopCompactWindow()` 只负责：
   - 读取 bounds / workArea。
   - 调用纯 layout。
   - 比较 snapshot。
   - 提交 setBounds / setShape / layout event。
2. `applyDesktopCompactLayoutToPage()` 只把 base surface 写给页面。
3. `applyDesktopCompactNativeRegion()` 只消费 hitRects。
4. `showDesktopCompactBallWindow()` 仍只按模型左侧小球规则，不受 history / preview 影响。
5. `saveDesktopCompactSurfacePosition()` 只能在用户拖动结束时保存 base surface，不能在 history / preview / toolFan 打开关闭时保存。
6. compact 主窗口在 history / preview / toolFan / choice 打开后仍必须保持在模型窗口上方；如果需要 `bringToFront`，只能作为窗口层级提交，不允许改变 base surface、window bounds 或 saved position。

完成标准：

1. 打开 / 关闭 history 不写 saved position。
2. 打开 / 关闭 preview 不写 saved position。
3. 打开 / 关闭 toolFan / 右侧展开按钮不写 saved position。
4. setBounds snapshot 不因 hitRects 变化而误判 window bounds 变化。
5. setShape snapshot 不因 window native bounds 变化而把 bounds-only item 写进 hit region。

#### R3：删除旧 fallback 和冲突逻辑

在新管线测试通过后，删除或收敛旧的危险逻辑。

必须删除 / 替换：

1. `item.hitRect ? ... : nativeRect` 这种 truthy fallback。
2. `hitRect || nativeRect` 这种 extra item hit fallback。
3. 任何把 extra union 写入 `window.__nekoDesktopCompactLayout.surface` 的路径。
4. 任何在非用户拖动结束时保存 compact surface position 的路径。
5. 任何给 history / preview 写桌面端专用高度公式的临时变量。

允许保留：

1. 普通旧 item 缺省 `hitRect` 时 fallback 到 nativeRect，但必须经过显式 `hasOwnProperty('hitRect')` 判断。
2. choice 自己的 above / below 重定位逻辑。
3. base surface clamp 到 workArea 的逻辑。

完成标准：

1. `rg -n "hitRect \\|\\| nativeRect|item.hitRect \\?" src/preload-chat-react.js` 不再命中危险路径，或命中的代码有明确 composite 保护。
2. `rg -n "compact-history-slot|history.*height|preview.*height" /Users/tonnodoubt/N.E.K.O.-PC/src` 不出现桌面端尺寸公式。

#### R4：契约测试和真实运行验证

测试必须先覆盖纯计算，再覆盖真实桌面。

契约测试最少覆盖：

1. `history:native` bounds-only。
2. `history:scroll` / `history:controls` / `history:preview` hit-capable。
3. 缺省 `hitRect` 的普通 item fallback。
4. history + preview + toolFan 组合不改变 base surface。
5. choice above / below 只改变 choice。
6. 拖动保存只保存 base surface。
7. layout feedback loop：打开 extra island 后 `layoutSurfaceForPage` 不变化。

真实运行最少覆盖：

1. 网页端和桌面端同场景截图对比。
2. 打开 history、preview、toolFan、choice above、choice below。
3. 长历史、无消息、输入态、展示态。
4. 靠近屏幕底部拖动后打开 history / preview。
5. 透明区域点击穿透。
6. 模型移动后用户保存的 compact 位置不被覆盖。
7. 桌面端 history / preview / compact 本体不被模型窗口压住；最小化小球位置仍独立于 history / preview。

完成标准：

1. 除桌面端暂未实现的毛玻璃外，用户可见结果和网页端一致。
2. 第 6 步列出的历史桌面问题全部复测通过。
3. 没有临时日志、fixture 敏感数据、截图或诊断脚本被误提交。

### 回退点和提交拆分

为了避免再次出现“大补丁里混着多个方向”的问题，后续提交必须按下面拆分：

1. 提交 A：只提交文档和 fixture 说明，不改正式逻辑。
2. 提交 B：NEKO 页面 geometry 加固和测试补齐。
3. 提交 C：只抽 NEKO-PC 纯 layout helper 和契约测试，不改变运行行为。
4. 提交 D：把 preload 生命周期切到新 helper，保持用户可见行为不变或只修复已确认问题。
5. 提交 E：删除旧 fallback / 冲突逻辑。
6. 提交 F：真实运行验证后的小范围视觉或阈值调整。

每个提交都必须能独立说明：

1. 本提交解决哪个根因。
2. 本提交没有碰哪些链路。
3. 本提交用什么测试证明。
4. 如果失败，回退到哪个提交不会破坏前序成果。

### 防止再次补丁化的硬规则

1. 任何新判断都必须能归到 base、extra、bounds-only、hit-capable、choice、saved-position 之一。
2. 如果一个判断同时修改 base surface 和 extra island，先停下重审设计。
3. 如果一个修复需要 NEKO-PC 知道 history / preview 的视觉高度公式，说明方向错了。
4. 如果一个修复让打开 extra island 后 `--compact-surface-*` 改变，说明 feedback loop 没切断。
5. 如果一个修复只能靠真实桌面肉眼判断、没有 fixture 或契约测试，不能收口。
6. 如果一个修复影响 full 导出窗口、普通聊天、GalGame 业务或模型窗口，必须拆成单独设计，不混入本阶段。

## 修改顺序

按下面顺序实施。除非当前代码事实变化，否则不要跳步。

1. 前端渲染合同和消息 block 共享。
2. React compact inline history 状态与基础 UI。
3. History UI、滚动锚定和气泡选择。
4. Inline 导出预览前端壳。
5. 网页端 geometry 精确命中，并完成桌面端风险预检查。
6. 桌面端基线复测和失败链路定位。
7. NEKO geometry / layout 合同加固。
8. NEKO-PC 桌面壳消费新增 geometry，并用真实桌面运行闭环。
9. 前端行为测试、桌面端契约测试和 i18n key 收口。
10. 导出能力无窗口化和宿主能力完善。
11. Inline preview 接入真实导出能力。
12. 构建、网页端 / 桌面端真实运行验证和收口。
13. 后续阶段再做历史拖拽。

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

1. History panel 是 compact surface extra island，默认保留在 React root 内并通过 geometry 扩展桌面窗口；除非第 6 步证明确有必要，不要先改成 body portal。
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
2. Inline preview 当前仍是前端壳 / 占位，真实 Markdown / Image 构建、复制、下载接入留到第 10 / 第 11 步。
3. Desktop 透明窗口 bounds / native hit / setShape 的诊断、修复和最终真实运行验收属于第 6 / 第 8 / 第 12 步；第 3 步只能保证 React DOM 和 CSS 为后续 geometry 提供稳定结构。
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
9. Preview 先通过清晰的 adapter 接口调用导出能力；能力可以暂时不可用，但接口形状要稳定，后续第 10 / 第 11 步再接真实实现。
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

收口结论：第 4 步已经完成 inline 导出预览的 React 前端壳，并完成与设计文档的目标一致性检查，可以继续进入 geometry 精确命中与桌面端消费阶段。真实 Markdown / Image 构建、复制、下载能力仍留到第 10 / 第 11 步接入。

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
4. 第 4 步不处理桌面端 setShape / bounds 最终验收；preview 的网页端 native hit 输出留到第 5 步，桌面壳消费留到第 8 步。

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
6. 如果 history 外层 rect 与内部可交互区不一致，先扩展 collector 支持显式 hit rect 或子 item，不用整块 wrapper rect 充当 hitRect。
7. History 的 geometry 采集必须消费实际渲染后的 DOM rect；不要在 NEKO-PC 里复刻 history 尺寸公式。尺寸公式只属于 NEKO React/CSS，桌面端只消费页面提供的 rect。
8. ChoicePrompt / GalGame 选项打开时：
   - choice item 层级高于 history / preview。
   - 只有选项 placement 在上方并覆盖 history / preview 时，history / preview 才进入视觉让位和不可点击状态。
   - 选项 placement 在下方时，history / preview 保持正常显示和交互。
   - 不发生重排，不推动聊天框。
9. 同步产出 NEKO-PC 可直接消费的 geometry 合同；网页端 collector 的输出必须能表达桌面端需要的 window bounds 和 native hit，不留给 preload 猜测。

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
4. `getCompactSurfaceBaseRect()` 的 base anchor 候选仍只包含 `input` / `capsule`；history / preview 不参与聊天框本体锚点计算。
5. Geometry item 语义已收敛为 composite：
   - `history:native` 用于窗口 bounds union。
   - `history:native` 不进入 hit，不吃点击。
   - scroll / controls / preview 子区域才进入真实 hit。
6. 设计文档中的 geometry 规则已同步修正：
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
3. 第 5 步不做桌面端 setShape / bounds 最终消费验收；该部分从第 6 步诊断开始，并在第 8 / 第 12 步闭环。
4. 第 5 步不接入真实 Markdown / Image 导出构建能力。

## 第 6 步：桌面端基线复测和失败链路定位

修改范围：

1. 原则上不改代码。
2. 允许新增临时诊断脚本或临时日志，但不得提交。
3. 如果诊断必须改代码才能获得证据，先把诊断改动和正式修复分开提交或在提交前清理。

结构设计问题处理原则：

1. 先把问题当成结构合同问题处理，不把它拆成“history 高度小”“preview 被压缩”“右侧展开按钮抖动”“点击漏到下方”这些彼此独立的 CSS 小问题。
2. 当前紧凑态桌面链路必须拆成五层分别验证：
   - Base surface：只负责聊天框本体位置，来源只能是 `capsule` / `input`。
   - Extra island：history、preview、choice、toolFan 只扩展可见区域和命中区域。
   - Window bounds：只包住 base surface 和 extra island 的真实可见 native rect。
   - Native hit / shape：只包含真实可交互 hit rect，不能包含透明 wrapper。
   - Saved position：只保存 base surface 的用户选择位置，不能保存 window union 或 extra island 位置。
3. 之前反复出现的问题都可以落到这五层的混淆上：
   - history / preview 被压缩，多半是 window bounds 或 DOM rect 没有消费真实 history 尺寸，或桌面端按猜测公式覆盖了页面尺寸。
   - 打开右侧展开按钮 / toolFan 剧烈抖动，多半是 extra island 参与了 base anchor、保存位置、window union 往返重算，或 shape/bounds 每帧互相触发。
   - 透明区域挡点击，多半是 `history:native.hitRect === null` 被 NEKO-PC 回填成 `nativeRect`。
   - GalGame / ChoicePrompt 选项位置异常，多半是把 choice 的空间不足重定位逻辑套到了 history / preview，或 history 推动了聊天框本体。
   - 拖动后吸回模型头部，多半是保存位置读取了 window union / extra item，或模型 bounds 更新重新覆盖了用户拖动位置。
4. 修复顺序必须先修结构合同，再修视觉参数：
   - 先确认 NEKO 输出的 `surfaceItems` 能表达真实 visible / hit 分离。
   - 再确认 NEKO-PC 消费时没有把 extra item 当 base anchor。
   - 最后才调 history 高度、滚动条、preview 视觉。
5. 禁止把结构问题用以下方式遮住：
   - 给 NEKO-PC 写 history 高度公式。
   - 用 `--compact-history-slot-height` 从桌面壳反向注入尺寸。
   - 把 history 默认挪到 body portal，却不验证 collector、输入态、preview、choice、toolFan 的组合。
   - 为了让桌面端不裁切而让聊天框本体自动上移、吸附模型或重写用户保存位置。
   - 用大透明容器吃事件来保证按钮可点。

目标：

1. 在改 NEKO-PC 之前先复现并记录桌面端真实表现，不能再凭网页端 DOM 或代码推断宣称桌面端完成。
2. 明确 history / preview 在桌面端的三条链路是否完整：
   - NEKO React 是否真实渲染了 history / preview。
   - `window.__nekoGetCompactInteractionGeometry()` 是否输出了 history / preview 的真实 rect。
   - NEKO-PC 是否把这些 rect 转成正确 window bounds 和 native hit region。
3. 把之前实际踩过的问题转成可复现检查：
   - 打开 history 什么都不显示。
   - history 高度比网页端窄很多。
   - input / default / options / choice / preview 状态高度不一致。
   - 打开右侧展开按钮 / 工具轮盘或 preview 时窗口剧烈抖动。
   - 透明 history 外壳吃掉大面积点击。
   - GalGame / ChoicePrompt 选项位于上方时可用，位于下方时 history 不应让位。

必须记录的观测数据：

1. 桌面端 compact 打开前后的 `window.__nekoDesktopCompactLayout`。
2. 桌面端 compact 打开 history 后的 `window.__nekoGetCompactInteractionGeometry()` 输出。
3. history 打开、preview 打开、choice 上方、choice 下方、右侧展开按钮 / toolFan 打开的 `surfaceItems`：
   - item id。
   - kind。
   - nativeRect。
   - hitRect。
   - interactive。
   - hitRegionKind。
4. NEKO-PC 当前窗口 bounds、hitRects、nativeRects。
5. 页面侧 `--compact-surface-left/top/width/height` 与 `window.__nekoDesktopCompactLayout.surface`：
   - 打开 history 前后。
   - 打开 preview 前后。
   - 打开右侧展开按钮 / toolFan 前后。
   - choice 上方 / 下方切换前后。
   这些值只能随 base surface 变化，不能随 extra island 外扩变化。
6. 截图至少覆盖：
   - history 有消息。
   - history 无消息。
   - preview 展开。
   - choice 上方覆盖 history。
   - choice 下方不覆盖 history。
   - 输入态和展示态各一次。

真实模拟检查矩阵：

1. 先在 NEKO 页面侧采集同一组场景的 geometry snapshot，不改 NEKO-PC：
   - 展示态 + history 关闭。
   - 展示态 + history 打开。
   - 输入态 + history 打开。
   - 输入态 + history 打开 + 右侧展开按钮 / toolFan 打开。
   - 输入态 + history 打开 + preview 展开。
   - 输入态 + history 打开 + choice 上方。
   - 输入态 + history 打开 + choice 下方。
   - 长历史消息列表。
   - 无消息空状态。
2. 对每个 snapshot 手工或脚本模拟 NEKO-PC 的核心布局输入 / 输出：
   - 输入：window bounds、workArea、avatar bounds、surfaceItems。
   - 输出：base surface screen rect、extra native rect union、window bounds、hitRects、compactChoicePlacement。
   - 模拟必须包含两类 hit 输入：一类是 `history:native` 的显式 `hitRect: null`，另一类是旧普通 item 缺省 `hitRect` 字段；两类不能在 NEKO-PC normalize 后被合并成同一个 native hit。
   - 如果当前 NEKO-PC 内部函数无法直接复用，应先临时复制等价 fixture 或抽出纯函数做模拟；模拟结果用于指导正式修复，临时脚本不得提交。
3. 每个场景都要回答五个问题：
   - base surface 是否仍然来自 `capsule` / `input`。
   - history / preview / toolFan / choice 是否只作为 extra item。
   - window bounds 是否包含真实可见区域。
   - hitRects 是否只包含真实可交互区域。
   - saved position 是否会被本场景改变。
4. 如果模拟输出已经会抖、错位或误 hit，先修算法和合同；不要启动桌面端靠肉眼反复试。
5. 如果模拟输出正确但真实桌面仍错，重点查：
   - React root / shell / `.chat-window` 裁切。
   - Electron setBounds / setShape 调用时序。
   - body class、CSS 构建产物和实际加载的 hash CSS 是否一致。
   - 是否有旧代码在 layout change 后再次写 CSS 变量或保存位置。

判定规则：

1. 如果 NEKO geometry 没输出 history / preview，先修 NEKO；不要在 NEKO-PC 猜尺寸。
2. 如果 NEKO geometry 正确但 NEKO-PC bounds / hit 错，修 NEKO-PC；不要改 React 布局绕过。
3. 如果 history 在网页端正确但桌面端被裁切，先检查窗口 bounds 是否包含 `history:native`，再检查 DOM 是否被 `.chat-window` / shell overflow 裁切。
4. 如果 history 在桌面端高度过窄，先比对 NEKO 输出 rect 与网页端真实 DOM rect；不能先给 NEKO-PC 加 `--compact-history-slot-height` 或类似猜测变量。
5. 如果透明区域挡点击，优先检查 NEKO-PC 是否把 `hitRect: null` 回填成 `nativeRect`。
6. 如果打开 preview、右侧展开按钮或 toolFan 导致聊天框跳动 / 剧烈抖动，优先检查 extra item 是否参与了 base anchor、保存位置、窗口 union 反复变化或 choice 重定位。
7. 如果 history / preview 真实 native rect 大于 workArea，高度收敛应回到 NEKO CSS 的 viewport / workArea 上限和内部滚动策略；NEKO-PC 不能通过缩放、压扁 preview、移动 base surface 或重写 saved position 来“塞进去”。

检查：

1. 诊断输出能定位问题属于 NEKO、NEKO-PC，还是两者合同不一致。
2. 诊断过程没有提交临时日志、截图、脚本或本地状态文件。
3. 得到第 7 / 第 8 步需要改哪些文件的精确范围。

第 6 步执行记录（2026-05-21）：

1. 仓库状态：
   - `N.E.K.O` 仅有本功能相关两份文档修改；另有多份未跟踪 `.agent/notes/*`，与本功能无关，未触碰。
   - `N.E.K.O.-PC` 工作区干净。
2. 基础检查：
   - `node --check static/app-react-chat-window.js` 通过。
   - `node --check /Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js` 通过。
   - `npm run typecheck`（目录：`frontend/react-neko-chat`）通过。
   - `npm run test`（目录：`frontend/react-neko-chat`）通过，`98 passed`。
   - `node --test test/*.test.js`（目录：`N.E.K.O.-PC`）当前有 3 个既有 `storage-window-display-contract` 失败，和紧凑聊天框无关，不能作为 compact 验收依据。
3. NEKO 页面侧代码事实：
   - `CompactExportHistoryPanel` 已输出 `data-compact-geometry-item="history"` 和 `data-compact-geometry-hit-scope="children"`。
   - history scroll / controls / preview 已通过 `data-compact-hit-region="true"` 暴露真实 hit 区域。
   - `static/app-react-chat-window.js::collectCompactHistoryGeometryItems()` 已输出 `history:native`：
     - `nativeRect`：history 可见 union。
     - `hitRect: null`。
     - `interactive: false`。
     - scroll / controls / preview 子区域才有真实 `hitRect`。
   - 因此“history 外层透明区不吃点击”的语义在 NEKO 页面侧已经能表达。
4. NEKO-PC 桌面壳代码事实：
   - `getDesktopCompactGeometryScreenItems()` 当前只保留 `kind/nativeScreenRect/hitScreenRect`，丢失 `id`、`interactive`、`hitRegionKind`，后续无法精确区分 `history:native` 与 `history:scroll/controls/preview`。
   - `buildDesktopCompactWindowLayout()` 当前对 extra item 使用 `hitRect || nativeRect`。
   - 这会把 `history:native.hitRect === null` 和 `toolFan:native.hitRect === null` 回填成 native hit，违反“native bounds 可见、hit 只真实交互”的合同。
5. 第 6 步矩阵模拟结论：
   - `default history closed`：无 false hit。
   - `default history open`：false hit = `history:native`。
   - `input history open`：false hit = `history:native`。
   - `input history + toolFan`：false hit = `history:native`、`toolFan:native`。
   - `input history + preview`：false hit = `history:native`。
   - `input history + choice above`：false hit = `history:native`。
   - `input history + choice below`：false hit = `history:native`。
   - `long history` / `empty history`：false hit = `history:native`。
   - 所有场景里 base surface 仍来自 `capsule` / `input`，native union 能覆盖可见区域；当前第一个确定失败点是 hit 区域污染。
6. 根因归类：
   - 当前已实证的问题主要属于 NEKO-PC geometry 消费错误，而不是 NEKO React history 面板没有渲染。
   - 透明区域挡点击是必现的结构问题，不是 CSS 透明度或局部视觉问题。
   - toolFan / 右侧展开按钮抖动风险和同一个结构问题相关：extra island 被粗糙地 fallback 成 hit，并且缺少稳定的 item 身份。
7. 按第 6 步判定规则，后续处理：
   - 不应给 NEKO-PC 增加 history 高度公式、preview 高度公式或桌面侧 CSS 变量反推。
   - 第 7 步只需加固 NEKO geometry 契约和测试，确认页面侧输出稳定。
   - 第 8 步必须优先重构 NEKO-PC 的 normalize / classify / layout helper：
     - 保留 `id`、`interactive`、`hitRegionKind`。
     - 用 `hasOwnProperty('hitRect')` 区分“显式 null”和“字段缺省”。
     - bounds union 使用 `nativeRect`。
     - hitRects 只使用真实 `hitRect`，不得对 composite native item 执行 `hitRect || nativeRect`。
     - `history:native` / `toolFan:native` 只能进 window bounds，不能进 native hit / setShape。
8. 真实运行备注：
   - 本机后端 `/health` 正常，且已有 N.E.K.O-PC 进程在运行；未启动第二个实例。
   - 当前桌面截图只证明桌面端运行中，界面未处在 history / preview 场景，不能替代 geometry 矩阵结论。
   - 因矩阵模拟已失败，按本文第 6 步规则，应先修算法和合同，再进入完整桌面视觉截图验收。

## 第 7 步：NEKO geometry / layout 合同加固

修改范围：

1. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
2. `frontend/react-neko-chat/src/App.tsx`
3. `frontend/react-neko-chat/src/styles.css`
4. `static/app-react-chat-window.js`
5. 相关 React 测试。

目标：

1. NEKO 页面必须输出桌面端可直接消费的真实 geometry，NEKO-PC 不需要复刻 UI 尺寸公式。
2. History / preview 仍保留在 React root 内，除非第 6 步证明 root 内无法通过 bounds 解决裁切；不要默认改成 body portal。
3. 如果确实要 body portal，必须同步修改 collector 允许范围，并用桌面端截图证明没有引入输入态失焦、history 消失或 geometry 丢失。
4. `history:native` 只用于窗口 bounds union，必须保持：
   - `interactive: false`
   - `hitRect: null`
5. scroll / controls / preview 子区域才进入真实 hit region。
6. 空状态也必须输出真实可见区域；无消息不能回退到旧导出 toast 或没有任何反馈。
7. history 的尺寸只能来自 CSS 变量和真实 DOM 测量：
   - `--compact-surface-left`
   - `--compact-surface-top`
   - `--compact-surface-width`
   - `--compact-surface-height`
   - history 自身 DOM rect。
8. 不新增 `--compact-history-slot-height` 这类由桌面壳反向注入的尺寸变量。
9. preview 展开后仍作为 history layer 的内部状态输出 geometry；不能让 preview 变成另一个 base anchor。
10. ChoicePrompt / GalGame 位于上方时 history 视觉让位并禁用交互；位于下方时 history 不让位。

必须加固的代码点：

1. `collectCompactHistoryGeometryItems()`：
   - 只读取 `[data-compact-hit-region="true"]` 或明确 fallback。
   - 不把外层 `.compact-export-history-anchor` rect 当 hitRect。
   - 子区域 `pointer-events: none`、`display: none`、`visibility: hidden`、接近透明时不进 hitRect。
2. `collectCompactSurfaceGeometryItems()`：
   - base anchor 候选只允许 `capsule` / `input`。
   - `dragHandle` 只能作为 base surface 的辅助命中，不作为保存位置锚点。
   - history / preview / choice / toolFan 都只能是 extra item。
3. `CompactExportHistoryPanel`：
   - 外层 `pointer-events: none`。
   - scroll / controls / preview `pointer-events: auto`。
   - preview 模式替换 history list 和 controls，不同时显示三套交互。
   - 控件折叠后 hit region 缩到折叠后的真实线条 / 三角区域。
4. `styles.css`：
   - history 高度使用本体宽度比例和 viewport 上限。
   - 不因为输入态、展示态或 preview 模式切换写死不同高度。
   - 长历史内容仍能滚动，不能用 `justify-content: flex-end` 造成内容超过高度后顶部不可达；最新消息贴底应通过滚动位置和内容容器策略实现。

检查：

1. 网页端 `window.__nekoGetCompactInteractionGeometry()`：
   - history 关闭时没有 history。
   - history 打开时有 `history:native`。
   - `history:native.hitRect === null`。
   - scroll / controls / preview 有各自 hitRect。
2. 无消息、短历史、长历史、preview、controls 折叠、choice 上方 / 下方都输出合理 rect。
3. 选中态、check、glow、滚动条显示 / 隐藏不改变 base anchor。
4. `capsule` / `input` 仍是聊天框本体位置的唯一锚点来源。
5. `node --check static/app-react-chat-window.js`
6. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact export history|compact export action|compact UI overlaps"`
7. `npm --prefix frontend/react-neko-chat run typecheck`

阶段 7 边界：

1. 第 7 步只加固 NEKO 输出合同，不修改 NEKO-PC。
2. 第 7 步不接入真实导出构建能力。
3. 第 7 步不能用 NEKO-PC 专属 CSS 反向污染网页端布局。

阶段 7 收口记录：

1. 已修正 `frontend/react-neko-chat/src/styles.css` 中历史消息列表的底部贴齐策略：
   - 不再用 `justify-content: flex-end` 直接压到底部。
   - 改为内容容器 `::before { margin-top: auto; }`，短历史仍贴近紧凑聊天框，长历史从真实顶部开始滚动，避免顶部内容不可达。
2. 已新增 `frontend/react-neko-chat/src/App.test.tsx` 契约测试：
   - history anchor 必须留在 React root 内，不能变成 body 直挂 portal。
   - history 外层保持 `data-compact-geometry-hit-scope="children"`，并且不带 `data-compact-hit-region`。
   - scroll / controls 才是真实 hit region。
   - input 仍是 compact 本体锚点，toolFan 仍作为 body portal extra island。
3. 已检查第 6 步结论与第 7 步边界一致：
   - NEKO 页面侧已输出 `history:native` bounds-only 语义。
   - 桌面端抖动、被压缩、空白吃点击的核心修复不应在 NEKO 侧补丁化完成，应进入第 8 步的 NEKO-PC geometry 消费重构。
4. 已执行检查：
   - `node --check static/app-react-chat-window.js` 通过。
   - `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact export history"` 通过，11 项相关用例通过。
   - `npm --prefix frontend/react-neko-chat run typecheck` 通过。
   - `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx` 通过，85 项通过。
   - `git diff --check` 通过。

阶段 7 二次复审记录：

1. 已按 `home-compact-chat-inline-export-history-design.md` 与 `home-compact-chat-mode-design.md` 重新对照第 7 步边界：
   - 第 7 步只处理 NEKO 页面侧 geometry / layout 合同，不修改 NEKO-PC。
   - `history` 继续留在 React root 内，不新增 body portal。
   - history / preview / choice / toolFan 均保持 extra island 语义，不参与 compact 本体保存位置。
2. 已对照实际代码确认：
   - `CompactExportHistoryPanel` 外层为 `history` geometry item，`hit-scope="children"`，自身不声明 `data-compact-hit-region`。
   - scroll / controls / preview 分别声明真实子 hit region。
   - preview 打开时替换 history list 和 controls，不三套交互并存。
   - ChoicePrompt 位于上方时只通过 `under-choice-prompt` 让 history / preview 视觉和命中让位；位于下方时不让位。
   - `static/app-react-chat-window.js` 的 history collector 输出 `history:native` 的 `hitRect: null` / `interactive: false`，并从子区域生成真实 hit item。
3. 已对照测试覆盖确认第 7 步没有明显遗漏：
   - 无消息空状态。
   - 有消息时开关 history 且不走 full 导出。
   - 消息清空后仍显示空状态。
   - 60 条长历史不裁剪。
   - 气泡选择、全选 / 取消 / 反选、selection limit。
   - controls 折叠后内容隐藏且 controls 仍是 hit region。
   - preview 替换 history list 和 controls，并输出 preview hit region。
   - `sending` 消息不可选、气泡内部 action 不误选。
   - 向上滚动后关闭 auto-scroll，文字以外的发送动作恢复底部贴边。
   - 点击 / 拖拽 / 键盘选择意图区分。
   - ChoicePrompt 位于上方 / 下方时 history 让位规则不同。
   - history root-owned、外层不吃 hit、子区域才是真实 hit region。
4. 仍明确留到第 8 步处理的内容：
   - NEKO-PC 对 `hitRect: null` 的保留。
   - NEKO-PC base surface / extra island 分类。
   - 桌面端 bounds / hitRects / saved position 去重与抖动修复。
   - 真实桌面截图与窗口稳定性验收。
   这些不应回写成第 7 步的 NEKO 侧补丁。

## 第 8 步：NEKO-PC 桌面壳消费新增 geometry

修改范围：

1. `/Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js`
2. 必要时新增 `/Users/tonnodoubt/N.E.K.O.-PC/test/*` 契约测试。
3. 不修改 NEKO 业务代码；如果发现 NEKO geometry 不足，回到第 7 步。

核心修复原则：

1. NEKO-PC 只消费页面输出的 geometry，不计算 history 高度、不推导 preview 尺寸、不复制导出业务。
2. `getDesktopCompactGeometryScreenItems()` 必须保留显式 `hitRect: null`：
   - `interactive === false` 时 hit 为 `null`。
   - `interactive === true` 但页面显式给了 `hitRect: null` 时，不能回填 nativeRect。
   - 只有普通非 composite item 且页面没有显式 hitRect 语义时，才可以回填 nativeRect。
   - 实现上必须先判断 `hasOwnProperty('hitRect')`，再决定是否 fallback；不能使用 `item.hitRect` 的 truthy 判断，因为 `null` 正是有效语义。
3. `buildDesktopCompactWindowLayout()` 的 extra item 也不能使用无条件 `hitRect || nativeRect`。
4. `history:native` 进入 window bounds union，但不进入 `hitRects` / setShape。
5. `history:scroll` / `history:controls` / `history:preview` 进入 bounds union 和 `hitRects`。
6. `history` / `preview` 不能参与：
   - `isDesktopCompactSurfaceAnchorKind`
   - `getDesktopCompactBaseSurfaceScreenRect`
   - `saveDesktopCompactSurfacePosition`
   - 最小化小球定位
   - choice 上 / 下重定位
7. `choice` 的空间不足重定位只对 `item.kind === 'choice'` 生效；history / preview 超出工作区时，第一阶段只通过 NEKO 侧高度上限 / 内部滚动解决，不移动聊天框本体。
8. 桌面窗口 bounds 可以包住 extra item，但 base surface 的 screen rect 必须保持用户保存的位置。
9. 高频 streaming 只在 rect snapshot 真实变化时触发布局；相同 bounds / hitRects 不重复 setBounds / setShape。

结构重构要求：

1. 如果现有 NEKO-PC 代码无法清晰表达 base surface 与 extra island 的差异，不要继续补丁式加条件；应在 `preload-chat-react.js` 内收敛为明确的中间结构：
   - `baseItems`
   - `extraItems`
   - `baseNativeRects`
   - `baseHitRects`
   - `extraNativeRects`
   - `extraHitRects`
   - `windowNativeRects`
   - `windowHitRects`
2. `item.kind` 不足以表达 composite 语义时，应消费 NEKO 输出的 `id` / `interactive` / `hitRegionKind`：
   - `history:native` 必须被识别为 bounds-only。
   - `history:scroll` / `history:controls` / `history:preview` 必须被识别为 hit-capable。
3. 保存位置函数必须只接收 base surface rect，不允许调用方传 window union。
4. bounds 计算必须先确定 base surface 的目标位置，再把 extra island 按相同 delta 平移；不能先 union 所有 rect 后再反推聊天框本体位置。
5. choice 重定位必须是独立分支，只处理 `choice`，并且不得把调整后的 choice 结果写回 history / preview。
6. toolFan / 右侧展开按钮也属于 extra island。打开时只能扩展 window bounds 和 hitRects，不能改变 base surface、保存位置或触发模型吸附。
7. 如果为了避免抖动需要节流，只能节流 setBounds / setShape 的重复提交，不能延迟或吞掉用户可见交互状态。
8. 如果重构后函数仍留在 `preload-chat-react.js` 闭包内，必须至少提供同文件内可测试的纯 layout helper 或测试专用导出入口；否则 NEKO-PC 契约测试只能测到表面，无法防止 `hitRect || nativeRect` 这类回归。

需要显式新增或调整的测试：

1. `history:native`：
   - nativeRect 进入 bounds union。
   - hitRect 为 null，不进入 hitRects。
   - 用例必须覆盖 `hitRect` 字段存在且值为 `null`。
2. `history:scroll` / `history:controls` / `history:preview`：
   - nativeRect 进入 bounds union。
   - hitRect 进入 hitRects。
3. 旧普通 item：
   - 缺省 `hitRect` 字段时可以 fallback 到 nativeRect。
   - 这条用例必须和 `history:native` 区分开，防止实现写成单纯 truthy/falsy。
4. `capsule` / `input`：
   - 仍是唯一保存位置锚点。
5. `choice`：
   - 仍可在空间不足时上 / 下移动。
   - history / preview 不复用这套移动逻辑。
6. 拖动保存：
   - 打开 history / preview 后保存位置仍读取 compact 本体，不读取 window union。
7. 透明点击：
   - 点在 `history:native` 空白区域不属于 hitRects。
   - 点在 scroll / controls / preview 区域属于 hitRects。
8. 右侧展开按钮 / toolFan：
   - 打开后进入 extra native / hit，不进入 base anchor。
   - 打开 / 关闭不会改变 saved surface position。
   - 连续打开 / 关闭不会产生不同 window bounds 间的往返震荡。
9. 组合场景：
   - history + toolFan。
   - history + preview。
   - history + choice above。
   - history + choice below。
   - history + preview + toolFan。
   每个组合都必须断言 base surface 不变、hitRects 只来自真实交互区。

真实桌面验证必须覆盖：

1. 初始 compact 展示态打开 history。
2. compact 输入态打开 history。
3. 有大量消息时 history 高度与网页端同一比例策略一致，不窄成小条。
4. 打开 preview 后内容不被压成窄条。
5. 打开右侧展开按钮 / toolFan 后聊天框本体不抖、不上移、不触发窗口 bounds 往返重算。
6. choice 位于上方时覆盖 history 且可点。
7. choice 位于下方时 history 不淡化、不失焦。
8. history 内滚动、气泡选择、controls 折叠、preview 按钮不会漏点到模型。
9. history 外透明区域可点击后方。
10. 拖动聊天框后松手不会吸回模型头部；打开/关闭 history 不写入新的保存位置。
11. 模型移动或 avatar bounds 更新时，已由用户拖动保存的位置不被 history / preview 重新定位。
12. compact 本体、history 和 preview 始终在模型窗口上方；如果需要窗口层级提交，不能改变 base surface、bounds 或 saved position。

检查：

1. `node --check src/preload-chat-react.js`
2. 新增或调整的 NEKO-PC 契约测试通过。
3. 至少一次真实启动 NEKO-PC，并保留截图或明确的观察结论。
4. `git diff --check`

阶段 8 边界：

1. 第 8 步只修桌面壳 geometry 消费。
2. 不在 NEKO-PC 中复制消息选择、导出格式、导出文件、图片发送或历史重发业务。
3. 不把 history / preview 做成新的桌面窗口或独立页面。

阶段 8 收口记录（2026-05-21）：

1. 已在 `N.E.K.O.-PC/src/desktop-compact-layout.js` 抽出桌面 compact layout 纯计算层，并把桌面端窗口 bounds / hit region 的判断从 `preload-chat-react.js` 闭包里拆成可测合同：
   - `normalizeDesktopCompactSurfaceItems()` 保留 `id`、`kind`、`interactive`、`hitRegionKind`。
   - 用 `hasOwnProperty('hitRect')` 区分显式 `hitRect: null` 与旧普通 item 缺省 `hitRect`。
   - `history:native` / `toolFan:native` 这类 bounds-only item 保持 `hitScreenRect: null`，只进入 window bounds，不进入 hitRects。
   - `capsule` / `input` 是唯一 base anchor；`dragHandle` 只作为 base hit；history / preview / toolFan / choice 都是 extra island。
   - choice relocation 只处理 `kind === 'choice'`，history / preview 不复用该重定位逻辑。
   - toolFan / 右侧展开按钮的 native reserve 由稳定 compact surface base anchor 推导，优先用 `input`，没有 `input` 时用同尺寸的 `capsule`；展示态 / 输入态 / 打开关闭之间不再因为 reserve 有无改变 window bounds，toolFan DOM 只提供真实可点按钮 hitRects。
2. 已将 `N.E.K.O.-PC/src/preload-chat-react.js` 切到共享 helper：
   - 删除原本 scattered 的 base / extra 分类和 `hitRect || nativeRect` fallback。
   - 桌面端测量 compact base surface 时只读取 `[data-compact-geometry-owner="surface"][data-compact-geometry-item="input|capsule"]`，不再直接量旧外层容器。
   - `window.__nekoDesktopCompactLayout.surface` 只写 base surface，不写 history / preview / toolFan / choice 的 union。
   - 增加页面 layout snapshot 去重，避免相同 surface / bounds / placement 重复触发页面侧 layout event。
   - `applyDesktopCompactNativeRegion()` 仍只消费 helper 产出的 hitRects；没有 hitRects 时才使用既有 1px 安全占位。
3. 已同步补齐 NEKO 页面侧必须配合桌面壳消费的 geometry / 交互合同，避免桌面端继续从不稳定 DOM 外壳推导 bounds：
   - `frontend/react-neko-chat/src/App.tsx` 中 `capsule` / `input` 的 `data-compact-geometry-item` 已收敛到同一个 `.compact-chat-surface-frame`；展示态和输入态不再挂载两套旧框。
   - `.compact-chat-surface-frame` 统一固定本体高度为 `54px`，输入态 textarea 固定内部滚动，不允许长文本、placeholder、focus 或按钮状态撑大 compact 本体。
   - `.compact-chat-surface-shell[data-compact-chat-state="input"]` 显式清除普通 composer `focus-within` 背景、边框和阴影，亮色 / 暗色主题都不再把普通输入框视觉和几何带入紧凑态。
   - `static/app-react-chat-window.js` 继续从页面真实 DOM 输出 compact geometry；toolFan 子按钮提供 hitRects，非交互预留区保持 bounds-only。
4. 桌面端层级问题未纳入本次代码提交：
   - 本阶段只提交 compact geometry 消费重构，不提交 `top-coordinator.js` 层级策略变更。
   - 如果后续仍出现模型压住 history / preview，需要单独做窗口层级验证；该验证不能改变 base surface、saved position 或 bounds 计算。
   - 层级方案必须独立测试 React Chat / compact ball / Pet 的关系，不能混入 geometry 重构提交。
5. 已修复右侧展开按钮 / history 开关在桌面端的收缩和打开顺序问题：
   - React 侧保持 toolFan 的 screen anchor；当 BrowserWindow bounds 因 extra island 变化但 base surface 没变时，toolFan 不重新从页面局部坐标漂移。
   - 点击 history 按钮时，桌面端先把 toolFan 收缩回当前真实按钮中心，再延迟打开 history；避免 history bounds 扩张期间把收缩焦点带到模型右侧或旧坐标。
   - 该延迟只作用于带 `__nekoDesktopCompactLayout.windowBounds` 的桌面端；网页端仍保持即时交互。
6. 已新增 / 调整 `N.E.K.O.-PC/test/desktop-compact-layout-contract.test.js` 契约测试：
   - 覆盖显式 `hitRect: null` 的 `history:native`。
   - 覆盖缺省 `hitRect` 的旧普通 item fallback。
   - 覆盖 base anchor 与 extra island 分离。
   - 覆盖 history bounds union 不进入 hitRects。
   - 覆盖 only choice relocation。
   - 覆盖 extra island 跟随 base delta 但不成为 anchor。
   - 覆盖 toolFan native bounds 稳定 reserve，DOM toolFan 只贡献 hitRects，关闭态不产生旧按钮 hitRects。
   - 覆盖同一可见 compact surface 在 `capsule` / `input` 两种状态下产出相同 native bounds，避免输入态 / 展示态切换触发 BrowserWindow bounds 跳变。
   - 覆盖 preload 不再使用危险 truthy fallback。
   - 层级验证保留为后续独立检查项，本阶段测试不强制 `top-coordinator.js` 变更。
7. 已新增 / 调整 NEKO 侧回归测试：
   - `frontend/react-neko-chat/src/App.test.tsx` 覆盖 compact geometry 不再挂在外层 shell，而是挂在可见 `capsule` / `input` 本体。
   - 覆盖桌面 bounds 变化但 base surface 不动时，toolFan 保持 screen anchor。
   - 覆盖 toolFan 自动收缩前会回到当前真实按钮中心。
   - 覆盖桌面端点击 history 按钮时先收缩 toolFan、再打开 history。
   - 保留 compact history root 内挂载、children hit region、选择 / 预览 / 空态等既有行为测试。
8. 已执行检查：
   - `node --check src/desktop-compact-layout.js` 通过。
   - `node --check src/preload-chat-react.js` 通过。
   - `node --test test/desktop-compact-layout-contract.test.js` 通过。
   - `npm --prefix frontend/react-neko-chat run typecheck` 通过。
   - `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx` 通过，88 项通过；输出中的 `HTMLMediaElement.play()` 警告来自既有 jsdom 限制，与本阶段 compact geometry 无关。
   - `bash build_frontend.sh` 通过，并已同步 `static/react/neko-chat` 构建产物。
   - `git diff --check` 与 `git -C /Users/tonnodoubt/N.E.K.O.-PC diff --check` 通过。
   - `node --test test/*.test.js` 当前 101 项中 98 项通过；3 个失败仍是既有 `storage-window-display-contract`，与 compact geometry / layer 改动无关。
9. 已执行辅助真实 DOM 几何验证：
   - 用 Playwright 加载构建后的 React IIFE 和 CSS，直接测量 compact default / input / long input / dark input 的实际 DOM rect。
   - display 态可见 `capsule` 为 `54px` 高，input 态可见 `input` 为 `54px` 高。
   - 长文本输入后 textarea 内部滚动，本体仍为 `54px`，不会把 base geometry 撑高。
   - 暗色主题 focus 下 `.compact-chat-surface-shell[data-compact-chat-state="input"]` 仍为透明、无边框、无阴影，不再继承普通 composer 的 focus 外观。
10. 真实桌面运行验证边界：
   - 本阶段不能用契约测试替代真实桌面视觉矩阵。
   - 后续验收需要重新启动 NEKO-PC，逐项截图 history、preview、toolFan、choice above/below、输入态 / 展示态。
   - 本阶段只闭环桌面壳 geometry 消费和基础窗口稳定性；层级问题作为独立项验证，不把它混入 geometry 提交。
11. 阶段 8 剩余边界：
   - 第 8 步已完成桌面壳 geometry 消费重构和层级合同收口。
   - 完整多场景视觉矩阵（history、preview、toolFan、choice above/below、长历史、空状态、输入态 / 展示态）仍属于第 12 步真实运行验收；不能用本阶段契约测试替代最终截图矩阵。
   - 后续若发现新的抖动，优先检查是否又有非本体 DOM 进入 base geometry，或 extra island 是否反向改变 saved surface / `window.__nekoDesktopCompactLayout.surface`；不要回到按单个现象给 NEKO-PC 写视觉补丁。

阶段 8 追加收口记录（2026-05-21）：

1. 在展示态 / 输入态双框结构已收敛后，仍发现第二个桌面端抖动风险：同一个可见 `.compact-chat-surface-frame` rect，在 `capsule` 状态没有 toolFan reserve，而 `input` 状态会额外加入 toolFan reserve，导致 NEKO-PC 透明窗口 native union 在状态切换时从聊天框本体尺寸跳到轮盘预留尺寸。
2. 该问题不是 React 外观问题，而是桌面壳 bounds 输入不稳定：BrowserWindow bounds 变化会反向影响页面坐标、geometry 采集和下一帧 `setBounds`，因此表现为半概率抽搐 / 闪烁。
3. 已将 `N.E.K.O.-PC/src/desktop-compact-layout.js` 的 toolFan reserve 改为从稳定 compact surface anchor 推导：优先 `input`，否则 `capsule`。这样展示态、输入态和 toolFan 关闭态都拥有相同 native reserve，切换状态不再改变 window bounds。
4. 已新增 `desktop compact layout keeps native bounds stable across capsule and input states` 契约测试，确认同一可见 compact surface 在两种状态下的 `surfaceUnion`、`toolFanReserveRect`、`nativeRects` 和 `hitRects` 一致。
5. 已用纯布局模拟复现原先会跳变的输入：同一 `520x54` surface 下，`capsule` / `input` 现在都产出相同 `nativeUnion: left=190, top=2, width=615, height=191`，`hitUnion` 仍只覆盖聊天框本体和拖拽条，不把 reserve 变成可点击透明区域。
6. 继续排查“一半概率”后确认另一个时序风险：第一帧若只量到可见 `.compact-chat-surface-frame`，但页面 `surfaceItems` 尚未准备好，旧算法会只使用 reserve 或缺少 reserve，下一帧 geometry 完整后 window bounds 再跳一次。
7. 已把 `measuredSurface` 作为 base native rect 和 toolFan reserve 的兜底输入：即使第一帧没有 `surfaceItems`，也会产出与后续 `capsule` / `input` geometry 帧一致的 native union。
8. 已新增 `desktop compact layout keeps first measured-surface frame aligned with later geometry frames` 契约测试，确认初始化帧不会因为 geometry 采集时序不同而改变 BrowserWindow bounds；无 geometry 的第一帧 hit 区域只回退到可见聊天框本体，不把 toolFan reserve 变成透明点击区。

阶段 8 回退链路清理记录（2026-05-21）：

1. 已删除 / 收紧 NEKO-PC 中会把桌面紧凑聊天框带歪的 fallback 链路：
   - `getDesktopCompactMeasuredSurfaceScreenRect()` 不再等待已有 `desktopCompactLayout` 才量真实 DOM；首帧即可读取当前可见 compact 本体。
   - `buildDesktopCompactWindowLayout()` 不再用 `desktopCompactLayout ? surfaceItems : []` 跳过首帧页面 geometry；页面已输出 geometry 时首帧就消费。
   - `buildFallbackSurfaceScreenRect()` 删除未使用的 `includeStored` 分支，删除基于当前 BrowserWindow bounds 的兜底定位；fallback 只保留 avatar 默认位，且只用于初始化布局。
   - 拖拽结束后的 clamp / save 只使用严格测量到的 base surface；量不到真实 `capsule` / `input` 时不再回退到 avatar 或当前 window 位置，避免把错误位置写入用户保存位置。
   - 删除 `preload-chat-react.js` 内未使用的重复 `clampScreenRectToWorkArea()`，避免新修改误以为应在 preload 里另走一套 clamp。
2. 已新增 `preload compact layout removes fallback paths that can skew desktop compact surface` 契约测试，防止以下回归：
   - 首帧真实 DOM 测量被 `desktopCompactLayout` 门控。
   - 首帧 geometry 被 `desktopCompactLayout ? ... : []` 门控。
   - fallback surface 重新读取 current BrowserWindow bounds。
   - 拖拽保存重新使用 avatar / window fallback surface。
3. 当前允许保留的 fallback 只有两类：
   - 没有保存位置时，用 avatar bounds 计算初始 compact 默认位。
   - 没有任何 hitRects 时，用 1px 安全占位避免透明窗口错误吃掉整窗输入。
   这两类 fallback 不允许写入用户保存位置，也不允许反向改变 `window.__nekoDesktopCompactLayout.surface`。

阶段 8 位置结构健壮性补强（2026-05-21）：

1. 已把桌面 compact layout 的位置结构约束加固为硬合同：当前帧必须存在真实 base surface（可见 `.compact-chat-surface-frame` 或页面输出的 `capsule` / `input` geometry）后，history / preview / toolFan / choice 这类 extra island 才能进入 native bounds / hitRects。
2. 若当前帧没有真实 base surface，只允许使用 stored / avatar fallback 形成本体占位和 toolFan reserve；extra island 全部忽略到下一帧，避免未来新增浮层先于聊天框本体被采集时反向拉动窗口位置。
3. 已新增 `desktop compact layout ignores extra islands until a real base surface is measured` 契约测试，防止没有本体时 history / toolFan / choice 抢占 window bounds 或 hitRects。
4. 已新增 `desktop compact layout keeps base surface invariant across new extra island combinations` 契约测试，覆盖未来新增 `preview`、toolFan、choice、history 组合时：
   - base surface 仍只来自 `capsule` / `input`。
   - extra island 可以扩展 native bounds 和真实 hitRects。
   - bounds-only history native 不会进入 hitRects。
   - extra item 不会改变 surfaceUnion 或保存位置输入。
5. 后续新增任何紧凑态浮层，都必须先满足这条结构：`base surface` 是位置真相，`extra island` 只能跟随和扩展，不能成为位置来源。

## 第 9 步：前端行为测试、桌面端契约测试和 i18n key 收口

修改范围：

1. `frontend/react-neko-chat/src/App.test.tsx`
2. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx` 的测试辅助只在必要时调整。
3. `/Users/tonnodoubt/N.E.K.O.-PC/test/*`
4. `static/locales/en.json`
5. `static/locales/es.json`
6. `static/locales/ja.json`
7. `static/locales/ko.json`
8. `static/locales/pt.json`
9. `static/locales/ru.json`
10. `static/locales/zh-CN.json`
11. `static/locales/zh-TW.json`

测试要求：

1. Compact 导出按钮 toggle history。
2. Full 导出按钮仍调用 `onExportConversationClick`。
3. Compact 无消息时打开带空状态的 history，不调用旧导出入口，不弹旧导出提示。
4. Compact “历史对话”按钮作为 toggle 时，`is-active`、`aria-pressed`、`data-compact-tool-active` 与 open state 同步。
5. History open 不改变 `compactChatState`，不关闭 compact input，不触发 `onCompactChatStateChange`。
6. 非 compact 表面不暴露 compact 专属派生状态。
7. 气泡点击选择 / 取消选择。
8. 全选 / 取消 / 反选和 selection limit。
9. Inline preview 前端壳与选择集合同步。
10. 超过 50 条消息 history 不裁剪。
11. 长历史顶部可达，底部贴边只由滚动策略控制。
12. ChoicePrompt / GalGame 选项位于上方时 history 视觉让位并不抢点击；选项位于下方时 history 不让位。
13. Pointer intent 区分点击、滚动、拖拽阈值。
14. History / preview 不参与 compact base anchor 的数据标记。
15. NEKO-PC 契约测试覆盖 `history:native` 不进 hitRects。

检查：

1. `npm --prefix frontend/react-neko-chat test`
2. `npm --prefix frontend/react-neko-chat run typecheck`
3. 8 个 locale JSON 可解析。
4. 新增 key 在 8 个 locale 中一致。
5. NEKO-PC 契约测试通过。
6. 不能只凭网页端 DOM 测试宣称桌面端安全。

### 第 9 步收口记录（2026-05-21）

已完成：

1. 在 `App.test.tsx` 补齐 compact history pointer intent 的滚动路径：气泡 `pointerdown` 后如果历史区域发生滚动，随后 `pointerup/click` 不会误触选择，且会关闭自动贴底。
2. 新增 `tests/unit/test_compact_export_history_static_contracts.py`，覆盖两类静态契约：
   - Full 导出入口仍由 `static/app-react-chat-window.js` 的 `handleExportConversationClick()` 调用既有 `window.appChatExport.open()` / 隐藏按钮 fallback，并继续发出 `chat-export-click`。
   - Compact inline export/history 相关 i18n key 在 8 个 locale 中全部存在，且插值占位与英文基线一致。
3. 复用并确认既有前端测试覆盖：compact toggle、空态、active/aria 状态、input 不被关闭、气泡选择、全选/取消/反选、selection limit、inline preview 选择同步、超过 50 条历史不裁剪、滚动贴底策略、非文字发送恢复贴底、GalGame 选项上方/下方让位差异、history/preview 几何标记。
4. 复用并确认 NEKO-PC 契约测试覆盖：`history:native` 不进入 hitRects、history 只扩展 native bounds、toolFan 使用独立 native bounds、base surface 不被 extra island 反向污染。

已执行检查：

1. `npm --prefix frontend/react-neko-chat test`：通过，3 个测试文件，103 项测试通过。
2. `npm --prefix frontend/react-neko-chat run typecheck`：通过。
3. `.venv/bin/python -m pytest tests/unit/test_compact_export_history_static_contracts.py`：通过，2 项测试通过。
4. `node --test /Users/tonnodoubt/N.E.K.O.-PC/test/desktop-compact-layout-contract.test.js`：通过，9 项测试通过。

边界：

1. 本步骤只收口测试、i18n 与静态/桌面契约，不新增 compact history 功能表现。
2. 本步骤不把网页端 DOM 测试当作桌面端视觉安全证明；桌面端真实截图矩阵和最终视觉一致性仍按第 12 步执行。

## 第 10 步：导出能力无窗口化和宿主能力完善

修改范围：

1. `static/app-chat-export.js`
2. `static/app-react-chat-window.js` 仅在需要把能力透给 React host 时修改。
3. 必要时新增导出能力单测或宿主适配测试。

目标：

1. 保留 `window.appChatExport.open()` 和 `window.appChatExport.close()` 的现有 full 导出窗口行为。
2. 从 full 导出窗口内部拆出可被 compact inline 调用的纯能力。
3. Compact inline 不调用 `openExportPreviewWindow()`。
4. `MAX_EXPORT_SELECTION = 100` 的限制继续由导出能力统一维护，compact 的全选 / 反选不能绕过。
5. 不新增 Python 后端接口。
6. 不新增 NEKO-PC 业务桥接。
7. 不改变 full preview window 的 DOM、选择逻辑、Open In Window、复制和下载行为。

建议公开能力：

1. `getMessages()`
   - 继续从 `window.reactChatWindowHost.getState().messages` 读取。
2. `buildEntries(messages, selectedIds)`
   - 复用当前 full preview 的 entry 结构。
   - `selectedIds` 为空时返回空选择结果，不自动解释为全量导出。
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

## 第 11 步：Inline preview 接入真实导出能力

修改范围：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx` 或拆出的 `CompactExportPreview`。
3. `static/app-chat-export.js` 能力接口调用层。
4. 必要时更新相关测试和 locale。

目标：

1. 把第 4 步确定的 preview 前端壳接到第 10 步提供的真实无窗口导出能力。
2. Markdown / Image 预览与 full 导出能力一致，但视觉仍保持 compact inline 透明轻量层。
3. 复制和下载调用 `app-chat-export.js` 无窗口能力。
4. 空选择继续禁用最终动作，不默认导出全部。
5. 真实导出失败时在 inline preview 内显示失败状态或 toast，不关闭 history，不丢选择。
6. Preview 展开 / 收起不改变聊天框本体 anchor、不写入桌面端保存位置。
7. Preview 的 DOM hit region 仍只来自真实可交互区域。

检查：

1. 选中 1 条消息后打开 preview，预览只包含该消息。
2. Preview 打开后再选 / 取消消息，预览同步变化。
3. Markdown / Image 预览与 full 导出能力一致。
4. 复制和下载可用。
5. Full 导出窗口不受 compact inline preview 改动影响。
6. 桌面端 preview 不被压缩成窄条，不被模型压住，不导致聊天框跳动。

## 第 12 步：构建、网页端 / 桌面端真实运行验证和收口

最终检查：

1. `git diff --check`
2. `npm --prefix frontend/react-neko-chat test`
3. `npm --prefix frontend/react-neko-chat run typecheck`
4. `bash build_frontend.sh`
5. `node --check static/app-react-chat-window.js`
6. `node --check /Users/tonnodoubt/N.E.K.O.-PC/src/preload-chat-react.js`
7. NEKO-PC 契约测试。
8. 对照总验证清单做网页端和桌面端真实运行验证；桌面端验证优先级高于网页端视觉自检。

如果只改文档，不运行构建；如果改了 `frontend/react-neko-chat/src` 或会影响构建产物，必须运行 `bash build_frontend.sh`。

网页端验证：

1. 打开首页，切到 compact。
2. 进入 input，打开工具轮盘。
3. 点击历史对话按钮，确认 history 出现在紧凑聊天框上方。
4. 再次点击历史对话按钮，确认 history 关闭。
5. 无消息时打开 history，确认显示空状态，不弹旧导出 toast。
6. 发送新消息，确认 history 打开期间实时追加。
7. 触发 assistant streaming，确认在底部时贴底，向上滚动后不抢回。
8. 选择几条消息，点击“导出”，确认 inline preview 展开并同步选择。
9. 触发 GalGame / ChoicePrompt，确认选项在上方时盖住 history / preview，在下方时 history 正常。

桌面端验证：

1. 启动 NEKO-PC。
2. 切到 compact。
3. 分别在展示态、输入态、右侧展开按钮 / toolFan 打开态、history 打开态、preview 打开态、choice 上方、choice 下方进行截图和功能检查。
4. 拖动紧凑聊天框到不同位置，包括靠近屏幕底部。
5. 打开 history，确认聊天框不跳、不跟模型重新吸附。
6. 打开 preview，确认 preview 高度和网页端策略一致，不被压成窄条，不保存错误位置。
7. 在 history 外透明区域点击，确认后方内容可交互。
8. 在 history 内滚动、选择、点击按钮，确认不会漏到下方。
9. 打开 GalGame / ChoicePrompt，确认选项可点且不被 history 挡住。
10. 最小化 / 恢复，确认小球和聊天框仍按原紧凑态规则工作。
11. 打开/关闭 history、preview、右侧展开按钮 / 工具轮盘和 ChoicePrompt 的组合，确认窗口不抖、不跳、不写入错误保存位置。
12. 模型移动或模型 bounds 更新时，已由用户拖动确定的紧凑聊天框位置不被 history / preview 重新吸附。
13. compact 本体、history 和 preview 不被模型窗口压住；最小化小球仍独立在模型左侧，不跟 history / preview 耦合。

完成标准：

1. 网页端和桌面端用户可见行为一致。
2. NEKO-PC 没有业务逻辑复制。
3. Full 导出窗口行为不变。
4. 普通聊天、紧凑输入、工具轮盘、GalGame / ChoicePrompt、最小化小球、拖动位置保存均未被污染。
5. 第 6 步诊断记录中列出的桌面端历史问题全部复测通过，不能只以代码检查代替真实启动。

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
16. Preview 收起后选择状态保留。
17. 空选择不默认导出全部。
18. Markdown / Image 导出能力与 full 预览一致。
19. History 打开期间继续聊天会实时更新。
20. Streaming 更新复用同一条气泡，不产生重复气泡。
21. 用户在底部时自动贴底。
22. 用户向上滚动后 streaming 不抢滚动。
23. 用户重新到底部或主动触发会产生新消息的操作后恢复贴底；覆盖文字、附件 / 图片、截图附件、GalGame 和 ChoicePrompt，不只覆盖打字发送。
24. `sending` 或缺少稳定 id 的消息不可选。
25. 当前 schema 下不引入 `pending` / `retrying` 分支。
26. History 打开期间下方紧凑输入仍可输入、发送和切换工具。
27. Pointer intent 能区分点击、滚动和拖拽阈值。
28. 滚动 history 不触发聊天框拖动、蓝线拖动或模型交互。
29. 气泡内部链接、按钮、图片预览不误触发整条选择。
30. History 外透明 wrapper 不吃事件。
31. Native hit region 只包含真实可交互区域。
32. ChoicePrompt / GalGame 选项位于上方时盖在 history / preview 上方；位于下方时不遮挡 history。
33. ChoicePrompt / GalGame 选项位于上方时 history / preview 视觉让位且不可点；位于下方时保持正常显示和交互。
34. ChoicePrompt 关闭后 history / preview 不抖动。
35. 桌面端 history / preview 不被裁切。
36. 桌面端透明区域不挡点击。
37. History / preview 不参与 base surface anchor。
38. History / preview 不改变用户保存的紧凑聊天框位置。
39. History / preview 不影响最小化小球位置。
40. History 滚动和 streaming 更新不会让桌面端 bounds 高频抖动。
41. 桌面端打开右侧展开按钮 / 工具轮盘不导致窗口剧烈抖动、不推动聊天框本体、不写入错误保存位置。
42. 桌面端 compact 本体、history 和 preview 不被模型窗口压住；最小化小球保持独立窗口和独立位置规则。
43. NEKO-PC 没有复制导出选择、格式化、文件构建或发送业务。
44. 新增用户可见文案同步 8 个 locale。
45. `git diff --check`、React 测试、NEKO-PC 契约测试、`bash build_frontend.sh` 通过。
