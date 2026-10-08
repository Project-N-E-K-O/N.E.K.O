"""
Plugin 模块

提供插件系统的核心功能和SDK。

包级名字按需导入（PEP 562）：``import plugin`` 本身不加载任何宿主模块。插件子进程
只需要 SDK，而导入 ``plugin.xxx`` 任意子模块都会先执行本文件——在这里全量导入
state / host / registry 会让每个插件进程白白多付约 1 秒和几十 MB。
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # Core
    "state": ("plugin.core.state", "state"),
    "GlobalState": ("plugin.core.state", "GlobalState"),
    "PluginContext": ("plugin.core.context", "PluginContext"),
    # Runtime
    "status_manager": ("plugin.core.status", "status_manager"),
    "PluginStatusManager": ("plugin.core.status", "PluginStatusManager"),
    "load_plugins_from_toml": ("plugin.core.registry", "load_plugins_from_toml"),
    "get_plugins": ("plugin.core.registry", "get_plugins"),
    "register_plugin": ("plugin.core.registry", "register_plugin"),
    "scan_static_metadata": ("plugin.core.registry", "scan_static_metadata"),
    "PluginHost": ("plugin.core.host", "PluginHost"),
    "PluginProcessHost": ("plugin.core.host", "PluginProcessHost"),
    "PluginCommunicationResourceManager": ("plugin.core.communication", "PluginCommunicationResourceManager"),
    # API
    "PluginPushMessageRequest": ("plugin._types.models", "PluginPushMessageRequest"),
    "PluginPushMessage": ("plugin._types.models", "PluginPushMessage"),
    "PluginPushMessageResponse": ("plugin._types.models", "PluginPushMessageResponse"),
    "PluginMeta": ("plugin._types.models", "PluginMeta"),
    "HealthCheckResponse": ("plugin._types.models", "HealthCheckResponse"),
    # Exceptions
    "PluginError": ("plugin._types.exceptions", "PluginError"),
    "PluginNotFoundError": ("plugin._types.exceptions", "PluginNotFoundError"),
    "PluginNotRunningError": ("plugin._types.exceptions", "PluginNotRunningError"),
    "PluginTimeoutError": ("plugin._types.exceptions", "PluginTimeoutError"),
    "PluginExecutionError": ("plugin._types.exceptions", "PluginExecutionError"),
    "PluginCommunicationError": ("plugin._types.exceptions", "PluginCommunicationError"),
    "PluginLoadError": ("plugin._types.exceptions", "PluginLoadError"),
    "PluginImportError": ("plugin._types.exceptions", "PluginImportError"),
    "PluginLifecycleError": ("plugin._types.exceptions", "PluginLifecycleError"),
    "PluginTimerError": ("plugin._types.exceptions", "PluginTimerError"),
    "PluginEntryNotFoundError": ("plugin._types.exceptions", "PluginEntryNotFoundError"),
    "PluginMetadataError": ("plugin._types.exceptions", "PluginMetadataError"),
    "PluginQueueError": ("plugin._types.exceptions", "PluginQueueError"),
    # SDK
    "NekoPluginBase": ("plugin.sdk.plugin", "NekoPluginBase"),
    "SDKPluginMeta": ("plugin.sdk.plugin", "PluginMeta"),
    "NEKO_PLUGIN_TAG": ("plugin.sdk.plugin", "NEKO_PLUGIN_TAG"),
    "NEKO_PLUGIN_META_ATTR": ("plugin.sdk.plugin", "NEKO_PLUGIN_META_ATTR"),
    "EventMeta": ("plugin._types.events", "EventMeta"),
    "EventHandler": ("plugin._types.events", "EventHandler"),
    "EventType": ("plugin._types.events", "EventType"),
    "EVENT_META_ATTR": ("plugin._types.events", "EVENT_META_ATTR"),
    "neko_plugin": ("plugin.sdk.plugin", "neko_plugin"),
    "on_event": ("plugin.sdk.plugin", "on_event"),
    "plugin_entry": ("plugin.sdk.plugin", "plugin_entry"),
    "lifecycle": ("plugin.sdk.plugin", "lifecycle"),
    "message": ("plugin.sdk.plugin", "message"),
    "timer_interval": ("plugin.sdk.plugin", "timer_interval"),
    "SystemInfo": ("plugin.sdk.plugin", "SystemInfo"),
    # Logger
    "PluginFileLogger": ("plugin.core.plugin_logger", "PluginFileLogger"),
    "enable_plugin_file_logging": ("plugin.core.plugin_logger", "enable_plugin_file_logging"),
    "plugin_file_logger": ("plugin.core.plugin_logger", "plugin_file_logger"),
    "EVENT_QUEUE_MAX": ("plugin.settings", "EVENT_QUEUE_MAX"),
    "MESSAGE_QUEUE_MAX": ("plugin.settings", "MESSAGE_QUEUE_MAX"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
