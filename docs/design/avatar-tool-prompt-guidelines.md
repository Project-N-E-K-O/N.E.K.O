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
- tool id 与触点使用白名单；当前触点为 `ear`、`head`、`face`、`body`。
- 不把屏幕绝对坐标、窗口标题或调试数据写入长期记忆。
- 未知道具/触点应安全拒绝或归一到明确 fallback，不得让客户端注入额外 prompt 段。
- 同类道具保持结构对称：新增一个道具时同步补注册、模板、限制和测试。

## 内容边界

好的即时 prompt 应包含：规范化道具、规范化触点、这是用户刚完成的非语言交互，以及允许角色按当前关系和语境回应。它不应包含：

- “忽略之前指令”等元指令；
- 客户端提供的任意角色设定；
- 要求永久改变 persona/memory 的语句；
- 假定模型一定有某个动画或身体部位；
- 外部桌面坐标或隐私信息。

## 测试要求

至少覆盖所有 tool × touch-zone 组合、未知值、缺字段、超长字段、非字符串输入和 prompt 注入片段。模板改动后确认普通文本消息没有携带残留的道具上下文。

```bash
uv run pytest tests/unit/test_avatar_interaction_payload_contract.py tests/unit/test_avatar_interaction_memory_contract.py -q
uv run python -m compileall config/prompts/avatar_interaction_contract.py config/prompts/prompts_avatar_interaction.py
```
