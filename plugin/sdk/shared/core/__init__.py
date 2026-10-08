"""Shared core building blocks for SDK v2.

This package is mixed: most modules are implemented, while a few helper modules
remain contract-only during the migration.
"""

from importlib import import_module

# Loading a path helper or an entry contract must not initialize the entire SDK.
# The facade keeps the same objects and star-import surface as before.
_EXPORT_GROUPS = {
    ".base": ("NEKO_PLUGIN_META_ATTR", "NEKO_PLUGIN_TAG", "NekoPluginBase", "PluginMeta"),
    ".config": ("PluginConfig", "PluginConfigBaseView", "PluginConfigProfiles"),
    "plugin.sdk.shared.models.exceptions": (
        "ConfigPathError", "ConfigProfileError", "ConfigValidationError", "PluginConfigError"
    ),
    ".decorators": (
        "PERSIST_ATTR", "EntryKind", "HookDecoratorMeta", "after_entry", "around_entry",
        "before_entry", "custom_event", "hook", "lifecycle", "message", "neko_plugin",
        "on_event", "plugin", "plugin_entry", "replace_entry", "timer_interval"
    ),
    ".context": ("SdkContext", "ensure_sdk_context"),
    ".bus_context": (
        "SdkBusContext", "SdkBusConversationRecord", "SdkBusDelta", "SdkBusEventRecord",
        "SdkBusLifecycleRecord", "SdkBusList", "SdkBusMemoryRecord", "SdkBusMessageRecord",
        "SdkBusWatcher", "ensure_sdk_bus_context"
    ),
    ".events": ("EVENT_META_ATTR", "EventHandler", "EventMeta"),
    ".hook_executor": ("HookExecutorMixin",),
    ".hooks": ("HOOK_META_ATTR", "HookHandler", "HookMeta", "HookTiming"),
    ".plugins": (
        "InvalidEntryRefError", "InvalidEventRefError", "PluginCallError", "PluginDescriptor",
        "Plugins", "parse_entry_ref", "parse_event_ref"
    ),
    ".router": ("EntryConflictError", "PluginRouter", "PluginRouterError", "RouteHandler"),
    ".types": (
        "EntryRef", "EventRef", "InputSchema", "JsonObject", "JsonScalar", "JsonValue",
        "LoggerLike", "Metadata", "MutableStateProtocol", "PluginContextProtocol",
        "PluginRef", "PushMessageResult"
    ),
}
_EXPORTS = {name: module for module, names in _EXPORT_GROUPS.items() for name in names}
# These modules were attributes of the facade after its eager imports, including
# the helpers imported transitively by those modules. Keep package.attribute
# navigation available without loading them when the package is imported.
_SUBMODULES = frozenset({
    "_facade", "base", "base_runtime", "bus_context", "cards", "config",
    "context", "decorators", "events", "finish", "hook_executor", "hooks",
    "plugins", "result_contract", "router", "types",
})


def __getattr__(name: str):
    if name in _SUBMODULES:
        value = import_module(f".{name}", __name__)
    else:
        module = _EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | _SUBMODULES)

__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
    "PluginConfig",
    "PluginConfigError",
    "ConfigPathError",
    "ConfigValidationError",
    "ConfigProfileError",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
    "Plugins",
    "PluginDescriptor",
    "PluginCallError",
    "InvalidEntryRefError",
    "InvalidEventRefError",
    "parse_entry_ref",
    "parse_event_ref",
    "PluginRouter",
    "PluginRouterError",
    "EntryConflictError",
    "RouteHandler",
    "EVENT_META_ATTR",
    "EventMeta",
    "EventHandler",
    "SdkContext",
    "ensure_sdk_context",
    "SdkBusContext",
    "SdkBusMessageRecord",
    "SdkBusEventRecord",
    "SdkBusLifecycleRecord",
    "SdkBusConversationRecord",
    "SdkBusMemoryRecord",
    "SdkBusList",
    "SdkBusWatcher",
    "SdkBusDelta",
    "ensure_sdk_bus_context",
    "HOOK_META_ATTR",
    "HookMeta",
    "HookHandler",
    "HookTiming",
    "HookExecutorMixin",
    "PluginRef",
    "EntryRef",
    "EventRef",
    "PluginContextProtocol",
    "PushMessageResult",
    "MutableStateProtocol",
    "JsonScalar",
    "JsonValue",
    "JsonObject",
    "Metadata",
    "InputSchema",
    "LoggerLike",
    "EntryKind",
    "HookDecoratorMeta",
    "PERSIST_ATTR",
    "neko_plugin",
    "on_event",
    "plugin_entry",
    "lifecycle",
    "message",
    "timer_interval",
    "custom_event",
    "plugin",
    "hook",
    "before_entry",
    "after_entry",
    "around_entry",
    "replace_entry",
]
