# 猫娘空闲状态分层

> **文档状态：历史功能基线。** 本文保留 Cat Mind 接入前的猫形态、动作分层和聊天 idle dock 合同，不是当前状态机或 return summary 的实施规范。当前实际计分规则见 [`cat-idle-state-machine-action-scoring.md`](cat-idle-state-machine-action-scoring.md)；设计与实施背景见 [`cat-idle-state-machine-design.md`](cat-idle-state-machine-design.md) 和 [`cat-idle-state-machine-v1-implementation.md`](cat-idle-state-machine-v1-implementation.md)。若冲突，以 `static/app/app-cat-mind.js`、当前测试和可复现运行结果为准。

## 当前入口

空闲角色逻辑已经拆到 `static/avatar/avatar-ui-buttons/`：

- `idle-actions-and-audio.js`
- `idle-assets-and-question.js`
- `idle-drag-and-subactions.js`
- `idle-journey-and-presentation.js`
- `idle-playground.js`

聊天窗口的 idle dock 适配位于 `static/app/app-react-chat-window/minimize-and-idle-dock.js`。素材位于 `static/assets/neko-idle/`。普通聊天、角色返回和 WebSocket 行为仍由当前 `static/app/`、router 与 `main_logic/core/` 包中的实际调用链负责。

Cat Mind 的 observation、动作选择和 return summary 位于 `static/app/app-cat-mind.js`；本页后续内容只描述它接入前仍需兼容的 UI 基线。

## 行为合同

- 用户主动让角色离开后，模型显示面被隐藏，返回入口切换为可交互的猫形态。
- idle tier 决定可用素材和动作；进入、退出与 tier 切换必须通过统一状态，不得只改 DOM class。
- 猫、问号方块和附属物可以有各自拖拽/点击规则；拖拽结束不能误触点击。
- Full、Compact、Minimized 聊天 surface 切换时，idle dock 要记住可恢复形态并避免双重显示。
- 返回角色、页面隐藏、模型切换和异常中断都必须停止音频、计时器、pointer handler 和 animation frame。
- reduced motion 或缺失素材时允许静态降级，返回入口仍必须可用。

## 状态所有权

idle tier 和 UI 呈现属于 avatar UI 模块；Cat Mind 的 observation 与动作选择属于 `app-cat-mind.js`；React Chat 只消费镜像状态并调整自己的 dock/surface。不要在多个模块维护独立 tier 真相。跨窗口或桌面宿主消息应带明确 action 和快照，但外部 N.E.K.O-PC 的原生窗口实现不在本仓库验证范围。

## 验证

```bash
uv run pytest tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_avatar_return_button_cat1_static.py tests/unit/test_react_chat_idle_dock_static.py tests/unit/test_cat_idle_state_machine_static.py -q
```

手工验收至少覆盖：反复离开/返回、拖拽后点击、Full/Compact/Minimized 往返、页面隐藏、模型切换、素材加载失败和 reduced motion。
