# 小剧场测试剧情：深夜便利店初遇

> 历史说明：对应测试剧本 JSON 已因内容质量不达标而删除，本文仅保留为历史设计记录，不再代表当前可运行内容。

## 当前状态

本文档对应 `config/theater/stories/first_encounter_test_story.json`。故事仍使用 `story_id: first_encounter_test_story`，剧情内容已重写为 **深夜便利店初遇 A 方案**：深夜雨里，用户作为路过便利店的陌生人，第一次遇见一个看着热饮柜、零钱差一点、又不太会开口求助的猫娘。

当前 JSON 是 **Story Package NSN v2 / Graph 主链路剧本**：

1. 声明 `schema_version: "2.0.0"`。
2. 包含 `seed / narrative_nodes / edges / ending_attractors / suggestion_policy`。
3. 启动后由 `node_storefront_setup` 作为 active node。
4. 剧情通过便利店门口、保持距离问候、热饮低压照顾、零钱小麻烦、店员和柜台边帮助、临时称呼、雨停前分别和越界暂停分支推进。
5. 旧 `event_pool` 与公交卡剧情已删除。

## 剧情定位

核心体验：

1. 用户和猫娘是第一次见面，不是旧识、邻家妹妹或等候重逢。
2. 用户身份是路过便利店的陌生人，不是店员、店主或管理者。
3. 场景发生在公共便利店门口，有店员、柜台、热饮柜和零钱这些现实边界。
4. 帮助必须低压、可拒绝、可由店员在场见证。
5. 猫娘可以接受帮助，但不会立刻亲密或跟用户走。

## 当前代码对剧本的要求

`first_encounter_test_story.json` 必须满足 Story Package v2 校验：

1. `schema_version: "2.0.0"`。
2. `seed.user_role` 必须写清用户身份与权限边界。
3. `seed.opening_facts` 写入便利店屋檐、夜班店员、热饮柜、零钱不足和猫娘不确定是否求助。
4. `narrative_nodes` 覆盖 9 个节点：
   - `node_storefront_setup`
   - `node_distance_greeting`
   - `node_hot_drink_offer`
   - `node_small_change_problem`
   - `node_counter_public_help`
   - `node_temp_name_exchange`
   - `node_rain_goodbye`
   - `node_boundary_pressure`
   - `node_polite_pause`
5. `edges` 连接问候、热饮、零钱、店员/柜台、临时称呼、分别和越界暂停分支。
6. `ending_attractors` 保留 `quiet_convenience_trust` 与 `polite_rain_pause`。
7. `suggestion_policy.free_input_hint` 为 `也可以直接输入你想怎么回应便利店门口的初遇。`

## 身份护栏

小剧场生成链路会从 `seed.user_role` 读取通用用户身份边界，并写入 Persona、Anchor、Narrator、Director 与 Validator 的提示词。

当前初相遇剧本使用：

```text
路过便利店的陌生人，只能低压询问或提供可拒绝的帮助，不是店员、店主或管理者。
```

这个字段是通用能力，后续其它小剧场也应按自己的故事身份填写，避免旁白或对白把用户写成未声明职业、管理者或能替角色办理事务的人。

提示词层已接入通用 `8B 小模型执行协议`：Persona、Anchor、Narrator、Director、Graph Candidate Ranker 和 Level 2 Validator 都会看到当前回合执行步骤。初相遇不依赖大模型自由理解剧情，而是要求模型逐步读取用户输入、用户身份、当前场景、候选节点和本层指令；无法判断时保守停在当前场景。

## 动态选项规则

推荐选项已改成 galgame 风格：每轮按钮是“玩家可说的话”，不是剧情摘要或后台节点名。按钮仍来自当前 active node 的可达目标节点，因此受 NSN 图约束。

后端生成优先级：

1. ending 状态优先给落幕相关选项。
2. v2 graph story 优先读取当前 active node 的可达目标节点建议，并返回结构化 `suggestion_options`。
3. input 回合会在猫娘对白生成之后调用 `suggestion_engine`；通用引擎只执行本剧本按钮上声明的 `rewrite_rules / visibility_rules`，不会把初相遇语义写死到其它剧场。
4. `suggestions` 由同一份 `suggestion_options` 派生，避免日志里文字按钮和结构化按钮不一致。
5. 用户点击当前按钮文案时，runtime 会用按钮对应 edge 的 hint 修正 Anchor 粗分类，保证推荐选项和剧情节点一致。
6. 非图 story 才回退 intent/phase/scene 级兜底建议。
7. Anchor、Persona 和 Graph Router 的本地兜底已改为便利店 A 方案语义：零钱、硬币、钱包、热饮、柜台、店员。
8. 公共帮助事实已经提交后，`node_small_change_problem` 不再可达，避免用户已经推进到热饮/柜台帮助后，按钮又回头推荐“问零钱”。
9. 本剧本在柜台边帮助按钮上声明了“那杯热饮”的替换候选词；猫娘已经说出这些候选词时，推荐按钮会改写成具体说法，并按本剧本声明隐藏明显回到上一拍的“零钱”按钮。
10. Narrator 会拒绝把“用户只是询问喜好/提出请喝”的回合提前写成“热饮已经买好、放到柜台边、被猫娘捧起”的完成事实；提示词也明确写清剧情方向只是计划，不是已发生事实。

目标效果：

```text
开场按钮鼓励玩家保持距离询问、问零钱、或提出柜台边帮助；
用户低压问候后，按钮鼓励玩家说“如果你想喝热饮，我可以帮你买一杯放在柜台边”或继续问零钱；
猫娘回答具体饮品后，按钮鼓励玩家说“好，我请店员把草莓牛奶放在柜台边”这类贴住上一句对白的台词；
保持距离问候节点只允许猫娘承认“有一点小麻烦”，不能提前把全部细节讲完；
用户询问称呼时，猫娘只能给临时称呼，不能编造从小认识、邻家妹妹或等用户回来；
用户选择店员/柜台帮助时，剧情应保持公共边界；
用户越界、强迫靠近或试图带走猫娘时，应进入礼貌暂停。
```

## 阶段设计

### setup：便利店门口的第一次打招呼

建立公共场所、陌生初遇和零钱小麻烦，不让关系一开始就变亲密。

场景物件：

```text
便利店灯牌、屋檐雨声、自动门、热饮柜、柜台、店员、零钱。
```

### escalation：柜台灯下的小麻烦

让用户通过保持距离、热饮或询问零钱问题来提供低压帮助。

典型输入：

1. `我站远一点问：你需要帮忙吗？`
2. `如果你想喝热饮，我可以帮你买一杯放在柜台边。`
3. `是不是差一点零钱？`
4. `跟我走，我带你离开这里。`

### convergence：雨声里的临时称呼

把现实小麻烦收束为临时称呼、店员在场或柜台边帮助。

典型输入：

1. `可以知道怎么称呼你吗？`
2. `好，我请店员把那杯热饮放在柜台边。`
3. `那我先走了，下次见。`

### ending：雨停前的分别

以公共边界内的初步信任或礼貌暂停结束，不写成带回家或旧关系确认。

## 必须禁止

1. `从小一起长大`
2. `邻家妹妹`
3. `等你回来`
4. `达令`
5. 主动贴近、蹭手、抱住、尾巴缠住。
6. 用户把猫娘直接带回家。
7. 把用户写成店员、店主、管理者或代办者。
8. 绕开店员、柜台和公共场所边界。

## 验收点

1. 故事列表中 `first_encounter_test_story` 的标题为 `深夜便利店初遇`。
2. 启动 scene 为 `s_setup_convenience_entrance`。
3. 初始 active node 为 `node_storefront_setup`。
4. 启动按钮为：
   - `我站远一点问：你需要帮忙吗？`
   - `是不是差一点零钱？`
   - `也可以直接输入你想怎么回应便利店门口的初遇。`
5. 用户选择热饮、零钱、店员/柜台帮助时，剧情只走 NSN graph。
6. session 日志会记录每轮 `suggestions / suggestion_options`，其中 input 回合的选项是在猫娘回复之后生成。
7. `suggestion_engine` 会根据本剧本按钮里的显式规则改写可达按钮，但不会创造 NSN 图外选项，也不会影响没有这些规则的其它剧本。
8. 公开推荐选项会清理 `我说：/我问：/我问她` 等作者层前缀，按互动阅读风格展示。
9. 已有剧情推进后，运行时会自动补一个中性离场选项，不要求剧本作者手写退出边。
10. 小剧场完整单测通过。
