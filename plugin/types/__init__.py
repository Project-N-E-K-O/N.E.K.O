"""
插件系统统一类型定义

提供所有公共类型、异常、Result 类型的统一导出。

Usage:
    from plugin.types import (
        # Result 类型
        Ok, Err, Result, ErrorCode, safe, async_safe,
        # 异常
        PluginError, PluginNotFoundError, PluginTimeoutError,
        # Protocol
        PluginContextProtocol,
    )
"""

from .result import (
    Ok,
    Err,
    Result,
    ErrorCode,
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
    # Result 类型
    "Ok",
    "Err",
    "Result",
    "ErrorCode",
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
