# sdk_v2.shared

高级共享能力入口（给高级插件和 SDK 内部使用）。

## 用途
- 聚合 core/bus/storage/runtime/transport/models/compat 等基础模块。
- 提供跨 `plugin/extension/adapter` 的公共能力。

## 使用建议
- 普通插件优先使用 `plugin|extension|adapter`。
- 仅在确有必要时直接依赖 `shared/*`。

## 稳定性
- `shared/*` 允许更快演进，变更频率可能高于三类入口。
- 新能力优先在 `shared` 验证，再上浮到三类稳定入口。

## 当前重点
- `shared.models.result`：v2 统一 `Result` 语义（`Ok/Err/must/map/bind/map_err/capture`）。
