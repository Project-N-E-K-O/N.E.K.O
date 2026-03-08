# sdk_v2.shared

SDK v2 的共享契约层（SDD first）。

## 目标
- 只定义 API 契约：方法名、输入输出、数据结构、边界语义。
- 渐进实现：`core` 已有可运行子集，`bus/storage/runtime/transport` 仍以契约占位为主。
- 统一错误模型：`Result[Ok|Err]` + `ok/fail` envelope。

## 规则
- Async-only：不提供同步 API。
- 命名统一：不使用 `_async` 后缀，默认即异步。
- 三类入口（plugin/extension/adapter）复用 shared 契约，不反向依赖 v1。

## 目录
- `core/`: 基础契约（base/config/plugins/router/events/hooks/decorators）
- `bus/`: bus 子域契约（messages/conversations/events/watchers/records/revision）
- `storage/`: store/state/database 契约
- `runtime/`: call_chain/memory/system_info 契约
- `transport/`: message plane 契约
- `models/`: `Result`、error code、envelope
- `compat/`: 兼容层占位（仅迁移期使用）
