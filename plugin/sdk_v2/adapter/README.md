# sdk_v2.adapter

适配器侧入口（协议桥接/网关能力）。

## 用途
- 面向 adapter 类型插件。
- 提供 transport/gateway/协议相关能力。

## 导入建议
- `from plugin.sdk_v2 import adapter as sdk_adapter`

## 约束（v2）
- Async-first。
- 错误处理统一为 `Result` + 异常桥接。
- 对外协议边界清晰：输入校验、超时、错误码映射必须可测试。

## 迁移说明
- 现阶段以兼容壳为主，后续逐步切到 v2 原生实现。
