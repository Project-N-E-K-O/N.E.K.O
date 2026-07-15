# Cat Mind 数值、动作与返回提示词规则

## 一、用途与口径

本文记录当前代码实际使用的精确数值和提示词规则，作为调参、测试和 review 的共同口径。数值变更必须同时更新代码、相关断言和本文。

五维在代码内部保存为 `0–1`，本文统一换算为 `0–100`。所有加减值均经过 `0–100` 截断。动作公式中的变量也使用 `0–100` 显示值。

## 二、初始值与时间流

### 2.1 进入猫形态的初始值

| 进入方式 | 食欲 | 困意 | 精力 | 社交需求 | 刺激需求 |
|---|---:|---:|---:|---:|---:|
| 手动 | 22 | 12 | 75 | 22 | 28 |
| 自动（含启动默认猫形态） | 32 | 22 | 66 | 32 | 42 |

### 2.2 每分钟自然变化

| tier | 食欲 | 困意 | 精力 | 社交需求 | 刺激需求 |
|---|---:|---:|---:|---:|---:|
| CAT1 | +0.55 | +0.45 | -0.45 | +2.40 | +3.20 |
| CAT2 | +0.45 | +1.35 | -0.80 | +1.00 | +0.65 |
| CAT3 | +0.30 | +0.85 | -0.35 | +0.55 | +0.30 |

时钟名义间隔为 30 秒，实际增量为“每分钟变化 × 真实经过分钟数”。后台暂停恢复不会补发大量离散动作判断，但会按真实 elapsed 结算五维。

### 2.3 tier 边界

| tier 变化后 | 强制边界 |
|---|---|
| CAT1 | 精力至少 45 |
| CAT2 | 困意至少 55；精力至多 45 |
| CAT3 | 困意至少 78；精力至多 25 |

## 三、Observation 的五维反馈

### 3.1 用户互动

| 事件 | 食欲 | 困意 | 精力 | 社交需求 | 刺激需求 |
|---|---:|---:|---:|---:|---:|
| `drag_start` | +0.5 | 0 | -0.5 | +1.5 | +2.0 |
| `drag_end` | +0.8 | 0 | -1.0 | +2.5 | +3.0 |
| `drag_cancelled` | 0 | 0 | 0 | +1.0 | +1.0 |
| `rapid_drag` | +2.0 | +2.0 | -2.0 | +3.5 | +4.0 |
| `cat_hover_reaction` | 0 | 0 | -0.3 | +2.0 | +5.0 |
| `thought_bubble_pop` | 0 | 0 | 0 | -6.0 | -4.0 |

hover 和拖拽表示用户正在主动逗猫，会短时提高她想回应、想继续活动的驱动力，因此提高社交/刺激需求，同时消耗精力；真实拖动还小幅增加食欲，rapid drag 的消耗更大并增加困意。需求余量会经过统一有界曲线，短时密集互动可以让多个动作更早进入竞争，却不能靠事件次数无限堆高最终分。点击气泡表示这一次回应已经完成，降低社交和刺激需求。

### 3.2 聊天窗和既有表现

| 事件 | 食欲 | 困意 | 精力 | 社交需求 | 刺激需求 |
|---|---:|---:|---:|---:|---:|
| `chat_minimized_visible` | 0 | 0 | 0 | 0 | 0 |
| `chat_minimized_moved_far` | 0 | 0 | 0 | 0 | 0 |
| `chat_compact_surface_visible` | 0 | 0 | 0 | 0 | 0 |
| `chat_idle_docked_near_cat` | 0 | 0 | 0 | 0 | 0 |
| `chat_expanded` | 0 | 0 | 0 | 0 | 0 |
| `cat1_walk_done_near_chat` | 0 | 0 | -4.0 | +5.0 | -4.0 |
| `cat1_stretch_done_near_chat` | 0 | +2.0 | -3.0 | 0 | -4.0 |
| `cat1_compact_top_edge_done` | 0 | 0 | -3.0 | +4.0 | -6.0 |
| `cat1_compact_top_edge_drop` | 0 | 0 | -2.0 | 0 | +4.0 |
| `edge_peek_after_drag` | 0 | 0 | -2.0 | 0 | +4.0 |

聊天窗形态和几何是 provider、near/far 与安全判断事实，不等同于用户需求，所以不直接改五维；它们仍进入 recent events 并排入下一轮异步判断。相同最小化状态和相同窗口矩形只记一次。聊天球中心移动至少 `24px` 才从后续最小化通知归类为 `chat_minimized_moved_far`，poll/heartbeat 不重复记录。

### 3.3 控制与边界 observation

| 事件 | 直接五维变化 | 作用 |
|---|---:|---|
| `desktop_occlusion_or_layer_change` | 0 | 更新桌面层级/遮挡事实，排入下一轮判断 |
| `return_click` | 0 | 冻结一次性 return 摘要并结束本轮 Cat Mind |
| `tier_changed` | 仅施加 2.3 的 tier 边界 | 切换当前合法动作池 |
| `tier_demoted_by_drag` | 由随后 `tier_changed` 施加边界 | 记录既有拖拽降级表现，不成为主动候选 |

这些事件不创建第六个需求字段，也不直接映射动作。`return_click`、tier 变化与动作结果仍遵守异步边界，不能在旧入口同步启动下一动作。

### 3.4 动作完成和中断

| 结果事件 | 食欲 | 困意 | 精力 | 社交需求 | 刺激需求 |
|---|---:|---:|---:|---:|---:|
| `social_ping_done` | 0 | 0 | -1 | -28 | 0 |
| `small_move_done` | 0 | +1 | -3 | 0 | -14 |
| `eat_done` | -28 | -1 | +8 | 0 | +2 |
| `play_done` | +10 | +5 | -10 | 0 | -28 |
| `sleep_feedback_done` CAT2 | +2 | -30 | +16 | 0 | +2 |
| `sleep_feedback_done` CAT3 | +2 | -38 | +20 | 0 | +2 |
| `action_interrupted_by_drag` | 0 | +2 | -4 | +6 | +6 |
| return/tier change 中断 | 0 | 0 | 0 | 0 | 0 |
| failed/cancelled | 0 | 0 | 0 | 0 | 0 |

只有严格匹配当前 active action 的结果才能结算。`started` 只写 cooldown，不提前写完成反馈。

## 四、动作分数

### 4.1 基础分公式

| 动作 | 基础分 |
|---|---|
| `cat1_social_ping` | `12 + 社交×0.55 + 刺激×0.08 + (100-困意)×0.04` |
| `cat1_small_move` | `10 + 刺激×0.40 + 精力×0.34 + 社交×0.06 - 困意×0.12 - 食欲×0.05` |
| `cat1_play_yarn` | `5 + 刺激×0.48 + 精力×0.36 + 社交×0.12 - 困意×0.18 - 食欲×0.08` |
| `cat1_eat_snack` | `18 + 食欲×0.55 + (100-精力)×0.06 + 困意×0.04 + 社交×0.06 - 刺激×0.05` |
| `cat2_nap_feedback` | `20 + 困意×0.55 + (100-精力)×0.22 - 刺激×0.08 + 食欲×0.03 - 社交×0.04` |
| `cat3_sleep_feedback` | `32 + 困意×0.55 + (100-精力)×0.22 - 刺激×0.08 + 食欲×0.03 - 社交×0.04` |

小移动和玩球把精力作为主要正向驱动。高频交互把精力耗尽后，即使刺激需求很高，这两个分数也应回落，而不是持续循环。

### 4.2 统一评分层

六个动作全部使用同一套最终分结构，不设置气泡硬间隔、不按连续次数禁用动作，也不到时间强制指定动作。

公共响应曲线是归一化三次 Hermite S 曲线：

```text
S(x) = x² × (3 - 2x),  x = clamp(x, 0, 1)
```

它在 `0` 和 `1` 两端斜率为零，输入连续变化时不会在阈值附近突然跳分。固定采样为：

| x | 0 | 0.25 | 0.5 | 0.75 | 1 |
|---:|---:|---:|---:|---:|---:|
| `S(x)` | 0 | 0.15625 | 0.5 | 0.84375 | 1 |

第一步把不同阈值的动作换成可比较的需求余量。负余量保留 `14` 点的平滑决策带；正余量使用更陡的 `4` 点响应带并封顶 `42`，让短时密集互动可以即时抬高多个动作的竞争力，同时保持输入连续：

```text
need_surplus = base_score - threshold

need_contribution = need_surplus                         , need_surplus <= -14
need_contribution = -14 × S(abs(need_surplus) / 14)      , -14 < need_surplus < 0
need_contribution = 42 × S(need_surplus / 4)             , 0 <= need_surplus < 4
need_contribution = 42                                   , need_surplus >= 4
```

正余量最多计入 `+42`，重复 observation 不能把需求贡献无限堆高；明显低于阈值的负余量保留原值，不会被节奏分无条件救起。正向曲线在 `+1 / +2 / +3 / +4` 分别给出 `6.5625 / 21 / 35.4375 / 42`，因此短时互动一旦把真实五维推过动作阈值，回应会明显加快，但没有单独的“高互动模式”。需求曲线采样：

| 原始余量 | -20 | -14 | -10.5 | -7 | -3.5 | 0 | +1 | +2 | +3 | +4 | +12 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 计入余量 | -20 | -14 | -11.8125 | -7 | -2.1875 | 0 | +6.5625 | +21 | +35.4375 | +42 | +42 |

第二步，所有动作共享同一个连续节奏曲线。`t` 是距最近一次 runner 真实 `started` 的分钟数；本轮还没有 started 时，从进入猫形态算起。完整恢复窗为 `4.85` 分钟，即 `291` 秒：

```text
p = clamp(t / 4.85, 0, 1)
cadence = -52 + 70 × S(p)
```

| 距真实 started | 0 | 0.5 分 | 1 分 | 1.5 分 | 2 分 | 2.425 分 | 3 分 | 4 分 | 4.85 分及以后 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 节奏分 | -52 | -49.92 | -44.30 | -36.05 | -26.11 | -17 | -4.78 | +12.30 | +18 |

这是连续评分，不是 3 分钟 gate 或 5 分钟强制触发。当前参数由项目的 30 秒 tick、五维自然流和真实完成反馈共同校准：无/少交互通常约 3.5–4.5 分钟出现一次，高需求可以约 3 分钟进入下一次竞争，需求不足则继续等待。

第三步减去本动作自己的软 cooldown。六个动作使用相同的 `36` 点基准、`2.4` 倍倍率和二次恢复曲线。`p` 是本 cooldown 已经过的比例：

```text
p = clamp(elapsed_ms / full_cooldown_ms, 0, 1)
cooldown_curve = 1 - p²
cooldown_penalty = 36 × 2.4 × cooldown_curve
                 = 86.4 × (1 - p²)
utility = need_contribution + cadence - cooldown_penalty
final_score = threshold + utility
```

二次恢复让重复惩罚在 cooldown 前半段保持明显，后段再逐步释放，比线性扣分更能避免同一动作反复刷屏。它只压低刚执行过的同一动作，其他动作可以正常竞争；需求足够强时同一动作仍可能重复，因此“一两次重复”来自分数而不是白名单。

### 4.3 阈值与软 cooldown 时长

| 动作 | 阈值 | cooldown | started | 已过 25% | 已过 50% | 已过 75% | 到期 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `cat1_social_ping` | 48 | 480 秒 | 86.4 | 81 | 64.8 | 37.8 | 0 |
| `cat1_eat_snack` | 52 | 480 秒 | 86.4 | 81 | 64.8 | 37.8 | 0 |
| `cat1_small_move` | 51 | 480 秒 | 86.4 | 81 | 64.8 | 37.8 | 0 |
| `cat1_play_yarn` | 52 | 600 秒 | 86.4 | 81 | 64.8 | 37.8 | 0 |
| `cat2_nap_feedback` | 54 | 600 秒 | 86.4 | 81 | 64.8 | 37.8 | 0 |
| `cat3_sleep_feedback` | 50 | 720 秒 | 86.4 | 81 | 64.8 | 37.8 | 0 |

所有 cooldown 都只在 runner `started` 后建立，同时重置共享节奏时钟。provider reject、accepted 后启动失败都不写 cooldown，也不重置节奏。

### 4.4 候选顺序

只有通过 hard/tier/provider gate 且 `utility >= 0` 的动作进入候选。候选按 utility 而不是 raw base score 排序，避免阈值较高的动作在跨动作比较时吃亏。CAT1 贴边半隐藏时 `edgePeekActive` 是环境 hard gate，整轮输出 `quiet`；即使状态快照与边缘 class 短暂不同步，provider 也会拒绝 CAT1 动作。utility 相同时按固定顺序：

1. `cat1_social_ping`
2. `cat2_nap_feedback`
3. `cat3_sleep_feedback`
4. `cat1_small_move`
5. `cat1_eat_snack`
6. `cat1_play_yarn`

无候选输出 `stay_idle`；hard gate 或已有 request/action 输出 `quiet`。两者都不是失败。

## 五、交互强度验收包络

固定节奏只用于构造可复现压力测试，不等于真实用户画像。真实使用还包含不规则 hover、拖拽取消、突然高频拖动、窗口变化、near/far 切换、tier 变化和 return，因此不能用单一模拟结果概括产品体验。

当前 CAT1 60 分钟压力测试使用四组输入：

| 场景 | hover | 普通拖拽 | rapid drag | 聊天窗变化/气泡 |
|---|---|---|---|---|
| 从不交互 | 无 | 无 | 无 | 无 |
| 少量交互 | 每 10 分钟 | 每 20 分钟 | 无 | 每 15 分钟一次聊天窗事实 |
| 正常交互 | 每 2 分钟 | 每 6 分钟 | 每 18 分钟 | 每 6 分钟一次，并点击出现的气泡 |
| 短时大量交互 | 前 12 分钟每 0.5 分钟 | 前 12 分钟每 1 分钟 | 前 12 分钟每 2 分钟 | 前 12 分钟每 2 分钟一次，并点击出现的气泡 |
| 短时大量交互、不点气泡 | 同上 | 同上 | 同上 | 前 12 分钟每 2 分钟一次，不发送 pop |

固定 CAT1 是主动动作最多的压力条件。测试同时跑 near-chat 和 far-chat：far-chat 的 `small_move / play_yarn` 必须始终为 0。短时大量交互只在前 12 分钟施压，验证“这段时间回应更密”，而不是把持续一小时的机械输入冒充真实用户。

当前确定性 near-chat 样本：

| 场景 | 60 分钟 started | 前段 started | 动作计数：吃/玩/移动/回应 | 最长同动作连续 |
|---|---:|---|---|---:|
| 从不交互 | 15 | 15 分钟 4 次；前 12 分钟 3 次 | 2 / 3 / 5 / 5 | 1 |
| 少量交互 | 16 | 15 分钟 4 次；前 12 分钟 3 次 | 3 / 4 / 4 / 5 | 1 |
| 正常交互 | 20 | 15 分钟 4 次；前 12 分钟 3 次 | 3 / 5 / 5 / 7 | 1 |
| 短时大量交互 | 20 | 前 12 分钟 6 次 | 4 / 5 / 3 / 8 | 1 |
| 短时大量交互、不点气泡 | 20 | 前 12 分钟 6 次 | 4 / 5 / 3 / 8 | 1 |

前 12 分钟的确定性动作序列：

- 从不/少量交互：`3.5m small_move -> 7m play_yarn -> 10.5m social_ping`；
- 正常交互：`3.5m small_move -> 6m play_yarn -> 9m social_ping`；
- 短时大量交互：`1.5m play_yarn -> 3m social_ping -> 4.5m small_move -> 8m eat_snack -> 10.5m social_ping -> 12m play_yarn`。

这组结果表达的是一条连续响应曲线：无/少交互保持约 3–5 分钟的旧节奏；正常交互从第二次回应开始变快；短时大量交互的首次回应落在 1.5 分钟，且 12 分钟内有 6 次、覆盖全部 4 类 CAT1 动作。高交互不是把每个 observation 映射为动作，回应动作也没有连续刷同一种。点击或不点击气泡在该确定性样本里不改变动作序列，证明重复抑制来自统一评分与本动作 cooldown，而不是要求用户替系统点掉气泡。

60 分钟自动断言：

- 从不交互和少量交互：所有按 30 秒滑动的完整 15 分钟窗口都有 `3–5` 次真实 started；相邻间隔为 `2.5–5` 分钟，平均间隔保持 `3–5` 分钟。`2.5` 来自 30 秒采样边界，不是另设的快速通道；
- 无/少交互首个 15 分钟至少 3 类动作；正常交互至少 3 类；高交互前 12 分钟至少 4 类；
- 所有 profile 的完整序列同动作最多连续 2 次，当前确定性样本实际为 1；这是评分结果验收，不是运行时连续次数禁令；
- 每个 profile 的 `social_ping` 都少于非 social 动作总和。高交互全程回应占 `8/20`，前 12 分钟为 `2/6`，两次回应被其他动作隔开；
- 短时大量交互前 12 分钟的真实 started 数必须高于无、少、正常交互，并在首次用户输入后不超过 90 秒出现首个回应；
- 正常、高交互 60 分钟均不超过 22 次，防止密集 observation 变成线性连发；
- 短时大量交互真实消耗精力，最终精力低于无交互；低精力时 `play_yarn` 最终分低于阈值；
- 所有 profile 在 far-chat 时 `small_move / play_yarn` 始终为 0。

此外还必须单独验证：

1. 从不交互时自然时间流能产生少量动作，不永久静止。
2. provider reject 不产生 cooldown、done、failed、result 或 return episode。
3. accepted 不等于 started；音频播放失败时无 cooldown。
4. done/cancel/drag interrupt/return interrupt/tier interrupt 的五维结果分别正确。
5. tier 变化只开放对应动作，不把 visual/presentation 行为变成候选。

## 六、return summary 规则

### 6.1 前端摘要结构

```json
{
  "duration_seconds": 900,
  "entry": "manual",
  "final_tier": "cat1",
  "has_started_autonomous_action": true,
  "episode": {
    "kind": "activity",
    "highlight": "played_yarn"
  }
}
```

其中：

- `duration_seconds`：本次猫形态真实停留秒数；
- `entry`：`manual` 或 `auto`；
- `final_tier`：`cat1 / cat2 / cat3`；
- `has_started_autonomous_action`：可选，只允许字面量 `true`，仅解除 `<180s` 的投递静默，不是可叙述事实；
- `episode.kind`：`activity / rest_after_activity / rested`；
- `episode.highlight`：仅 `played_yarn / ate_snack / small_move / social_ping`，`rested` 禁止 highlight。

后端丢弃未知字段、开放文本、数组、错误类型、非有限数和非法 kind/highlight 组合。顶层 return 的 duration/tier/was_auto 是 canonical 事实，会覆盖 summary 同名语义。duration 限制在 `0–7 天`。

### 6.2 episode 归并

活动顺序固定为 social、eat、small move、play，仅用于去重与确定是否是单一 highlight，不作为对话中的动作清单。

- 活动段只有一种完成动作：`activity + 对应 highlight`。
- 活动段混合多种完成动作：`activity`，无 highlight。
- 活动后严格完成 CAT2/CAT3 睡眠反馈：`rest_after_activity`，单一活动可保留 highlight。
- 没有活动但严格完成睡眠反馈：`rested`。
- 休息后有新活动：优先新活动。
- 休息后只有新用户互动、没有新完成动作：返回无 episode，不能复用旧休息。
- failed、cancelled、interrupted、window、presentation、legacy journey、tier-only、interaction-only 都不产生 episode。

### 6.3 时长分档

| 行为 | 默认静默线 | 长时分界 |
|---|---:|---:|
| awake / CAT1 | 180 秒 | 900 秒 |
| nap / CAT2 | 180 秒 | 1800 秒 |
| sleep / CAT3 | 180 秒 | 1800 秒 |

`<180s` 且无严格 started：静默。`<180s` 且有 started、无 episode：中性短 return。`<180s` 且有 started 和严格 done episode：无 elapsed 的短 episode wrapper。`>=180s` 时按最终 tier、时长档、entry 和 episode 选择提示词。

## 七、返回提示词

以下列出中文实际文案结构。其他 locale 使用同一枚举、分档和约束，英文作为 fallback。`{master}`、`{elapsed}`、`{reason_hint}`、`{cat_form_scene}`、`{episode_return_tone}` 在服务端格式化；猫形态 return 的 `{time_hint}` 固定为空，不注入早餐、午饭、晚饭、深夜等提示。

### 7.1 进入原因

- 自动：`刚才{master}忙着没顾上你，`
- 手动：`刚才{master}请你去一旁歇着，`

### 7.2 没有 episode 时的六组基础提示

清醒·短：

```text
{reason_hint}你就变成猫咪的样子在旁边待了{elapsed}，一直醒着等{master}。现在{master}把你叫回来了。
你心情轻松，想随口跟{master}打个招呼，可以提一句刚才变成猫咪等着的事。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

清醒·久：

```text
{reason_hint}你就变成猫咪的样子在旁边醒着待了{elapsed}，一直没人理，都快憋坏了。现在{master}总算把你叫回来。
你带着等久了的小情绪，想跟{master}撒娇或抱怨几句一个人待了这么久。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

打盹·短：

```text
{reason_hint}你就变成猫咪的样子眯了{elapsed}，没睡多沉，随便打了个盹。{master}把你叫回来了。
你懒洋洋地伸个懒腰，没什么大不了地跟{master}打个招呼就行。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

打盹·久：

```text
{reason_hint}你就变成猫咪的样子打盹打了{elapsed}，睡得有点迷糊。{master}把你叫醒、叫回来了。
你还有点没睡醒的慵懒，迷迷糊糊地跟{master}打个招呼。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

熟睡·短：

```text
{reason_hint}你就变成猫咪的样子小睡了{elapsed}。{master}把你叫回来，你迷糊一下就醒了。
没什么负担，你睡眼惺忪地跟{master}打个招呼就好。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

熟睡·久：

```text
{reason_hint}你就变成猫咪的样子蜷成一团睡了{elapsed}，睡得很沉。{master}把你叫醒、叫回来了，你刚醒还迷迷糊糊，但有点“终于等到你”的想念。
你带着这份刚睡醒又想念的心情，跟{master}打个招呼。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

### 7.3 episode scene 映射

| kind | highlight | 中文 scene |
|---|---|---|
| `activity` | 无 | 刚才以猫的样子活动了一会儿。 |
| `activity` | `played_yarn` | 刚才以猫的样子自己玩了会儿毛线。 |
| `activity` | `ate_snack` | 刚才以猫的样子自己吃了点零食。 |
| `activity` | `small_move` | 刚才以猫的样子小小活动了一下。 |
| `activity` | `social_ping` | 刚才以猫的样子轻轻回应过。 |
| `rest_after_activity` | 无 | 刚才活动了一会儿，后来安静歇了歇。 |
| `rest_after_activity` | `played_yarn` | 刚才玩了会儿毛线，后来安静歇了歇。 |
| `rest_after_activity` | `ate_snack` | 刚才吃了点零食，后来安静歇了歇。 |
| `rest_after_activity` | `small_move` | 刚才小小活动了一下，后来安静歇了歇。 |
| `rest_after_activity` | `social_ping` | 刚才轻轻回应过，后来安静歇了歇。 |
| `rested` | 无 | 刚才以猫的样子安静歇了歇。 |

### 7.4 episode 的回归语气

| 最终状态 | 时长档 | 中文语气 |
|---|---|---|
| awake | short | 心情可以轻松些，顺着这段经历自然地打个招呼。 |
| awake | long | 这段时间已经有些久了，语气可以带一点软软的撒娇或小情绪。 |
| nap | short | 语气可以放松、轻柔，顺着这段经历自然地打个招呼。 |
| nap | long | 语气可以懒洋洋、放慢一些，顺着这段经历自然地打个招呼。 |
| sleep | short | 语气可以安静柔和，顺着这段经历自然地打个招呼。 |
| sleep | long | 这段时间较久，语气可以柔软、带一点想念，顺着这段经历自然地打个招呼。 |

### 7.5 有 episode 的标准 wrapper

```text
{reason_hint}你变成猫咪待了{elapsed}。刚才作为猫真实经历的是：{cat_form_scene}现在{master}把你叫回来了。
{episode_return_tone}
这段真实经历是本次猫形态经过的唯一事实，回归时必须自然带出它。可以自然提到等待和被叫回来，但不能把刚才说成全程只有等待、什么也没做，或擅自说成打盹、熟睡、刚醒。不要逐项报动作、次数或过程，也不要把它归因于对方。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

### 7.6 `<180s` 且有完成 episode 的 wrapper

```text
{reason_hint}你刚才变成了猫咪。刚才作为猫真实经历的是：{cat_form_scene}现在{master}把你叫回来了。
{episode_return_tone}
这段真实经历是本次猫形态经过的唯一事实，回归时必须自然带出它。可以自然提到回来，但不能把刚才说成全程只有等待、什么也没做，或擅自说成打盹、熟睡、刚醒。不要逐项报动作、次数或过程，也不要把它归因于对方。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

### 7.7 `<180s`、有 started 但无完成 episode 的 wrapper

```text
{reason_hint}你刚才变成了猫咪，现在{master}把你叫回来了。
这次没有可叙述的已完成猫形态经历。只自然回应已经回来；不要猜测或声称刚才全程在等待、什么也没做、打盹、熟睡、刚醒，或任何动作已经完成。不要列举动作、次数或过程，也不要把它归因于对方。
用符合你性格的方式直接说出来，简短自然即可，不要生成思考过程。
```

所有实际模板外层还有环境提示起止标记。有合法 episode 时，episode 是本次猫形态活动事实的唯一来源，tier 和时长只决定回归语气，不能覆盖 episode 或再声称另一段未经完成动作证明的经历。没有 episode 且停留达到 `180s` 时，保留原有按最终 visual tier 生成的清醒/打盹/熟睡基础提示；这是既有视觉层基线，不等同于严格 `sleep_feedback_done` 事实。scene 不能解释成“和用户一起完成”，不能推演用户意图、缺席原因或关系结论。

## 八、全链路验收

至少覆盖下列 return 场景：

1. 大量交互并完成多个动作：只输出最后一个可信自然段；混合动作不伪造单一 highlight。
2. 正常或少量交互并完成单一动作：输出相应 activity/highlight。
3. 只有交互、没有严格完成动作：无 episode；达到 180 秒时走基础 tier 提示，短时无 started 静默。
4. 从不交互、无动作完成：不凭时间流虚构经历。
5. 活动后严格休息：输出 `rest_after_activity`，保持先后顺序。
6. 单独严格休息：输出 `rested`，不声称睡眠深度和时长。
7. failed/cancelled/interrupted/provider reject：不进入 episode。
8. `<180s` started 后立即 return：允许一次中性回归；只有 done 才能说具体经历。
9. 摘要消费一次；无 socket、发送失败、重复 return 不泄漏到下一轮。
10. 后端 instruction 不含 raw JSON、动作 ID、次数、坐标、分数、五维或未知文本。
11. cat greeting 的 instruction 不调用普通时间提示，不混入午饭、晚饭、深夜等问候。
12. voice、takeover、busy、用户抢占和 session 启动失败继续保持原静默/中止语义，不为了说经历强行发送。
