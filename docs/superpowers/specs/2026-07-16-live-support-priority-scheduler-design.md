# NEKO Live 礼物优先调度设计

## 目标

在不绕过现有 Pipeline、Safety Guard、dry-run、Dispatcher、隐私投影和会话隔离的前提下，为已验证的 Gift、Super Chat 与 Guard 事件增加真实生效的去重、连击聚合、分级和串行优先调度。

## 已确认的产品行为

- 采用插件内独立支持事件调度器，不修改宿主 N.E.K.O 的全局输出队列。
- 高价值事件不会打断猫娘已经开始说的一句话，只会越过尚未开始的低优先级等待项。
- 普通弹幕无论包含何种礼物描述，都不能升级为已验证支持事件。
- 低价值或免费礼物可以在高峰期聚合感谢，不得阻塞或消耗高价值礼物的处理资格。
- 所有最终输出仍走 `handle_live_payload()` 和现有 pipeline，不允许调度器直接调用 Dispatcher。

## 非目标

- 不实现跨平台金额换算。
- 不增加主播可编辑的礼物价格表、复杂优先级规则或奖励承诺。
- 不中断正在播放的 TTS。
- 不修改普通弹幕的选择窗口。
- 不扩大到抖音生产接入；只保持 provider-neutral 数据结构和兼容边界。

## 方案选择

### 采用：`live_support_events` 前的会话级调度器

调度器只消费已经通过 provider 层验证并发布到 EventBus 的 `gift`、`super_chat` 与 `guard`。它负责规范化调度字段、幂等去重、连击收束、优先队列与低价值聚合，然后将最终安全摘要逐条交给 `ctx.handle_live_payload()`。

该边界保留 `live_support_events` 的单一职责：为一个已决定播报的支持事件构建请求与提示词。调度器不生成回复内容，也不持有原始 provider 包。

### 未采用：复用普通弹幕 Selection 窗口

普通弹幕窗口按内容价值挑选候选并受聊天冷却影响。把支持事件重新放入该窗口会造成付费事件被聊天窗口延迟或丢弃，并把连击状态混入房间话题选择逻辑。

### 未采用：修改宿主全局输出队列

宿主级优先队列能统一所有插件输出，但会扩大当前插件任务范围，并可能改变其他插件的行为。当前需求只需要 NEKO Live 支持事件内部的顺序保证。

## 数据契约

支持事件的安全公共摘要在现有字段基础上增加以下可选字段：

- `support_verified: bool`：provider 已确认这是结构化支持事件。
- `support_evidence: str`：受限枚举，例如 `bilibili_typed_command`。
- `provider_event_id: str`：平台或桥接层提供的公开不透明事件 ID。
- `provider_event_type: str`：原始结构化命令类型，例如 `SEND_GIFT` 或 `COMBO_SEND`。
- `provider_timestamp_ms: int`：平台事件时间，仅用于排序和重放保护。
- `combo_id: str`：同一连击的公开不透明标识。
- `combo_count: int`：当前累计数量。
- `combo_end: bool`：平台明确表示连击结束。
- `coin_type: str`：受限为 `gold`、`silver` 或空值。

这些字段必须经过 provider-neutral 安全投影。不得保留原始事件对象、cookie、token、签名、原始聊天内容或可持久关联的额外身份字段。

缺少 `support_verified` 的历史测试/手动模拟事件只在明确的本地模拟来源中兼容；真实 provider 路径必须 fail closed。

## 优先级

调度器使用固定优先级，不把 provider 的原始金额直接跨平台比较：

1. `P0 milestone`：Super Chat、Guard，以及未来显式标记的里程碑事件。
2. `P1 high`：B 站 `gold` 且 `gift_total_coin >= 10000` 的礼物。
3. `P2 medium`：B 站 `gold` 且 `1000 <= gift_total_coin < 10000` 的礼物。
4. `P3 light`：其余礼物，包括 `silver`、免费礼物、缺失或低于 1000 的金额。

同级事件保持稳定到达顺序。高优先级只能抢占尚未开始的等待项，不能取消正在执行的 pipeline 请求。

## 幂等与连击

### 幂等键

优先使用 `(provider, room_ref, provider_event_id)`。已处理 ID 在当前直播会话内保留有界 TTL 缓存。

平台未提供事件 ID 时，使用受限回退指纹：provider、房间、事件类型、UID、礼物 ID/名称、累计数量、金额与离散时间桶。回退指纹只用于短时间重复包，不作为长期身份。

### 连击收束

- 非连击礼物直接进入队列。
- 有 `combo_id` 的连击更新同一个 accumulator，不重复入队。
- `combo_end=true` 时立即完成收束。
- 缺少明确结束标志时，在最后更新后 1 秒无新包即完成收束。
- 更新中的累计数量只能增大；迟到的小计包不得回退总数。
- 会话停止、房间切换或 generation 失效时，取消所有 accumulator，不把旧房间礼物带入新会话。

## 调度与背压

每个直播会话只有一个支持事件 worker。worker 从稳定优先队列取出一项，调用现有 `ctx.handle_live_payload()`，等待其返回后再处理下一项。

- 队列有界，默认上限沿用 `queue_limit`，但最低保证能容纳 P0/P1。
- P0/P1 到达满队列时，先合并或淘汰最旧的 P3，再考虑 P2；不得静默淘汰 P0/P1。
- P3 在队列压力下按短窗口聚合为一个感谢摘要；聚合摘要不得虚构精确总金额。
- P0/P1 单独播报；P2 可按同一 UID、同一礼物和短窗口合并；P3 可跨用户聚合。
- 调度器不复用当前单一 1.5 秒“先到先占”的支持冷却。真正的输出节奏继续由 Safety Guard 与 pipeline 控制。

`handle_live_payload()` 抛出异常时，该项记为失败而不是成功消费；最多进行一次有界重试。重试仍失败后记录安全审计并继续处理下一项，避免队列永久阻塞。

## 可观测性

状态只暴露聚合数字和安全枚举：

- 各优先级 pending 数量。
- 活跃 combo 数量。
- 最近一次调度优先级与事件类型。
- 去重、连击收束、低价值聚合、队列淘汰和执行失败计数。

timeline/audit 使用固定原因码，例如：

- `support.duplicate_event`
- `support.combo_updated`
- `support.combo_finalized`
- `support.queued`
- `support.light_aggregated`
- `support.lower_priority_evicted`
- `support.dispatch_failed`

不得记录昵称、原始 UID、原始消息、原始礼物包或凭证。

## 测试策略

1. Provider 投影测试：B 站结构化事件保留安全验证、事件 ID、coin type 与 combo 字段；普通弹幕不能伪造这些字段。
2. 调度器单元测试：稳定优先顺序、同级 FIFO、高价值越过等待中的低价值、当前任务不被打断。
3. 连击测试：增长包只更新 accumulator、结束包单次入队、1 秒空闲收束、迟到小计不回退、会话重置取消。
4. 幂等测试：事件 ID 去重优先、回退指纹有界、不同真实事件不被 350ms 误杀。
5. 背压测试：P3 聚合或淘汰、P0/P1 保留、满队列不死锁。
6. 集成测试：最终仍通过 `handle_live_payload()`、Safety Guard、dry-run 和 Dispatcher；失败只重试一次。
7. 会话隔离测试：停止、重连、切换房间后旧队列和 combo 不得输出。
8. 完整回归：插件 pytest、Ruff、CLI 静态检查、diff-check，以及 8 个 locale key 集合检查；本功能原则上不新增用户可见文案。

## 兼容与迁移

现有 `support_event_tier` 提示词元数据继续保留，但 tier 从纯描述升级为调度结果。现有 Gift/SC/Guard 请求构建和输出质量规则保持不变。

当前工作区已有未提交的直播会话隔离、防伪、运行时、文档、UI 与测试改动。实现必须在这些改动之上增量修改，禁止还原、覆盖或把无关文件混入提交。
