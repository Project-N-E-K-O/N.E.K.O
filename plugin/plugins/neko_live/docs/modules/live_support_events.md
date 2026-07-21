# live_support_events

## Purpose

处理 provider 已验证的 Gift / SC / Guard 候选：去重、合并连击、按价值有界调度，并通过统一 pipeline 产生克制的短句致谢。

普通弹幕中的“送了礼物”“人气票 x999999”等文本不是可信支持事件，不能进入本模块。

## Owner And Contracts

- Owner：`modules/live_support_events/**`。
- 输入：`live_events` 选择后的 provider-neutral 支持事件 payload。
- 输出：交给 `ctx.handle_live_payload(...)`，继续经过 Pipeline、SafetyGuard 和 Dispatcher。
- 状态：只投影脱敏事件类型、价值、队列和最近处理结果。

模块不得直接调用 `push_message`，不得读取 provider raw，也不得建立独立 LLM 或 TTS 通道。

## Scheduling

- provider event ID 或等价稳定证据用于去重；
- combo 更新必须区分“同一连击的迟到更新”和“新事件”；
- SC / Guard / 高价值礼物优先于普通礼物；
- 队列、去重集合、combo 状态和并发 worker 都有硬上限；
- 队列满时保留更高价值事件，并留下稳定 skip reason；
- 每个会话使用 generation/ownership，旧会话结果不能进入新房间。

## Safety Boundary

- setup 未完整成功时 fail-closed，不启动半可用 worker；
- 断线、换房、重连、停止插件时取消 worker 并处理旧队列；
- 致谢必须短、不索要更多支持、不朗读隐私字段；
- dashboard/audit 不保存原始消息、cookie、token、头像 bytes 或 provider payload；
- dry-run 仍走完整调度，但不真实开口。

## Testing

至少覆盖：

- 普通伪礼物文本不会进入支持事件；
- 重复 event ID 只处理一次；
- 连击迟到更新不重复致谢；
- 不同支持事件保持正确优先级；
- 队列硬上限与淘汰原因；
- setup 失败无残留 task；
- 切房/断线/teardown 清理旧 worker；
- Pipeline、SafetyGuard、Dispatcher 唯一出口；
- Timeline 与 dashboard 只有脱敏字段。

## Rollback

出现异常时可以关闭 `live_support_events_enabled`，其他弹幕、主持和观众功能继续运行。回滚不能删除或绕过共享 pipeline，也不能恢复直接 `push_message`。
