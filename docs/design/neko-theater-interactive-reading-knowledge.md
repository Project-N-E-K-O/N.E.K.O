# 小剧场互动阅读知识库

## 来源状态

1. 用户指定参考页：`https://www.66rpg.com/t_113/Qh04NbjX1.shtml?target=1`。
2. 当前页面只返回“努力制作中，敬请期待……”，没有可整理的教程正文。
3. 本文不搬运橙光教程原文，只沉淀可用于 N.E.K.O 小剧场的互动阅读产品原则。

## 可复用产品原则

1. 玩家看到的是自然选择，不是系统命令。
2. 选项应像“下一步我要怎么做 / 怎么说”，不要暴露 `node_id`、`behavior_hint`、`meaning_hint`、`我说：`、`我问：` 等作者层或调试层文本。
3. 作者面向非编程用户时，只需要描述剧情、角色、场景、选择和结局；复杂图边、条件、证据和 fallback 由 SDK 生成。
4. 每段互动都要有可继续、可询问、可离开的基础出口，避免玩家卡在已经想结束但图状态不允许结束的循环。
5. 好结局可以要求剧情证据；玩家主动离场不应要求好结局证据，也不应被包装成好结局。

## 对 NSN 的约束

1. 玩家层：
   - 只显示干净 `label`，例如“是不是差一点零钱？”、“那我先走了，下次见。”。
   - 不显示 `我说：`、`我问她` 这类作者层句式。
2. 运行时层：
   - `suggestion_options` 仍保留 `behavior_hint / meaning_hint` 供路由使用。
   - 点击干净 label 后，Graph Router 要能匹配回原始 suggestion。
   - 当图没有离场边且故事已经推进过至少一个节点时，运行时补一个中性 `user_exit` 选项。
3. SDK 层：
   - 自动生成基础离场路径和中性退出选项。
   - 自动校验每个主要节点至少有继续或退出路径。
   - 作者无需手写 `rewrite_rules / visibility_rules`；这些只能作为 SDK 内部产物或高级模式。
4. Ending 层：
   - `story_ending` 需要 NSN 证据支撑。
   - `user_exit` 表示玩家主动结束本次小剧场，不要求好结局证据，不写成达成信任结局。

## 当前实现对齐

1. `services/theater/suggestion_engine.py` 会清洗公开选项前缀。
2. `services/theater/graph_router.py` 可用清洗后的选项 label 反查图边信号。
3. `services/theater/suggestion_engine.py` 会在已有剧情推进后补通用离场选项。
4. `services/theater/ending_engine.py` 已区分 `story_ending` 和 `user_exit`。
5. `services/theater/runtime.py` 会在 Director 选出目标节点后对齐 NSN graph phase；没有目标节点时才回退 active node phase，避免只按轮次提前切到 ending。
