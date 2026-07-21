# live_audience_session

## Purpose

维护一次直播监听会话内的轻量统计：互动人数、弹幕数、支持事件、NEKO 发言数和最近互动观众。它服务“观众 → 本场直播”页面，不参与选择和输出。

## Contracts

- 数据只存在内存并按会话重置；
- 订阅 provider-neutral EventBus 事件；
- 使用平台前缀 UID 进行会话内关联；
- 列表和计数器有硬上限；
- 只向 Dashboard 暴露脱敏、截断后的摘要。

## Safety Boundary

本模块不得：

- 保存原始弹幕或 provider raw；
- 写入长期观众档案；
- 建立贡献排行榜或无限期支持流水；
- 影响 Selection、Pipeline、SafetyGuard 或 Dispatcher；
- 因统计失败阻断直播事件。

## Session Lifecycle

开始新直播、切换房间或 session generation 改变时创建新统计窗口。旧会话异步结果不得写入新会话。断开和 teardown 后清理引用、timer 与订阅。

## Testing

覆盖计数、去重、容量、脱敏、会话重置、旧 generation 拒绝、模块异常隔离和 Dashboard 投影。统计失败时应降级为空数据而不是影响主链路。
