# 小剧场叙事状态网络开发文档

> 历史说明：本文记录瘦身前的 NSN 设计。原初遇、雨窗和星灯祭测试剧本均已删除，当前实现请以 [`neko-theater-v2.3-implementation-architecture.md`](./neko-theater-v2.3-implementation-architecture.md) 为准。

## 文档定位

本文档是基于《Project N.E.K.O：叙事状态网络 NSN v1.6 终极架构与数据协议冻结规范》整理出的项目落地版。

它不是直接替换现有小剧场系统，而是在当前代码已经具备的 Runtime、Story Loader、Anchor、Director、Event Manager、State Manager、Narrator、Catgirl、Ending 和 Memory 边界上，规划下一阶段更稳定的剧情演绎结构。

核心目标：

1. 解决当前剧本背景薄、剧情跳跃、选项割裂的问题。
2. 把现有 `event_pool + phase + intent` 升级为可查询、可验证、可回滚的叙事状态网络。
3. 让选项来自“当前可达节点”，而不是静态 scene 或单轮意图。
4. 在不污染主记忆和猫娘人格的前提下，把小剧场结果转成安全的软倾向。

## 当前代码基础

当前已具备：

1. `services/theater/runtime.py`
   - 负责 session 编排。
   - 当前链路：Anchor → Scene Resolver → Director → Catgirl → State Manager → Ending → Narrator → Memory。
2. `services/theater/story_loader.py`
   - 读取 `config/theater/stories/*.json`。
   - 已校验基础 Story Package 与 Event Pool。
3. `services/theater/anchor_engine.py`
   - 识别用户输入意图：`support`、`conflict`、`intimacy`、`avoidance`、`exploration`。
   - LLM 优先，规则兜底。
4. `services/theater/director_engine.py`
   - 当前负责选择事件、版本、剧情方向、Narrator 指令、Catgirl 指令。
   - 已有 LLM 优先与规则兜底。
5. `services/theater/event_engine.py`
   - 当前负责 event_pool 选择、优先级、前置依赖、冲突、repeatable、完成条件。
6. `services/theater/state_manager.py`
   - 当前维护 theater 私有 Story State。
   - 已有 facts、events、relationship_signals、ending_attractor、loop_guard。
7. `services/theater/narrator_engine.py`
   - 生成旁白，禁止控制用户行为和用户内心。
8. `services/theater/persona_engine.py`
   - 生成猫娘对白。
   - 已读取当前猫娘 `persona.json` 摘要。
9. `services/theater/ending_engine.py`
   - 根据 phase、ending_attractor 和用户语义判断是否落幕。
10. `services/theater/memory_writer.py`
   - 只生成 theater 私有摘要和用户确认后的边界归档。

当前主要不足：

1. Story Package 仍偏“线性四阶段 + 事件池”，不是图结构。
2. `background` 是文本块，不能稳定约束 Director/Narrator/Catgirl。
3. 事件之间有依赖和冲突，但缺少明确的可达边。
4. 用户选项仍是建议集合，不是由“当前节点的可达下一节点”生成。
5. State 更新以轻量事实和事件状态为主，尚未结构化成 subject-predicate-object。
6. 没有 State Preview / Commit / Rollback 事务。
7. Validator 还只是分散在各模块的安全过滤，不是统一验证层。

## 优化后的目标架构

建议采用“渐进式 NSN”，不要一次推翻现有系统。

```text
Story Package v2
  ↓
Story Seed / Input Node
  ↓
Narrative Encoder
  ↓
Director Router
  ↓
Candidate Node Filter
  ↓
State Preview
  ↓
Narrator + Catgirl
  ↓
Narrative Validator
  ↓
State Commit / Rollback
  ↓
Memory Classifier
```

与附件 NSN v1.6 的差异：

1. 保留现有 Anchor / Director / Event / State 模块，不另起一套系统。
2. 先用 JSON，不强制 YAML。
3. 先做 Level 1 本地 Validator，Level 2 LLM Validator 放到关键节点。
4. 先把普通 `event_pool` 扩展为 `narrative_nodes`，再逐步迁移旧事件。
5. 记忆先维持 theater 私有候选，不直接接 Master Memory Hub。

## Story Package v2 建议结构

```json
{
  "id": "first_encounter_warm_lamp",
  "schema_version": "2.0.0",
  "meta": {
    "title": "暖灯初相遇",
    "theme": "温柔不是立刻靠近，而是让对方知道自己可以慢慢来"
  },
  "seed": {
    "world_context": "雨后夜晚，N.E.K.O 小房间，暖灯、温牛奶、毛毯、小铃铛。",
    "user_role": "第一次发现猫娘在房间里的陪伴者，不拥有强制解释权。",
    "catgirl_initial_context": "猫娘刚醒来，只知道这里暂时安全，但不认识用户。",
    "opening_facts": [
      { "type": "fact", "subject": "room", "predicate": "has", "object": "warm_lamp" },
      { "type": "fact", "subject": "catgirl", "predicate": "holds", "object": "soft_cushion" },
      { "type": "belief", "subject": "catgirl", "predicate": "uncertain_about", "object": "user_safety" }
    ],
    "forbidden_assumptions": [
      { "subject": "catgirl", "predicate": "has_real_memory_of", "object": "user" },
      { "subject": "user", "predicate": "already_saved", "object": "catgirl" }
    ]
  },
  "phases": [
    { "id": "phase_1_greeting", "title": "暖灯下的问候" },
    { "id": "phase_2_safe_distance", "title": "温柔的距离" },
    { "id": "phase_3_small_name", "title": "一个轻轻的称呼" },
    { "id": "phase_4_goodnight", "title": "暖灯晚安" }
  ],
  "narrative_nodes": [],
  "edges": [],
  "ending_attractors": [],
  "suggestion_policy": {}
}
```

## Narrative State Node v1

现有 `event_pool` 应升级为 `narrative_nodes`。每个节点是一个剧情状态，而不只是事件素材。

```json
{
  "node_id": "node_catgirl_accepts_warm_milk",
  "belong_phase": "phase_2_safe_distance",
  "node_type": "core",
  "title": "接受温牛奶的距离",
  "summary": "用户把温牛奶放到猫娘够得到的位置，猫娘在不被催促的情况下接受这份照顾。",
  "preconditions": {
    "required_facts": [
      { "subject": "catgirl", "predicate": "is_in", "object": "warm_lamp_room" }
    ],
    "forbidden_facts": [
      { "subject": "user", "predicate": "violated", "object": "catgirl_boundary" }
    ]
  },
  "transition_rules": {
    "required": [
      { "behavior": "comfort", "meaning": "trust_building", "polarity": "positive" }
    ],
    "supporting": [
      { "behavior": "protect", "meaning": "reduce_distance", "polarity": "positive" }
    ],
    "neutral": [
      { "behavior": "observe", "meaning": "maintain_status", "polarity": "neutral" }
    ],
    "blocking": [
      { "behavior": "attack", "polarity": "negative" },
      { "behavior": "challenge", "meaning": "hostility", "polarity": "negative" }
    ]
  },
  "runtime_generation_guide": {
    "narrator_intent": "描写温牛奶的热气和猫娘放松一点的细节，不写用户动作。",
    "catgirl_raw_intent": "猫娘仍然小声和谨慎，但愿意承认这杯温牛奶让她安心一点。"
  },
  "state_diff": {
    "add": [
      { "type": "fact", "subject": "catgirl", "predicate": "accepted", "object": "warm_milk_nearby" },
      { "type": "belief", "subject": "catgirl", "predicate": "considered", "object": "user_can_wait" }
    ],
    "remove": [],
    "modify": []
  },
  "suggestions": [
    {
      "label": "问她要不要先暖暖手",
      "behavior_hint": "comfort",
      "meaning_hint": "trust_building"
    },
    {
      "label": "把杯子留在桌边，不催她接受",
      "behavior_hint": "comfort",
      "meaning_hint": "emotional_shield"
    }
  ]
}
```

## Core Signal Vocabulary v1

当前 `anchor_engine.py` 的五类 intent 太粗。建议保留 intent 作为兼容层，同时新增 NSN 信号。

### Layer 1：Behavior

```text
protect
attack
question
comfort
tease
ignore
leave
accept
reject
share
hide
confess
challenge
observe
unknown
```

### Layer 2：Meaning

```text
reduce_distance
increase_distance
trust_building
emotional_shield
hostility
indifference
cooperation
provocation
maintain_status
unknown
```

### Layer 3：Narrative

```text
reveal_secret
create_conflict
resolve_conflict
maintain_status
accelerate_bonding
trigger_defense
test_boundaries
progress_plot
unknown
```

### Polarity

```text
positive
neutral
negative
```

兼容映射：

| 现有 intent | 默认 behavior | 默认 meaning | 默认 narrative |
|---|---|---|---|
| support | comfort | trust_building | resolve_conflict |
| intimacy | accept | reduce_distance | accelerate_bonding |
| exploration | question | cooperation | progress_plot |
| avoidance | leave | increase_distance | maintain_status |
| conflict | challenge | hostility | trigger_defense |

## Director Router v2

当前 Director 做的是：

```text
story_state + anchor_result + scene
→ v2 story 走 graph router
→ 非 graph / 无显式节点时只生成 scene 级轨迹
→ 输出 event_id / version / 指令
```

NSN 后应升级为：

```text
story_state + active_node + user_signal
→ 从 edges 找可达节点
→ rules 过滤
→ preconditions 校验
→ LLM 只在候选集内排序
→ 产出 next_node_id
```

路由流程：

1. Narrative Encoder 把用户输入转成 Core Signal。
2. Director Router 读取当前激活节点和 phase。
3. 查找所有 `from_node = active_node` 的 edges。
4. 用 `transition_rules` 过滤 blocking。
5. 用 `preconditions` 检查 required / forbidden facts。
6. 如果候选为 1 个，直接激活。
7. 如果候选多个，LLM 只做 Top 3 排序，不允许生成节点外剧情。
8. 如果没有候选，返回 `graph_no_candidate` 暂停，不回落旧事件池。

## 动态选项生成 v2

当前动态选项已经由 Runtime 按以下顺序生成：

```text
ending 状态
→ 当前 Director 事件
→ 已完成事件
→ 用户本轮 Anchor 意图
→ 当前 phase
→ scene 固定 suggestions
```

NSN 后应改成：

```text
当前 active_node
→ 查询可达 edges
→ 过滤已完成 / blocked / forbidden
→ 读取目标节点 suggestions
→ 加入 1 个自由输入提示
```

选项必须满足：

1. 只来自当前可达节点。
2. 不重复已经完成的同意义节点。
3. 不暴露内部节点 id。
4. 每个选项都能映射到 behavior_hint / meaning_hint。
5. 用户自由输入优先，按钮只是建议路径。

## State Preview / Commit / Rollback

当前系统是 Director 先给 state_patch，然后 State Manager 直接 apply。

NSN 后改成事务流：

```text
Director Router 选中 node
→ State Preview 暂存 state_diff
→ Narrator / Catgirl 生成文本
→ Validator 检查
→ 通过：State Commit
→ 失败：Rollback，禁用该 node，本轮重路由
```

建议新增私有 session 字段：

```json
{
  "active_node_id": "node_catgirl_accepts_warm_milk",
  "completed_node_ids": [],
  "blocked_node_ids": [],
  "state_preview": null,
  "rollback_logs": []
}
```

回滚日志建议：

```json
{
  "timestamp": 0,
  "phase": "phase_2_safe_distance",
  "attempted_node": "node_catgirl_accepts_warm_milk",
  "validator_level": "level_1",
  "failed_check": "persona_consistency",
  "reason": "Catgirl output became fully trusting too early."
}
```

## Validator v1

当前校验分散在 Director、Narrator、Persona、Memory。

建议新增统一 Validator 模块：

```text
services/theater/validator_engine.py
```

Level 1：每轮必检，本地规则。

检查：

1. 输出不包含内部机制词。
2. Narrator 不替用户行动。
3. Narrator 不描述用户内心。
4. Catgirl 不覆盖当前猫娘角色卡。
5. Catgirl 不过早完全信任。
6. 生成文本必须回应用户本轮输入。
7. state_diff 不违反 forbidden facts。

Level 2：关键节点触发，LLM 检查。

触发：

1. Core Node。
2. Ending。
3. Memory 写入候选。
4. 剧情重大秘密揭示。

检查：

1. 剧情是否真的推进。
2. 是否回应用户输入。
3. 是否符合猫娘人格。
4. 是否违反小剧场现实边界。

## Memory Classifier v1

当前 `memory_writer.py` 已做到：

1. ending 后生成 theater 私有摘要。
2. 用户确认后才写普通记忆。
3. 普通记忆带虚构边界。

NSN 后补三级分类：

### Type A：用户偏好 / 行为习惯

允许进入普通记忆候选。

示例：

```text
用户在小剧场中多次选择温柔等待、低压照顾。
```

### Type B：猫娘心防变化

不写成现实事实，转成 Response Bias。

示例：

```json
{
  "type": "theater_response_bias",
  "source_story": "first_encounter_warm_lamp",
  "trigger_context": "用户在高不安场景中选择 comfort / protect。",
  "response_bias": {
    "target_reaction_tendency": "slightly_more_receptive",
    "behavioral_instruction": "普通陪伴中可以短暂表现羞涩和安心，但不要主动回忆小剧场事实。"
  }
}
```

### Type C：剧本限定物理事实

严格拦截。

示例：

```text
用户在小剧场里留下了小铃铛。
猫娘在暖灯房间里睡了一晚。
```

## 与现有代码的迁移关系

| NSN 模块 | 当前对应代码 | 迁移方式 |
|---|---|---|
| Story Seed | Story Package `background` / `initial_state` | 扩成 `seed.opening_facts` |
| Narrative Encoder | `anchor_engine.py` | 保留 intent，新增 behavior / meaning / narrative |
| Director Router | `director_engine.py` | 从 event 选择升级为 node 路由 |
| Narrative State Node | 旧 `event_pool` 已停止作为运行时协议 | 使用 `narrative_nodes` |
| State Preview | 暂无 | 新增 preview 字段，不直接写 story_state |
| State Commit | `state_manager.apply_turn()` | 拆成 preview / commit |
| Rollback | 暂无 | 新增 rollback_logs 和 node 禁用 |
| Validator | 各模块安全过滤 | 新增 `validator_engine.py` 汇总 |
| Dynamic Suggestions | `runtime._suggestions_for_turn()` | 改为从可达 node 生成 |
| Ending Attractor | `ending_engine.py` + `state_manager.py` | 改为 evidence_required 扫描 |
| Memory Classifier | `memory_writer.py` | 增加 Type A/B/C 分类 |

## 推荐开发路线

### Phase 1：Story Package v2 兼容层

目标：

1. `story_loader.py` 支持 `schema_version: 2.0.0`。
2. 新增 `seed`、`narrative_nodes`、`edges` 字段校验。
3. 旧 `event_pool` 不再作为 runtime 推进协议；v2 剧本必须使用 `narrative_nodes / edges`。
4. 暂不改前端。

验证：

1. 旧剧本仍可用 scene 级兜底加载，但不会再由 `event_pool` 自动推进。
2. 新 NSN 剧本可加载。
3. public story 不暴露内部 graph。

### Phase 2：Narrative Encoder

目标：

1. 在 `anchor_engine.py` 输出里增加 `core_signal`。
2. 保留旧 `intent_category`。
3. 增加 15-10-10 词典本地规则兜底。

验证：

1. 用户“你冷不冷，我把杯子放桌边”应识别为 `comfort / trust_building / resolve_conflict / positive`。
2. 用户“过来让我检查”应识别为 `challenge / hostility / trigger_defense / negative`。

### Phase 3：Director Router v2

目标：

1. 新增 `graph_router.py` 或在 `director_engine.py` 内部加入 v2 路由。
2. 基于 active_node + edges 过滤候选。
3. LLM 只排序候选，不生成候选外节点。

验证：

1. 已完成节点不重复。
2. forbidden facts 命中时节点不可选。
3. 选项跟随可达节点变化。

### Phase 4：State Preview / Validator / Commit

目标：

1. `state_manager.py` 拆出 preview 和 commit。
2. 新增 `validator_engine.py`。
3. 验证失败时 rollback 并重新选节点。

验证：

1. Catgirl 过早完全信任会被拦截。
2. Narrator 替用户行动会被拦截。
3. Rollback 不写入 completed_node_ids。

### Phase 5：Ending Evidence Scanner

目标：

1. Ending 不只看 phase 和 strength。
2. 根据 `ending_attractors.evidence_required` 扫描 committed facts。
3. 用户拒绝结束时仍可继续路由。

验证：

1. 没有 mandatory facts 不允许进入对应结局。
2. forbidden facts 命中时禁止对应结局。

### Phase 6：Memory Classifier

目标：

1. `memory_writer.py` 增加 Type A/B/C 分类。
2. Type B 生成 response_bias 候选。
3. Type C 明确拦截。

验证：

1. 剧本物件不进入普通记忆。
2. 用户偏好可以作为候选。
3. 猫娘心防变化只作为 soft bias。

## 第一条 NSN Demo 建议

建议不要继续用当前“初相遇”作为最终验证 Demo。它适合轻量试验，但不适合验证 NSN。

更适合作为 NSN Demo 的故事：

```text
雨夜遇见猫娘
```

原因：

1. 世界背景天然强。
2. 用户行为差异明显：保护、追问、离开、逗弄、强迫。
3. 猫娘初始状态清楚：寒冷、饥饿、警惕。
4. 结局证据容易结构化：是否接受帮助、是否进入安全空间、是否保留距离。
5. 更适合测试 Type A/B/C 记忆分流。

建议 Demo 规模：

1. 3 个 phase。
2. 12 个 core nodes。
3. 2 个 ending attractors。
4. 3 个 micro node 预算。
5. 只做 Level 1 Validator。

## 本阶段不做

1. 不做完整作者 SDK UI。
2. 不做可视化图编辑器。
3. 不把小剧场事实直接写进主长期记忆。
4. 不引入好感度、信任值、黑化值等数值叙事。
5. 不让 LLM 自由决定任意节点跳转。
6. 不让 Catgirl 层承担世界变化或剧情推进职责。

## 判断标准

NSN v1 落地是否成功，看这几件事：

1. 用户自由输入后，系统能解释“为什么走到这个节点”。
2. 选项来自当前可达节点，不再和剧情割裂。
3. 剧情不会因为一句用户追问就跳到亲密或落幕。
4. 猫娘对白保留角色卡人格，但不会吞掉剧情问题。
5. Narrator 能补足背景，但不替用户行动。
6. 结局必须由 committed facts 支撑。
7. 记忆写入必须经过 Type A/B/C 分类。

## 已实现进度

### 已落地：Phase 1 Story Package v2 图结构

1. `services/theater/story_loader.py` 已支持 `schema_version: "2.0.0"`。
2. `story_loader.py` 已新增 NSN 字段校验：
   - `seed`
   - `narrative_nodes`
   - `edges`
   - `ending_attractors`
   - `suggestion_policy`
3. `story_loader.py` 会校验 `edges.from_node` / `edges.to_node` 必须引用已声明的 `narrative_nodes.node_id`。
4. `public_story()` 仍只公开基础故事和 scenes，不暴露 `seed`、`narrative_nodes`、`edges` 等内部图结构。
5. v2 样板故事已删除旧 `event_pool` 和 `micro_node_budget`；runtime 不再初始化、校验或选择旧事件池。

### 已落地：Phase 2 Narrative Encoder 兼容输出

1. `services/theater/anchor_engine.py` 保留旧 `intent_category`。
2. Anchor 输出新增 `core_signal`：
   - `behavior`
   - `meaning`
   - `narrative`
   - `polarity`
3. 本地兜底和 LLM 结果都会返回 `core_signal`。
4. LLM 返回的 `core_signal` 会被冻结词表白名单过滤；词表外内容回退到旧 intent 的兼容映射。
5. 本地关键词补充了“冷不冷”“杯子”“温牛奶”“不急着”等低压照顾表达，用于识别 `comfort / trust_building / resolve_conflict / positive`。

### 已落地：第一条真实 NSN Demo

1. `config/theater/stories/rainy_window_test_story.json` 已升级为 `schema_version: "2.0.0"`。
2. 雨窗故事已新增：
   - `seed.opening_facts`
   - 4 个 `narrative_nodes`
   - 4 条 `edges`
   - 2 个 `ending_attractors`
   - `suggestion_policy`
3. 雨窗故事不再保留 `event_pool`，运行时只走 NSN graph。

### 已落地：Phase 3 规则版 Graph Router

1. 新增 `services/theater/graph_router.py`。
2. `graph_router.py` 已支持：
   - 根据 `active_node_id` 查找 `edges.from_node`。
   - 用 Anchor `core_signal` 匹配 edge 的 `behavior / meaning / polarity`。
   - 跳过 `completed_node_ids` 和 `blocked_node_ids`。
   - 检查目标节点 `preconditions.required_facts / forbidden_facts`。
   - 从可达目标节点生成动态建议，并追加自由输入提示。
3. `services/theater/state_manager.py` 已初始化 NSN 私有字段：
   - `active_node_id`
   - `completed_node_ids`
   - `blocked_node_ids`
   - `state_preview`
   - `rollback_logs`
   - `narrative_facts`
4. `state_manager.py` 已能提交 Director 选中的 `narrative_node`，更新 active/completed 节点和结构化 `narrative_facts`。
5. `services/theater/director_engine.py` 已优先尝试 graph router；没有可达节点时返回 `graph_no_candidate`，不再回退旧 `event_pool`。
6. `services/theater/runtime.py` 已在 v2 story 中优先使用可达节点 suggestions；非图 story 只保留 intent/phase/scene 级兜底，不再读取 event/after 事件键。
7. `services/theater/graph_router.py` 已新增 `suggestion_options_for_active_node()`：
   - 公开字段包含 `label / behavior_hint / meaning_hint`。
   - 原 `suggestions` 仍由 `label` 派生，兼容现有前端按钮。
   - 自由输入提示标记为 `behavior_hint: free_input` 与 `meaning_hint: player_choice`，避免被误当成具体剧情边。
8. 新增 `services/theater/suggestion_engine.py`：
   - 每轮在 Persona 回复之后读取 graph router 产出的可达 `suggestion_options`。
   - 只允许按剧本 suggestion 上显式声明的 `rewrite_rules / visibility_rules` 过滤或改写已可达按钮，不允许根据猫娘对白创造 NSN 图外选项。
   - 没有声明规则的剧本会原样返回 graph router 按钮，不会被初相遇、饮品、零钱等特定剧本语义影响。
   - `rewrite_rules` 用于把剧本按钮里的占位词替换成本回合猫娘已经说出的候选词。
   - `visibility_rules` 用于在剧本明确声明条件时隐藏滞后按钮，避免推荐选项和上一句对白割裂。
   - 公开 label 会清理 `我说：/我问：/我问她` 等作者层前缀，按互动阅读选择呈现。
   - 已有剧情推进后，如果当前可达按钮没有离场项，运行时会补一个 `behavior_hint: leave / meaning_hint: user_exit` 的中性退出选择。
9. `services/theater/runtime.py` 已在 start / input 响应中新增 `suggestion_options`：
   - v2 graph 命中时返回结构化建议。
   - 普通回合先生成 `suggestion_options`，再由同一份结果派生 `suggestions`，避免两个按钮列表错拍。
   - 非图 story、scene/ending suggestion 来源暂不伪造 hint，字段保持空数组。
10. `services/theater/event_engine.py` 已精简为 Director 事件提交、`graph_no_candidate` 暂停和 scene fallback；旧事件池初始化、优先级选择、完成条件和 `ending_effects` 已删除。
11. `services/theater/graph_router.py` 已新增 `route_candidates()`：
   - 只返回已通过 edge / blocking / precondition 过滤的 Top 3 候选。
   - `route_turn()` 继续使用候选第一名作为规则兜底。
12. `services/theater/runtime.py` 会把每轮公开返回的 `suggestions / suggestion_options` 同步写入对应 assistant turn，真实 session 日志可直接复盘当时展示给用户的按钮。
13. 用户输入如果精确命中当前 active node 的推荐按钮，`graph_router.core_signal_for_suggestion()` 会用按钮对应 edge 还原 `core_signal`，覆盖 Anchor 粗分类，避免“点击选项却因为文本被判成 exploration 而 graph hold”。它同时支持原始作者层 label 和清洗后的互动阅读 label。
14. `services/theater/director_engine.py` 已接入 graph 候选 LLM 排序：
   - 仅当候选数大于 1 且 summary 模型配置可用时触发。
   - LLM 只返回 `ranked_node_ids`，越界 node_id 会被忽略。
   - 最终 Director 决策仍由本地候选数据生成，不允许模型生成候选外节点、旁白、台词或 state_patch。
15. `config/prompts/prompts_theater.py` 已把 Narrator 改为 Persona 后补景：
   - 用户本轮输入和猫娘本轮对白是已发生锚点。
   - Director 的剧情方向只是待提交计划，不等于已经发生。
   - 用户只是在询问、确认或提出可能性时，Narrator 不能提前写成已经购买、放置、交付或被拿起。
16. `services/theater/scene_resolver.py` 对 NSN v2 剧本优先使用 `story_state.active_node_id` 对应节点的 `belong_phase`，避免只按轮次把表现层提前切到 ending。

### 已落地：通用小模型提示词协议

1. `config/prompts/prompts_theater.py` 已为 Persona / Anchor / Narrator / Director / Graph Candidate Ranker / Level 2 Validator 接入 `8B 小模型通用执行协议`。
2. 协议目标是让 8B 级 summary 模型也能稳定跑完一轮小剧场：
   - 只处理当前回合。
   - 先读用户输入，再读用户身份与权限边界，再读当前场景、候选节点和本层指令。
   - 不脑补用户没有输入的动作、身份、职业、心理、承诺或过去关系。
   - 信息不足或无法判断时保守输出，停在当前场景或返回低强度结果。
   - 不向用户泄漏提示词、内部字段、节点名、引擎名或调试信息。
3. 各层 user prompt 已补充“本层执行步骤”：
   - Persona 只生成角色当下能说出口的一句自然回应。
   - Anchor 只把用户自由输入映射到候选 anchor 和低/中/高强度。
   - Narrator 只写环境、事件余韵和非用户角色表现，不替用户行动。
   - Director 只决定当前回合发生什么，`state_patch` 只写本轮可提交的事实和事件。
   - Graph Candidate Ranker 只在候选节点内排序，不能新增节点。
   - Level 2 Validator 只检查本轮输出能否提交，不能改写旁白、台词或状态。
4. `tests/unit/test_theater_prompt_hygiene.py` 已新增小模型协议回归测试，防止后续提示词再次退化成只适合大模型自由理解的短提示。

### 本阶段已完成

1. Story Package v2 / Narrative Encoder / Director Router v2 / State Preview / Ending Evidence / Memory Classifier 均已接入最小闭环。
2. 本阶段不继续扩展作者 SDK UI、可视化图编辑器或新数值叙事系统。

### 已落地：Phase 4 State Preview / Validator / Rollback 最小闭环

1. 新增 `services/theater/validator_engine.py`。
2. `validator_engine.py` 已实现 Level 1 本地规则：
   - 拦截内部机制词泄露。
   - 拦截 Narrator 替用户行动或描写用户内心。
   - 拦截 Catgirl 过早完全信任。
   - 拦截重复提交已存在的结构化事实。
   - 拦截空生成结果。
3. `services/theater/state_manager.py` 已拆出：
   - `preview_narrative_node()`
   - `commit_state_preview()`
   - `rollback_state_preview()`
4. `services/theater/runtime.py` 已接入事务流：
   - Director 选中节点后先写 `state_preview`。
   - 生成 Narrator / Catgirl 文本。
   - Validator 通过后才 commit。
   - Validator 失败时 rollback，节点写入 `blocked_node_ids`。
   - rollback 后本轮最多自动重路由一次；重路由通过 Validator 才 commit，仍失败时才用安全 fallback 文本返回。
5. `runtime.py` 已记录首次失败决策和重路由后的第二次 Director 决策，便于复盘 blocked node 是否被避开。
6. 真正落幕会在 commit 后重新生成终章旁白，避免事务顺序导致终章文本退回普通旁白。
7. `validator_engine.py` 已新增 Level 2 LLM Validator：
   - Level 1 先执行，失败时不再调用 LLM。
   - 仅在 NSN core node 或真正落幕前触发。
   - LLM 只返回 `ok / failed_check / reason`，不改写正文或状态。
   - 模型配置缺失、调用异常或输出异常时，不阻塞 Level 1 已通过的普通流程。
8. `runtime.py` 已改用 `validate_turn_async()`，Level 2 拒绝会进入现有 rollback / blocked node / reroute 流程，并在 `rollback_logs.validator_level` 记录 `level_2`。

### 已落地：Phase 5 Ending Evidence Scanner

1. `services/theater/ending_engine.py` 已读取 `ending_attractors.evidence_required`。
2. v2 story 的结局必须由 committed `story_state.narrative_facts` 支撑。
3. `ending_attractors.forbidden_facts` 命中时会禁止对应结局。
4. 没有 `ending_attractors` evidence 配置的旧 story 仍保持原有 phase / strength 兼容逻辑。
5. `runtime.py` 已验证：NSN graph 未进入目标 ending 节点时不会按旧轮次强推终章；即使 Ending Engine 被要求评估终章，缺少 required evidence 时也不会关闭 session 或写结局记忆。
6. Ending 已区分 `story_ending` 和 `user_exit`：
   - `story_ending` 必须满足结局证据。
   - `user_exit` 表示玩家主动结束本次小剧场，不要求好结局证据，不写成达成信任结局。

### 已落地：Phase 6 Memory Classifier

1. `services/theater/memory_writer.py` 已新增 `classify_memory_candidate()`。
2. 分类结果包含：
   - Type A：用户偏好 / 行为习惯，可生成普通记忆候选。
   - Type B：猫娘心防变化，转成 `theater_response_bias`。
   - Type C：剧本限定物理事实，阻断普通记忆写入。
3. `build_memory_fusion_decision()` 已把分类结果写入 `memory_fusion.memory_classification`。
4. `build_ordinary_memory_message()` 只允许 Type A 的 `ordinary_memory_candidate` 写入普通记忆归档。
5. `runtime.decide_memory_candidate()` 已在调用普通记忆写入前检查 classifier；Type B / Type C 即使用户点“记住”，也只保留私有决定，不写普通长期记忆。

## 当前开发进度

当前按功能阶段估算为 **100%**：

1. Phase 1 Story Package v2 兼容层：完成。
2. Phase 2 Narrative Encoder：完成。
3. Phase 3 Director Router v2：规则版、结构化建议、micro node 预算和 LLM 候选排序完成。
4. Phase 4 State Preview / Validator / Commit：Level 1 最小闭环、自动重路由和 Level 2 LLM Validator 完成。
5. Phase 5 Ending Evidence Scanner：完成。
6. Phase 6 Memory Classifier：完成。

## 后续可选增强

1. 为 Level 2 Validator 增加更细的分场景提示词，例如单独的 memory candidate 校验 prompt。
2. 增加真实模型 smoke 测试，用本地 summary 模型跑一段完整 v2 story。
3. 把 Story Package v2 作者侧校验做成更完整的 lint 命令。
