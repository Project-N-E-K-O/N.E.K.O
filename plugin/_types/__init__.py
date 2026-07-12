"""
插件系统统一类型定义

提供插件核心使用的公共类型和异常。
这是 types/ 层的入口，所有类型定义都应该从这里导入。

Usage:
    from plugin._types import (
        # 错误码
        ErrorCode, ERROR_NAMES, get_error_name, get_http_status,
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
