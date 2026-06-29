# 记忆浏览器：粒子消散功能开发说明

## 目的

这份文档描述“记忆浏览器聊天记录删除/清空”的粒子消散动效实现，目标是：

- 保证用户看到自然的消失动画；
- 统一删除流程，后续可复用到更多按钮；
- 避免页面关闭、重复点击等场景下卡死或状态异常；
- 降低删除/清空操作对主流程的侵入性。

## 相关文件

- `static/js/memory_browser.js`
  - `dissolveChatItems`
  - `deleteChat`
  - `#clear-memory-btn` 的点击绑定
  - `addMemoryItemParticles`、`sampleMemoryElementParticles`、`startMemoryParticles`
  - `teardownMemoryParticleCanvas`
- `static/css/memory_browser.css`
  - `.is-dissolving`、`.is-collapsing`、`#memory-particle-canvas` 动画与样式
- `tests/frontend/test_memory_browser.py`
  - 涵盖动画、`prefers-reduced-motion`、`pagehide` 清理的回归场景

## 核心行为概述

删除和清空不再直接执行 DOM 删除，而是走“先动画、后重渲染”流程：

1. 删除入口拿到目标元素列表；
2. 修改 `chatData` 数据源；
3. 调用 `dissolveChatItems` 执行粒子动画；
4. 动画完成后回调刷新列表（`renderChatEdit`）；
5. 最后恢复按钮状态。

这个流程把动画时序集中在 `dissolveChatItems`，删除入口只负责找到目标、修改数据和传入完成回调。

## 现有入口

### `dissolveChatItems(items, onComplete)`

这是当前记忆浏览器的粒子消散入口。它接收要消散的 `.chat-item` 列表，并在动画结束后执行 `onComplete`。

当前职责：

- 检查 `prefers-reduced-motion` 和目标数量；
- 设置 `memoryDissolveInProgress`，禁用清空和单条删除按钮；
- 为目标元素错峰添加粒子和 `.is-dissolving`；
- 调用 `collapseMemoryItem` 收起元素高度；
- 在收尾时执行 `onComplete` 并恢复按钮状态。

执行顺序：

1. 过滤空目标；
2. reduced-motion 或目标过多时直接执行完成回调；
3. 启动粒子画布和逐项消散动画；
4. 动画结束后执行完成回调。

## 现有接入方式

### 单条删除

每个 `.delete-btn` 由渲染逻辑绑定到 `deleteChat(i)`，内部按真实 DOM 和数据索引执行：

- 通过 `data-chat-index` 找到当前 `.chat-item`；
- 执行 `chatData.splice(idx, 1)`；
- 调用 `dissolveChatItems([item], renderChatEdit)`。

### 清空按钮

`#clear-memory-btn` 使用现有点击处理器直接接入：

- 查询所有 human/ai 消息节点；
- 无可清空项时直接提示；
- 过滤 `chatData`，仅保留非 human/ai 的消息（例如 system 备忘录）；
- 调用 `dissolveChatItems(itemsToDissolve, callback)`；
- 在回调中执行 `renderChatEdit()` 并展示清空提示。

这意味着后续新增“删除某类消息”按钮时，应沿用同样模式：入口先收集 DOM 目标并修改 `chatData`，然后把目标列表和完成回调交给 `dissolveChatItems`。

## 使用新功能的接入模板（新增按钮）

```js
document.getElementById('btn-delete-ai-only').onclick = function () {
  if (memoryDissolveInProgress) return;
  const itemsToDissolve = Array.from(
    document.querySelectorAll('#memory-chat-edit .chat-item[data-role="ai"]')
  );
  if (!itemsToDissolve.length) {
    showSaveStatus('当前没有 AI 消息可删除', false);
    return;
  }

  chatData = chatData.filter(function (item) {
    return item && item.role !== 'ai';
  });
  dissolveChatItems(itemsToDissolve, function () {
    renderChatEdit();
    showSaveStatus('已删除 AI 消息', false);
  });
};
```

> 建议：DOM 目标查询必须与 `chatData` 过滤语义一致。否则会出现“动画了却没删/删了但动画不对上”的问题。

## 关键状态与并发控制

### `memoryDissolveInProgress`

在动画期间禁用清空和单条删除入口，避免重复触发导致 UI 与数据错位。

### `memoryDissolveRunId`

每次 `dissolveChatItems` 开始前自增，用于 `setTimeout` 回调中的并发防护。
防止新一轮交互触发后，旧定时器仍然回写旧回调，导致按钮错位或重复渲染。

## 粒子与动效参数

- 默认粒子数量与项目上限：
  - `maxParticleItems = 40`：超过该数量时走无粒子快速路径（直接完成）；
  - `maxStaggeredItems = 6`：分摊最多 6 个元素的错峰时间，避免长列表时整体等待过长；
  - `prefers-reduced-motion` 时直接跳过粒子与折叠动画，保持可访问性友好。
- `collapseMemoryItem` 使用 `offsetHeight` 捕获高度，避免 `transform` 带来的快照误差。

## 生命周期与清理

粒子画布统一通过 `ensureMemoryParticleCanvas` 与 `teardownMemoryParticleCanvas` 管理，并在以下场景触发清理：

- 组件关闭（`closeMemoryBrowser`）；
- `pagehide`；
- `beforeunload`；
- 溶解收尾（内部兜底）。

清理时会移除 `resize` 监听、清空动画上下文、移除全屏画布，并恢复操作按钮，避免再次进入页面后按钮残留禁用。

## 回归点（建议）

新增接入前建议按以下场景验证：

1. 单条删除动画 + 完成后重渲染；
2. 清空所有消息（含无消息时）；
3. `prefers-reduced-motion: reduce` 用户下点击按钮行为；
4. 动画中关闭弹层（`pagehide`）后无残留节点和按钮卡死；
5. 后续新增按钮只需复用“查询目标、修改 `chatData`、调用 `dissolveChatItems`”的接入模式。

## 与现有工作流关系

该功能是前端展示/交互层增强，不改写数据持久化约定。若要在后端或接口层新增“按条件物理删除”能力，应继续保留现有数据通道，前端动画层仅负责 UX 表达与节奏控制。
