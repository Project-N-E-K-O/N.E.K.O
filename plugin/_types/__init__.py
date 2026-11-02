"""
Unified type definitions for the plugin system

Provides common types and exceptions used by the plugin core.
This is the types/ layer entry point; import all type definitions from here.

Usage:
    from plugin._types import (
        # Error codes
        ErrorCode, ERROR_NAMES, get_error_name, get_http_status,
        # Exceptions
        PluginError, PluginNotFoundError, PluginTimeoutError,
        # Events
        EventMeta, EventHandler, EventType, EVENT_META_ATTR,
        # Protocol
        PluginContextProtocol,
        # Models
        PluginMeta, PluginAuthor, PluginDependency,
        # Version
        SDK_VERSION,
    )
"""

# Version
from .version import SDK_VERSION

# Error codes
from .errors import (
    ErrorCode,
    ERROR_NAMES,
    get_error_name,
    get_http_status,
)

# Exceptions
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

# Events
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

# Models
from .plugin_types import (
    PluginType,
    SCAFFOLDABLE_PLUGIN_TYPES,
    SUPPORTED_PLUGIN_TYPES,
)

# .models contains server-API pydantic models and takes over 200 ms to import; plugin subprocesses entering
# through lightweight plugin._types submodules (such as bus_sort) do not need it, so import on demand.
_LAZY_MODEL_EXPORTS = frozenset({
    "RunStatus",
    "RunCreateRequest",
    "RunCreateResponse",
    "PluginAuthor",
    "PluginDependency",
    "PluginMeta",
    "HealthCheckResponse",
    "PluginPushMessageRequest",
    "PluginPushMessage",
    "PluginPushMessageResponse",
})


def __getattr__(name):
    if name in _LAZY_MODEL_EXPORTS:
        from . import models

        value = getattr(models, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _LAZY_MODEL_EXPORTS)

__all__ = [
    # Version
    "SDK_VERSION",
    # Error codes
    "ErrorCode",
    "ERROR_NAMES",
    "get_error_name",
    "get_http_status",
    # Exceptions
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
    # Events
    "EVENT_META_ATTR",
    "STANDARD_EVENT_TYPES",
    "StandardEventType",
    "EventType",
    "EventMeta",
    "EventHandler",
    # Protocol
    "PluginContextProtocol",
    # Models
    "RunStatus",
    "RunCreateRequest",
    "RunCreateResponse",
    "PluginAuthor",
    "PluginDependency",
    "PluginType",
    "SUPPORTED_PLUGIN_TYPES",
    "SCAFFOLDABLE_PLUGIN_TYPES",
    "PluginMeta",
    "HealthCheckResponse",
    "PluginPushMessageRequest",
    "PluginPushMessage",
    "PluginPushMessageResponse",
]
