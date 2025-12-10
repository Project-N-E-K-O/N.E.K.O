"""
Plugin 模块 - 向后兼容导入

提供向后兼容的导入路径，允许旧代码继续工作。
"""

# 向后兼容：从旧路径导入
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
from plugin.sdk.decorators import (
    neko_plugin,
    on_event,
    plugin_entry,
    lifecycle,
    message,
    timer_interval,
)

# 向后兼容：提供旧模块路径的别名
import sys
import types

# 创建虚拟模块以支持旧导入路径
_old_modules = {
    'plugin.server_base': types.ModuleType('plugin.server_base'),
    'plugin.event_base': types.ModuleType('plugin.event_base'),
    'plugin.plugin_base': types.ModuleType('plugin.plugin_base'),
    'plugin.resource_manager': types.ModuleType('plugin.resource_manager'),
}

# 填充虚拟模块
_old_modules['plugin.server_base'].state = state
_old_modules['plugin.server_base'].PluginRuntimeState = PluginRuntimeState
_old_modules['plugin.server_base'].PluginContext = PluginContext
_old_modules['plugin.server_base'].EVENT_QUEUE_MAX = 1000
_old_modules['plugin.server_base'].MESSAGE_QUEUE_MAX = 1000

_old_modules['plugin.event_base'].EventMeta = EventMeta
_old_modules['plugin.event_base'].EventHandler = EventHandler
_old_modules['plugin.event_base'].EventType = EventType
_old_modules['plugin.event_base'].EVENT_META_ATTR = EVENT_META_ATTR

_old_modules['plugin.plugin_base'].NekoPluginBase = NekoPluginBase
_old_modules['plugin.plugin_base'].PluginMeta = SDKPluginMeta
_old_modules['plugin.plugin_base'].NEKO_PLUGIN_TAG = NEKO_PLUGIN_TAG
_old_modules['plugin.plugin_base'].NEKO_PLUGIN_META_ATTR = NEKO_PLUGIN_META_ATTR

_old_modules['plugin.resource_manager'].PluginCommunicationResourceManager = PluginCommunicationResourceManager

# 注册虚拟模块
for name, module in _old_modules.items():
    sys.modules[name] = module

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
]

