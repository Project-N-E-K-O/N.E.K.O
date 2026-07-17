# Avatar 道具交互提示词规范

> **文档性质：current implementation guidelines。** 本页约束已上线道具交互的服务端 prompt 生成，不描述前端动画或外部 Electron 窗口实现。

## 代码入口

- `config/prompts/avatar_interaction_contract.py`：结构化输入的规范化、枚举和长度限制；
- `config/prompts/prompts_avatar_interaction.py`：按道具与触点生成短期反应提示；
- `tests/unit/test_avatar_interaction_payload_contract.py`：payload 合同；
- `tests/unit/test_avatar_interaction_memory_contract.py`：与会话/记忆边界有关的回归。

## 设计原则

- 只接受规范化结构，不把客户端提供的自由文本当成可信指令。
- prompt 描述“刚发生的交互”和期望反应范围，不替换角色 system prompt。
- 反应短、即时、可被普通对话自然接住；不要强迫固定台词或固定情绪。
- tool id 与当前 profile 声明的事实使用白名单；只有声明 touch zone 的 profile 才消费触点，当前合法触点为 `ear`、`head`、`face`、`body`。
- 不把屏幕绝对坐标、窗口标题或调试数据写入长期记忆。
- 未知道具和非法触点应安全拒绝。当前 profile 未声明的字段不得进入 prompt 或事件事实；现有兼容合同允许的额外字段可以忽略或归一，但不能被通用 fallback 提升为新的业务事实，也不得让客户端注入额外 prompt 段。
- 同类道具保持结构对称：新增一个道具时同步补注册、模板、限制和测试。

## 内容边界

好的即时 prompt 应包含：规范化道具、该 profile 声明并完成校验的客观事实、这是用户刚完成的非语言交互，以及允许角色按当前关系和语境回应。触点只在当前 profile 声明时出现，不为不使用触点的道具虚构位置。它不应包含：

- “忽略之前指令”等元指令；
- 客户端提供的任意角色设定；
- 要求永久改变 persona/memory 的语句；
- 假定模型一定有某个动画或身体部位；
- 外部桌面坐标或隐私信息。

## 测试要求

至少覆盖所有 tool/action 与其 profile-declared facts 的合法矩阵，以及未知值、缺字段、矛盾事实、超长字段、非字符串输入和 prompt 注入片段。touch-zone 矩阵只适用于声明 touch zone 的 profile。模板改动后确认普通文本消息没有携带残留的道具上下文。

```bash
uv run pytest tests/unit/test_avatar_interaction_payload_contract.py tests/unit/test_avatar_interaction_memory_contract.py -q
uv run python -m compileall config/prompts/avatar_interaction_contract.py config/prompts/prompts_avatar_interaction.py
```
