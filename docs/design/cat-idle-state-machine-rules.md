# Cat Mind 数值、动作与返回提示词规则

## 一、用途与口径

本文记录当前代码实际使用的精确数值和提示词规则，作为调参、测试和 review 的共同口径。数值变更必须同时更新代码、相关断言和本文。

本规则由 Web 主页面和 NEKO-PC 的 Pet renderer 页面内同一套 Cat Mind 执行。NEKO-PC 桌面壳只提供 observation、跨窗口坐标和窗口安全事实；它不持有五维、短时意图、cooldown、pending/active action 或 episode，不运行 selector，也不发 action request。因此下列数值、provider、生命周期和摘要规则在两端完全共用，不存在桌面专用评分分支。

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

### 2.3 社交与刺激 overflow 回落

自然时间流仍按 2.2 增长。只有社交需求或刺激需求高于当前 tier 的舒适带时，超出部分才按半衰期泄漏；舒适带以下不做均值回归，保留原来的自然增长：

```text
pre = current + rate_per_minute × elapsed_minutes
next = comfort + (pre - comfort) × 0.5^(elapsed_minutes / half_life),  pre > comfort
next = pre,                                                           pre <= comfort
```

| tier | 社交舒适带 | 社交半衰期 | 刺激舒适带 | 刺激半衰期 |
|---|---:|---:|---:|---:|
| CAT1 | 62 | 4 分钟 | 70 | 3 分钟 |
| CAT2 | 48 | 5 分钟 | 42 | 5 分钟 |
| CAT3 | 36 | 6 分钟 | 30 | 6 分钟 |

这条泄漏只处理密集交互留下的高位 overflow，使短时高频回应集中在互动附近；它不是 interaction mode，也不把无/少交互的普通需求强行拉回某个固定值。

### 2.4 tier 边界

| tier 变化后 | 强制边界 |
|---|---|
| CAT1 | 精力至少 45 |
| CAT2 | 困意至少 55；精力至多 45 |
| CAT3 | 困意至少 78；精力至多 25 |

## 三、Observation 的五维反馈

### 3.1 用户互动

社交/刺激的正向互动不是直接加法，而是把表中的 dose 通过同一有界合并式写入当前值。公式内部使用 `0–1`，下表仍以 `0–100` 展示 dose：

```text
next = current + dose × (1 - current) × (1 + 0.8 × current)
```

| 事件/手势阶段 | 食欲 | 困意 | 精力 | 社交 dose | 刺激 dose |
|---|---:|---:|---:|---:|---:|
| `cat_hover_reaction` | 0 | 0 | -0.1 | 0.6 | 1.8 |
| `drag_start` | 0 | 0 | 0 | 1.0 | 3.5 |
| 普通 `drag_end` | 见 3.2 | 见 3.2 | 见 3.2 | 2.5 | 18.0 |
| `drag_cancelled` | 见 3.2 | 见 3.2 | 见 3.2 | 1.2 | 1.6 |
| 本手势首次 `rapid_drag` | 0 | 0 | 0 | 8.0 | 40.0 |
| rapid 手势的 `drag_end` | 见 3.2 | 见 3.2 | 见 3.2 | 3.0 | 12.0 |

一段拖拽最多结算一次 start、一次 rapid immediate 和一次 terminal。出现 rapid 后，terminal 使用 `3/12`，不再叠加普通结束的 `2.5/18`；重复 rapid 帧不重复计分，同一 `activityId` 的重复/别名 terminal 也不重复结算社交、刺激或物理负荷。未结算的手势状态超过 `10s` 后，收到下一条手势事实时会先重建，防止遗留状态把两次真实手势合成一次。实际位移产生的食欲、精力和困意只由 3.2 的终态物理事实结算。

点击活动气泡不是正向证据，而是按当前剩余需求满足一部分：

```text
social_need      *= 0.72   # 满足 28%
stimulation_need *= 0.82   # 满足 18%
```

普通 hover、拖拽和 rapid drag 都只修改五维，不直接指定 social、eat、move 或 play，也不创建 interaction level。hover 很容易因指针经过而频繁触发，所以只作为低权重背景证据；完整拖拽和 rapid 才提供更明确的活动反馈与物理负荷。合并式和 2.3 的 overflow 泄漏共同形成“真实高频输入越多、短时回应越明显，但不会随 move 帧线性爆发”的连续曲线。

### 3.2 真实物理活动

return-ball 拖拽、CAT1 既有 walk、compact 上缘落位和自主 small move 只在结束或取消等终态携带物理事实：非空且稳定的 `activityId`、实际 `pathDistancePx` 和 `durationMs`。同一个 `activityId` 只结算一次；start、move、rapid 等中间帧只表达交互阶段，不重复计算路程。没有稳定 `activityId` 或没有有效正路径时都不产生物理负荷，避免重复别名被多次收费。

```text
d = clamp(pathDistancePx / 160, 0, 1)
t = clamp(durationMs / 2200, 0, 1)
L = S(0.8d + 0.2 × min(d, t))

食欲 += 4L
精力 -= 4.5L
困意 += 2L
```

`durationMs` 不可用时先读取 small move 的 `plannedDurationMs`，仍不可用则令 `t=d`；距离候选按实际 path、distance、moved distance、displacement 的既有兼容顺序取首个有效正值。物理负荷只修改现有五维，不创建“运动量”第六维，也不选择动作。

### 3.3 聊天窗和既有表现

| 事件 | 食欲 | 困意 | 精力 | 社交需求 | 刺激需求 |
|---|---:|---:|---:|---:|---:|
| `chat_minimized_visible` | 0 | 0 | 0 | 0 | 0 |
| `chat_minimized_moved_far` | 0 | 0 | 0 | 0 | 0 |
| `chat_compact_surface_visible` | 0 | 0 | 0 | 0 | 0 |
| `chat_idle_docked_near_cat` | 0 | 0 | 0 | 0 | 0 |
| `chat_expanded` | 0 | 0 | 0 | 0 | 0 |
| `cat1_walk_done_near_chat` | 见 3.2 | 见 3.2 | 见 3.2 | +5.0 | -4.0 |
| `cat1_stretch_done_near_chat` | 0 | +2.0 | -3.0 | 0 | -4.0 |
| `cat1_compact_top_edge_done` | 见 3.2 | 见 3.2 | -3.0 并见 3.2 | +4.0 | -6.0 |
| `cat1_compact_top_edge_drop` | 0 | 0 | -2.0 | 0 | +4.0 |
| `edge_peek_after_drag` | 0 | 0 | -2.0 | 0 | +4.0 |

聊天窗形态和几何是 provider、near/far 与安全判断事实，不等同于用户需求，所以不直接改五维；它们仍进入 recent events 并排入下一轮异步判断。相同最小化状态和相同窗口矩形只记一次。聊天球中心移动至少 `24px` 才从后续最小化通知归类为 `chat_minimized_moved_far`，poll/heartbeat 不重复记录。`cat1_walk_done_near_chat` 的社交/刺激只属于成功到达；携带 `completed=false` 或 `cancelled=true` 时跳过这部分语义反馈，仅按 3.2 结算已经走过的真实路程。compact 上缘成功落位同时结算既有表现反馈和真实路程。

### 3.4 控制与边界 observation

| 事件 | 直接五维变化 | 作用 |
|---|---:|---|
| `desktop_occlusion_or_layer_change` | 0 | 更新桌面层级/遮挡事实，排入下一轮判断 |
| `return_click` | 0 | 冻结一次性 return 摘要并结束本轮 Cat Mind |
| `tier_changed` | 仅施加 2.4 的 tier 边界 | 切换当前合法动作池 |
| `tier_demoted_by_drag` | 由随后 `tier_changed` 施加边界 | 记录既有拖拽降级表现，不成为主动候选 |

这些事件不创建第六个需求字段，也不直接映射动作。`return_click`、tier 变化与动作结果仍遵守异步边界，不能在旧入口同步启动下一动作。

### 3.5 动作完成和中断

| 结果事件 | 食欲 | 困意 | 精力 | 社交需求 | 刺激需求 |
|---|---:|---:|---:|---:|---:|
| `social_ping_done` | 0 | 0 | -1 | -34 | 0 |
| `small_move_done` | 见 3.2 | 见 3.2 | 见 3.2 | 0 | -14 |
| `small_move_cancelled` | 见 3.2 | 见 3.2 | 见 3.2 | 0 | 0 |
| `eat_done` | -34 | -1 | +14 | 0 | +2 |
| `play_done` | +7 | +5 | -8 | 0 | -28 |
| `cat1_local_play_done` | +7 | +5 | -8 | 0 | -28 |
| `sleep_feedback_done` CAT2 | +2 | -30 | +16 | 0 | +2 |
| `sleep_feedback_done` CAT3 | +2 | -38 | +20 | 0 | +2 |
| `action_interrupted_by_drag/return/tier_change` | 0 | 0 | 0 | 0 | 0 |
| 其他 failed/cancelled | 0 | 0 | 0 | 0 | 0 |

只有严格匹配当前 active action 的结果才能结算。`started` 只写 cooldown，不提前写完成反馈。`small_move_done` 的 `-14` 刺激是动作完成语义；取消时不领取这部分完成反馈，但已经发生的实际位移仍按同一终态的 3.2 物理事实结算。

`interrupted` 先按动作自身终态处理，例如 small move 仍可结算已经发生的真实路程；随后生成的 drag/return/tier interruption observation 只是生命周期元数据，五维全部为零。拖拽本身已经通过 3.1 和 3.2 结算，不能再由中断事件重复收费。

journey 到达聊天球后的局部 `25%` 玩球不是 selector 动作。只有它真实 done 时才使用上表的 play 完成反馈并消费尚未满足的毛线意图；cancelled 不结算完成反馈。两者都不写 Cat Mind cooldown、严格 action result、`has_started_autonomous_action` 或 return episode，也不会单独制造下一轮 selector 机会。

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

曲线形态采用“短时激活、有界饱和、同动作习惯化后恢复”的统一思路：普通互动通过五维同时提高多个合法候选，避免事件直接指定动作；真正 started 后的恢复惩罚只压对应动作，让其他动作接替而不是全局沉默。具体数值以本项目的真实 runner、30 秒 tick、provider、动作终态和场景 harness 校准为准。

公共响应曲线是归一化三次 Hermite S 曲线：

```text
S(x) = x² × (3 - 2x),  x = clamp(x, 0, 1)
```

它在 `0` 和 `1` 两端斜率为零，输入连续变化时不会在阈值附近突然跳分。固定采样为：

| x | 0 | 0.25 | 0.5 | 0.75 | 1 |
|---:|---:|---:|---:|---:|---:|
| `S(x)` | 0 | 0.15625 | 0.5 | 0.84375 | 1 |

第一步把不同阈值的动作换成可比较的需求余量。负余量保留 `14` 点的平滑决策带；正余量先用 `8` 点响应带到达 `78`，再用指数尾部渐近 `98`：

```text
need_surplus = base_score - threshold

need_contribution = need_surplus                         , need_surplus <= -14
need_contribution = -14 × S(abs(need_surplus) / 14)      , -14 < need_surplus < 0
need_contribution = 78 × S(need_surplus / 8)             , 0 <= need_surplus <= 8
need_contribution = 78 + 20 × (1 - exp(-(need_surplus - 8) / 8)), need_surplus > 8
```

明显低于阈值的负余量保留原值，不会被节奏分无条件救起。`0..+8` 保留及时响应，高于 `+8` 后仍能区分多个强需求动作，但新增收益越来越小且永远不超过 `98`，避免高互动时所有动作都卡在同一个 `78` 平台。更深的共享节奏底部压住无/少交互，高余量则可以通过同一曲线越过它，因此真实密集交互会提前回应，但没有单独的“高互动模式”。需求曲线采样：

| 原始余量 | -20 | -14 | -10.5 | -7 | -3.5 | 0 | +1 | +2 | +4 | +6 | +8 | +12 | +16 | 很高 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 计入余量 | -20 | -14 | -11.8125 | -7 | -2.1875 | 0 | 3.3516 | 12.1875 | 39 | 65.8125 | 78 | 85.8694 | 90.6424 | 渐近 98 |

第二步，所有动作共享同一个连续节奏曲线。`t` 是距最近一次 runner 真实 `started` 的分钟数；本轮还没有 started 时，从进入猫形态算起。完整恢复窗为 `4.85` 分钟，即 `291` 秒：

```text
p = clamp(t / 4.85, 0, 1)
cadence = -58 + 76 × S(p)
```

| 距真实 started | 0 | 0.5 分 | 1 分 | 1.5 分 | 2 分 | 2.425 分 | 3 分 | 4 分 | 4.85 分及以后 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 节奏分 | -58 | -55.74 | -49.64 | -40.69 | -29.89 | -20 | -6.74 | +11.82 | +18 |

这是连续评分，不是 3 分钟 gate 或 5 分钟强制触发。当前参数由项目的 30 秒 tick、五维自然流、runner 真实终态和完成反馈共同校准：无/少交互的持续平均目标是约 3–5 分钟一次，高需求可以更早进入下一次竞争，需求不足则继续等待。

第三步加入对应动作的短时意图贡献。意图是选择器上下文，不是第六维，也不直接启动动作。证据合并采用饱和式累积；`current` 和 `strength` 均限制在 `0–1`：

```text
next = current + strength × (1 - current)
```

每条证据先完整保留 `30s`，之后以 `90s` 为半衰期连续衰减：

```text
level(age) = level(updated_at)                                      , age <= 30s
level(age) = level(updated_at) × 0.5^((age - 30s) / 90s)             , age > 30s
intent_curve = S(level(age))
intent_contribution = 84 × intent_curve
```

意图贡献上限为 `84`。强度 `0.90` 的新鲜 far→near 邀请贡献 `81.648`；强度 `0.62` 的单次猫旁递球贡献约 `56.830`，两次饱和合并后的 level 为 `0.8556`、贡献约 `79.251`。它比普通五维波动更明显，但仍通过统一 utility 竞争。当前证据强度：

| observation | 对应动作 | strength | 条件与语义 |
|---|---|---:|---|
| 毛线从 far 拖到 near | `cat1_play_yarn` | 0.90 | 用户主动、结束在猫旁，且起点在 far、起终点直接接近量达到有效移动阈值 |
| 猫旁再次递毛线 | `cat1_play_yarn` | 0.62 | 用户主动、结束在猫旁，且直接接近量或完整路径达到有效移动阈值；重复递球按饱和公式增强 |

普通 hover、拖拽和 rapid drag 只通过五维影响所有动作，不生成 action-specific intent。用户把毛线明显拖离猫时会清除 `cat1_play_yarn` 的未消费意图。provider reject、`accepted` 和启动前失败都不消费意图；对应 Cat Mind runner 真实 `started`，或 journey 局部玩球真实 `done`，才清除这份邀请。这样暂时受 near-chat 或表现锁阻断的意图仍能在后续合法判断中生效，但会自然衰减，不会永久等待。

毛线证据来自 avatar 侧只读 observation adapter。它兼容显式 start/move/end、缺失 start 的首个 move、Wayland/self-ball stop/cancel/blur、嵌入式 minimized chat 拖拽和重复 terminal 通知，只汇总一段真实用户手势，不调用会写入 journey approach side 的目标函数。拖拽期间 `yarnDragActive=true`，终态后先进入 `yarnSettling=true`；正常路径等待既有 journey 的双 RAF 几何同步，后台 RAF 暂停时由带 sequence guard 的 `250ms` fallback 释放，随后才派发 `chat_yarn_drag_completed`。这两个阶段都使本轮 selector 输出 `quiet`，避免原始 move/end 帧在落位前抢跑，也不会永久锁住 gate。意图分只能提高合法候选，`cat1_play_yarn` 的 settled near-chat 与毛线隐藏/恢复能力仍是 provider 硬条件。

第四步减去本动作自己的软 cooldown。六个动作使用相同的 `36` 点基准、`4.2` 倍倍率和 `1.9` 次恢复曲线。`p` 是本 cooldown 已经过的比例；只有该动作有明确意图时，才按同一个 intent curve 最多减轻一半惩罚：

```text
p = clamp(elapsed_ms / full_cooldown_ms, 0, 1)
cooldown_curve = 1 - p^1.9
cooldown_base_penalty = 151.2 × cooldown_curve
intent_relief = 0.5 × intent_curve
cooldown_penalty = cooldown_base_penalty × (1 - intent_relief)
utility = need_contribution + cadence + intent_contribution - cooldown_penalty
final_score = threshold + utility
```

`1.9` 次恢复让重复惩罚在 cooldown 前半段保持明显，后段再逐步释放。它只压低刚执行过的同一动作，其他动作可以正常竞争；没有意图的普通动作不获得 relief。即使 far→near 意图为 `0.9`，relief 也只有 `48.6%`，新鲜 cooldown 仍保留 `51.4%`，所以明确邀请可以提高即时性但不能变成事件直连或无惩罚连播。需求足够强时同一动作仍可能重复，因此“一两次重复”来自分数而不是白名单。

### 4.3 阈值与软 cooldown 时长

| 动作 | 阈值 | cooldown | started | 已过 25% | 已过 50% | 已过 75% | 到期 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `cat1_social_ping` | 36 | 480 秒 | 151.2 | 140.34 | 110.69 | 63.67 | 0 |
| `cat1_eat_snack` | 40 | 480 秒 | 151.2 | 140.34 | 110.69 | 63.67 | 0 |
| `cat1_small_move` | 51 | 480 秒 | 151.2 | 140.34 | 110.69 | 63.67 | 0 |
| `cat1_play_yarn` | 52 | 600 秒 | 151.2 | 140.34 | 110.69 | 63.67 | 0 |
| `cat2_nap_feedback` | 54 | 600 秒 | 151.2 | 140.34 | 110.69 | 63.67 | 0 |
| `cat3_sleep_feedback` | 50 | 720 秒 | 151.2 | 140.34 | 110.69 | 63.67 | 0 |

所有 cooldown 都只在 runner `started` 后建立，同时重置共享节奏时钟。provider reject、accepted 后启动失败都不写 cooldown，也不重置节奏。

阈值是各动作基础公式的校准基线，不是按交互档位分支。social/eat 的基础输出区间低于移动/玩球，而且它们是 far-chat 仍可执行的两个 CAT1 动作；`36 / 40` 让正常和大量交互在远离聊天球时仍能提前得到回应。有明确意图证据的动作一旦真实 started，自己的 `151.2` 起始惩罚立即生效，而且对应意图同时被消费，所以提高后的邀请权重仍不能无视新鲜 cooldown 连续重播。near-chat 的完整动作池继续由同一需求曲线、节奏、可选短时意图和 cooldown 竞争。

### 4.4 候选顺序

只有通过 hard/tier/provider gate 且 `utility >= 0` 的动作进入候选。候选按 utility 而不是 raw base score 排序，避免阈值较高的动作在跨动作比较时吃亏。CAT1 贴边半隐藏、毛线拖拽 active 或毛线几何 settling 时都是环境 hard gate，整轮输出 `quiet`；即使状态快照与边缘 class 短暂不同步，provider 也会拒绝 CAT1 动作。utility 相同时按固定顺序：

1. `cat1_social_ping`
2. `cat2_nap_feedback`
3. `cat3_sleep_feedback`
4. `cat1_small_move`
5. `cat1_eat_snack`
6. `cat1_play_yarn`

无候选输出 `stay_idle`；hard gate 或已有 request/action 输出 `quiet`。两者都不是失败。

### 4.5 请求、runner 与终态

```text
queued decision
  -> DOM-free request
  -> rejected | accepted(runId)
  -> started(runId)
  -> done | failed | cancelled | interrupted
  -> post-action settle
  -> one queued reevaluation
```

- provider `dryRun` 必须只读。provider 拒绝发生在 request 之前；adapter 也可对已发 request 回 `rejected`。两者都不写 cooldown、done/failed/result、五维完成反馈或 episode，也不消费意图。
- request 没有 ack 时的租约为 `5s`；accepted 后没有 started 时的租约为 `12s`。deadline 到达时由独立 timer 异步释放 pending 并记录协议失败；pending 期间若合并了用户触发，只排入一次后续判断，无输入时不自动重发 request，也不伪造终态或在 timer 回调里同步启动 runner。
- `accepted` 必须带唯一 `runId`，只绑定 request；`started` 必须匹配同一个 `actionId + requestId + runId`。只有 started 才建立本动作 cooldown、重置 cadence、设置短时投递资格并消费对应 Cat Mind 动作意图。
- 音频 runner 必须等 `audio.play()` 成功才报告 started；创建 `Audio`、选择素材或显示准备态都不算 started。
- 严格终态只接受 `source=cat_mind` 且完整匹配 active 三元组的 `done/failed/cancelled/interrupted`。不匹配的 legacy/presentation 结果只留作调试，不结算。
- accepted 后若终态先于 started，释放 request 并记录 `result_before_started`，但不写 cooldown、五维完成反馈、episode 或 started 投递资格。
- done 结算动作完成反馈，并把 CAT1 活动或 CAT2/CAT3 休息写入有界 episode accumulator；其他终态永远不是完成经历。interrupted 先执行动作自身取消语义，再追加零五维的 interruption 元数据。
- pending/active 期间到达的用户触发按 observation type 合并保存。终态后的第一轮只完成 post-settle，随后用 `setTimeout(0)` 排入一次新判断；wakeup、observation 和 action result 都不能在旧同步栈里连播。
- 有新鲜明确意图但 provider 暂不可用时保留 `providerRecheckNeeded`。既有 walk/stretch 的完成只可唤醒一次重新 dry-run，仍不是 selector 候选。

## 五、交互强度验收包络

固定节奏只用于构造可复现包络，不等于真实用户画像。真实使用还包含不规则 hover、拖拽取消、连续快速甩动、窗口变化、near/far 切换、tier 变化和 return，不能用单一机械节奏概括产品体验。所有表格只统计 adapter 确认的真实 `started`；provider reject、accepted 但未 started 和普通 observation 都不计数。

### 5.1 场景分层

验收至少覆盖四种持续程度，而不是把“有交互”合成一个 profile：

| 场景 | 输入语义 | 主要观察目标 |
|---|---|---|
| 从不交互 | 只有真实时间流 | 仍有克制生命感，不永久静止 |
| 少量交互 | 偶尔 hover 或单次短拖拽 | 不退化为气泡单循环，整体仍接近安静节奏 |
| 正常交互 | 间歇 hover、完整拖拽、偶尔气泡点击 | 短时和一段时间内比无/少更早、更多，但不连续轰炸 |
| 短时间大量交互 | 数秒到数分钟内多次真实 hover/拖拽，包含可选 rapid | 互动附近显著提前首响并增加多种动作，停止后逐步回落 |
| 持续大量交互 | 长时间重复真实手势 | 仍受精力、完成反馈、overflow、节奏和本动作 cooldown 约束 |

短时样本必须按生产链压缩事实：一次 `mouseenter` 只是一条 hover；一段连续拖拽只有一组 start/terminal；rapid 在同一手势至多一次。pointer-down 的 pending 不取消 runner，只有真实位移后的 active 才能中断；每个 runner 使用自己的实际 started/terminal 时机，不使用统一假时长，也不把 request 或 accepted 统计成动作。

near-chat 用于验证 CAT1 四动作的完整竞争；far-chat 只有 social/eat provider 合法，`small_move / play_yarn` 必须为零。强毛线意图也不能为满足测试而主动 walk-to-chat。两种夹具不能用同一动作总量硬比较。

### 5.2 产品包络

| 指标 | 可验证目标 |
|---|---|
| 无/少交互持续节奏 | started 的长期平均间隔约 `3–5` 分钟；允许采样边界波动，不要求每个滑窗完全同数 |
| 动作自然度 | near-chat 的约 15 分钟普通观察窗内通常有 `3–5` 次 started，并覆盖多种合法动作；允许一两次重复，不允许长期只刷同一种 |
| 正常交互梯度 | 首响和前段 started 数应高于无/少交互；差异来自五维累积和统一评分，不来自档位分支 |
| 短时大量交互首响 | gate 与 provider 已可用时，应在数秒到数十秒内出现首个真实 started，而不是仍等待普通 3–5 分钟 |
| 短时大量交互前段 | 前几分钟的 started 数和动作种类应与正常、无/少拉开；near-chat 应有机会覆盖多种 CAT1 动作，不锁定固定顺序 |
| burst 回落 | 高频输入停止后，社交/刺激 overflow、动作完成反馈和 cooldown 使节奏逐步回到普通区间，不留下永久高频模式 |
| 重复抑制 | 同动作新鲜 cooldown 仍显著；气泡点击是额外满足而非唯一防刷机制，不能依赖气泡硬间隔 |

这些是区间和相对关系，不锁死具体动作顺序、精确分钟序列或单一真人画像。调参时必须同时看短时首响、3/10/15 分钟累计、60 分钟分布、动作种类、连续重复、终态类型和最终五维；任何一项变好都不能以破坏其他项为代价。

### 5.3 生命周期与场景独立验收

1. 从不交互时自然时间流能产生少量动作，不永久静止。
2. provider reject 不产生 cooldown、done、failed、result 或 return episode，也不消费尚在衰减的短时意图。
3. accepted 不等于 started；音频播放失败时无 cooldown，意图仍保留；只有真实 started 同时建立 cooldown、重置节奏并消费对应 Cat Mind 动作的意图。未确认 request 的 `5s` 租约、accepted 未 started 的 `12s` 租约到期后只释放调度，不伪造结果。
4. done-before-started 记录协议失败并释放 request，但不产生 cooldown、五维完成反馈、episode 或短时 started 投递资格。
5. drag end/cancel、walk done/cancel、compact top-edge done、small move done/cancel 的物理负荷只按非空终态 `activityId` 结算一次；取消只结算已发生的位移，不领取成功到达或动作完成反馈；drag/return/tier interruption 元数据不重复修改五维。
6. 六个已接入动作都使用 `need + cadence + intent - cooldown` 的统一结构；没有证据的动作 intent 为 0，CAT2/CAT3 只开放对应动作。
7. 毛线拖拽 active/settling 时保持 quiet；far-chat 的小移动和玩毛线保持 provider hard reject，强毛线意图也不能主动 walk-to-chat 或越过 provider。provider-ready 的 walk/stretch 只可唤醒被保留的意图，不得自己成为 selector 候选。
8. active action 期间的大量用户触发必须去重合并，并在终态恢复后只排入一次异步 reevaluation；不得同步连播，也不得把这段交互全部丢失。
9. journey 局部玩球 done 只结算 play 五维并消费毛线意图；不写 cooldown、严格 result、started 投递资格或 episode。cancelled 不领取完成反馈。
10. 更换 runner、tick、五维反馈、意图或动作公式时必须用真实 runner 时长和终态重新模拟，不能只改文档、debug 展示或固定断言。

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

### 6.4 一次性投递链

1. return 时 Cat Mind 冻结本轮短摘要；只有 Live2D、VRM、MMD 的现行 return source 保留草稿，PNGTuber 只 reset，不能把草稿留给下一个 avatar。
2. return 消费者读取后立即清空草稿，再派发专用 `cat_greeting_check`；无 WebSocket、发送失败或重复 return 都不能把旧摘要带到下一轮。
3. WebSocket 只在停留 `>=180s`，或摘要明确带字面量 `has_started_autonomous_action=true` 时发送。这个布尔位只开短时投递 gate，不提供动作完成事实。
4. 后端以消息顶层 `cat_duration_seconds / tier / was_auto` 为 canonical，只从摘要读取 allowlist episode 和 started 布尔位；摘要中的 duration、entry、final tier 不成为第二事实源。
5. 服务端把合法 enum 映射为自己持有的 scene 文案，通过独立 `trigger_cat_greeting` 生成一次 ephemeral prompt；摘要不写数据库、长期 memory 或角色设定。
6. 该路由继续受 goodbye silent、voice、takeover、session、主动消息状态机和写锁约束。`time_hint` 固定为空，不调用或拼接早餐、午饭、晚饭、深夜等普通 greeting。

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
