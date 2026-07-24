# User Activity Tracker

> **文档性质：current implementation record。** 本页记录当前规则优先、LLM 仅作辅助描述的用户活动追踪器。状态和 propensity 是启发式信号，不是对用户真实行为的事实判定。

## 作用

活动追踪器为主动对话提供结构化快照，区分离开、专注、浏览、聊天、语音等上下文，避免只用“最后输入时间”决定是否打扰。规则状态机负责门控；可选 LLM enrichment 只补充软分数、活动猜测和 open threads，失败时必须退回规则结果。

## 当前代码

- `main_logic/activity/tracker.py`：`UserActivityTracker`、信号入口、异步/同步快照；
- `main_logic/activity/state_machine.py`：状态迁移和规则判断；
- `main_logic/activity/snapshot.py`：公开快照数据结构；
- `main_logic/activity/system_signals.py`：系统信号采集；
- `main_logic/activity/llm_enrichment.py`：可选 LLM enrichment；
- `main_logic/activity/activity_guess_gate.py`：活动猜测的调用门；
- `main_logic/core/manager.py`：创建并持有 tracker；
- `main_logic/conversation_turns.py`：把用户/助手回合送入 tracker。

## 稳定接口

```python
from main_logic.activity import UserActivityTracker

tracker = UserActivityTracker(lanlan_name="xiao8")
tracker.on_user_message(text="...")
tracker.on_ai_message(text="...")
tracker.on_voice_mode(True)
tracker.on_voice_rms()

snapshot = await tracker.get_snapshot()
```

`get_snapshot()` 是异步主入口；需要纯规则、不可等待的调用方可使用 `get_snapshot_sync()`。字段定义以 `ActivitySnapshot` 为准。新增字段应提供安全默认值，避免破坏主动对话和测试替身。

## 决策原则

- 规则路径对 gating 有最终权威；LLM enrichment 不得直接放行被规则关闭的来源。
- `private` 等敏感状态应尽早短路，避免为被禁止的场景采集或缓存更多上下文。
- 窗口标题、进程名和原始对话可能含隐私；不得写入持久日志或遥测。
- 系统信号不可用、权限不足或 enrichment 失败时必须可用规则默认值继续运行。
- dwell、idle、语音 RMS 和回合时间使用各自的时钟语义；测试中要显式传 `now`，避免真实时间造成不稳定。
- 活动“猜测”必须在提示和 UI 中保持不确定语气，不能伪装成观测事实。

## 数据流

```text
conversation/system/voice signals
        -> UserActivityTracker
        -> rule state machine
        -> optional LLM enrichment
        -> ActivitySnapshot
        -> proactive source filtering and prompt context
```

主动对话对 snapshot 的具体消费位于 `main_logic/core/proactive.py` 及相关 system-router 流程。不要在 tracker 内直接发送消息；它只提供状态。

## 验证

```bash
uv run pytest tests/test_activity_tracker_followup.py tests/unit/test_activity_signal_router.py -q
```

涉及状态分类时还应针对敏感应用、窗口切换、idle/return、语音状态和 enrichment 失败补回归用例。

## 剩余工作

- 阈值需要依据匿名、聚合的真实分布校准；
- 新系统信号必须有权限失败和平台差异的降级路径；
- 若公开 snapshot schema 发生不兼容变化，应同步主动对话消费者与文档，而不是在消费者侧猜字段。
