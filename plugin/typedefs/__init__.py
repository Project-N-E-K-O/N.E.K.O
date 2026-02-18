"""
插件系统统一类型定义

提供所有公共类型、异常、Result 类型的统一导出。

Usage:
    from plugin.typedefs import (
        # Result 类型
        Ok, Err, Result, ErrorCode, safe, async_safe,
        # 异常
        PluginError, PluginNotFoundError, PluginTimeoutError,
        # Protocol
        PluginContextProtocol,
    )
"""

# 统一错误码（从 errors.py 导出）
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

# 从现有位置重导出异常（保持向后兼容）
from plugin.api.exceptions import (
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

# 从现有位置重导出 Protocol（保持向后兼容）
from plugin.sdk.types import PluginContextProtocol

__all__ = [
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
    # Protocol
    "PluginContextProtocol",
]
