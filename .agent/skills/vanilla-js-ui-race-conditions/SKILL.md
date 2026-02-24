---
name: Vanilla JS UI Race Conditions (VRM vs Live2D)
description: Dealing with delayed DOM generation, lazy loading, and optimistic state synchronization in vanilla JavaScript without a reactive framework.
---

# 应对原生 JS 中的 DOM 竞态与乐观更新陷阱

## 症状
- 界面组件（如侧边栏、HUD）在页面初次加载时由于丢失状态而不显示，但手动交互后又能弹出。
- 界面在开启和关闭之间发生闪烁，或者无法通过代码正确关闭。
- 绑定在 DOM 元素上的事件监听器未能触发。

## 根本原因
### 原因 1: 不同模型/模块的 DOM 懒加载时机不一致
- **问题**: 在处理跨模型（如 Live2D 和 VRM）或模块化应用时，某些弹窗会在首次点击时才 createElement（懒加载）。
- **为什么发生**: 性能优化导致 DOM 并非一上来就在 HTML 里。如果使用固定的 setTimeout(..., 100) 去抓取 DOM 绑定事件，极大概率会面临扑空（DOM 还未生成）。
- **解决方案**: 使用**自我终结的递归轮询**替代死等。

### 原因 2: 乐观 UI (Optimistic UI) 与底层状态的竞态冲突
- **问题**: 用户点击开关后，前端乐观地将按钮置为开启并附带动画，同时向后端发起请求。但在后端返回真实 lag 之前，如果其他组件（如轮询的 App）根据空后端或者默认的错误 DOM 被触发检查，会产生互相覆盖的 BUG。
- **为什么发生**: 缺乏统一的单向数据流。
- **解决方案**: 在读取源头状态时，判断 UI 是否处于 disabled（加载中）。如果是，则优先信任 gent_ui_v2 缓存的 optimistic 状态或后端缓存 machineFlags，而不是盲目相信刚建立且尚未同步的 DOM 节点。

## 代码解决方案

### 1. 应对懒加载 DOM 的绑定轮询（安全可靠）
`javascript
const bindEvents = () => {
    // 兼容多个 ID 解析
    const getEl = (ids) => {
        for (let id of ids) {
            const el = document.getElementById(id);
            if (el) return el;
        }
        return null;
    };

    const targetEl = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);

    // 如果没找到，自己建立一个 500ms 后的新宏任务来重试，然后 return 本次任务。
    if (!targetEl) {
        setTimeout(bindEvents, 500);
        return;
    }

    // 找到了，顺次执行并结束，不会留下任何定时器垃圾
    targetEl.addEventListener('change', myLogic);
    myLogic(); // 手动触发第一次检查
};

setTimeout(bindEvents, 100); // 发动第一下
`

### 2. 多源状态判定降级（防止覆盖）
`javascript
const checkState = () => {
    const isUiInteractive = domCheckbox && !domCheckbox.disabled;
    let isFeatureOn = false;

    if (!isUiInteractive) {
        // UI 在转圈加载，信任乐观状态或后端缓存
        isFeatureOn = optimisticState !== undefined ? optimisticState : backendFlags;
    } else {
        // UI 处于可用交互态，百分百信任当前 DOM
        isFeatureOn = optimisticState !== undefined ? optimisticState : domCheckbox.checked;
    }
}
`

## 关键经验
- 永远不要用硬编码的 setTimeout 时长来假定某个资源或 DOM 一定会加载完毕。
- 如果没有 React 这种 Virtual DOM 框架，手动处理跨组件通讯时要随时当心当前脚本运行时的真实 DOM 快照情况。
