"""
Plugin 模块

提供插件系统的核心功能和SDK。
"""

from plugin.core.state import state, PluginRuntimeState
from plugin.core.context import PluginContext
from plugin.runtime.status import status_manager, PluginStatusManager
from plugin.runtime.registry import (
    load_plugins_from_toml,
    get_plugins,
    register_plugin,
    scan_static_metadata,
)
from plugin.runtime.host import PluginProcessHost
from plugin.runtime.communication import PluginCommunicationResourceManager
from plugin.api.models import (
    PluginTriggerRequest,
    PluginTriggerResponse,
    PluginPushMessageRequest,
    PluginPushMessage,
    PluginPushMessageResponse,
    PluginMeta,
    HealthCheckResponse,
)
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
from plugin.sdk.base import NekoPluginBase, PluginMeta as SDKPluginMeta, NEKO_PLUGIN_TAG, NEKO_PLUGIN_META_ATTR
from plugin.sdk.events import (
    EventMeta,
    EventHandler,
    EventType,
    EVENT_META_ATTR,
)
from plugin.sdk.logger import (
    PluginFileLogger,
    enable_plugin_file_logging,
    plugin_file_logger,
)
from plugin.settings import EVENT_QUEUE_MAX, MESSAGE_QUEUE_MAX
from plugin.sdk.decorators import (
    neko_plugin,
    on_event,
    plugin_entry,
    lifecycle,
    message,
    timer_interval,
)

__all__ = [
    # Core
    'state',
    'PluginRuntimeState',
    'PluginContext',
    # Runtime
    'status_manager',
    'PluginStatusManager',
    'load_plugins_from_toml',
    'get_plugins',
    'register_plugin',
    'scan_static_metadata',
    'PluginProcessHost',
    'PluginCommunicationResourceManager',
    # API
    'PluginTriggerRequest',
    'PluginTriggerResponse',
    'PluginPushMessageRequest',
    'PluginPushMessage',
    'PluginPushMessageResponse',
    'PluginMeta',
    'HealthCheckResponse',
    # Exceptions
    'PluginError',
    'PluginNotFoundError',
    'PluginNotRunningError',
    'PluginTimeoutError',
    'PluginExecutionError',
    'PluginCommunicationError',
    'PluginLoadError',
    'PluginImportError',
    'PluginLifecycleError',
    'PluginTimerError',
    'PluginEntryNotFoundError',
    'PluginMetadataError',
    'PluginQueueError',
    # SDK
    'NekoPluginBase',
    'SDKPluginMeta',
    'NEKO_PLUGIN_TAG',
    'NEKO_PLUGIN_META_ATTR',
    'EventMeta',
    'EventHandler',
    'EventType',
    'EVENT_META_ATTR',
    'neko_plugin',
    'on_event',
    'plugin_entry',
    'lifecycle',
    'message',
    'timer_interval',
    # Logger
    'PluginFileLogger',
    'enable_plugin_file_logging',
    'plugin_file_logger',
]

