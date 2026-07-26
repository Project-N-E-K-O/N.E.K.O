# Telemetry distribution / steam_user_id 竞态记录

> **文档性质：historical incident record。** 客户端竞态已经修复并有回归测试。本页记录不变量和影响边界，不把历史函数、commit 或生产诊断快照当成当前实现说明。

## 历史问题

旧实现分别读取 telemetry `distribution` 和 `steam_user_id`，两次 Steamworks `GetSteamID()` 可能跨过异步初始化完成边界，从而产生矛盾组合：

```text
distribution = release
steam_user_id = <non-empty Steam64>
```

服务端若把第一次上报当成最终元数据，低事件量设备可能长期保留错误分布。

## 当前修复

`utils/token_tracker/telemetry.py` 通过一次 `_get_telemetry_metadata()` 观测共同派生两个值。当前核心不变量是：

```text
steam_user_id 非空  =>  distribution == steam
```

单次元数据获取最多调用一次 Steam ID 读取；Steamworks 不可用或抛错时安全降级为没有 Steam ID 的 release/dev 判定。

## 影响边界

客户端修复阻止新增矛盾事件，但不会自动修复服务端历史行。服务端 canonical identity、设备与 Steam ID 多对多关系、历史回填和生产统计口径属于 telemetry server 的独立数据治理问题，必须用服务器代码和实际数据库另行验证。

本文不保留旧生产查询数字，因为它们是时点快照；任何当前影响评估都应重新运行只读查询，并记录查询时间、数据库版本和去重口径。

## 验证

```bash
uv run pytest tests/unit/test_telemetry_metadata_consistency.py tests/unit/test_telemetry_canonical.py -q
```

## 防回归

- 不重新拆成两个独立 Steamworks 读取；
- payload 构建只消费同一 metadata snapshot；
- 服务端不能把 `distribution` 当成稳定身份；
- telemetry 日志和测试不得包含真实用户 Steam ID。
