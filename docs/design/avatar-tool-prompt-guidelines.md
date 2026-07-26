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

## 即时 prompt 与 memory note

- 即时 prompt 服务当前一次模型反应，应保留该 profile 声明且已经验证的回应所需事实，并继续让已有 system prompt 决定人格、关系和语气。
- memory note 服务道具互动的记忆显示和既有持久化链路，应使用本地化的简短事件摘要。它可以按道具规范省略低价值细节，但不能增加客户端未提交或后端未验证的事实。
- memory note 对人的称呼使用当前用户实际名字；名字不可用时使用各语言中性称呼，不使用“主人”、`master`、`ご主人さま`、`주인`、`Хозяин` 等物化称呼。
- 同类互动使用稳定的 `memory_dedupe_key`；只有存在强度升级语义时才提高 `memory_dedupe_rank`。去重只控制重复持久化，不得改变本次 prompt、画面、声音或已确认结果。
- 普通文本消息不得继承上一次道具的 prompt 或 memory note；道具轮继续使用现有隔离、turn meta 和 ack 生命周期，不为单个道具另建旁路。

## 猜拳

猜拳只消费严格验证且彼此一致的三个事实：

```json
{
  "user_gesture": "rock | scissors | paper",
  "avatar_gesture": "rock | scissors | paper",
  "round_result": "user_win | avatar_win | draw"
}
```

- 九种手势组合必须由合同校验出唯一胜负；prompt 和 memory 不重新随机或重新判断。
- 即时 prompt 使用当前用户和猫娘的实际名字，保留双方本局手势与胜负，并让当前人格、关系和对话语境决定自然反应。
- 胜负只作为一次客观事实，不再追加“赢后应如何反应 / 输后应如何反应”的结果重点，也不要求用固定台词、情绪、动作或表情证明理解。
- 可以提示不必先复述胜负，但不能把回应限制为“只根据本局事实”，以免切断当前 persona、关系和对话上下文。
- memory note 只保留互动对象与猫娘视角的结果，不重复双方手势。中文语义固定为：`[和{用户称呼}猜拳，输了]`、`[和{用户称呼}猜拳，赢了]`、`[和{用户称呼}猜拳，平手]`。
- 猜拳使用 `memory_dedupe_key="rps_round"`、`memory_dedupe_rank=1`；不构造比分、连胜、胜率、赌注、奖励或历史战绩。

## 多语言

正式道具提示词与 memory note 必须同时维护 `zh`、`zh-TW`、`en`、`ja`、`ko`、`ru`、`es`、`pt`。各语言使用当地自然的道具名、手势名和结果表达；不得只替换枚举值或机械直译中文句式，但八语言表达的事实和边界必须一致。

## 测试要求

至少覆盖所有 tool/action 与其 profile-declared facts 的合法矩阵，以及未知值、缺字段、矛盾事实、超长字段、非字符串输入和 prompt 注入片段。touch-zone 矩阵只适用于声明 touch zone 的 profile。模板改动后确认普通文本消息没有携带残留的道具上下文。

猜拳还必须覆盖：

- 九种合法手势组合与三种唯一结果；
- 八种 locale 中双方手势、双方实际名字和胜负事实一致；
- 八种 locale 的 memory note 只按猫娘视角区分赢、输、平手，不包含手势战报；
- 缺字段、未知手势、矛盾胜负及额外 `action/intensity/touchZone` 被拒绝；
- memory note 的中性称呼回退、反物化禁词和 `rps_round` 去重元数据保持有效。

```bash
uv run pytest tests/unit/test_avatar_interaction_payload_contract.py tests/unit/test_avatar_interaction_memory_contract.py -q
uv run python -m compileall config/prompts/avatar_interaction_contract.py config/prompts/prompts_avatar_interaction.py
```
