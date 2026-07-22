# NEKO Live Runtime Observability

运行态观测的目标是回答：**事件走到哪一步、为什么没有输出、主播下一步该做什么**。观测字段不是第二套业务状态，也不能携带 raw 数据。

## 三层视图

### Runtime Timeline

按 `trace_id` 记录单个事件经过的阶段。每条记录只保留：

- `trace_id`：随机、短生命周期关联 ID；
- `at`：时间戳；
- `stage`：稳定处理阶段；
- `status`：该阶段结果；
- `reason`：白名单化原因码；
- `route`：安全模块或路由名；
- `uid`：会话盐 HMAC 得到的 `viewer_<12hex>`；
- `source`：安全、截断后的来源。

Timeline 不记录昵称、原文、头像 URL、cookie、token 或 provider raw。

### Recent Results / Dashboard

Recent results 展示有界的 pipeline 结果摘要；Dashboard 把连接、开播准备、模块、最近活动和本场数据投影为主播可理解的状态。两者都只能从 runtime/audit 的安全事实派生。

### Monitor

`tools/monitor_live.ps1` 用于真机复盘。它读取安全投影并生成稳定告警分类，不是业务控制面，也不替代 Dashboard、recent results 和后端日志的人工核对。

## Stage 命名

当前主链路常见 stage：

| Stage | 含义 |
|---|---|
| `ingest` | provider 收到、清洗或丢弃事件 |
| `event_bus` | 安全事件已发布 |
| `live_events.select` | 候选窗口选择了事件 |
| `live_events.signal` | 事件只作为信号，不进入普通回复 |
| `live_support_events.receive` | 支持事件模块接收可信候选 |
| `live_input.normalize` | runtime 把输入归一为 pipeline 事件 |
| `live_input.signal_only` | 明确为不触发 AI 的状态信号 |
| `pipeline.received` | pipeline 开始处理 |
| `permission_gate` | 来源权限检查 |
| `live_status_gate` | 直播状态、暂停和连接门禁 |
| `safety_guard.before_event` | 事件级安全检查 |
| provider identity stage | 解析安全观众身份 |
| `viewer_profile` | 读取或更新受控档案 |
| `module_gate` | 功能开关检查 |
| `pipeline.route` | 选择响应模块 |
| `request.build` | 构造受控请求 |
| `live_session` | 会话代际检查 |
| `safety_guard.before_output` | 输出级安全检查 |
| `dispatcher.push` | dry-run、跳过、失败或真实派发 |
| `result.record` | 结果写入有界 recent results |

新增 stage 应表示新的稳定责任边界，而不是把每个函数名都暴露到 Timeline。

## Status 与 Outcome

Stage status 使用简短稳定值，例如 `received`、`published`、`ok`、`selected`、`skipped`、`blocked`、`failed`、`pushed`、`dropped`。

Pipeline 的最终状态至少能区分：

- `ok`：完成真实输出或预期动作；
- `dry_run`：完整链路成功但未真实开口；
- `skipped`：按策略预期不输出；
- `blocked`：被权限、直播状态或安全门阻止；
- `failed`：非预期异常或依赖失败；
- `signal_only`：只更新状态，不进入 AI 输出。

不要把“没有开口”都报成失败，也不要把异常吞成普通 skipped。

## Reason Code

Reason 必须是稳定机器码，不能直接写异常文本或用户数据。当前常见族：

- 准备与控制：`room_not_configured`、`live_room_offline`、`live_disabled`、`manual_paused`、`output_channel_unavailable`；
- 安全与限流：`cooldown`、`safety_degraded`、`safety_tripped`；
- ingest：`ingest.duplicate_support_event`；
- selection：`selection.low_value_danmaku`、`selection.quiet_low_priority`、`selection.queue_limit`、`selection.lower_score`；
- dispatcher：`dispatcher.dry_run`、`dispatcher.pushed`、`dispatcher.skipped`、`dispatcher.failed`；
- signal/support：`signal_only.<type>`、`support.<type>`；
- 会话：使用稳定的 stale-session reason，区分旧会话事件。

新增原因前先判断是否能复用已有语义；新增后同步代码白名单、测试、Dashboard/monitor 解释和本文。

未知 reason 不应原样投影。异常最多归一为安全类别，详细堆栈只留在不含隐私的开发日志中。

### 人猫同播参与策略

人猫同播策略的稳定 reason code 用于解释 allow、defer、skip 和 downgrade 决策，不代表已经产生 Dispatcher Outcome：

- `co_stream.policy.solo_passthrough`
- `co_stream.policy.capability_off`
- `co_stream.policy.host_speaking`
- `co_stream.policy.host_holding`
- `co_stream.policy.turn_yielded`
- `co_stream.policy.turn_unknown`
- `co_stream.policy.host_support_only`
- `co_stream.policy.nonverbal_safe`

Dashboard 可投影能力 ID、requested/effective participation level、activation、bounded priority、host-turn state、reliability、confidence 和安全来源枚举。不得投影音频、转写、平台 raw payload、观众正文或私人对话上下文。

当前投影必须保持 `read_only=true`、`enforced=false`，不得消费或修改最新话轮信号，也不得解释为 Event Outcome 或 Dispatcher Outcome。`solo_stream` 中出现 `co_stream.policy.solo_passthrough` 只用于证明隔离契约，不会阻断或改变独播链路。当前没有手动交棒 action、专用输出模块或真实自动开口路径；`conditional_auto` 只有在专用 consent version 精确匹配时才可视为已确认。

## Freshness

Dashboard 必须区分：

- 当前事实的产生时间；
- 最后成功刷新时间；
- 当前请求是否 pending；
- 状态是否可能过期。

刷新失败不能清空最近成功状态，也不能把已成功的开始/结束动作改报失败。超出可接受新鲜度时显示 stale，而不是假装实时。

## Monitor 告警分组

告警用于复盘，不直接改变业务行为：

- 连接与准备：`live_disconnected`、`live_not_ready`、`live_disabled`、`latest_stale`；
- 结果与延迟：`latest_failed`、`recent_failed`、`latency_warn`、`latency_slow`；
- 输出质量：`long_reply`、`reply_repeat`、`generic_host_prompt`、`reply_quality_fallback*`；
- 锐评与主题：`avatar_repeat`、`avatar_bias`、`topic_repeat` 及 topic/host beat 偏置告警；
- 主持节奏：`warmup_repeat`、`idle_missing`、`active_missing`、`active_blocks_idle`；
- 污染与播放：`contamination_*`、`playback_watchdog`；
- 测试态：`dry_run`、`test_isolation`。

告警只说明“值得复核”，不是自动判定 bug。真机结论要结合对应时间段的 Dashboard、recent results、Timeline 和脱敏日志。

## 新路径检查清单

新增或修改事件路径时确认：

1. 起点有 trace_id，跨 EventBus/Pipeline 保持同一 ID。
2. 每个责任边界有合适 stage，但没有函数级噪音。
3. 预期不输出有稳定 skip reason。
4. SafetyGuard 与 Dispatcher 阶段仍可见。
5. 最终 result 能映射到 status/outcome。
6. UID 已不可逆关联化，其他私密字段未进入投影。
7. 容器有硬上限，会话结束后能清理。
8. Dashboard 和 monitor 能解释新状态，并有负例测试。
