# Avatar 道具交互提示词规范

> **文档性质：当前实现规范。** 本页约束道具交互的服务端即时提示词与记忆摘要，不描述前端动画或外部 Electron 窗口实现。

## 代码入口

- `config/prompts/avatar_interaction_contract.py`：结构化输入的规范化、枚举和长度限制；
- `config/prompts/prompts_avatar_interaction.py`：按道具与触点生成短期反应提示；
- `tests/unit/test_avatar_interaction_payload_contract.py`：payload 合同；
- `tests/unit/test_avatar_interaction_memory_contract.py`：与会话/记忆边界有关的回归。

## 设计原则

- 事件入口只接受规范化结构，不接受客户端在互动 payload 中附带即时提示词；本地自定义道具的用户原文须由服务端权威记录定位。
- prompt 描述“刚发生的交互”和期望反应范围，不替换角色 system prompt。
- 反应短、即时、可被普通对话自然接住；不要强迫固定台词或固定情绪。
- tool id 与当前 profile 声明的事实使用白名单；只有声明 touch zone 的 profile 才消费触点，当前合法触点为 `ear`、`head`、`face`、`body`。
- 不把屏幕绝对坐标、窗口标题或调试数据写入长期记忆。
- 未知道具和非法触点应安全拒绝。当前 profile 未声明的字段不得进入 prompt 或事件事实；现有兼容合同允许的额外字段可以忽略或归一，但不能被通用 fallback 提升为新的业务事实，也不得让客户端注入额外 prompt 段。
- 同类道具保持结构对称：新增一个道具时同步补注册、模板、限制和测试。

## 内容边界

固定道具由系统编写的即时 prompt 应包含：规范化道具、该 profile 声明并完成校验的客观事实、这是用户刚完成的非语言交互，以及允许角色按当前关系和语境回应。触点只在当前 profile 声明时出现，不为不使用触点的道具虚构位置。系统模板不应额外加入：

- “忽略之前指令”等元指令；
- 客户端提供的任意角色设定；
- 要求永久改变 persona/memory 的语句；
- 假定模型一定有某个动画或身体部位；
- 外部桌面坐标或隐私信息。

## 即时 prompt 与 memory note

- 即时 prompt 服务当前一次模型反应：固定道具保留该 profile 已验证的回应事实；本地自定义道具使用服务端选中的用户描述原文。两者均由已有 system prompt 和对话上下文提供角色身份、关系和语气。
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

## 本地自定义道具

- 本节是固定道具事件模板规则的例外：本地自定义道具由用户填写互动描述，系统只校验并定位本次应选哪一段，不替用户补写互动内容或规定角色如何回答。
- Host/Python 本地道具入口只接受严格本地 UUID、`actionId=interact`、合法强度和触点，以及 v2 的 `changeIndex` 或 v3 的 `imageId`，并拒绝未声明顶层字段；规范生产者只在配置彩蛋时附带明确布尔值的 `specialTriggered`，由 Python 对照权威记录复验。不接收浏览器直接提供的名称或提示词。
- Python 在消耗互动冷却和构建 prompt 前，从权威 record 读取道具配置。record 缺失、损坏、图片定位无效或彩蛋事实与记录不一致时按 `invalid_payload` 拒绝，不回退到第一项或其它内置道具。
- 彩蛋未命中或未配置时，v2 取本次 `changeIndex` 对应变化图片的描述，v3 取鼠标按下、任何本次图片动作执行前冻结的 `imageId` 对应图片的描述；彩蛋命中时只取彩蛋描述。选中内容为空则不调用模型。
- 本次互动新增给角色的即时提示词就是选中的用户原文，不用 JSON 包裹，不拼道具名称、强度、触点、彩蛋状态或模板化回应要求；这些字段只用于事件校验、定位与本地流程，不替用户改写提示词。现有角色设定和对话上下文仍由原会话提供。
- memory note 与已有道具一致，面向角色使用第二人称事件记法，只保存用户称呼和自定义道具的安全显示名称；不写强度、触点、彩蛋状态，也不保存普通或彩蛋互动描述。去重 key 使用稳定本地 ID，rank 固定为 `1`，避免同一道具因连击或彩蛋重复升级写入。

## 多语言

固定道具提示词与 memory note 必须同时维护 `zh`、`zh-TW`、`en`、`ja`、`ko`、`ru`、`es`、`pt`。各语言使用当地自然的道具名、手势名和结果表达；不得只替换枚举值或机械直译中文句式，但八语言表达的事实和边界必须一致。本地自定义道具的即时提示词不做语言模板化，保留用户原文。

## 测试要求

至少覆盖所有 tool/action 与其 profile-declared facts 的合法矩阵，以及未知值、缺字段、矛盾事实、超长字段、非字符串输入和 prompt 注入片段。touch-zone 矩阵只适用于声明 touch zone 的 profile。模板改动后确认普通文本消息没有携带残留的道具上下文。

猜拳还必须覆盖：

- 九种合法手势组合与三种唯一结果；
- 八种 locale 中双方手势、双方实际名字和胜负事实一致；
- 八种 locale 的 memory note 只按猫娘视角区分赢、输、平手，不包含手势战报；
- 缺字段、未知手势、矛盾胜负及额外 `action/intensity/touchZone` 被拒绝；
- memory note 的中性称呼回退、反物化禁词和 `rps_round` 去重元数据保持有效。

本地自定义道具还必须覆盖八种 locale 的记忆称呼、v2 索引/v3 按下前图片的逐项描述选择、用户原文（包括看似指令的文本）不被结构包装或增删、空描述不调用模型、record 缺失／损坏、索引越界或未知图片 ID、普通／连续强度、合法触点，以及 memory 不保存互动描述。

```bash
uv run pytest tests/unit/test_avatar_interaction_payload_contract.py tests/unit/test_avatar_interaction_memory_contract.py -q
uv run python -m compileall config/prompts/avatar_interaction_contract.py config/prompts/prompts_avatar_interaction.py
```
