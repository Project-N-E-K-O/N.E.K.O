# 首页紧凑态历史层与工具轮盘 UI 优化设计

> 本文只设计紧凑态 inline history 基础功能稳定后的 UI / 交互优化。
> 本文不替代 `home-compact-chat-inline-export-history-design.md` 和 `home-compact-chat-inline-export-history-implementation-plan.md`。
> 若本文与当前代码、测试或真实运行结果冲突，以当前代码和可复现证据为准。

## 设计目标

本次优化只处理用户可见的历史层布局和右侧工具轮盘交互。

目标效果：

1. 历史对话记录区域更宽，阅读空间比紧凑聊天框本体更舒展。
2. 历史气泡保留 user 右侧、assistant / 猫娘左侧的聊天身份，同时通过轻微错落和不同宽度产生“随手翻看旧聊天”的自然感。
3. 右侧展开按钮在支持 hover 的设备上鼠标移入即展开工具轮盘；键盘 focus 也可展开。
4. 工具轮盘两侧渐隐按钮保持当前视觉样式，但只要视觉上可见且 action 可用，就必须可以点击、聚焦并进入桌面端 hit region。
5. 常用按钮显示在最前、最靠近展开按钮、最高视觉权重的位置，形成类似 speed dial 的高频入口。
6. 网页端和 NEKO-PC 桌面端用户可见结果一致，不污染 compact input、full 导出窗口、ChoicePrompt / GalGame、拖动保存位置和最小化小球。

## 成功标准

1. History 可以按 compact 本体比例加宽，并受 viewport / workArea 上限保护。
2. History 加宽后不参与 base anchor、saved position 或最小化小球定位。
3. 气泡错落 token 稳定，同一消息 streaming 更新或重渲染不跳位。
4. 气泡随意感不破坏 user / assistant 身份语义，不破坏选择、链接、按钮和图片命中。
5. Hover-capable 设备上 pointer hover 展开右侧工具轮盘。
6. Keyboard focus 可展开工具轮盘，Escape 可关闭。
7. Touch / no-hover 设备仍可通过 click / tap 展开工具轮盘。
8. Pointer 从展开按钮移动到工具轮盘按钮时不误收起。
9. 工具轮盘两侧淡出按钮保持当前视觉样式且都可点击。
10. 常用按钮排序稳定，前排按钮最靠近展开按钮且视觉权重最高。
11. 优化不改变导出数据能力、full 导出窗口、消息 schema 或 Python 后端。

## 非目标

1. 不改变消息 schema、导出数据能力、历史存储或 Python 后端接口。
2. 不修改 full 导出窗口的选择、复制、下载和 Open In Window 行为。
3. 不把历史列表改成横向 carousel、瀑布流、网格或卡片墙。
4. 不新增使用统计、埋点、localStorage 频率记录或跨会话偏好写入。
5. 不在 NEKO-PC 里实现工具轮盘状态机、工具排序或导出业务。
6. 不把加宽后的 history 作为 compact base surface、saved position 或最小化小球定位依据。

## 参考原则

这些资料只作为交互方案参考，最终仍以 NEKO 当前代码和 geometry 合同为准。

1. Material Design floating action button / speed dial：`https://m1.material.io/components/buttons-floating-action-button.html`
   - 参考少量相关动作从主按钮展开、动作数量克制、动作必须相关、展开后主按钮仍可达。
2. MUI Speed Dial：`https://mui.com/material-ui/react-speed-dial/`
   - 参考 controlled open state、ARIA、tooltip、focus 和 keyboard 行为；NEKO 不引入 MUI 组件。
3. W3C WCAG 2.1 SC 1.4.13 Content on Hover or Focus：`https://www.w3.org/WAI/WCAG21/Understanding/content-on-hover-or-focus`
   - Hover / focus 触发内容必须 dismissible、hoverable、persistent；NEKO 工具轮盘需要 Escape、focus 和 pointer 留存规则。
4. Android Developers Carousel / Material 3 carousel：`https://developer.android.com/develop/ui/compose/components/carousel`
   - 只借鉴 multi-browse / uncontained 中“同一滚动上下文里不同 item 宽度和视觉权重”的节奏，不把聊天历史改成横向 carousel。

## 历史层布局

### 加宽策略

1. History 外层宽度可大于紧凑聊天框本体，宽度由 `--compact-surface-width` 或当前 compact 本体宽度变量派生。
2. 宽度使用比例和上限表达，例如“本体宽度的约 1.15-1.35 倍 + 安全边距”；具体数值通过真实网页端和桌面端截图收口。
3. 禁止写死当前屏幕下的固定宽度，例如直接固定 `430px`、`510px`。
4. 加宽后仍受 viewport / workArea 上限保护，不能出屏，不能压住 ChoicePrompt / GalGame 的优先交互。
5. 内部气泡、图片、buttonGroup、link preview、空状态和 inline preview 都要跟随新容器比例约束，不能保留旧固定 px 宽度导致外层变宽、内容不变。

### Extra Island 边界

加宽后 history 仍是 compact surface extra island：

1. 不参与 base anchor。
2. 不写 saved position。
3. 不影响最小化小球。
4. 不推动 ChoicePrompt / GalGame。
5. Native rect 和 hit rect 都来自最终实际 DOM rect，不能用未加宽前的 compact surface rect 或理论宽度反推。

## 气泡随意感

### 保持聊天语义

1. 历史列表仍是纵向聊天记录，最新消息在底部，消息顺序不变。
2. assistant / 猫娘整体偏左，user 整体偏右。
3. 错落不能跨过中线，不能让用户误判消息是谁发的。
4. 随意感只通过轻微错落、气泡最大宽度差异、间距节奏和轻微边缘变化表达。

### 稳定 Token

错落 token 必须稳定、可复现，建议按 `message.id` 或稳定 sort index 派生：

1. `staggerX`
2. `maxWidthRatio`
3. `verticalGapTone`
4. `edgeSoftness`

规则：

1. 禁止使用每次 render 都变化的 `Math.random()` 或当前时间制造错落。
2. Streaming 更新和重渲染不能让同一条消息跳位。
3. 气泡选择、check 角标、hover、active、drag intent、链接 / 按钮点击命中都必须绑定真实气泡元素 rect，不能按错落前 wrapper 判断。
4. 长文本、URL、代码块、图片和 buttonGroup 必须保持换行、内部滚动或容器约束，不能撑破 history 或遮挡 controls。

## 工具轮盘交互

### Hover / Focus 展开

1. 在 `matchMedia('(hover: hover) and (pointer: fine)')` 成立时，右侧展开按钮 pointer hover 直接打开工具轮盘。
2. Keyboard focus 到展开按钮时也应打开工具轮盘。
3. Touch / no-hover 设备继续保留 click / tap toggle。
4. 工具轮盘打开后，pointer 从展开按钮移动到任意工具按钮时必须保持打开。
5. Pointer 离开展开按钮和工具轮盘整体后，允许短延迟收起；delay 只用于跨透明间隙防误收起，不应用来延迟打开。
6. Escape 必须关闭工具轮盘。
7. 打开状态可以记录临时 open source，例如 `hover`、`focus`、`click`、`programmatic`，但不持久化到宿主或后端。

Hover 设备上的 click 语义实施前必须收口为以下之一：

1. 关闭已打开的工具轮盘。
2. 或执行明确设计过的默认高频动作。

禁止同时造成双 toggle 或误触发 full 导出。

Hover 展开不能：

1. 改变 `compactChatState`。
2. 关闭 compact input。
3. 自动打开 inline history。
4. 调用 `window.appChatExport.open()`。

### 淡出按钮可点击

1. 工具轮盘两侧淡出按钮的视觉样式保持不变，可以继续使用 opacity、mask、filter 或渐隐层表达边缘退场。
2. 只要按钮视觉上仍可见并对应有效 action，就必须保持 enabled、`pointer-events: auto`、keyboard focusable，并输出真实 geometry hit rect。
3. 渐隐遮罩层必须 `pointer-events: none`，不能盖住按钮。
4. 禁止用父容器 `overflow` / `clip-path` / `mask` 让按钮可见但 hit rect 被裁掉；如果视觉裁切不可避免，geometry 应以实际可点击按钮区域为准。
5. `toolFan:native` 或 reserve 区只扩展 bounds；每个真实 button hit region 才进入 hitRects。
6. 视觉 opacity 小于 1 不影响 hit rect。只有完全不可见、disabled 或业务上不可用的 action 才能从 hit region 移除。

### 常用按钮优先

1. 建立 compact tool action 排序函数，输入为当前 actions 和 compact 上下文，输出稳定有序数组。
2. 第一版优先级只使用静态规则：
   - 当前代码已有 action order / priority。
   - 产品明确的静态优先级。
   - 当前状态下可用性，例如有历史时历史对话优先，无附件能力时附件不抢前排。
3. 常用按钮处在最靠近展开按钮的位置，拥有最大 opacity / scale / z-index 和最短 pointer travel path。
4. 低频按钮可以向两侧排布并渐隐，但仍要满足“淡出按钮可点击”规则。
5. 排序必须稳定：同一上下文下 hover、focus、streaming token 不改变顺序；action disabled 只影响可用性和视觉，不让剩余按钮在用户正在 hover 时突然跳位。
6. Tooltip / aria label 跟随 action，不跟随位置硬编码。
7. 所有新增用户可见文案必须同步 8 个 locale。

## Geometry 与桌面端

1. History 加宽和气泡错落后的 geometry 必须来自最终实际 DOM rect。
2. 加宽 history 只能扩展 window bounds 和真实 hit region，不能写入 compact base surface。
3. Hover 展开导致 toolFan 打开时，base surface 仍只来自 capsule / input；toolFan 不参与 saved position、base anchor 或 `--compact-surface-*` 回写。
4. 工具轮盘淡出按钮必须输出按钮自身 hit rect；`toolFan:native` 这类 bounds-only item 仍不得进入 hitRects。
5. NEKO-PC 只能消费页面 geometry 和 input region，不在 preload 里重新实现 hover / focus / click 状态机。
6. 桌面端如果淡出按钮点击失败，优先检查 NEKO 输出的 button hit rect、mask `pointer-events`、NEKO-PC hitRects 和 setShape，而不是改按钮透明度。
7. History 加宽、hover 展开和 toolFan 打开 / 关闭都不能改变用户保存的 compact surface position。
8. 最小化小球仍按独立规则显示，不跟 history 加宽或 toolFan bounds 耦合。

## 修改范围

允许修改：

1. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
2. `frontend/react-neko-chat/src/App.tsx` 中 compact tool fan / export history open 状态相关代码。
3. `frontend/react-neko-chat/src/styles.css`
4. `static/app-react-chat-window.js` 中 compact geometry collector，只在需要补充 history / toolFan button hit rect 时修改。
5. `frontend/react-neko-chat/src/App.test.tsx`
6. 必要时更新 8 个 locale 的 tooltip / aria 文案。

原则上不修改：

1. `static/app-chat-export.js` 的导出构建能力，除非 UI 文案或 preview 状态需要读取已有能力。
2. NEKO-PC 业务逻辑；若桌面端点击失败来自 geometry 消费，只按既有 compact geometry 合同修 hitRects / bounds。
3. Python 后端、消息 schema、历史存储、full 导出窗口。

## 实施顺序

1. 先读当前 compact tool fan、history panel、geometry collector 的真实代码和 `git status`。
2. 先实现历史层加宽，并用 DOM / geometry snapshot 确认 history 不参与 base anchor。
3. 再实现稳定错落 token，确认 streaming / selection 不跳位。
4. 再实现 hover / focus 展开，保留 click / tap 兜底。
5. 再修淡出按钮命中，确保最左 / 最右按钮网页端和桌面端都可点。
6. 最后做常用按钮排序，确保排序稳定且不在 hover 中跳位。

## 检查

1. `npm --prefix frontend/react-neko-chat test -- --run App.test.tsx -t "compact"`
2. `npm --prefix frontend/react-neko-chat run typecheck`
3. `bash build_frontend.sh`
4. `node --check static/app-react-chat-window.js`
5. 如果改到 NEKO-PC geometry 消费，运行对应 NEKO-PC 契约测试。
6. `git diff --check`

## 真实运行验收

网页端：

1. 打开 compact，确认 history 比本体更宽但不出屏。
2. 长历史、短历史、空状态、preview 展开都保持宽度策略一致。
3. 气泡左右身份清楚，边缘不完全齐平但仍可读、可选。
4. Hover 到右侧展开按钮，工具轮盘自动展开。
5. Pointer 从展开按钮移动到工具轮盘按钮时不误收起。
6. 点击最左和最右淡出按钮，action 正常触发。
7. 常用按钮位于前排，tooltip / aria label 正确。

桌面端：

1. History 加宽后窗口 bounds 包含可见区域，但透明空白不挡点击。
2. History 加宽不改变 compact 本体保存位置，不影响最小化小球。
3. Hover 展开 toolFan 不导致窗口抖动或 compact 本体跳位。
4. 最左 / 最右淡出按钮都能点击，且不会漏点到模型。
5. ChoicePrompt / GalGame 位于上方时仍覆盖 history；位于下方时 history 正常。
6. 输入态、展示态、preview、toolFan、history 组合下用户可见结果与网页端一致。

## 禁止方案

1. 禁止历史层加宽后把 history union 写入 base surface、保存位置或最小化小球定位。
2. 禁止用每次 render 随机数制造历史气泡错落。
3. 禁止 hover 展开只支持鼠标而没有 focus / click / Escape 兜底。
4. 禁止让视觉淡出的工具按钮变成不可点击，除非该 action 业务上明确 disabled 或完全不可见。
5. 禁止为了“常用按钮在最前”新增未经设计的行为统计、后端接口或跨会话数据写入。
6. 禁止在 NEKO-PC 复制工具排序、hover 状态机、导出选择或格式化业务。
