"""
插件系统统一类型定义

提供所有公共类型、异常、Result 类型的统一导出。
这是 types/ 层的入口，所有类型定义都应该从这里导入。

Usage:
    from plugin._types import (
        # 错误码
        ErrorCode, ERROR_NAMES, get_error_name, get_http_status,
        # Result 类型
        Ok, Err, Result, ResultError, safe, async_safe,
        # 异常
        PluginError, PluginNotFoundError, PluginTimeoutError,
        # 事件
        EventMeta, EventHandler, EventType, EVENT_META_ATTR,
        # Protocol
        PluginContextProtocol,
        # 模型
        PluginMeta, PluginAuthor, PluginDependency,
        # 版本
        SDK_VERSION,
    )
"""

# 版本
from .version import SDK_VERSION

# 错误码
from .errors import (
    ErrorCode,
    ERROR_NAMES,
    get_error_name,
    get_http_status,
)

# Result 类型
from .result import (
    Ok,
    Err,
    Result,
    ResultError,
    safe,
    async_safe,
    try_call,
    try_call_async,
    from_optional,
    collect_results,
)

# 异常
from .exceptions import (
    PluginError,
    PluginNotFoundError,
    PluginNotRunningError,
    PluginTimeoutError,
    PluginExecutionError,
    PluginCommunicationError,
    PluginLoadError,
    PluginImportError,
    PluginLifecycleError,
    PluginTimerError,
    PluginEntryNotFoundError,
    PluginMetadataError,
    PluginQueueError,
)

# 事件
from .events import (
    EVENT_META_ATTR,
    STANDARD_EVENT_TYPES,
    StandardEventType,
    EventType,
    EventMeta,
    EventHandler,
)

# Protocol
from .protocols import (
    PluginContextProtocol,
)

# 模型
from .models import (
    RunStatus,
    RunCreateRequest,
    RunCreateResponse,
    PluginAuthor,
    PluginDependency,
    PluginType,
    PluginMeta,
    HealthCheckResponse,
    PluginPushMessageRequest,
    PluginPushMessage,
    PluginPushMessageResponse,
)

__all__ = [
    # 版本
    "SDK_VERSION",
    # 错误码
    "ErrorCode",
    "ERROR_NAMES",
    "get_error_name",
    "get_http_status",
    # Result 类型
    "Ok",
    "Err",
    "Result",
    "ResultError",
    "safe",
    "async_safe",
    "try_call",
    "try_call_async",
    "from_optional",
    "collect_results",
    # 异常
    "PluginError",
    "PluginNotFoundError",
    "PluginNotRunningError",
    "PluginTimeoutError",
    "PluginExecutionError",
    "PluginCommunicationError",
    "PluginLoadError",
    "PluginImportError",
    "PluginLifecycleError",
    "PluginTimerError",
    "PluginEntryNotFoundError",
    "PluginMetadataError",
    "PluginQueueError",
    # 事件
    "EVENT_META_ATTR",
    "STANDARD_EVENT_TYPES",
    "StandardEventType",
    "EventType",
    "EventMeta",
    "EventHandler",
    # Protocol
    "PluginContextProtocol",
    # 模型
    "RunStatus",
    "RunCreateRequest",
    "RunCreateResponse",
    "PluginAuthor",
    "PluginDependency",
    "PluginType",
    "PluginMeta",
    "HealthCheckResponse",
    "PluginPushMessageRequest",
    "PluginPushMessage",
    "PluginPushMessageResponse",
]
