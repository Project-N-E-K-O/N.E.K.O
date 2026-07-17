# 首页紧凑聊天框

> **文档性质：current implementation record + external integration boundary。** 本页记录本仓库 React Chat 与网页宿主的 Compact 模式。N.E.K.O-PC 的原生窗口裁切、穿透和置顶行为必须在外部仓库单独验证。

## 单一 UI 实现

聊天 UI 的实现位于 `frontend/react-neko-chat/`，构建为 `neko-chat-window.iife.js`，由主页和独立聊天页挂载到 `#react-chat-window-root`。旧 `#chat-container` 不是新增功能入口。

网页宿主适配拆在 `static/app/app-react-chat-window/`，其中：

- `minimize-and-idle-dock.js` 管理 surface、Compact 子状态和最小化；
- `resize-drag-and-api.js` 管理拖拽、geometry 和对外 API；
- `message-bundle-actions-and-prompts.js` 管理消息动作、位置与 Compact 交互请求。

## 状态合同

聊天 surface 是 `full`、`compact` 或 `minimized`。Compact 内部还有展示、输入、工具/历史等临时 UI 状态；它们不能替代 surface 真相。

- surface 变更由宿主统一提交，React 根据 props/事件渲染；
- 从 minimized 展开时恢复上一个真实 surface，而不是永远回 full；
- Compact 的拖拽表面与按钮/输入等 no-drag 区域必须分离；
- 进入输入、工具扇面或历史时，geometry 变化要通过统一事件同步；
- 页面隐藏、模型切换、idle dock 和教程锁定都必须清理临时 Compact 状态；
- Full 与 Compact 共用消息、语音、道具和历史业务逻辑，只允许呈现差异。

## Geometry 合同

geometry payload 应描述可交互区域的稳定 id、矩形与可选语义。矩形来自当前 DOM 测量，窗口缩放或状态改变后必须刷新。外部宿主可以消费这些矩形来设置 native hit region，但本仓库只保证 producer 结构；不能在这里断言外部 preload 当前实现。

不要用写死像素复制 DOM 布局，也不要让 Electron 专用分支渗入 React 业务组件。

## 输入与历史

Compact 输入仍走普通 composer 提交流程，语音和发送状态与 Full 对偶。历史导出/拖放必须使用现有 React 组件和宿主 action；不能绕过消息验证直接拼 payload。切换 surface 不应丢失未发送草稿或重复发送。

## 验证

```bash
npm --prefix frontend/react-neko-chat test -- --run
uv run pytest tests/unit/test_react_chat_window_static.py tests/unit/test_react_chat_idle_dock_static.py -q
```

手工验收覆盖网页主页、独立 `/chat` 页面和 Electron 环境；后者只有在 N.E.K.O-PC 实测后才能标记通过。

## 修改规则

- 在 React 源码修改聊天 UI，不恢复旧 DOM 聊天实现；
- Full/Compact 同类能力保持结构对称；
- 每个新增 geometry/action 都要有稳定命名和 teardown；
- 原生窗口协议变更要同步外部仓库并分别测试，不能只改单侧。
