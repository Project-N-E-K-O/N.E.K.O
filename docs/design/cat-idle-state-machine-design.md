# 猫咪 idle 状态机设计

> 本文是猫咪 idle 状态机的目标设计入口，不代表当前已经实现。当前已落地行为以 `cat-idle-states-feature.md` 和当前代码为准。旧版 `cat-idle-state-machine.md` 只作为流程参考，不作为后续实施边界。

## 1. 设计目标

猫咪形态后续要从“按随机 timer 播动作”变成“根据内部状态、用户交互和桌面上下文产生行为”。

目标不是把所有现有动作都接管，而是让猫有一层自己的内部状态：

1. 能感知用户和窗口发生了什么。
2. 能把这些事实转成食欲、困意、精力、互动需求、刺激需求等内部倾向。
3. 能在合法场景下主动选择适合的自主表现。
4. 能在变回猫娘前压缩成经历摘要，让猫娘回来时带着刚才猫形态的记忆。

一句话边界：

```text
用户或现有直接交互/表现流程触发的事情 = observation
猫自己根据状态想做的表现 = state-machine action
```

## 2. 核心原则

1. **不替代 goodbye / return 主链路。**
   手动离开、自动 idle、点击回来、模型转场、资源暂停和恢复仍由现有流程负责。

2. **不接管用户直接交互。**
   拖拽、hover、点击气泡、点击回来、拖拽导致 tier 降级，都只作为 observation 回写状态。

3. **不把现有表现流程强行改成状态机动作。**
   CAT1 走向聊天球、compact top edge / mirror、chat idle-dock 等已经由当前表现逻辑触发的流程，不进入主动动作候选。

4. **状态机只调度猫自己的自主表现。**
   第一版聚焦轻声表达、吃东西、玩毛线、睡眠反馈等可被 provider 拆分、可失败、可跳过的动作。

5. **接管前先统一封口。**
   一旦确定某批动作要交给状态机，就先删除或封口该批动作的旧随机、timer、概率和点击派生直触发调度。旧入口不得作为回退或第二个调度源；如仍保留时间信号，只能上报 observation 或异步唤醒 scheduler。封口完成后，只有 selector 能提出 action request，只有 adapter 能调用 runner。

6. **触发频率不写死。**
   动作不是固定低频或高频；频率由用户互动强度、内部状态、打扰成本、cooldown 和 provider 能力共同决定。

7. **桌面端克制。**
   不抢焦点、不遮挡、不越忽略越吵。用户正在操作或窗口链路正在变化时，状态机应该安静退让。

8. **先语义，后数值。**
   第一版保留 Cat Mind 五维语义，但不提前写死复杂初始值、每分钟变化量、公式和阈值。权重通过真实运行日志和观感再调。

## 3. 总体模型

状态机由三层组成：

| 层 | 职责 | 边界 |
|---|---|---|
| Cat Mind | 保存五维状态、最近经历、动作结果 | 不操作 DOM，不启动动作 |
| Decision Runtime | 处理 hard gate、tier gate、provider gate、utility selector | 只输出建议动作或 quiet |
| Action Adapter | 调用拆分后的表现 runner，回报 done / failed / interrupted | 不改 goodbye / return / tier 规则 |

这不是严格线性流水线。实际代码可以在任意早期判断失败时直接返回 `quiet`，例如拖拽中、return pending、转场中、已有独立动作运行中。

## 4. Cat Mind

第一版保留五个语义字段：

| 字段 | 含义 | 主要影响 |
|---|---|---|
| `appetite` | 想吃东西或被零食吸引的倾向 | 推动 `cat1_eat_snack` |
| `sleepiness` | 想睡、想打盹、被消耗后的疲惫倾向 | 推动 CAT2/CAT3 睡眠反馈，压低活跃表现 |
| `energy` | 当前能否支撑活跃动作 | 支撑玩毛线、小范围移动等表现 |
| `social_need` | 想表达、回应用户、贴近用户/窗口的倾向 | 推动猫形态内的轻声表达，也轻微推动 CAT1 活动 |
| `stimulation_need` | 无聊、想动、想探索或找事做的倾向 | 推动玩毛线、小范围探索、窗口相关表现 |

这些字段只在猫形态期间完整存在。return 后清理运行态，只保留经历摘要进入问候链路。

第一版不要求精确数值模型，只要求：

1. 每个字段有可调试输出。
2. observation 能按语义影响字段。
3. 动作完成或打断后能回写字段。
4. selector 可以读取字段做偏好判断。

## 5. Observation 输入

Observation 是状态机的“知觉”。它只描述事实，不直接播放动作。

桌面聊天窗口的周期性 `poll` 只用于确认桥接仍存活。normalizer 必须按最小化状态和窗口几何去重：首次观察到、状态改变或几何确实改变才生成窗口 observation；来自原生 IPC、BroadcastChannel 或本地 UI 的相同状态/几何都必须合并，不能更新五维、recent events 或创造新的 selector 判断机会。`idle-dock-enter` 是独立事实：同一次落位只记录一次，离开后再次进入才可重新记录。

### 5.1 时间类

| 事件 | 含义 |
|---|---|
| `cat_elapsed` | 猫形态持续时间变化 |
| `inactive_elapsed` | 用户无互动时长 |
| `since_last_action` | 距离上一次自主动作的时间 |

### 5.2 用户交互类

| 事件 | 含义 | 处理方式 |
|---|---|---|
| `drag_start` / `drag_end` | 用户拖动 return ball | 拖拽中 hard gate；结束后回写互动和消耗 |
| `rapid_drag` | 快速甩动 | 强互动经历，增加被打扰/回应倾向 |
| `cat_hover_reaction` | hover / click 态 GIF 被触发 | 轻互动 observation |
| `thought_bubble_pop` | 气泡被点爆 | 只回写互动，不再直接触发吃东西 |
| `return_click` | 用户点击回来 | return 前生成记忆摘要 |

### 5.3 窗口和桌面类

| 事件 | 含义 |
|---|---|
| `chat_minimized_visible` | 聊天球可见 |
| `chat_minimized_moved_far` | 聊天球移动较远 |
| `chat_compact_surface_visible` | compact surface 可见 |
| `chat_expanded` | chat 展开 |
| `chat_idle_docked_near_cat` | CAT2/CAT3 idle-dock 把 chat 放到猫旁边 |
| `desktop_occlusion_or_layer_change` | 桌面层级或遮挡状态变化 |

### 5.4 现有表现流程结果

这些不是状态机主动调度的动作，只回写结果：

| 事件 | 来源 |
|---|---|
| `cat1_walk_done_near_chat` | CAT1 走到聊天球附近 |
| `cat1_stretch_done_near_chat` | CAT1 走完后的伸展 |
| `cat1_compact_top_edge_done` | compact top edge / mirror settled |
| `cat1_compact_top_edge_drop` | compact top edge drop |
| `edge_peek_after_drag` | 拖拽到边缘后的半藏 |
| `tier_changed` | CAT1 / CAT2 / CAT3 自动变化 |
| `tier_demoted_by_drag` | 拖拽导致 tier 降级 |

### 5.5 自主动作结果

| 事件 | 含义 |
|---|---|
| `social_ping_done` / `social_ping_failed` | 轻声表达完成或失败 |
| `small_move_done` / `small_move_cancelled` | 已落位猫—球小幅移动完成或取消 |
| `eat_done` / `eat_cancelled` | 吃东西完成或取消 |
| `play_done` / `play_cancelled` | 玩毛线完成或取消 |
| `sleep_feedback_done` / `sleep_feedback_failed` | 睡眠反馈完成或失败 |
| `action_interrupted_by_drag` | 动作被拖拽打断 |
| `action_interrupted_by_return` | 动作被 return 打断 |
| `action_interrupted_by_tier_change` | 动作被 tier 变化打断 |

## 6. 判定层次

状态机判断按层次拆开，每层只回答自己的问题。

| 层次 | 回答的问题 | 失败结果 |
|---|---|---|
| Observation normalizer | 这个输入是否可信、是否需要去重/合并？ | 丢弃或只记录日志 |
| Cat Mind reducer | 这个事实如何影响五维状态和最近经历？ | 不触发动作 |
| Runtime hard gate | 当前是否处于 return、drag、transition、active action 等硬锁？ | `quiet` |
| Tier gate | 当前 CAT tier 是否允许该类动作？ | 候选动作移除 |
| Provider gate | 当前表现能力是否可用？ | 候选动作移除或失败回报 |
| Utility selector | 合法候选里哪个最适合？ | `quiet` / `stay_idle` |
| Runner / adapter | 如何调用现有表现能力并回报结果？ | `failed` / `interrupted` |

### 6.1 Hard Gate

以下条件出现时，状态机不主动播放动作：

1. `returnPending`
2. `dragPending` / `dragging`
3. `transitionActive`
4. `activeIndependentAction`
5. 当前 return ball 不可见
6. 当前角色或页面不属于猫形态可运行范围

上游 CAT1 playground drop 是用户主动进入的独立长生命周期；其 entry、state、pointer 或 yarn 事件不成为 Cat Mind 的 observation、candidate、score 或 result。Cat Mind 只依赖上游既有 `activeIndependentAction` 硬锁保持 `quiet`。

### 6.2 Tier Gate

| 动作 | CAT1 | CAT2 | CAT3 |
|---|---|---|---|
| `cat1_social_ping` | 允许 | 禁止 | 禁止 |
| `cat1_eat_snack` | 允许 | 禁止 | 禁止 |
| `cat1_play_yarn` | 允许 | 禁止 | 禁止 |
| `cat1_small_move` | 条件允许 | 禁止 | 禁止 |
| `cat2_nap_feedback` | 禁止 | 允许 | 禁止 |
| `cat3_sleep_feedback` | 禁止 | 禁止 | 允许 |
| `quiet` / `stay_idle` | 允许 | 允许 | 允许 |

被禁止的动作直接移除，不靠低分保留。

### 6.3 Provider Gate

Provider 负责确认表现能力真的可用。

| 动作 | Provider 必须确认 |
|---|---|
| `cat1_social_ping` | CAT1；无拖拽；无 active action；声音或气泡能力可用；当前不会干扰 compact mirror |
| `cat1_eat_snack` | CAT1；吃东西 runner 已拆出；无 play/eat active；可见；无 return / drag |
| `cat1_play_yarn` | CAT1；near chat；yarn 可隐藏/恢复；无 chat transition 冲突 |
| `cat1_small_move` | CAT1；已 settled near chat；有安全移动空间；不在 compact top edge 强跟随 |
| `cat2_nap_feedback` | CAT2；睡眠声音或 ZZZ 能力可用；无拖拽 |
| `cat3_sleep_feedback` | CAT3；睡眠声音或 ZZZ 能力可用；无拖拽 |

Provider 不能为了满足动作而主动拉起走向聊天球、compact top edge、chat idle-dock 或 tier 降级。

### 6.4 Utility Selector

Selector 使用“合法候选 + 偏好打分”，但第一版不写死公式。

打分方向：

1. 主驱动是否强：食欲、困意、刺激需求、互动需求。
2. 精力是否足够。
3. 用户刚刚是否有互动。
4. 当前窗口/桌面上下文是否适合表现。
5. 是否刚做过同类动作。
6. 是否刚被打断。
7. 当前动作的打扰成本是否过高。

Selector 的输出是建议，不是命令。Runner 启动失败时，只回报失败，不用强行补播另一个动作。

## 7. 自主动作池

### 7.1 第一版建议接入

| 动作 | 目标 | 当前状态 | 拆分要求 |
|---|---|---|---|
| `cat1_social_ping` | CAT1 轻声表达 / 普通气泡 | 目前与 CAT1 ambient sound timer、thought bubble、compact mirror reaction 耦合 | 拆出可请求一次的 provider；成功/失败可观测 |
| `cat1_eat_snack` | CAT1 独立吃东西 | 当前入口耦合在 thought bubble click 后 | 拆成独立 runner；气泡点击只做 observation |
| `cat1_small_move` | CAT1 与聊天球的小幅同行移动 | 复用现有 pair move；已完成 provider / lifecycle 证明 | 只在 settled near chat 且两侧几何间距、移动空间均安全时允许；不得主动 walk-to-chat |
| `cat1_play_yarn` | CAT1 玩毛线 | 当前 runner 已有；另有 journey 内部的既有概率表现分支 | 自主候选由状态和 provider 决定；既有 journey 分支不接入 selector |
| `cat2_nap_feedback` | CAT2 打盹反馈 | 当前由 sleep sound timer 触发 | 改由 sleepiness、互动上下文和 cooldown 决定 |
| `cat3_sleep_feedback` | CAT3 熟睡反馈 | 当前由 sleep sound timer 触发 | 基础倾向更克制，但仍由状态和互动上下文决定 |
| `quiet` / `stay_idle` | 不主动表现 | 当前默认待机 | 作为正常输出 |

### 7.2 已完成的接入证明

`cat1_small_move` 曾因 CAT1 journey、pair move、chat bounds 与既有桌面窗口联动而暂缓。现已在网页 renderer 收口为：状态机只选择动作；provider 只读确认已 near chat、无 journey / hover / drag / compact lock 且有安全空间；adapter 复用既有 pair move。它不在 NEKO-PC 保存 Cat Mind 或选择动作。

### 7.3 不作为主动动作

| 内容 | 状态机角色 |
|---|---|
| goodbye / return | 初始化、摘要、清理 |
| tier change / tier demotion | observation |
| drag / rapid drag / edge peek | observation + hard gate |
| walk-to-chat / stretch | observation |
| compact top edge / mirror | observation |
| chat minimized / compact / expanded / idle-dock | observation |
| NEKO-PC 原生拖拽 / 窗口 setBounds / 层级维护 | 基础设施 |

## 8. Provider 拆分目标

### 8.1 `cat1_social_ping`

自主候选的当前事实：

```text
CAT1 ambient sound timer
  -> play random cat1 voice
  -> show thought bubble for sound
  -> compact top edge 时同步 mirror reaction
```

目标：

```text
state machine requests cat1_social_ping
  -> provider checks capability
  -> runner plays one social expression
  -> optional bubble / mirror reaction follows actual success
  -> done / failed updates Cat Mind
```

注意：

1. 当前没有独立“普通气泡”动作，不能假设已有。
2. compact mirror reaction 是场景联动，不是独立动作。
3. 声音失败、气泡失败、mirror 不可用都要能观测。

### 8.2 `cat1_eat_snack`

当前事实：

```text
thought bubble click
  -> pop bubble
  -> _playNekoIdleCat1EatAction()
```

目标：

```text
thought bubble click
  -> observation: thought_bubble_pop

state machine requests cat1_eat_snack
  -> selector chooses eat by appetite and context
  -> provider checks CAT1 / no active action / runner capability
  -> runner plays eat action
  -> eat_done / eat_cancelled updates Cat Mind
```

注意：

1. 气泡点击不再直接调用吃东西。
2. 吃东西启动成功后才写该动作 cooldown。
3. 被拖拽或 return 打断时，只回写 interrupted，不补播。

### 8.3 `cat1_play_yarn`

当前事实：

```text
pair move 的旧概率分支
  -> 已封口，不再直接尝试 play action
```

目标：

```text
state machine requests cat1_play_yarn
  -> provider checks CAT1 / near chat / yarn visibility / no conflict
  -> runner plays yarn action
  -> play_done / play_cancelled updates Cat Mind
```

注意：

1. 状态机不能为了玩毛线主动启动 walk-to-chat。
2. near-chat 是 provider 硬条件。
3. 玩毛线不能破坏 chat 主链路恢复。
4. 例外：猫走到毛线球边后的既有 journey 表现，仍按固定 25% 播放玩球、否则伸展；它只是该表现链内部的二选一，不是 `cat1_play_yarn` 自主候选，不发 action request/result，也不写 cooldown 或五维。walk finish 只额外上报到球边这一 observation。

### 8.4 `cat2_nap_feedback` / `cat3_sleep_feedback`

当前事实：

```text
CAT2/CAT3 sleep sound timer
  -> play sleep sound
  -> show sleeping ZZZ bubble
```

目标：

```text
state machine requests sleep feedback
  -> provider checks tier / audio or ZZZ capability / no drag
  -> runner plays one sleep feedback
  -> sleep_feedback_done / failed updates Cat Mind
```

注意：

1. CAT3 基础表现更克制，但不是固定低频。
2. 不允许主动位移。
3. 用户拖拽打断时只记录 observation。

## 9. return 会话摘要

return 前生成的是一条**最后、可信、可自然表达的会话经历**，不是 recent events 的截断列表，也不是 return 瞬间五维的快照。

猫娘只消费这一份一次性结构化投影；五维继续只属于猫形态内的调度与调试，不跨 return 解释成“想被关注”“被冷落”等语言。

示例：

```json
{
  "duration_seconds": 1260,
  "entry": "auto",
  "final_tier": "cat1",
  "episode": {
    "kind": "rest_after_activity",
    "highlight": "played_yarn"
  }
}
```

内部只维护有界的两个归并段，进入猫形态时创建、return 后立即清除：

1. 当前活动段：是否有明确用户互动，以及本段中严格完成的 CAT1 自主动作种类。
2. 最近休息段：是否是活动后发生的可信休息，以及活动段唯一可点名的动作。

归并和选择规则：

1. 只有 Cat Mind 严格匹配的 `source: cat_mind`、`requestId + runId` 对应的 `done` 才能写入归并：CAT1 动作写活动段，CAT2/CAT3 sleep feedback 写休息段；失败、取消、打断、旧 journey 内 25% 玩球都不写入。
2. drag start/end/cancel、rapid drag、hover、气泡只标记当前活动段“发生过明确互动”，不把拖拽、次数或原始操作直接说给用户。互动只形成章节边界，不产生独立可说类型，也不表示用户造成或共同完成了动作。
3. 只有严格完成的 CAT2/CAT3 sleep feedback 表现才形成 `lastRest`；它不是对真实休息时长的断言。瞬时 tier change、最终 tier、拖拽降级、窗口变化不能单独被说成“睡过”或“被弄醒”。
4. sleep feedback 到来时，当前活动段有成功 CAT1 动作则建立新的“活动后休息”；当前段只有互动则建立新的 `rested`；只有当前段完全为空时，CAT2 后 CAT3 才延续同一休息段。这样不会复用旧章节的活动前置关系。
5. return 时，若当前活动段已有成功动作，优先投影 `activity`；若只有休息后的新互动，则不产生 `episode`，不能复用旧休息；否则才投影最近休息段。窗口或噪声始终不产生 `episode`。
6. 同段重复同类动作合并；同段混合多种动作时不假装某一项最重要，省略 `highlight`，只允许 prompt 概括为“活动了一会”。
7. `episode.kind` 只允许 `rest_after_activity`、`rested`、`activity`；`highlight` 是可选的固定动作 enum，且 `rested` 不允许 highlight。没有可信段时省略整个 `episode`。

这样，`活动 → CAT2 → CAT3 → 拖拽降 CAT1 → return` 仍可可信地概括为“活动后休息”；而 `活动 → 休息 → 新活动 → 休息 → return` 只保留最后一个自然段，避免很早的行为污染最后一句。

摘要原则：

1. 少于现有静默阈值时仍可不触发问候。
2. 不传次数、分数、坐标、窗口状态、原始事件、开放文本或完整时间线。
3. 不责备用户，不根据互动或五维推断用户意图、缺席原因或关系结论。
4. 问候失败不能影响模型恢复；摘要不进入长期记忆或下一轮猫形态。
5. 有可信 `episode` 时，它决定问候中“刚才作为猫经历了什么”的实际经过；既有 tier × 时长 × entry 只提供等待、被叫回来和回归的语气/措辞。可以自然同时提到等待，但不得遗漏、弱化或用“全程只有等待 / 什么也没做”替代 `episode`。无 `episode` 时，完全沿用既有 tier × 时长 × entry 的等待问候。
6. 猫形态 return 是封闭问候链：不得注入普通主动问候的时段、餐食、节日、话题或其它跨功能提示；它只消费本次 return 的入口、时长、tier 和可选 episode。

当前实施范围：

1. Live2D / VRM / MMD return 已把这条 episode 单次附着到既有网页 `cat_greeting_check`；时长、tier 和入口仍以本次 return 的 canonical 顶层值为准，后端只消费 allowlist enum。
2. 短时静默、无 socket 或发送失败时，draft 仍属于本次 return，不会缓存给下一次；Cat Mind 缺失或读取异常时保留原问候 payload。临时 episode 场景只进入本次 `prompt_ephemeral` instruction，不进入长期 memory。
3. PNGTuber 仍不接入经历问候：保留原 return observation / `return-summary` 事件，但不保存 draft，避免无人消费的内容跨 return 残留。
4. NEKO-PC 不保存、选择、解释或发送 episode；桌面端只继续提供既有 observation 与窗口安全能力。

## 10. 实施顺序

第一阶段：只建立感知和状态，不改动作触发。

1. 增加 Cat Mind 运行态。
2. 接入 observation normalizer。
3. 记录五维调试输出和 recent events。
4. return 前生成摘要，但先不改变 prompt。

第二阶段：拆 provider，不启用 selector 接管。

1. 拆 `cat1_social_ping` provider。
2. 拆 `cat1_eat_snack` 独立 runner。
3. 包装 `cat1_play_yarn` runner。
4. 包装 CAT2/CAT3 sleep feedback runner。

第三阶段：启用 selector 接管第一批动作。

1. 确定第一批接管动作范围。
2. 删除或统一封口该范围内的旧随机 timer / 概率 / 点击派生直触发入口。
3. 将封口后的入口改为 observation 或 scheduler wakeup。
4. 启用 hard gate / tier gate / provider gate。
5. 启用 utility selector，让完整动作池按 gate 和评分产生候选。
6. 运行日志观察触发频率、打扰成本和动作来源。

第四阶段：接入 return 记忆问候。

1. 已将本地 raw summary draft 替换为有界的会话经历归并；严格动作完成和既有用户 interaction observation 是唯一落账入口。
2. 已将一次性 `episode` 投影传入既有 cat greeting 逻辑，并原子消费。
3. 后端只消费白名单 enum；有 episode 时 prompt 以这一个自然段作为猫形态经过的事实场景，tier × 时长 × entry 只调回归语气；无 episode 时保留原等待问候。
4. 已保留失败静默，不影响 return；PNGTuber 仍明确不接入经历问候。

`cat1_small_move` 已在完成 provider、生命周期和几何边界验证后作为 CAT1 扩展动作接入；后续只基于真实调试日志调整节奏，不恢复旧随机直跑。

## 11. 完成标准

目标状态完成后，应能观察到：

1. 猫形态期间有可调试的 Cat Mind 五维状态。
2. 用户拖拽、hover、气泡点击、窗口变化、tier 变化都能进入 observation。
3. 第一批动作接管前，旧直接入口已统一封口，不与 selector 并行调度。
4. 气泡点击只改变内部状态，不再直接触发吃东西。
5. 吃东西可以由 `appetite` 独立触发。
6. 自主 `cat1_play_yarn` 由 `stimulation_need`、`energy` 和 provider 决定；走到球边后的既有 25% 玩球 / 否则伸展仅是 journey 内部表现，不参与 selector。
7. CAT2/CAT3 睡眠反馈由 `sleepiness`、用户互动上下文和 cooldown 决定。
8. goodbye / return、窗口拖拽、chat idle-dock、NEKO-PC 原生窗口链路不被状态机接管。
9. return 后，支持的网页 avatar 只单次消费一条有界、可信的 episode；后端不会持久化它。有 episode 时猫娘必须自然表达这段经历（可同时带等待语境），无 episode 时才走原等待问候。PNGTuber 仍不发送或保留经历 draft。
