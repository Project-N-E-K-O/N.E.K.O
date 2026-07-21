# live_hosting

## Purpose

管理 Independent Mode 的暖场、冷场陪播和主动营业。它决定“现在是否适合主动开口”和“用什么类型的话题”，不建立独立输出通道。

## Flows

- **Warmup**：开播后一次自然开场。
- **Idle Hosting**：直播间安静且满足门禁时补位。
- **Active Engagement**：在节奏允许时提出具体、可接的话题。

三者共享直播主题、最近上下文、话题/材料 family、节奏和去重状态。刚收到弹幕或支持事件时，主动主持应让路。

## Contracts

- 只在 `solo_stream` 或明确允许的模式下运行；
- `activity_level` 统一派生时间阈值和间隔；
- 选中内容后仍进入 Pipeline、SafetyGuard、Dispatcher；
- 话题与 host beat 使用有界最近历史避免复读；
- 没有合适材料时安全沉默，不退化为“有人吗”“大家互动”。

## Safety Boundary

- 不伪装真人主播、工作人员或后台操作员；
- 不泄露未公开活动、内部安排或观众隐私；
- 不把礼物、SC、Guard 当成普通主持素材；
- session 改变时取消旧 loop 和 pending task；
- 模块关闭时不阻塞普通弹幕和支持事件。

## Testing

覆盖开场只触发一次、弹幕后等待、冷场门禁、主动营业间隔、材料去重、generic prompt 负例、会话回收、dry-run 和输出唯一出口。
