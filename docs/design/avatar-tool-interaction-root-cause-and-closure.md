# 道具交互共因问题记录与收口

状态：**已确认根因，待后续修复**

这份文档只记录一类共享链路上的问题，不按单个道具拆开写。棒棒糖、拳套、锤子等道具的实际反应不同，但它们共用同一套“选中态 - 光标态 - 模型范围判断 - 关闭清理”链路，所以这里把共因、复现条件、修复边界和收口标准一次固定。

## 涉及范围

- 前端道具交互：棒棒糖、拳套、锤子
- React 聊天窗道具光标态
- 头像模型屏幕范围判断
- Electron / 关闭窗口时的全局光标清理

## 已确认现象

1. 喂棒棒糖时，棒棒糖图标有时会变成默认的完整棒棒糖图标。
2. 在选中棒棒糖等道具交互内容时，不取消直接关闭 Neko，鼠标箭头会直接消失。
3. 第 1 个问题总是伴随着“棒棒糖从模型判断范围内变成判断范围外”的状态切换。

## 真实根因

### 根因 1：范围命中抖动直接驱动了棒棒糖切图

棒棒糖不是“随机换图”，而是被当前范围命中状态驱动。

- 关键链路在 [frontend/react-neko-chat/src/App.tsx](../../frontend/react-neko-chat/src/App.tsx)
- 范围命中入口：`updateCursorRangeState`、`getAvatarRangeHit`、`isPointInsideAvatarBounds`
- 光标图切换点：`avatarCursorOverlayImagePath`
- 棒棒糖的有效图像选择依赖 `isCursorOverAvatarRange`

也就是说，只要 `isCursorOverAvatarRange` 短暂变成 false，棒棒糖就会从“头像范围内图标”切到“范围外/默认完整图标”。你现在看到的“完整棒棒糖”不是另一个独立状态，而是同一套状态机在外部范围分支上的结果。

### 根因 2：关闭窗口没有经过统一的光标清理收口

关闭 Neko 时，窗口层虽然会把 overlay hidden 掉并通知道具状态变为 inactive，但没有显式走一遍统一的工具光标清理入口。

- React 侧统一清理入口：`clearActiveCursorToolSelection`
- 关闭路径在 [static/app-react-chat-window.js](../../static/app-react-chat-window.js) 的 `closeWindow`
- 相关全局状态包括：
  - `html.neko-tool-cursor-active`
  - `--neko-chat-tool-cursor`
  - `cursor: none`

如果关闭时正好处于选中道具态，窗口先隐藏，清理却没有完整收口，鼠标箭头就可能留在“已被接管但又看不到覆盖层”的状态。

## 共享链路

共享链路不是单个道具逻辑，而是下面这条：

1. 选中道具
2. 计算当前指针是否在模型范围内
3. 根据范围结果决定 icon / cursor 的显示态
4. 在窗口关闭、失焦、隐藏、重置时统一清理

其中第 2 步决定第 1 个问题，第 4 步决定第 2 个问题。

### 相关实现点

- [frontend/react-neko-chat/src/App.tsx](../../frontend/react-neko-chat/src/App.tsx)
  - `getAvatarBoundsEntries`
  - `getAvatarRangeHit`
  - `resolveEffectiveCursorVariant`
  - `clearActiveCursorToolSelection`
  - `applyResolvedCursor`
  - `avatarCursorOverlayImagePath`
- [static/app-react-chat-window.js](../../static/app-react-chat-window.js)
  - `deactivateToolCursor`
  - `closeWindow`
  - `handleAvatarToolStateChange({ active: false })`
- [frontend/react-neko-chat/src/styles.css](../../frontend/react-neko-chat/src/styles.css)
  - `html.neko-tool-cursor-active`
  - `--neko-chat-tool-cursor`

## 可复现路径

### 复现 1：棒棒糖切回默认完整图标

1. 选中棒棒糖。
2. 把鼠标停在模型判定边缘附近。
3. 让指针在“范围内 / 范围外”之间轻微抖动。
4. 可以观察到棒棒糖图标会从头像范围态切到范围外默认态。

这个复现成立的前提，是 `getAvatarRangeHit` 返回值在短时间内发生了 false / null 切换。

### 复现 2：关闭 Neko 后鼠标箭头消失

1. 选中任意道具光标态。
2. 不点“恢复鼠标”。
3. 直接关闭 Neko 窗口。
4. 可以观察到鼠标箭头未按预期恢复。

这个复现成立的前提，是关闭路径只隐藏了窗口，但没有走完整的工具光标清理闭环。

## 后续修复必须遵守的设计规范

1. 不要把修复写成“只给棒棒糖打补丁”。这次问题的本质是共享范围判断和共享光标清理，不是棒棒糖单点 bug。
2. 不要改坏棒棒糖、拳套、锤子的交互语义。图标、触发区域、命中反馈可以整理，但实际交互反应不能被重定义。
3. 不要改动消息协议、记忆、提示词、道具 payload 的字段含义来绕过这个问题。
4. 不要让 Electron 单窗口 / 多窗口模式的鼠标行为出现分叉。
5. 不要影响普通聊天输入、语音模式、面板折叠、窗口打开关闭等无关流程。
6. 清理逻辑必须收口到单一入口，不能在多个关闭分支里各写一段半吊子清理。

## 建议的修复原则

- 范围命中要允许短暂抖动缓冲，避免模型 bounds 瞬时失效就把棒棒糖切成外部态。
- 模型 bounds 缓存失效、隐藏、失焦、关闭、卸载等路径要共享同一个清理入口。
- 清理必须同时覆盖 React 状态和全局样式态，至少包括：
  - `activeCursorToolId`
  - `toolMenuOpen`
  - `isCursorOverAvatarRange`
  - `isCursorOverCompactCursorZone`
  - `html.neko-tool-cursor-active`
  - `--neko-chat-tool-cursor`
  - `cursor`
- 关闭窗口时，先清掉工具态，再隐藏 overlay，避免“界面没了但鼠标还被接管”的尾巴。

## 收口标准

后续修复只有满足下面几条，才算真正收口：

1. 棒棒糖在指针仍稳定位于模型范围内时，不再无故切到范围外默认完整图标。
2. 关闭 Neko 时，即使当前选中了棒棒糖、拳套或锤子，也能恢复原生鼠标箭头。
3. 现有正常交互不回退，尤其是范围内触发、范围外触发、桌面光标覆盖层、关闭窗口与恢复鼠标按钮。
4. 新增或更新的测试能稳定覆盖上述两条回归路径。

## 备注

这份文档的目标不是给某个道具写说明书，而是把共享链路的真实故障模式钉住。以后如果再做道具交互，只要碰到“范围判断”和“关闭清理”两条线，就必须先对照这里的约束。
