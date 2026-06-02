# 紧凑聊天框 geometry 抖动问题清单

> 本文记录 2026-06-01 基于 NEKO 后端页面、NEKO-PC Electron 壳、真实 `/chat` compact 交互复现得到的问题清单。
> 目的不是记录临时测试日志，而是给后续修复提供完整问题面和优先级。
> 若本文与当前代码或新的复现实验冲突，以当前可复现证据为准，并先更新本文。

## 结论摘要

这次抖动的核心不是 `default / options / input` 三态本身。实测表明，主要问题是 compact surface 的 base anchor、choice、tool fan、history 等 extra island 在页面和 NEKO-PC 之间形成 geometry feedback loop。实施 1-3 后，idle 阶段的持续 relayout 回环已有明显收敛，但用户实测仍能看到抖动、闪烁、短暂露出其它状态；这说明当前问题面已经从“持续 geometry 回环”推进到“首帧/过渡帧/状态切换时序未收口”。

本文约束所有后续修复：不能为了消除抖动而破坏 `home-compact-chat-mode-design.md` 中定义的完整功能流程，也不能通过简单裁剪、隐藏、缩小、禁用 overflow 等方式让展开内容显示不全。Choice、tool fan、history、resize、drag、minimized ball、history drag/drop 等能力都必须在修复后继续可见、可点、可拖、可恢复。视觉验收不能只看 idle 事件计数，还必须检查启动、展开、关闭、拖动、缩放前 300ms 内没有闪出错误状态或未定位状态。

简化链路：

1. 页面采集 compact geometry。
2. 页面派发 `neko:compact-interaction-geometry-change`。
3. NEKO-PC preload 收到后 relayout，计算窗口 bounds/native region/hit region。
4. NEKO-PC 通过 `__nekoDesktopCompactLayout` 回写页面。
5. 页面收到 `neko:desktop-compact-layout-change` 后重新同步 anchor 和 geometry。
6. extra island 的尺寸、位置、滚动条、动画或 placement 又改变 geometry，继续触发 relayout。

## 已证实的核心问题

### 1. 页面与 NEKO-PC 之间存在 geometry feedback loop

相关代码：

1. `static/app-react-chat-window.js`
   - `syncCompactInteractionGeometry()`
   - `neko:desktop-compact-layout-change` listener
   - `scheduleCompactMinimizeBallTracking()`
2. `N.E.K.O.-PC/src/preload-chat-react.js`
   - `neko:compact-interaction-geometry-change` listener
   - `activateDesktopCompactWindow()`
   - `applyDesktopCompactLayoutToPage()`

问题：

页面发 geometry change，PC relayout 并回写 layout；页面收到回写后又重新采样。只要 geometry 中有动态项，这条链就可能反复触发。

影响：

1. 桌面窗口 `setBounds` 反复执行。
2. 页面 CSS 变量反复回写。
3. native shape / hit region / passthrough 反复刷新。
4. 用户看到输入框、选项层、历史层或透明点击区抖动。

### 2. `desktop-compact-layout-change` 会无条件清空 surface anchor snapshot

相关代码：

1. `static/app-react-chat-window.js`
   - `compactSurfaceAnchorSnapshot = ''`
   - `scheduleCompactMinimizeBallTracking()`

问题：

NEKO-PC 每次回写 layout，页面都把 anchor snapshot 清掉。即使 base surface 位置和尺寸没有变化，只是 extra island、windowBounds 或 ball 有变化，页面也会重新 apply surface anchor。

影响：

1. 不必要的 `--compact-surface-*` CSS 变量重写。
2. 不必要的 `neko:compact-surface-layout-change`。
3. 额外触发 geometry 采样和 PC relayout。

修复方向：

只有 base surface 目标变化时才重置 anchor snapshot。`windowBounds`、ball、extra island 变化不能默认重置 base anchor。

### 3. base surface 与 extra island 边界不清

相关代码：

1. `static/app-react-chat-window.js`
   - `collectCompactSurfaceGeometryItems()`
   - `surfaceUnion`
   - `baseSurfaceRect`
2. `N.E.K.O.-PC/src/desktop-compact-layout.js`
   - `isDesktopCompactSurfaceAnchorKind()`
   - `buildDesktopCompactLayoutRects()`

问题：

当前已经有 `baseSurfaceRect` 和 base item 分类，但 choice、tool fan、history 仍会进入 surface native union，并反推 window bounds。实际运行中这些 extra island 应该是围绕 base surface 的扩展交互岛，而不是新的 base anchor。

影响：

1. Choice 打开后 surface union 高度变大。
2. Tool fan 打开后 surface union 宽高变大。
3. History 打开后 surface union 被大面板强烈扩张。
4. PC carrier window 被 extra island 牵引移动，进而改变页面局部坐标。

修复方向：

1. base surface 只由 `surfaceShell | capsule | input` 决定。
2. drag handle 可属于 base hit region，但不应改变 base anchor。
3. choice/toolFan/history 是 extra island，只能扩展 native/hit region，不能更新保存的 surface anchor。
4. PC 回写给页面的 `layout.surface` 必须稳定来自 base surface。

### 4. History 面板是最强抖动源之一

相关代码：

1. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
2. `frontend/react-neko-chat/src/styles.css`
   - `.compact-export-history-anchor`
   - `.compact-export-history-scroll`
   - `.compact-export-history-controls`
3. `static/app-react-chat-window.js`
   - history geometry collection

实测现象：

History 首屏打开后，history rect 很大，并且可能出现负坐标。即使静止 1.5 秒，仍继续新增页面 geometry 和桌面 relayout。

问题：

1. history 外框、scroll 区、controls、scrollbar、子项都可能参与 geometry。
2. hover scrollbar、mask、controls collapsed、auto-scroll、内容高度变化会改变 DOMRect。
3. history 的大面板被并入 surface union 后强烈反推 window bounds。

修复方向：

1. history native rect 应使用稳定外框或稳定 reserve rect。
2. 内部子项、scrollbar、hover 状态不应持续改变 native union。
3. 子项只作为 hit rect 或 history drag 数据，不作为 carrier window 的动态 anchor。
4. history open 状态持久化要谨慎处理，避免启动即打开大面板导致首帧 relayout。

### 5. Choice placement 锚点不正规

相关代码：

1. `frontend/react-neko-chat/src/App.tsx`
   - compact choice placement effect
   - `appShellRef`
   - `compactChoiceLayerRef`
2. `frontend/react-neko-chat/src/styles.css`
   - `.compact-chat-choice-anchor`
3. `N.E.K.O.-PC/src/desktop-compact-layout.js`
   - `compactChoicePlacement`

问题：

React 侧 choice placement 使用 `appShellRef` 参与测量，并且每帧追踪。compact 模式下正确锚点应是 compact surface/input shell，而不是整个 app shell 或 carrier window。

影响：

1. above/below 判断可能跟 PC relayout 互相打架。
2. PC 算出的 `compactChoicePlacement` 与页面自算 placement 不总是同源。
3. 拖拽或扩窗后 choice 可能从上方跳到下方，或反复触发 geometry。

修复方向：

1. 页面自算 placement 时以 compact surface shell/input shell 为锚点。
2. NEKO-PC 已给出 `compactChoicePlacement` 时，React 应优先信任并停止每帧自算。
3. 只在选项内容高度、surface base rect 或 PC placement 改变时重新计算。

### 6. Geometry diff 过于敏感

相关代码：

1. `static/app-react-chat-window.js`
   - `JSON.stringify(snapshot)` 精确比较

问题：

整个 geometry snapshot 用完整 JSON 精确比较。DOMRect 小数、过渡动画中间帧、滚动条显示、hover、mask、内容高度微变都会触发 change。

影响：

1. 动画期间每帧触发 relayout。
2. 静止前的中间态被 PC 当作真实布局。
3. 小数级变化造成不必要的窗口/shape 更新。

修复方向：

1. 对 native/window 相关 rect 做整数或半像素稳定化。
2. 分离 visual rect、hit rect、native rect 的 diff 级别。
3. 动画中只发必要 hit 更新，动画结束再发 native final。
4. PC 侧收到事件后先判断是否影响 native/window bounds，再决定 relayout。

### 7. Tool fan 细项过多

相关代码：

1. `frontend/react-neko-chat/src/App.tsx`
   - `.compact-input-tool-fan`
   - tool wheel state
2. `static/app-react-chat-window.js`
   - `collectCompactToolFanGeometryItems()`
3. `N.E.K.O.-PC/src/desktop-compact-layout.js`
   - `buildDesktopCompactToolFanReserveRect()`

问题：

Tool fan 打开后会产生大量 `toolFan` geometry item。PC 虽然有 reserve rect 思路，但页面仍会把很多按钮、hover 区、popover 子项作为变化源。

影响：

1. tool wheel hover、slot、动画、drag guard 会产生多次 geometry change。
2. native union 易被细项变化扰动。

修复方向：

1. native bounds 使用稳定 reserve rect。
2. 细按钮只作为 hit rect。
3. tool wheel 动画中不要持续刷新 native union。

### 8. Drag / resize 期间 source of truth 不明确

相关代码：

1. `frontend/react-neko-chat/src/App.tsx`
   - `neko:compact-surface-resize-request`
   - `--compact-surface-resize-width`
2. `static/app-react-chat-window.js`
   - resize session
   - layout change
3. `N.E.K.O.-PC/src/preload-chat-react.js`
   - `desktopCompactSurfaceResizeActive`
   - `desktopCompactSurfaceDragActive`
   - `desktopCompactPendingWindowBounds`

问题：

拖拽和缩放时，页面、static bridge、PC preload 都可能参与写 surface 位置/宽度。用户操作期间应该有唯一 source of truth，但当前阶段边界不够清晰。

影响：

1. resize 过程中页面临时宽度与 PC 回写宽度可能抢优先级。
2. drag 过程中 PC target、pageBounds、pending bounds 可能滞后一帧。
3. 操作结束后可能出现一次或多次补偿 relayout。

修复方向：

1. drag/resize active 时由用户操作 target 做唯一 source of truth。
2. PC 回写只更新 carrier/native，不反向清空页面 anchor。
3. end 阶段保存最终 surface，再做一次稳定 relayout。
4. `desktopCompactPendingWindowBounds` 与 actual bounds 对齐前不要引入新的 anchor 写入。

## 其它会放大抖动的问题

### 9. React 侧 choice placement 存在多重追踪源

相关代码：

1. `frontend/react-neko-chat/src/App.tsx`
   - `requestAnimationFrame(trackPlacement)`
   - `ResizeObserver`
   - `visualViewport` listeners

问题：

同一件事同时由 rAF、ResizeObserver、window resize、visualViewport resize/scroll 触发。PC 已经有 layout 回写时，这些监听容易重复计算。

修复方向：

只保留必要触发源。PC compact 环境下优先使用 PC placement；非 PC 环境再使用 ResizeObserver/resize。

### 10. React 会发空 detail 的 geometry-change

相关代码：

1. `frontend/react-neko-chat/src/App.tsx`
   - 工具扇状态 effect 派发 `neko:compact-interaction-geometry-change`
2. `N.E.K.O.-PC/src/preload-chat-react.js`
   - 收到事件即 relayout

问题：

部分 geometry-change 事件没有 detail。PC 收到后无法知道变化是否影响 native/window bounds，只能 relayout。

修复方向：

1. 页面侧提供变化原因和影响范围。
2. PC 侧无 detail 或不影响 native/window 的事件不触发完整 relayout。
3. 将 hit-only 更新和 native/window 更新拆成不同事件或不同 phase。

### 11. CSS 变量 source of truth 分裂

相关变量：

1. `--compact-surface-left`
2. `--compact-surface-top`
3. `--compact-surface-width`
4. `--compact-surface-height`
5. `--desktop-compact-surface-left`
6. `--desktop-compact-surface-top`
7. `--desktop-compact-surface-width`
8. `--desktop-compact-surface-height`
9. `--compact-surface-resize-width`

问题：

页面 static bridge、React、NEKO-PC preload 都会写或读取这些变量。不同变量的优先级在不同 CSS 规则里不完全一致。

影响：

1. history、choice、surface 使用的宽度/位置可能不是同一个来源。
2. resize 结束清理临时变量时可能出现瞬跳。
3. PC 回写和页面 fallback 同时存在时容易误判。

修复方向：

1. 明确 PC compact 下唯一布局输入是 `__nekoDesktopCompactLayout.surface`。
2. 非 PC/web 下使用 `--compact-surface-*`。
3. resize 临时变量只在非 PC 或 active resize 阶段生效。

### 12. History open 状态持久化可能导致首帧抖动

相关代码：

1. `frontend/react-neko-chat/src/App.tsx`
   - `neko.reactChatWindow.compactExportHistoryOpen`

问题：

如果 history 上次打开，下一次 compact 初始化时 history 会直接挂载，导致首帧就有大 extra island 进入 geometry。

修复方向：

1. PC compact 下可考虑不持久化 history open，或延迟到 base layout 稳定后再恢复。
2. 恢复 history 前先冻结 base surface anchor。

### 13. PC pending bounds 可能滞后一帧

相关代码：

1. `N.E.K.O.-PC/src/preload-chat-react.js`
   - `desktopCompactPendingWindowBounds`
   - `sameWindowBounds(actualBounds, desktopCompactPendingWindowBounds)`

问题：

`setBounds` 后 OS 实际 bounds 可能下一轮才追上。此时 relayout 使用 pending bounds 或 actual bounds 的切换可能造成二次 rebase。

修复方向：

1. pending bounds 只用于 carrier window 对齐，不参与 base anchor 更新。
2. actual bounds 未追上前，不向页面派发会清空 anchor 的 layout change。

### 14. Renderer bounds rebase 是第二条回写路径

相关代码：

1. `N.E.K.O.-PC/src/preload-chat-react.js`
   - `syncDesktopCompactLayoutToRendererBounds()`
   - `rebaseDesktopCompactLayoutToWindowBounds()`

问题：

当实际 renderer bounds 与预期 window bounds 不一致时，preload 会 rebase 并再次回写 page layout。这条兜底路径在 extra island 扩窗时可能放大反馈。

修复方向：

1. rebase 只更新 page-local rect，不改变 base surface screen anchor。
2. rebase 后的 layout-change 不应触发页面重新定位 base surface。

### 15. History scrollbar / hover / controls collapse 会改变 hit 或 visual 几何

相关代码：

1. `frontend/react-neko-chat/src/styles.css`
   - `.compact-export-history-scroll:hover`
   - `.compact-export-history-controls`
   - `.controls-collapsed`

问题：

hover scrollbar、controls collapse、preview open 等视觉状态可能改变可测 DOMRect 或 hit region。

修复方向：

1. history 外层 native rect 固定。
2. scrollbar/hover 只影响视觉，不影响 native carrier bounds。
3. controls collapse 若改变高度，应显式发送一次 final geometry，而不是持续被 DOM 采样捕获。

## 目标重构结构

修复不应继续在现有事件链上零散打补丁。建议把 compact desktop geometry 重构成“页面声明、桥接稳定化、Electron 承载”的三层结构，并在数据上明确区分 anchor、carrier、content。

可参考当前模型与模型旁边按钮的 Electron 交互模式：模型拖动时没有明显抖动，说明 Electron 透明窗口、`setBounds`、置顶、shape/passthrough 本身不是必然抖动源。这个模式的关键是主锚点单一，旁边按钮是跟随项；按钮可以显示、点击、跟随模型，但不会反向重定义模型本体的锚点。compact chat 应采用同样原则：compact surface 是主锚点，choice/tool fan/history 是 follower island。

### 目标分层

#### 1. React UI 层：只负责内容和交互

所属文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
3. `frontend/react-neko-chat/src/styles.css`

职责：

1. 渲染 compact surface、choice、tool fan、history、preview、drag/drop。
2. 提供必要 DOM 标记，声明哪些元素是 base surface，哪些是 extra island。
3. 在用户操作时发出明确 intent：
   - resize start/move/end
   - drag start/move/end
   - tool fan open/close
   - history open/close
   - choice open/close
4. 不直接决定 Electron carrier window 的 screen bounds。
5. 在 NEKO-PC 已提供 placement/layout 时，不再每帧自算桌面布局。

#### 2. Static bridge 层：负责稳定 geometry contract

所属文件：

1. `static/app-react-chat-window.js`

职责：

1. 从 DOM 采集 geometry。
2. 输出稳定的 `CompactInteractionGeometrySnapshot`。
3. 将 DOMRect 分成三类：
   - `baseSurfaceRect`：input/capsule/surface shell，唯一锚点。
   - `extraIslands`：choice/toolFan/history 等扩展内容。
   - `hitRects`：实际可点、可拖、可滚动区域。
4. 对 geometry 做稳定化：
   - round rect。
   - 区分 native/window 变化、hit-only 变化、visual-only 变化。
   - 动画中合并事件，结束后提交 final。
5. 只在 base surface 真实变化时更新 surface anchor。
6. 不把 extra island union 写回为新的 base surface。

#### 3. NEKO-PC preload 层：负责 Electron carrier 与 native hit/shape

所属文件：

1. `N.E.K.O.-PC/src/preload-chat-react.js`
2. `N.E.K.O.-PC/src/desktop-compact-layout.js`

职责：

1. 读取页面声明的 geometry。
2. 转换 page rect 到 screen rect。
3. 根据 base surface 计算稳定 surface anchor。
4. 根据 extra island 计算 carrier window union。
5. 根据 hit rect/native rect 更新 input shape 和 passthrough。
6. 将结果回写为 `__nekoDesktopCompactLayout`。
7. 回写 layout 时只更新页面需要的局部坐标，不改变产品语义。
8. drag/resize active 时以用户操作 target 为唯一临时 source of truth。

#### 4. Main process 层：只执行窗口能力

所属文件：

1. `N.E.K.O.-PC/src/main/window-control-ipc.js`
2. `N.E.K.O.-PC/src/window-manager.js`
3. 相关 top/shape/ball window 模块

职责：

1. 执行 `setBounds`。
2. 维护 always-on-top / z-order。
3. 管理外部 minimized ball window。
4. 应用 native shape / input region。
5. 不读取或推断 compact 产品状态。

### 新数据模型建议

#### 页面输出：`CompactInteractionGeometrySnapshot`

建议结构：

```ts
type CompactInteractionGeometrySnapshot = {
  mode: 'compact' | 'minimized';
  compactChatState: 'default' | 'options' | 'input';
  phase?: 'idle' | 'opening' | 'closing' | 'dragging' | 'resizing';
  reason?: 'base' | 'choice' | 'toolFan' | 'history' | 'resize' | 'drag' | 'ball';
  viewport: { width: number; height: number };
  baseSurfaceRect: Rect | null;
  islands: {
    choice?: CompactIslandGeometry;
    toolFan?: CompactIslandGeometry;
    history?: CompactIslandGeometry;
  };
  hitRects: CompactHitRect[];
  ballRect?: Rect | null;
  externalBall?: Rect | null;
  stableKey: string;
};
```

原则：

1. `baseSurfaceRect` 是唯一 anchor。
2. `islands.*.nativeRect` 用于 carrier window 扩展。
3. `islands.*.hitRects` 用于点击/拖拽/滚动。
4. `islands.*.visualRect` 只用于调试或视觉判断，不直接触发 window bounds。
5. `stableKey` 只包含会影响 Electron carrier/native/hit 的稳定字段。

#### PC 回写：`DesktopCompactLayout`

建议结构：

```ts
type DesktopCompactLayout = {
  surface: Rect;              // page-local base surface
  surfaceScreenRect: Rect;    // screen-space base surface
  windowBounds: WindowBounds; // Electron carrier
  workArea: Rect;
  ball: Rect | null;
  compactChoicePlacement: 'above' | 'below' | null;
  nativeRects: Rect[];
  hitRects: Rect[];
  layoutVersion: number;
  anchorVersion: number;
};
```

原则：

1. `surface` 永远表示 base surface，不表示 choice/toolFan/history union。
2. `windowBounds` 可以因 extra island 扩大。
3. `nativeRects/hitRects` 可以包含 extra island。
4. `anchorVersion` 只在 base surface screen rect 改变时递增。
5. 页面只有看到 `anchorVersion` 变化时才允许重置 anchor snapshot。

### 新事件流建议

#### idle / 普通展开

1. React 渲染 base surface 和 extra island。
2. Static bridge 采集 stable geometry。
3. 如果 `stableKey` 与上一轮相同，不派发事件。
4. 如果 native/window 相关字段变化，派发 `neko:compact-interaction-geometry-change`。
5. PC preload 判断影响范围。
6. PC 只在 carrier/native/hit 需要变化时 relayout。
7. PC 回写 `DesktopCompactLayout`。
8. 页面只根据 `anchorVersion` 判断是否重置 base anchor。

#### resize

1. React 发送 `neko:compact-surface-resize-request`，包含 phase 和 screenRect target。
2. PC 设置 resize active。
3. resize active 阶段 PC 用 target 作为 base surface source of truth。
4. 页面只做视觉临时宽度，不从 PC 回写反向清 anchor。
5. end 阶段 PC 保存最终 surface width/position。
6. PC 清 active flag，发一次 final layout。

#### drag

1. React / preload drag bridge 发送 drag target。
2. PC 设置 drag active。
3. drag active 阶段 avatar sync、extra island relayout 不覆盖 drag target。
4. end 阶段保存最终 surface。
5. final layout 后恢复普通 geometry 消费。

#### choice / tool fan / history 展开

1. React 打开对应 extra island。
2. Static bridge 输出 extra island native reserve rect 和 hit rect。
3. PC 扩展 carrier window 和 hit/native shape。
4. PC 不更新保存的 surface anchor。
5. 页面不因 carrier 扩展重新定位 base surface。

### 展开内容显示优化原则

修复抖动时，不能把 extra island 简单从 carrier/native 中移除。正确策略是“锚点不污染，内容仍完整承载”。

### 参考模型旁按钮模式

模型旁按钮的稳定拖动模式可作为 compact chat 的实现参照：

1. 主体锚点单一：
   - 模型位置由模型拖动逻辑决定。
   - compact chat 中对应的是 base surface。
2. 附属内容单向跟随：
   - 模型旁按钮跟随模型。
   - compact chat 中对应 choice/tool fan/history 跟随 base surface。
3. 附属内容不反向污染主体：
   - 按钮不会改变模型中心或模型保存位置。
   - choice/tool fan/history 不应改变 compact surface anchor。
4. Electron 只做承载和命中：
   - 透明窗口、shape、passthrough 为主体和按钮提供可见/可点区域。
   - compact chat 中 carrier window 应容纳 base surface 与 extra island，但不把 extra island union 回写成新的 base surface。
5. 操作期 source of truth 单一：
   - 模型拖动时鼠标拖动目标是唯一位置来源。
   - compact surface drag/resize 时用户操作 target 应是唯一临时来源。

这个参考模式可作为判断修复是否走偏的快速检查：如果某个实现让 follower island 反过来推动 anchor，或为了不抖直接裁掉 follower island，就不符合该模式。

#### Choice

1. 使用 base surface 作为位置锚点。
2. PC compact 下优先使用 PC 回写的 `compactChoicePlacement`。
3. 可见内容必须在 carrier/native 覆盖内。
4. 空间不足时使用内部滚动，而不是让 carrier 裁掉选项。

#### Tool fan

1. native reserve rect 覆盖整个工具扇可见区域。
2. 每个按钮继续作为 hit rect。
3. hover 区可以影响 hit，但不影响 base anchor。
4. popover 若超出 reserve rect，需要显式加入 extra island native/hit。

#### History

1. history 外层 anchor/panel 提供稳定 native rect。
2. scroll 内容、bubble、scrollbar、hover thumb 不持续改变 carrier bounds。
3. preview open/controls collapse 若改变真实可见区域，只提交一次 final native rect。
4. 内容过多时内部滚动，不能依赖 carrier 裁剪。

#### Resize 后对齐

1. choice/history/tool fan 都应读取同一个 base surface width。
2. resize active 时允许使用临时宽度。
3. resize end 后统一切回 PC 保存后的 base surface width。

### 文档内容优化方向

后续维护本文时，建议保持以下结构，不再把临时测试过程混入正文：

1. 问题清单：记录症状、相关文件、影响、修复方向。
2. 目标结构：记录理想分层、数据模型、事件流。
3. 实施方案：记录可提交的步骤、涉及文件、验收标准。
4. 验证用例：记录必须跑的真实流程和截图/rect 断言。
5. 已废弃猜测：如果某个猜测被证伪，只在一句话说明，不展开成主线。

这样文档服务于后续实现，而不是变成一次排查过程的流水账。

## 建议修复顺序

### 实施前判定标准

进入代码实施前，先统一以下判断，避免修复时重新把边界打散：

1. `default / options / input` 三态不是本轮抖动根因。本轮不以删除或改写三态作为主修复路径；若实现中碰到三态逻辑，只能修正它与 geometry contract 的接入方式。
2. `layout.surface` 与 `surfaceScreenRect` 的语义必须固定为 base surface。`windowBounds`、`nativeRects`、`hitRects` 可以被 choice/toolFan/history 扩大，但不能把这些 extra island 的 union 回写成新的 surface。
3. `anchorVersion` 是目标结构；如果第一阶段还没有新增该字段，则用 stable base surface snapshot 临时代替。判断依据只能是 base surface 的 screen rect，而不是 carrier window bounds。
4. drag/resize active 期间允许 base target 持续变化，但 source of truth 必须是用户操作 target。此阶段 PC layout 回写不能让页面重新采样并覆盖正在操作的 target。
5. choice/toolFan/history 展开、关闭、hover、滚动、内部动画只允许改变 extra native/hit 或 visual 状态；除非用户主动拖动/缩放 compact surface，否则不得递增 anchor 或重置 page anchor snapshot。
6. 修复是否正确，以“base anchor 稳定 + carrier 完整承载 + hit 区域可用”三件事同时成立为准。只做到不抖但内容显示不全，不算通过。

### 硬性非回归约束

以下约束优先于单点修复方案。任何实现若满足“少抖动”但违反这些约束，都不算完成。

1. 设计文档要求的完整功能流程不能断：
   - compact / minimized 切换。
   - default / options / input 内部状态。
   - GalGame options / ChoicePrompt 展开与选择。
   - 工具转轮展开、点击、hover、drag guard。
   - 历史面板展开、选择、预览、导出。
   - 历史内容拖拽到角色并发送。
   - 左右 resize。
   - compact surface drag。
   - 独立 minimized ball 显示、点击、恢复。
2. 展开内容不能被粗暴裁剪：
   - choice 选项不能被 carrier window、native shape、CSS `overflow` 裁掉。
   - tool fan 的按钮、hover 区、popover 不能显示不全。
   - history 面板、controls、preview、scroll 内容不能被窗口边界裁掉到不可用。
   - resize 后 history/choice/tool fan 必须跟随新的 base surface 宽度正常对齐。
3. 修复不能用隐藏功能绕过问题：
   - 不能直接禁用 history、tool fan、choice、drag、resize。
   - 不能把 extra island 从 hit/native 中完全移除导致点不到。
   - 不能用固定小窗口牺牲内容完整显示。
4. NEKO-PC carrier window 仍必须能容纳所有当前展开的可见交互岛：
   - base surface 是锚点。
   - extra island 不污染 base anchor。
   - 但 extra island 仍要进入 carrier/native/hit 计算，保证显示和点击完整。
5. Web / 独立 `/chat` / NEKO-PC 需要分别验证。PC 专属优化不能破坏普通网页 compact 体验。

### P0：阻断反馈链

1. `desktop-compact-layout-change` 不再无条件清空 page anchor snapshot。
2. PC 收到 `neko:compact-interaction-geometry-change` 后先判断是否影响 native/window bounds。
3. base surface anchor 与 extra island union 拆开。

### P1：稳定 extra island

1. History 改稳定 native reserve rect。
2. Choice 使用 compact surface shell/input shell 锚点。
3. Tool fan native 使用稳定 reserve rect，细项仅用于 hit。

### P2：修正操作期所有权

1. Drag active 阶段由 drag target 独占 surface source of truth。
2. Resize active 阶段由 resize target 独占 width/source of truth。
3. end 阶段保存最终 surface，再统一 relayout。

### P3：降低事件敏感度

1. Geometry diff 做 rounded/stable summary。
2. 区分 native/window 变化、hit-only 变化、visual-only 变化。
3. 动画中节流，动画结束提交 final geometry。

### P4：收口持久化和启动恢复

1. 评估是否保留 history open 持久化。
2. PC compact 启动时先稳定 base surface，再恢复 history/choice/tool fan。
3. 检查 `--compact-surface-resize-width` 与 desktop width 的优先级。

## 后续验证用例

修复后至少跑以下真实流程：

1. 打开 NEKO-PC `/chat` compact，保持 idle 2 秒，确认无持续 relayout。
2. GalGame options 打开，确认 choice 不反推 base surface anchor。
3. Tool fan 打开/关闭，确认 native window 不被细项动画持续推动。
4. History 打开，确认 idle 2 秒内无持续 geometry-change/relayout。
5. 右侧 resize，确认宽度稳定保存，结束后只发生一次 final relayout。
6. 拖动 compact surface，确认窗口移动后 base surface 不回弹、不二次漂移。
7. Choice above/below 在靠近工作区边缘时只切一次，不往复跳。
8. 最小化/恢复 compact，确认 ball 和 surface 不互相反推。
9. 每个展开态都要截图或 DOM rect 验证内容完整显示：
   - choice 三个选项完整可见或可滚动。
   - tool fan 所有当前可交互按钮完整可见且可点击。
   - history scroll、controls、preview 不被裁掉到不可操作。
   - resize 后展开内容仍跟随 surface 对齐。
10. Web 普通浏览器模式至少验证一次 compact 打开、choice、tool fan、history，不因 PC 修复出现回归。

### 最小日志验收口径

自动化或手动复现时，建议统一记录以下计数，避免只凭肉眼判断：

1. 页面侧：
   - `neko:compact-interaction-geometry-change`
   - `neko:desktop-compact-layout-change`
   - surface anchor apply / reset
   - `neko:compact-surface-layout-change`
2. NEKO-PC preload 侧：
   - geometry event received
   - relayout scheduled / executed
   - page layout apply
   - duplicate layout skipped
   - `setBounds` requested / skipped
3. 判断标准：
   - 初次进入 compact 可以有初始化 relayout。
   - choice/toolFan/history 打开或关闭可以有有限次 relayout。
   - 每个展开态稳定后 2 秒内，不应继续新增 native/window relayout。
   - hover、scrollbar 显示、tool fan hover、history 内部滚动不应触发 carrier bounds 变化。
   - drag/resize active 中可以高频更新，但 end 后必须收敛到一次 final layout，随后 idle 为 0。

## 分点实际实施方案

本节把前面的问题拆成可落地的修改点。实施时建议一组一组提交，每组都跑对应验证用例，避免一次性改动让 geometry 链路不可追踪。

### 推荐提交边界

为了降低风险，建议按以下边界拆提交：

1. 先改 NEKO-PC layout contract：
   - 固定 `layout.surface` / `surfaceScreenRect` 为 base surface。
   - extra island 只影响 carrier/native/hit。
   - 不碰 React 视觉和功能逻辑。
2. 再改 static bridge 去重与 anchor reset：
   - 阻断无条件 reset。
   - 增加 stable base snapshot / stable geometry summary。
   - 保留旧 snapshot 字段，避免一次性改坏消费者。
3. 再改 React ownership：
   - 首帧/过渡帧视觉 gating。
   - choice placement PC 优先。
   - history native reserve rect。
   - tool fan native/hit 分层。
4. 再处理 drag/resize active 期所有权和持久化恢复。
5. 最后做 geometry diff 降噪、CSS 变量优先级与 smoke 自动化固化。
6. 每一步都跑对应最小验证，不把多个问题合并到一个不可拆的大改里。

### 当前实施基线

2026-06-01 实施前检查结果：

1. 工作区状态：
   - `N.E.K.O.-PC` 当前无未提交改动。
   - `N.E.K.O` 当前与本任务相关的未跟踪文件是本文档。
   - `.agent/notes/` 已存在为未跟踪目录，本任务不依赖也不处理。
2. 基线命令：
   - `node --test test/desktop-compact-layout-contract.test.js`
   - `node --check static/app-react-chat-window.js`
   - `node --check src/preload-chat-react.js && node --check src/desktop-compact-layout.js`
   - `npm run typecheck` in `frontend/react-neko-chat`
   - `./build_frontend.sh`
3. 基线结果：
   - NEKO-PC desktop compact contract tests：32 passed。
   - `static/app-react-chat-window.js` syntax check passed。
   - `preload-chat-react.js` / `desktop-compact-layout.js` syntax check passed。
   - React typecheck passed。
   - Frontend full build passed。Vite 仅输出已有 chunk size / dynamic import warning。
4. 已有合同：
   - `desktop-compact-layout.js` 已有 base anchor 与 extra island 分类。
   - 现有 contract tests 已覆盖 history bounds-only、tool fan reserve、choice relocation、drag/resize 等部分核心原则。
5. 实施前风险集中点：
   - static bridge 仍对完整 geometry snapshot 做 `JSON.stringify` 精确比较。PC 侧 relayout gate 已由实施 3 处理；页面侧 stable summary 留给 P3。
   - `neko:desktop-compact-layout-change` listener 会在非 resize active 时无条件清空 `compactSurfaceAnchorSnapshot`。此项已由实施 2 处理。
   - React choice placement 曾存在持续 rAF tracking，PC forced placement 虽优先，但 tracking 源仍多；该项已由实施 4 收口，实施 6 只保留 GalGame/options 首帧和边缘空间专项。
   - tool fan 页面侧仍输出多个 circle slice 与按钮 native rect，PC 虽有 reserve 兜底，但事件源仍偏细。此项留给实施 7 / P3。

### 第一阶段改动入口

第一阶段建议只处理 P0，不直接动视觉样式：

1. `static/app-react-chat-window.js`
   - `collectCompactSurfaceGeometryItems()`
   - `getCompactInteractionGeometrySnapshot()`
   - `syncCompactInteractionGeometry()`
   - `syncCompactSurfaceAnchor()`
   - `neko:desktop-compact-layout-change` listener
2. `N.E.K.O.-PC/src/preload-chat-react.js`
   - `buildDesktopCompactWindowLayout()`
   - `applyDesktopCompactLayoutToPage()`
   - `rebaseDesktopCompactLayoutToWindowBounds()`
3. `N.E.K.O.-PC/src/desktop-compact-layout.js`
   - `isDesktopCompactSurfaceAnchorKind()`
   - `classifyDesktopCompactItems()`
   - `buildDesktopCompactLayoutRects()`
4. 优先补的测试：
   - layout 回写只变 `windowBounds` / `compactChoicePlacement` 时，页面不 reset anchor。
   - history/toolFan/choice extra island 改变时，`layout.surface` 不变。
   - stable geometry summary 相同但完整 visual rect 小数不同，不派发 native/window relayout。

### 实施 1：建立 base surface 与 extra island 的明确契约

目标：

1. base surface 是 compact 对话框本体唯一锚点。
2. choice、tool fan、history 是 extra island，只能扩展 carrier window / native rect / hit rect。
3. extra island 不得改变保存的 surface position，也不得反向更新 `layout.surface` 的语义。
4. extra island 仍必须完整参与 carrier/native/hit 覆盖，不能因为不当 anchor 就被裁剪或点不到。

涉及文件：

1. `static/app-react-chat-window.js`
2. `N.E.K.O.-PC/src/desktop-compact-layout.js`
3. `N.E.K.O.-PC/src/preload-chat-react.js`

实施步骤：

1. 在页面 geometry snapshot 中继续保留：
   - `surfaceItems`
   - `surfaceUnion`
   - `baseSurfaceRect`
   其中 `surfaceUnion` 可以作为调试或完整可见 union 使用，但不能作为 `layout.surface` 的来源。
2. 明确 `baseSurfaceRect` 只由以下 kind 组成：
   - `surfaceShell`
   - `capsule`
   - `input`
3. 将 choice/toolFan/history 的 role 标记为 extra island。可以通过现有 kind 分类，不一定新增字段；但 PC 侧必须只用 base rect 作为 surface anchor。
4. 在 `desktop-compact-layout.js` 中确保：
   - `layout.surfaceScreenRect` 来自 base anchor 或 stored surface。
   - carrier/window union 可以来自 base surface + extra island native rect。
   - extra island 只进入 `nativeRects` / `hitRects` / `extraNativeRects` / `extraHitRects`。
5. 在 `preload-chat-react.js` 中确保回写给页面的 `layout.surface` 始终对应 base surface，而不是 extra island union。

验收标准：

1. GalGame choice 打开后，`layout.surface.width/height/left/top` 不因 choice 改变。
2. Tool fan 打开后，carrier window 可以扩展，但保存的 compact surface position 不变。
3. History 打开后，base surface 不上移、不回弹、不被 history 面板中心牵引。
4. Choice、tool fan、history 的可见内容没有被窗口边界或 CSS overflow 裁掉。

实施记录：

1. 页面 geometry item 已新增 `geometryRole`：
   - `baseAnchor`：`surfaceShell | capsule | input`。
   - `baseHit`：`dragHandle`。
   - `extraIsland`：choice/toolFan/history 等其它 surface owner 项。
2. 页面 snapshot 已新增分组字段：
   - `baseSurfaceItems`
   - `baseSurfaceNativeRects`
   - `baseSurfaceHitRects`
   - `extraIslandItems`
   - `extraIslandNativeRects`
   - `extraIslandHitRects`
3. 旧字段仍保留：
   - `surfaceItems`
   - `surfaceUnion`
   - `baseSurfaceRect`
   - `surfaceHitRects`
   - `surfaceNativeRects`
4. NEKO-PC layout helper 当前已有 base anchor / extra island 分类和合同测试，本阶段未改动 PC layout 主逻辑。
5. 验证：
   - `node --check static/app-react-chat-window.js` passed。
   - `./.venv/bin/python -m pytest -q tests/unit/test_react_chat_window_static.py`：18 passed。
   - `node --test test/desktop-compact-layout-contract.test.js` in `N.E.K.O.-PC`：32 passed。

### 实施 2：阻断 `desktop-compact-layout-change` 的无条件 anchor reset

目标：

页面收到 NEKO-PC layout 回写时，不再默认清空 `compactSurfaceAnchorSnapshot`。

涉及文件：

1. `static/app-react-chat-window.js`

实施步骤：

1. 在 `neko:desktop-compact-layout-change` listener 中读取 event detail。
2. 比较上一轮 desktop layout 的 base surface snapshot 与新 layout 的 base surface snapshot。
   - 若已实现 `anchorVersion`，优先比较 `anchorVersion`。
   - 若尚未实现，则比较 rounded 后的 `layout.surface` 或 `surfaceScreenRect`。
3. 只有当 `layout.surface.left/top/width/height` 真实变化时，才：
   - `compactSurfaceAnchorLocked = false`
   - `compactSurfaceAnchorSnapshot = ''`
   - `scheduleCompactMinimizeBallTracking()`
4. 如果只变化了：
   - `windowBounds`
   - `ball`
   - `compactChoicePlacement`
   - native/hit extra island

   则不要清空 surface anchor。
5. drag/resize active 期间，即使 base target 变化，也由 active target 管理，不通过 layout-change listener 抢写页面 anchor。
6. 保留首次进入 compact、layout 从 null 变为非 null、退出 compact 时的清理逻辑。

验收标准：

1. idle 状态 NEKO-PC 回写重复 layout 不触发页面重新 apply anchor。
2. choice/toolFan/history 展开期间，页面不因 PC 回写而反复写 `--compact-surface-*`。
3. 最小化/恢复仍能正确设置 surface 与 ball。

实施记录：

1. 页面 static bridge 已新增 desktop layout anchor snapshot：
   - `compactDesktopSurfaceAnchorSnapshot`
   - `serializeCompactSurfaceRectSnapshot()`
   - `getCompactDesktopLayoutAnchorSnapshot()`
   - `handleDesktopCompactLayoutChange()`
2. anchor 判断优先级：
   - 若 PC 后续提供 `anchorVersion`，优先使用 `anchorVersion`。
   - 当前版本优先使用 `layout.surfaceScreenRect`。
   - 只有缺少 screen rect 时才 fallback 到 page-local `layout.surface`。
3. `neko:desktop-compact-layout-change` listener 不再直接清空：
   - `compactSurfaceAnchorLocked`
   - `compactSurfaceAnchorSnapshot`
4. 只有 desktop base anchor snapshot 真实变化，且不在 desktop resize active 阶段，才清空 page anchor snapshot。
5. 仍保留每次 layout change 后的 `scheduleCompactMinimizeBallTracking()`，确保 ball、native/hit、geometry 仍能继续同步。
6. 验证：
   - `node --check static/app-react-chat-window.js` passed。
   - `./.venv/bin/python -m pytest -q tests/unit/test_react_chat_window_static.py`：19 passed。
   - `node --test test/desktop-compact-layout-contract.test.js` in `N.E.K.O.-PC`：32 passed。

### 实施 3：让 PC 侧 geometry-change 事件具备影响判断

目标：

NEKO-PC 不再对每一个 `neko:compact-interaction-geometry-change` 都完整 relayout。

涉及文件：

1. `N.E.K.O.-PC/src/preload-chat-react.js`
2. 可选：`static/app-react-chat-window.js`
3. 可选：`frontend/react-neko-chat/src/App.tsx`

实施步骤：

1. 在 PC preload 中维护上一轮用于 native/window 的 geometry summary。
2. 收到 `neko:compact-interaction-geometry-change` 后，先读取当前 geometry。
3. 只比较会影响窗口/native/hit 的字段：
   - base surface screen rect
   - extra native rect summary
   - extra hit rect summary
   - ball rect
   - compact choice placement
4. 如果只是 visual-only 变化，跳过 `scheduleDesktopCompactRelayout()`。
5. 对无 detail 的事件，不直接全量 relayout；可以延迟到下一帧读取 geometry 后判断。
6. 对连续动画帧加轻量 coalescing：同一 rAF 内最多安排一次 relayout。

验收标准：

1. 工具扇 hover 或动画期间 relayout 次数显著下降。
2. history hover scrollbar 不再触发完整窗口 bounds 变化。
3. 无 detail 的 geometry-change 不会无条件造成 PC relayout。

实施记录：

1. NEKO-PC preload 已新增 stable geometry summary：
   - `desktopCompactInteractionGeometrySummary`
   - `serializeDesktopCompactScreenRect()`
   - `serializeDesktopCompactScreenRects()`
   - `getDesktopCompactGeometrySummaryWindowBounds()`
   - `buildDesktopCompactInteractionGeometrySummary()`
   - `shouldScheduleDesktopCompactRelayoutForGeometryChange()`
2. summary 当前覆盖：
   - surface mode / compact state
   - base anchor screen rect
   - base hit rects
   - extra native rects
   - extra hit rects
   - ball / external ball
   - compact choice placement
3. `neko:compact-interaction-geometry-change` listener 不再无条件 `scheduleDesktopCompactRelayout()`。
4. 对于 resize active、drag active、history drag active，仍直接允许 relayout，避免操作期 target 或 drag hit/native 丢失。
5. 对无 detail 事件，PC 会回读 `window.__nekoGetCompactInteractionGeometry()` / `window.__nekoCompactInteractionGeometry` 后再判断 summary。
6. 实测补充：`preload-chat-react.js` 的 compact DOM hook 已改为 `document.readyState` 兼容初始化，避免 `DOMContentLoaded` 已触发时整段 PC compact bridge 未上线。
7. 当前为保守收口：
   - visual-only 变化会被跳过。
   - native/hit/base/ball/choice placement 变化仍会触发 relayout。
   - hit-only 与 native/window 的进一步拆分留给后续 P3。
8. 验证：
   - `node --check src/preload-chat-react.js` in `N.E.K.O.-PC` passed。
   - `node --test test/desktop-compact-layout-contract.test.js` in `N.E.K.O.-PC`：35 passed。

### 当前状态复核：实施 4 收口

结论：

实施 4 的可收口部分已经明确：保留完整 carrier 和展开内容，用 base surface anchor 驱动拖拽；撤回所有会破坏右侧展开按钮原有动效的 settling CSS；choice placement 改为 surface 锚点和事件驱动。实施前真实 Electron 复测曾抓到 F7 从 84×84 折叠态恢复时短暂走 legacy 展开路径，窗口先变成 440×600，再跳到 y=264，最后才回到 compact desktop layout 的 609×346 + 56×56 独立球。前置项 A 已将 minimized → compact 的所有打开/关闭恢复入口统一到 direct restore helper，后续主问题转入 resize source-of-truth。

已解决或已缓解：

1. 页面 geometry 已能区分 base anchor、base hit、extra island。
2. 页面收到 PC layout change 时不再无条件 reset anchor。
3. PC 侧 geometry-change 不再无条件 relayout。
4. `preload-chat-react.js` compact DOM hook 已兼容 `DOMContentLoaded` 已触发的真实启动路径。
5. Drag 已恢复为 base surface anchor drag，不再移动 history-sized carrier，也不缩小 carrier 裁掉展开内容；用户实际复测反馈拖拽基本没有问题，后续只作为回归项保留。
6. Choice placement 已改用 compact input shell/PC forced placement，不再每帧用 app shell 自算。

仍需作为后续 P0/P1 处理：

1. 当前真实交互主问题已从拖拽/minimized restore 转为缩放：resize start/move/end 期间页面临时宽度、PC 回写宽度、carrier native/hit、展开内容跟随仍可能互相抢写。
2. Tool fan、history、GalGame 打开时，carrier、页面 CSS、React 内容挂载不是同一帧完成，可能出现内容先闪、窗口后追，或窗口先扩、内容后追。
3. History/GalGame/tool fan 的 open 状态、持久化状态、hover 状态可能互相抢占视觉层。
4. 自动化 idle 计数不等价于视觉无闪烁，后续需要截图/逐帧 rect/screenshot smoke。

实施 4 后的后续实施队列：

1. 前置项 A：minimized/hotkey restore 直达 compact desktop layout。
   - 处理 `window.__nekoFocusReactChatInputFromHotkey` 在 `eMinimized` 时走 `doExpand()` 的 legacy 440×600 中间态。
   - 覆盖所有 minimized → compact 打开入口：F7 focus、F6 toggle、折叠态 drag handle/球点击、minimize button、`chat-surface-mode-change` 回 compact、idle dock compact restore、独立 compact ball click 转发兜底。
   - 恢复路径必须直接进入 compact desktop surface + 独立球布局，不能先渲染全窗 expand。
   - 验收重点是从 84×84 恢复的前 300ms，不出现 440×600 或其它非 compact desktop 状态。
   - 当前已实施：minimized → compact 分支统一优先走 `restoreCompactDesktopFromMinimized()` / `restoreCompactDesktopFromMinimizedOrExpand()`，先用 stored/fallback compact surface 直接构建 desktop compact carrier；只有 direct restore 不可用时才 fallback 到 legacy `doExpand()`。
   - 当前已复测：F6 关闭后窗口为 84×84；F6/F7 打开后采样直接进入 compact carrier + 56×56 独立球，未再采到 440×600 中间态；运行日志未再出现 `[WindowControl][expand]`。
2. 实施 5：history native reserve / visible first frame。
   - 修 history 是否显示都占位、首帧旧位置、滚动/hover 推动 carrier 的问题。
   - history 必须完整显示且可操作，但不参与 base surface 锚点。
3. 实施 6：Choice/GalGame 剩余首帧专项。
   - 实施 4 已完成 surface/input shell 锚点、PC forced placement、事件驱动；后续只处理 loading/options 首帧、高度切换和边缘空间。
   - 不再把“React 自算和 PC 自算抢 ownership”作为主要未完成项。
4. 实施 7：Tool fan native/hit + visual integrity。
   - 右侧展开按钮的转动、完整样式、原有 reserve 行为必须保持修改前效果。
   - 如仍裁切，必须从按钮 CSS/DOM/native-hit 分层定位，不能用扩大 reserve 或禁用 transition 掩盖。
5. 实施 8：resize source-of-truth。
   - Drag 主路径已由实施 4/8 收口；后续只保留 drag 回归验证。
   - Resize start/move/end 是接下来实施 8 内部的主要内容，不新增独立实施编号。
   - 必须按操作 target 做唯一临时布局来源，避免页面宽度、PC 回写宽度、carrier native/hit、展开内容跟随互相抢写。
6. 实施 9：page-side stable geometry diff。
   - PC 侧 summary gate 已完成；页面侧仍需把 native/window、hit-only、visual-only diff 分层，减少无意义事件。
7. 实施 10：CSS 变量优先级。
   - 收口 PC compact、Web compact、resize active 的变量来源，避免首帧 fallback 宽高参与可见渲染。
8. 实施 11：启动与持久化状态恢复。
   - history/GalGame/options 的恢复顺序要在 base surface 和 PC layout 来源确定之后。
   - tool fan hover/open 不做持久化恢复。
9. 实施 12：视觉 smoke 自动化。
   - 不能只看 idle 事件收敛；必须补前 300ms 的逐帧 rect 或截图断言。

当前代码状态：

1. `N.E.K.O` 已修改：
   - `frontend/react-neko-chat/src/App.tsx`
   - `frontend/react-neko-chat/src/styles.css`
   - `static/app-react-chat-window.js`
   - `tests/unit/test_react_chat_window_static.py`
   - `docs/design/compact-chat-geometry-issue-list.md`
2. `N.E.K.O.-PC` 已修改：
   - `src/preload-chat-react.js`
   - `test/desktop-compact-layout-contract.test.js`
3. 已跑过的验证：
   - `node --check static/app-react-chat-window.js`
   - `./.venv/bin/python -m pytest -q tests/unit/test_react_chat_window_static.py`：20 passed。
   - `node --check src/preload-chat-react.js`
   - `node --test test/desktop-compact-layout-contract.test.js`：35 passed。
   - `./build_frontend.sh` 成功，仅有既有 Vite chunk/dynamic import warning。
   - `git diff --check && git -C ../N.E.K.O.-PC diff --check` 成功。
4. 当前未解决风险：
   - 自动化只能证明 idle 收敛；前置项 A 已用真实 Electron 采样确认 F7 minimized restore 不再出现 440×600 legacy 中间态。
   - 不能把“事件数稳定”当作“画面稳定”的替代品。

真实 Electron 复测记录：

1. 运行态：`electron-forge start`，React Chat 和独立 compact ball 已由现有进程创建。
2. 采样方法：不重启进程，使用 macOS 窗口列表按约 50ms 间隔采样；低层键盘事件触发 F6/F7；没有向源码加入临时日志。
3. F6 `toggleReactChatWindow`：
   - 初始：独立球 `N.E.K.O` 为 56×56，React Chat 透明窗口约 609×346。
   - F6 后：独立球消失，React Chat 变为 84×84 折叠态。
   - 该 idle 状态稳定，没有持续 bounds 抖动。
4. F7 `focusReactChatInput`：
   - 起点：84×84 折叠态。
   - 实施前中间态 1：React Chat 变为 440×600，位置仍靠近折叠球。
   - 实施前中间态 2：React Chat 仍为 440×600，但 y 从 780 跳到 264。
   - 实施前终态：恢复为独立 56×56 ball + 609×346 React Chat 透明窗口。
   - 前置项 A 实施后：F7 采样直接进入 compact carrier + 56×56 独立球，未再采到 440×600 中间态。
5. F6 `toggleReactChatWindow`：
   - 关闭：compact carrier 收为 84×84。
   - 打开：直接恢复 compact carrier + 56×56 独立球，未再采到 440×600 中间态。
6. 其它打开/关闭入口：
   - 折叠态 drag handle/球点击、minimize button、`chat-surface-mode-change` 回 compact、idle dock compact restore、独立 compact ball click 转发兜底都已接入同一 direct restore helper。
   - 自动化合同测试覆盖这些入口不再直接走 legacy `doExpand()`。
7. 判断：
   - 实施前最先要修的是 minimized/hotkey restore 链路，不应先进入 history reserve。
   - 根因是 minimized → compact 的多个入口仍可走 `doExpand()`；`doExpand()` 使用 `loadExpandBounds()` 的 legacy expand bounds，并在 `W.expand(eSavedBounds)` 之后才 `setReactChatSurfaceMode('compact')`，这与 compact desktop 的 source of truth 冲突。
   - 前置项 A 已将这些入口统一到 direct restore helper；后续判断重心转入 resize source-of-truth。

### 实施 4：首帧与过渡帧视觉收口

目标：

消除 compact 启动、恢复、展开/关闭工具扇、history、GalGame options、drag、resize 时短暂闪出其它状态的问题。这个实施只处理“用户看见的帧”，不把问题继续推迟到 history/choice/tool fan 专项。

涉及文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/styles.css`
3. `static/app-react-chat-window.js`
4. `N.E.K.O.-PC/src/preload-chat-react.js`

实施步骤：

1. 建立 compact 视觉 ready 状态：
   - 页面初次进入 compact 后，extra island 仍按设计即时可见、可交互。
   - 不采用全局禁用 transition/animation 的 settling CSS。右侧展开按钮等现有动效必须保持修改前的视觉行为。
   - Web 非 PC compact 不能被这个 gating 影响。
2. 状态切换要按顺序提交：
   - base surface、carrier/native/hit、extra island 使用同一轮 layout 来源。
   - history/choice/tool fan 内容不延迟显示，但首帧必须使用当前 compact surface/PC layout 的 CSS 变量。
   - 不允许 extra island 先挂到 fallback 或旧位置，再等待 PC 回写修正。
3. CSS transition 管理：
   - 不对 `.compact-chat-surface-shell *` 做全局禁用 transition/animation。
   - 只对 opacity/transform 做视觉过渡，不能让 left/top/width/height 中间态参与 geometry。
4. 持久化状态恢复要延迟：
   - history open、galgame on、tool fan hover/open 等状态恢复不能先用默认/fallback 几何渲染一帧。
   - 若逻辑状态已恢复，视觉内容仍即时出现；需要保证出现时已经绑定正确的 compact surface/PC layout 来源。
5. 真实复测必须覆盖前 300ms：
   - 启动 compact。
   - hover tool fan。
   - 打开 history。
   - 生成 GalGame options。
   - drag start/move/end。
   - resize start/move/end。

验收标准：

1. 启动 compact 时不闪默认大窗、不闪错误 options/history 状态。
2. tool fan/history/GalGame 打开时不出现“内容先闪到旧位置，再跳到新位置”。
3. drag/resize 开始和结束时不闪其它 compact state。
4. idle relayout 仍保持收敛。
5. 展开内容不被裁剪，不通过永久 hidden 或禁用功能来掩盖闪烁。
6. 不采用“稳了再可见”的方案；修复必须保持设计文档要求的即时展开流程。

实施 4 收口记录：

1. 已保留：
   - React choice placement 使用 `compactInputShellRef` 作为锚点。
   - PC compact forced placement 优先；layout change 事件触发重新计算；不再每帧 `requestAnimationFrame(trackPlacement)`。
   - Static bridge 将 base surface 与 extra island 分组，作为后续视觉收口的数据基础。
   - Desktop layout change 只有 base surface anchor 改变时才 reset page anchor。
   - PC preload 对 geometry-change 做 stable summary gate，减少 visual-only 回环。
   - Drag 恢复 `anchorDrag: true` + `returnAnchorRect: true`，拖拽期间保留完整 carrier，不隐藏 history/tool/choice。
2. 已撤回：
   - settling CSS 全局禁用 transition/animation。
   - 扩大 tool fan reserve 以掩盖按钮裁切。
   - drag start surface-only carrier。该方案会导致拖拽时展开内容被物理窗口裁掉。
   - preload 内无消费者的 `desktopCompactLayoutPhase` / ready timer 机制。
3. 缺漏留给后续：
   - history/tool fan/GalGame 打开首帧是否完全同帧仍需真实逐帧复测。
   - resize 操作期还未完整按 source-of-truth 方案收口。
   - 自动化还缺截图或逐帧 rect 断言。

后续实施边界：

1. 不再采用：
   - “稳了再可见”隐藏展开内容。
   - drag 时临时缩成 surface-only carrier。
   - 全局 settling CSS 禁用 transition/animation。
   - 扩大 tool fan reserve 来掩盖按钮样式被吞。
2. 保留为后续基底：
   - base surface anchor 与 carrier/native/hit 分离。
   - 完整 carrier 承载 history/tool/choice 展开内容。
   - PC compact forced placement 优先。
   - geometry-change stable summary gate。
3. 后续每个实施都必须同时满足：
   - 设计功能流程不降级。
   - 展开内容不被隐藏、裁剪到不可用、或靠卸载规避闪烁。
   - 前 300ms 视觉路径没有错误状态闪现。

### 实施 5：稳定 history 的 native reserve rect

目标：

History 面板打开后，PC carrier window 可容纳 history，但 history 内部滚动、hover、controls 状态不持续推动 bounds。

涉及文件：

1. `frontend/react-neko-chat/src/CompactExportHistoryPanel.tsx`
2. `frontend/react-neko-chat/src/styles.css`
3. `static/app-react-chat-window.js`
4. `N.E.K.O.-PC/src/desktop-compact-layout.js`

实施步骤：

1. 给 `.compact-export-history-anchor` 明确一个稳定 native rect 来源。
2. `static/app-react-chat-window.js` 采集 history 时：
   - nativeRect 使用 anchor/panel 外框稳定 rect。
   - hitRect 可以由可交互子区域生成。
   - scrollbar、bubble 子项、hover thumb 不参与 nativeRect union。
3. 若需要 history passthrough，单独输出 passthrough rect，不让它改变 carrier bounds。
4. controls collapse/expand 时只在结束态提交一次 native 变化。
5. 可评估 PC compact 下不恢复持久化 history open，或延迟恢复。
6. history 视觉挂载必须等待 carrier/native reserve 至少完成一轮：
   - 不允许先显示 0,0 或旧位置的大面板。
   - 不允许先显示完整历史，再由 PC 把窗口扩到位。
   - open 动画只能从最终 anchor/reserve rect 开始。

验收标准：

1. History 打开后 idle 2 秒内无持续 geometry-change/relayout。
2. 滚动 history 不改变 carrier window bounds。
3. hover scrollbar 不触发 native/window relayout。
4. history drag/drop 仍能正常命中角色区域。
5. history 面板、controls、preview、scroll 内容完整显示；必要时可滚动，但不能被裁剪到无法操作。
6. history open/close 前 300ms 内不闪其它 compact state，不闪未定位大面板。

实施记录：

1. `static/app-react-chat-window.js` 已将 history geometry 拆成稳定 native reserve 与 hit-only 子区域：
   - `history:native` 继续由 `.compact-export-history-anchor` 外框提供，负责 carrier/native/passthrough 的稳定 reserve。
   - `history:scrollbar` 和 `[data-compact-hit-region="true"]` 的 history 子项只输出 `hitRect`，`nativeRect` 为 `null`。
2. `N.E.K.O.-PC/src/desktop-compact-layout.js` 已允许带 `hitRect` 但无 `nativeRect` 的 surface item 进入 hit 计算；choice relocation 仍只处理有 native rect 的 choice item。
3. History drag 结束阶段已在 `N.E.K.O.-PC/src/preload-chat-react.js` 单独收口：
   - returning/sending 清理 drag state 后，短暂保留上一轮 drag carrier bounds，避开 release/send 动画末帧直接缩窗导致的透明窗口闪烁。
   - 这段 restore 窗口内强制 `setIgnoreMouseEvents(true, { forward: true })`，避免整屏透明 carrier 阻挡桌面点击。
   - restore 计时结束后不主动缩回 carrier；上一轮 drag carrier 作为 passive carrier 保留，靠普通 hit/passthrough 判断保证透明区域点击穿透。
   - passive carrier 在 compact 隐藏/退出等 native region 清理路径释放，避免拖拽结束时触发透明窗口缩窗闪烁。
   - drag carrier 会 clamp 到 workArea，避免越界拖拽把窗口扩到超出屏幕的大尺寸。
4. 已补测试覆盖：
   - history 子区域 hit-only 不丢失。
   - history 只有外框 native reserve 参与 extra native bounds 和 passthrough。
   - static bridge 中 history scrollbar / 子 hit region 不再输出 native rect。
   - history drag returning/sending 结束阶段走独立 restore 状态，restore 期间保持 mouse passthrough。

### 实施 6：收口 choice placement 的所有权

目标：

Choice above/below 由同一个锚点和同一个决策源决定，避免 React 自算和 PC 自算打架。实施 4 已完成主要 ownership 收口；本节后续只处理 GalGame loading/options 首帧、高度变化、屏幕边缘和可视完整性。

涉及文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. `frontend/react-neko-chat/src/styles.css`
3. `N.E.K.O.-PC/src/desktop-compact-layout.js`
4. `N.E.K.O.-PC/src/preload-chat-react.js`

实施步骤：

已完成基底：

1. React 非 PC 环境下使用 compact surface shell/input shell 作为 placement 锚点。
2. React PC compact 环境下：
   - 如果 `__nekoDesktopCompactLayout.compactChoicePlacement` 是 `above | below`，直接使用它。
   - 不再每帧自算 placement。
3. `requestAnimationFrame(trackPlacement)` 已改为事件驱动：
   - choice open/close
   - options count/loading 变化
   - base surface rect 变化
   - PC compact placement 变化
   - `neko:compact-surface-layout-change` 触发非 PC compact surface move 后的 placement 重算

后续步骤：

4. PC 侧 placement 决策只基于 base surface + workArea + choice reserve rect。
5. choice 内容挂载时同步确定 placement：
   - PC compact 有 forced placement 时，choice anchor 与 options 同帧使用 forced placement。
   - 非 PC compact 用 base surface shell/input shell 计算 placement，避免 app shell/carrier window 参与锚定。
   - loading → options 的切换不能让 choice anchor 高度先坍缩再展开。

验收标准：

1. 靠近屏幕上下边缘时，choice 只切换一次。
2. 拖动 surface 时 choice 跟随，不出现 above/below 来回跳。
3. choice 不改变 base surface anchor。
4. choice 内容在 above/below 两种 placement 下都完整可见；空间不足时按设计滚动，而不是被 carrier 裁掉。
5. GalGame options 生成前后不闪 input/history/tool fan 的错误状态。

### 实施 7：稳定 tool fan native/hit 分层

目标：

Tool fan 打开后，native carrier 使用稳定 reserve rect；按钮、popover、hover 区域只影响 hit。

涉及文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. `static/app-react-chat-window.js`
3. `N.E.K.O.-PC/src/desktop-compact-layout.js`

实施步骤：

1. 保留 tool fan 的整体 reserve rect。
2. `collectCompactToolFanGeometryItems()` 中区分：
   - `toolFan:native`：稳定 reserve rect。
   - `toolFan:*`：hit rect。
3. PC 侧 `buildDesktopCompactToolFanReserveRect()` 只吃 reserve/native，不从每个按钮反推 carrier bounds。
4. tool wheel drag/hover/fast animation 时，只刷新 hit 或 pointer state。
5. tool fan hover/open 时，reserve/hit 与按钮视觉同帧提交：
   - 不允许按钮从输入框中心散开过程中被中间 geometry 反推 carrier。
   - 不允许 hover 离开后残留可见但不可点的中间态。

验收标准：

1. 打开/关闭 tool fan 只产生有限次 relayout。
2. tool wheel 旋转不改变 carrier window bounds。
3. 按钮点击、hover、drag guard 保持可用。
4. tool fan 可见按钮和 popover 不被透明窗口边界裁掉。
5. hover 首帧不闪旧位置、不闪 disabled/hidden 状态。

### 实施 8：明确 resize 操作期 source of truth

目标：

拖动和缩放期间，用户操作 target 是唯一临时布局来源。Drag 主路径已完成并通过用户实际拖拽确认，当前剩余主问题是缩放；后续实施重点转为 resize，drag 只做回归验证。

涉及文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. `static/app-react-chat-window.js`
3. `N.E.K.O.-PC/src/preload-chat-react.js`

实施步骤：

1. 先确认 resize source-of-truth 链路：
   - resize handle 发出的 screenRect target 是否在整个拖动过程中单调、连续。
   - `--compact-surface-resize-width` 是否只在 resize active 期间作为临时视觉宽度，不在 PC 回写后继续覆盖最终宽度。
   - PC carrier 计算是否始终使用 resize target 作为 base surface，而不是被页面重采样 rect 或展开内容 union 抢回。
   - history/choice/tool fan 在 resize active 时只跟随新的 base surface 宽度，不反向改变 base surface。
   - resize end 后是否只保存一次最终 surface width/position，并只触发一次 final relayout。
2. resize start：
   - React 发送 screenRect target。
   - PC 设置 `desktopCompactSurfaceResizeActive = true`。
   - 页面不再从 PC 回写中清空 anchor。
3. resize move：
   - PC 使用 resize target 计算 carrier。
   - React 的 `--compact-surface-resize-width` 只作为视觉临时宽度。
4. resize end：
   - PC 保存最终 surface position/width。
   - 清 active flag。
   - 安排一次 final relayout。
5. drag start/move 回归约束：
   - PC 使用 drag target 作为 active surface。
   - avatar bounds sync 不打断 drag。
6. drag end 回归约束：
   - 保存最终 surface。
   - 清 pending bounds。
   - final relayout 后再恢复普通 geometry 监听。
7. 操作期视觉状态锁定：
   - drag/resize active 时不允许 history/choice/tool fan 的 hover 中间态重置 base surface。
   - drag/resize end 后先提交 final surface，再恢复普通 transition；extra island 不因这个流程被隐藏或卸载。

验收标准：

1. resize 过程中宽度不来回跳。
2. resize end 后只出现一次最终稳定 layout。
3. drag 过程中 surface 不被 avatar sync 或 extra island 拉回。
4. drag end 后 surface 保持在用户放开的位置。
5. drag/resize start 和 end 前 300ms 不闪其它 compact state。

实施记录：

1. Drag 主路径已恢复为 base surface anchor drag：
   - drag active 阶段暂停 compact relayout、renderer bounds sync、window resize relayout。
   - 使用主进程 `anchorDrag: true` 回报 base surface 的 screen rect。
   - preload 用 `DRAG_ANCHOR_MOVE` 更新 `desktopCompactSurfaceDragTarget`，再按完整 native/hit 重新计算 carrier。
   - drag end 优先保存主进程返回的 final anchor rect，再做一次 final relayout。
2. 用户真实拖拽复测已确认：无展开内容状态下，之前的明显拖拽抖动已被修正。
3. `anchorRect` 重新作为 drag source of truth，而不是只用于 main process 的 keep-visible clamp：
   - `anchorRect` 表示 base surface。
   - 传 `anchorDrag: true`。
   - 请求 `returnAnchorRect: true`。
   - history/tool/choice 仍作为 extra island 进入 carrier/native/hit，不因拖拽被裁掉。
4. 已证伪方案：drag start 临时切到 surface-only carrier layout 会让 history/tool island 在拖拽时被物理窗口裁掉，属于“隐藏/裁剪展开内容”，违反设计约束，已撤回。
5. 当前正确方向是：拖拽时保留完整 carrier，但用独立 base surface anchor 坐标驱动 surface，不裁剪 history/tool。
6. 右侧 tool fan 展开按钮视觉已回退：
   - 撤回 frontend settling CSS，不再禁用 `.compact-chat-surface-shell *` 的 transition/animation。
   - PC 侧 tool fan reserve 数值回到修改前的 116 / 140 / 190。
   - 后续如果仍有裁切，应从原始按钮 CSS/DOM 层定位，不能靠扩大 reserve 改动原有效果。
7. 当前未完成项：
   - resize 操作期还需要继续按本节方案收口，不能因为 drag 已修就认为实施 8 全部完成。
   - history/choice/tool fan 的专项稳定仍分别属于实施 5/6/7。
8. 已验证：
   - `node --check src/preload-chat-react.js` in `N.E.K.O.-PC` passed。
   - `node --test test/desktop-compact-layout-contract.test.js` in `N.E.K.O.-PC`：35 passed。
   - `node --check static/app-react-chat-window.js` passed。
   - `./.venv/bin/python -m pytest -q tests/unit/test_react_chat_window_static.py`：20 passed。
   - `./build_frontend.sh` passed。
   - `git diff --check` in both repos passed。
9. 2026-06-01 补充发现：
   - 用户反馈 compact 聊天框 resize 只有在先拖拽过上方 history 气泡后才稳定，否则 resize 会闪烁。
   - 根因指向 history drag passive carrier 的副作用：拖拽 history 后 carrier window 保持较大的被动 bounds，后续 resize 不再频繁缩放透明 BrowserWindow。
   - `N.E.K.O.-PC/src/preload-chat-react.js` 已为 resize 建立独立 passive carrier，不再依赖 history drag 留下的 carrier。
   - resize active 时以 `desktopCompactSurfaceResizeTarget` 计算 surface，同时用 `desktopCompactSurfaceResizeCarrierBounds` 保持 carrier bounds；resize end 后保留 passive carrier，由 hit/passthrough 保证透明区域穿透。
   - `activateDesktopCompactWindow()` 已跳过 native bounds 未变化时的 no-op `W.setBounds()`，避免 final relayout 因 surface width snapshot 改变而重打同一个透明窗口 bounds。
10. 本次补充验证：
   - `node --check src/preload-chat-react.js` in `N.E.K.O.-PC` passed。
   - `node --test test/desktop-compact-layout-contract.test.js` in `N.E.K.O.-PC`：36 passed。

### 实施 9：降低 geometry diff 敏感度

目标：

让 geometry change 只在有意义变化时触发。

当前状态：

PC preload 侧 stable summary gate 已完成；本节后续重点是页面侧 `syncCompactInteractionGeometry()` 的 summary 分层，避免 page-local 小数、hit-only、visual-only 状态继续制造不必要事件。

涉及文件：

1. `static/app-react-chat-window.js`
2. `N.E.K.O.-PC/src/preload-chat-react.js`

实施步骤：

1. 生成 stable geometry summary：
   - rect 数值 round 到整数或 0.5px。
   - native/window 相关字段单独 summary。
   - hit-only 字段单独 summary。
2. `syncCompactInteractionGeometry()` 使用 stable summary 做去重。
3. 仍保留完整 snapshot 给消费者读取，但事件派发以 stable summary 是否变化为准。
4. PC 侧也做 screen-space stable summary，避免 page-local rebase 小数造成重复 relayout。
5. 将 diff 分为三类：
   - `baseWindowSummary`：会影响 carrier/window。
   - `nativeHitSummary`：会影响 native/hit/shape。
   - `visualSummary`：只影响页面视觉，不触发 PC relayout。
6. 动画中间帧默认只允许更新 visual/hit，native/window 只吃开始态和结束态。

验收标准：

1. 静止状态 2 秒内 geometry-change 为 0。
2. CSS hover、scrollbar thumb、透明 mask 不触发 native/window 变化。
3. 动画过程中事件数量下降，最终态仍正确。
4. visual-only 状态变化不造成 PC carrier 抖动，也不造成一帧位置闪烁。

### 实施 10：收口 CSS 变量优先级

目标：

PC compact、Web compact、resize active 三种场景分别有明确 CSS 变量来源。

涉及文件：

1. `frontend/react-neko-chat/src/styles.css`
2. `frontend/react-neko-chat/src/App.tsx`
3. `static/app-react-chat-window.js`
4. `N.E.K.O.-PC/src/preload-chat-react.js`

实施步骤：

1. PC compact：
   - surface/choice/history 优先使用 `--desktop-compact-surface-*`。
   - `--compact-surface-resize-width` 只在 resize active 时覆盖。
2. Web compact：
   - 使用 `--compact-surface-*`。
3. Resize active：
   - 临时宽度变量只能影响视觉和当前操作 target。
   - end 后清理，并由保存后的 base surface width 接管。
4. 检查 history/choice/surface CSS 是否使用同一宽度来源。
5. 首帧 CSS 默认值要安全：
   - PC compact 未 ready 前不能使用会导致大幅跳变的 fallback left/top/width。
   - `--compact-surface-*` 与 `--desktop-compact-surface-*` 不得互相覆盖出中间宽度。
   - transition 不应作用于会参与 geometry 采样的 left/top/width/height 中间态。

验收标准：

1. PC compact 下 history/choice 与 input 对齐同一个 base surface。
2. resize end 后没有宽度瞬跳。
3. Web 非 PC 行为不回退。
4. PC compact 首帧没有从 fallback 宽高跳到 desktop 宽高的可见闪烁。

### 实施 11：处理 history open 持久化与启动恢复

目标：

避免启动首帧用 fallback 几何挂载大 history 面板并反推 base layout。

涉及文件：

1. `frontend/react-neko-chat/src/App.tsx`
2. 可选：`static/app-react-chat-window.js`

实施步骤：

1. 判断是否 PC compact 环境。
2. PC compact 下可选策略：
   - 保留 history open 持久化，但恢复首帧必须使用 PC compact layout 来源。
   - 若无法保证同帧绑定正确 layout，应只延迟持久化状态读取，不允许先显示错误位置再跳动。
3. 如果恢复 history，恢复前不允许重置 base anchor。
4. 退出 compact 或 minimized 时清理不必要的 transient open 状态。
5. 恢复顺序：
   - 先确定 base surface 与 PC layout 来源。
   - 再挂载 history/galgame/options 的视觉展开。
   - tool fan hover/open 不做持久化恢复，只保留用户当前交互触发。

验收标准：

1. 打开 NEKO-PC compact 首帧不因 history 恢复而扩窗跳动。
2. 用户主动打开 history 后行为正常。
3. 非 PC web 端持久化行为按产品预期保留。
4. 启动恢复不闪历史、选项、工具扇的旧状态。

### 实施 12：补充回归测试与自动化复现脚本

目标：

把这次问题转成可重复验证的测试，避免后续改动重新引入。

涉及文件：

1. `frontend/react-neko-chat/src/*.test.tsx`
2. 可选：新增 NEKO-PC smoke test 脚本
3. 可选：新增 Playwright/Electron 本地验证脚本

实施步骤：

1. React 单测覆盖：
   - compact choice placement 优先 PC forced placement。
   - history open 不改变 base surface props。
   - tool fan native/hit 分层数据稳定。
2. 静态桥测试覆盖：
   - stable geometry diff。
   - baseSurfaceRect 与 surfaceUnion 分离。
3. NEKO-PC smoke 覆盖：
   - idle 无持续 relayout。
   - choice/toolFan/history 打开后 carrier 扩展但 base surface 不漂移。
   - drag/resize end 后最终 layout 稳定。
   - 展开内容 rect 位于 carrier/native 覆盖内，没有不可操作裁剪。
   - 启动、展开、关闭、drag、resize 的前 300ms 截图或逐帧 rect 没有错误状态闪现。
4. 自动化输出必须分开记录：
   - event delta。
   - DOM rect。
   - screenshot/视觉状态。
   - Electron main log。

验收标准：

1. `build_frontend.sh` 成功。
2. React tests 成功。
3. NEKO-PC smoke 在本地真实 Electron 环境跑通。
4. 对 choice、tool fan、history 至少保留一组截图或 rect 断言，证明正常显示没有回归。
5. smoke 不能只用 idle event count 判定通过，必须包含视觉无闪烁证据。
