# sdk_v2.plugin

插件侧主入口（标准插件开发优先使用）。

## 用途
- 提供插件开发常用 API 的稳定导出面。
- 面向插件作者，默认不要求接触 `public/*` 分层细节。

## 导入建议
- `from plugin.sdk_v2 import plugin as sdk`
- 或 `from plugin.sdk_v2.plugin import ...`
- extension 请用 `plugin.sdk_v2.extension`
- adapter 请用 `plugin.sdk_v2.adapter`

## 约束（v2）
- Async-first（不新增 sync API）。
- 统一错误语义：优先 `Result`（`Ok/Err`）+ 边界层异常桥接。
- 业务逻辑放插件，不放 SDK 基础层。

## 迁移说明
- 当前目录仍含兼容导出壳，后续会逐步替换为 v2 原生实现。
