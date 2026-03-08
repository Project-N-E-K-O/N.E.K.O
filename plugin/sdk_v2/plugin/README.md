# sdk_v2.plugin

标准插件侧主入口。

## 用途
- 提供标准插件开发的稳定封装面。
- 暴露插件作者高频使用的 `base / decorators / runtime` 能力。
- 默认不要求插件作者接触 `public/*` 内部实现层。

## 导入建议
- `from plugin.sdk_v2 import plugin as sdk`
- 或 `from plugin.sdk_v2.plugin import ...`

## 封装结构
- `base.py`：插件基类与元信息
- `decorators.py`：插件入口、事件、hook 装饰器
- `runtime.py`：配置、跨插件调用、路由、结果模型、运行时工具

## 约束（v2）
- Async-first（不新增 sync API）。
- 统一错误语义：优先 `Result`（`Ok/Err`）+ 边界层 envelope。
- 业务逻辑放插件，不放 SDK 基础层。

## 迁移说明
- 外层保持为显式 facade。
- 内部模型与拼装细节可以继续下沉到 `public/*`。
