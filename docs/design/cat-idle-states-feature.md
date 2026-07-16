# 猫娘空闲状态分层

> **文档性质：current implementation record。** 本页记录“请她离开”后的猫形态、动作分层和聊天 idle dock 合同。素材演出细节以当前源码和测试为准，不再保留旧的逐帧施工剧本。

## 当前入口

空闲角色逻辑已经拆到 `static/avatar/avatar-ui-buttons/`：

- `idle-actions-and-audio.js`
- `idle-assets-and-question.js`
- `idle-drag-and-subactions.js`
- `idle-journey-and-presentation.js`
- `idle-playground.js`

聊天窗口的 idle dock 适配位于 `static/app/app-react-chat-window/minimize-and-idle-dock.js`。素材位于 `static/assets/neko-idle/`。普通聊天、角色返回和 WebSocket 行为仍由当前 `static/app/`、router 与 `main_logic/core/` 包中的实际调用链负责。

## 行为合同

- 用户主动让角色离开后，模型显示面被隐藏，返回入口切换为可交互的猫形态。
- idle tier 决定可用素材和动作；进入、退出与 tier 切换必须通过统一状态，不得只改 DOM class。
- 猫、问号方块和附属物可以有各自拖拽/点击规则；拖拽结束不能误触点击。
- Full、Compact、Minimized 聊天 surface 切换时，idle dock 要记住可恢复形态并避免双重显示。
- 返回角色、页面隐藏、模型切换和异常中断都必须停止音频、计时器、pointer handler 和 animation frame。
- reduced motion 或缺失素材时允许静态降级，返回入口仍必须可用。

## 状态所有权

idle 状态属于 avatar UI 模块；React Chat 只消费镜像状态并调整自己的 dock/surface。不要在两个模块各维护一份独立 tier 真相。跨窗口或桌面宿主消息应带明确 action 和快照，但外部 N.E.K.O-PC 的原生窗口实现不在本仓库验证范围。

## 验证

```bash
uv run pytest tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_avatar_return_button_cat1_static.py tests/unit/test_react_chat_idle_dock_static.py -q
```

手工验收至少覆盖：反复离开/返回、拖拽后点击、Full/Compact/Minimized 往返、页面隐藏、模型切换、素材加载失败和 reduced motion。
