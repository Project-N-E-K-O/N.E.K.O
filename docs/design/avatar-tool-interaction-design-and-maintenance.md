# Avatar 道具交互设计与维护说明

> **文档性质：current implementation record。** 本页记录本仓库 React 聊天、静态宿主和后端 prompt 合同。N.E.K.O-PC 的原生窗口、命中区域与 preload 代码属于外部仓库，除非在那里单独验证，否则只能视为集成边界。

## 当前范围

内置道具包括 `lollipop`、`fist` 和 `hammer`。Full/Compact Chat 共用注册表、选择和提交语义，呈现布局可以不同。

主要入口：

- `frontend/react-neko-chat/src/avatarTools.ts`：道具注册表、类型和 payload；
- `frontend/react-neko-chat/src/AvatarToolQuickbar.tsx`：选择入口；
- `frontend/react-neko-chat/src/AvatarToolItemManager.tsx`：拖拽/投放生命周期；
- `config/prompts/avatar_interaction_contract.py`：服务端规范化与允许值；
- `config/prompts/prompts_avatar_interaction.py`：即时反应 prompt；
- `static/avatar-interaction-contract.test.cjs` 与 `static/react-chat-avatar-interaction-host.test.cjs`：浏览器/宿主静态合同。

## 提交合同

道具交互必须提交结构化 payload，而不是把前端任意文本直接拼入 system prompt。服务端规范化工具负责：

- 校验 tool id；
- 把触点归一到 `ear`、`head`、`face`、`body`；
- 限制坐标和可选字段；
- 生成稳定、短小的交互描述；
- 对未知值安全拒绝或降级。

前端坐标是交互提示，不是对 avatar 像素的永久身份。窗口缩放、不同载体和外部桌面 overlay 都可能改变坐标系；跨进程传递时必须明确使用的 rect 与归一化方式。

## 生命周期

1. 用户从 quickbar 选择道具；
2. React 创建可拖拽物和本次 interaction id；
3. 投放命中后由宿主适配层形成规范化 payload；
4. 普通消息提交链路携带该 payload；
5. 后端生成一次性反应上下文；
6. 成功、取消、超时、窗口隐藏或模式切换都清理道具状态。

一次投放只能消费一次。旧异步回调必须通过 interaction id/状态检查，不能重复发送或污染下一条消息。

## Full、Compact 与桌面边界

两种 React surface 应共享业务组件和 payload schema，只允许布局、入口密度和拖拽表面不同。网页端可在本仓库完整验证；桌面原生窗口的跨窗口 relay、透明区域命中和 always-on-top 行为必须在 N.E.K.O-PC 仓库验证。本仓库测试只能证明生产方 payload 与前端适配合同。

## 验证

```bash
npm --prefix frontend/react-neko-chat test -- --run
node --test static/avatar-interaction-contract.test.cjs static/react-chat-avatar-interaction-host.test.cjs
uv run pytest tests/unit/test_avatar_interaction_payload_contract.py tests/unit/test_avatar_interaction_memory_contract.py -q
```

新增道具时同步更新注册表、规范化白名单、prompt 映射和测试；不要只在某一个聊天 surface 增加分支。
