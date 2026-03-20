# sdk_v2 Unit Tests

## 分类
- `plugin/`: `plugin.sdk_v2.plugin` 的公开契约与导出面测试
- `shared/`: `plugin.sdk_v2.shared` 的共享契约测试（core/bus/storage/runtime/transport/models）

## 命名约定
- 文件名：`test_sdk_v2_<domain>_<topic>.py`
- 优先按源目录结构分组，不在 `unit/sdk_v2` 根目录堆放测试文件

## 覆盖率目标
- 对于 contract-only 模块：
  - 导出面（`__all__`）
  - 数据结构（dataclass/typed dict/常量）
  - 占位行为（`NotImplementedError`）
  均需覆盖
