# 悬浮角色 Day 2–4 功能编排

> **文档性质：proposal（已被七日实现吸收的历史提案）。** 本页只保留 Day 2–4 的产品边界与仍适用的设计原则。当前运行事实见本仓库 `static/tutorial/yui-guide/days/`，不要按本文恢复旧的临时播放器或缺失文档。

## 提案结论

Day 2–4 后来已经并入统一的七日教程：

- Day 2：屏幕与语音入口；
- Day 3：角色互动；
- Day 4：陪伴设置。

它们由 `static/tutorial/yui-guide/days/day2-screen-voice-guide.js`、`day3-interaction-guide.js`、`day4-companion-guide.js` 定义，经 `static/tutorial/yui-guide/director/` 和 `static/tutorial/core/` 播放。本文不再维护 scene ID、台词或 selector 的副本。

## 仍有效的设计原则

- 每日只解释一个清晰主题，入口说明优先于高级选项。
- 教程演示必须使用真实可见 UI；不可见、禁用或不适用于当前宿主的目标应跳过或降级。
- spotlight、ghost cursor、真实点击是三个独立动作，不能因为有光标动画就默认触发业务点击。
- 屏幕分享、麦克风、隐私与 Agent 等能力必须先说明限制，再提供操作。
- 每个 round 必须支持 skip、异常清理、临时切模恢复和 reduced-motion。
- 跨页或跨窗口目标只能通过当前 bridge/command bus 交接，不保存 DOM 对象或屏幕坐标。

## 不再成立的方案

- 不新增独立的 `HomeAvatarFloatingGuideDirector`。
- 不保留 Day 2–4 临时播放器兜底作为第二实现。
- 不从已删除的 Day 1–7 设计页或“功能树”文档加载事实。
- 不把 `#chat-container` 当作聊天 UI 或教程目标。
- 不假设 Electron overlay 与本仓库 DOM overlay 是同一实现。

## 变更入口

| 变更 | 位置 |
| --- | --- |
| 每日内容 | 对应 `static/tutorial/yui-guide/days/dayN-*.js` |
| 通用时序与生命周期 | `static/tutorial/core/` |
| 导演层适配 | `static/tutorial/yui-guide/director/` |
| 高亮/光标/转场 | `static/tutorial/visual/` |
| 重置与教程角色 | `static/tutorial/avatar/` |
| React 聊天目标 | `frontend/react-neko-chat/` 与 `static/tutorial/core/chat-window-adapter.js` |

## 验收

1. 自动启动、手动重启、完成、跳过和刷新恢复使用同一状态机。
2. 不存在目标时流程可结束，不留下接管层。
3. Web 单窗口与 Electron 多窗口分别验证。
4. 所有新增文案同步对应 locale。
5. 相关 tutorial unit/frontend tests 通过。
