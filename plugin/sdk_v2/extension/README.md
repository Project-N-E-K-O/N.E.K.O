# sdk_v2.extension

扩展侧入口（能力边界比标准插件更窄）。

## 用途
- 为 extension 类型提供更受限、更可控的 API 面。
- 避免 extension 直接依赖过多底层能力。

## 导入建议
- `from plugin.sdk_v2 import extension as sdk_ext`

## 约束（v2）
- Async-first。
- 仅暴露 extension 需要的最小能力集。
- 默认不依赖 `shared/*` 深层模块，除非明确需要。

## 迁移说明
- 当前为薄导出层，后续会强化 extension 专属契约。
