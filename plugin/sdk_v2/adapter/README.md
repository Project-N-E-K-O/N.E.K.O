# sdk_v2.adapter

适配器侧主入口（协议桥接 / 网关能力）。

## 用途
- 为 adapter 类型插件提供稳定封装面。
- 暴露 adapter 作者高频使用的 `base / decorators / runtime / types` 能力。
- 将网关细节与内部实现逐步下沉到 `public/adapter/*`。

## 导入建议
- `from plugin.sdk_v2 import adapter as sdk_adapter`
- 或 `from plugin.sdk_v2.adapter import ...`
- 运行时相关聚合优先从 `plugin.sdk_v2.adapter.runtime` 获取

## 常用导入模板
```python
from plugin.sdk_v2.adapter import (
    AdapterBase,
    AdapterConfig,
    AdapterContext,
    AdapterMode,
    NekoAdapterPlugin,
    on_adapter_event,
    on_adapter_startup,
    on_adapter_shutdown,
)
```

```python
from plugin.sdk_v2.adapter.runtime import (
    AdapterGatewayCore,
    DefaultRequestNormalizer,
    DefaultPolicyEngine,
    DefaultRouteEngine,
    DefaultResponseSerializer,
    CallablePluginInvoker,
    Result,
    ok,
    fail,
)
```

```python
from plugin.sdk_v2.adapter import (
    Protocol,
    RouteRule,
    RouteTarget,
    AdapterMessage,
    AdapterResponse,
)
```

## 封装结构
- `base.py`：adapter 基类、配置、上下文
- `decorators.py`：adapter 生命周期 / 事件装饰器
- `runtime.py`：网关运行时、默认实现契约、结果模型
- `types.py`：传输与路由相关类型

## 迁移建议
- adapter 作者优先依赖 `adapter` / `adapter.runtime`，不要直接依赖 `public/adapter/*`
- 网关模型和协议契约对外可见，但内部实现归属允许继续下沉
- 适配器插件与标准插件边界清晰时，优先继承 `NekoAdapterPlugin`

## 约束（v2）
- Async-first。
- 错误处理统一为 `Result` + 异常桥接。
- 协议边界清晰：输入校验、超时、错误码映射必须可测试。

## 迁移说明
- 外层保持为显式 facade。
- 不属于 facade 的网关细节继续下沉到 `public/adapter/*`。
