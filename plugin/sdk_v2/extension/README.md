# sdk_v2.extension

扩展侧主入口（能力边界比标准插件更窄）。

## 用途
- 为 extension 类型提供更受限、更可控的封装面。
- 暴露 extension 作者高频使用的 `base / decorators / runtime` 能力。
- 避免 extension 直接依赖过多底层能力。

## 导入建议
- `from plugin.sdk_v2 import extension as sdk_ext`
- 或 `from plugin.sdk_v2.extension import ...`

## 封装结构
- `base.py`：扩展基类与元信息
- `decorators.py`：extension entry / hook 装饰器
- `runtime.py`：配置、路由、传输、结果模型、运行时工具

## 约束（v2）
- Async-first。
- 仅暴露 extension 需要的最小能力集。
- 外层保持显式 facade，不退化为单纯转导入。

## 迁移说明
- 内部实现可下沉到 `public/extension/*`。
- facade 语义、导出名和推荐导入路径保持稳定。
