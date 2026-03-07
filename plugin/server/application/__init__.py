from __future__ import annotations

from importlib import import_module

__all__ = [
    "BusMutationService",
    "BusQueryService",
    "BusSubscriptionService",
    "AdminCommandService",
    "AdminQueryService",
    "LogQueryService",
    "PluginQueryService",
    "PluginLifecycleService",
    "PluginUiQueryService",
    "PluginRouterQueryService",
    "PluginDispatchService",
    "ConfigQueryService",
    "ConfigCommandService",
    "RunService",
    "RunIpcService",
    "MessageQueryService",
    "UserContextQueryService",
    "MemoryQueryService",
    "MetricsQueryService",
]

_EXPORT_MAP: dict[str, tuple[str, str]] = {
    "BusMutationService": ("plugin.server.application.bus", "BusMutationService"),
    "BusQueryService": ("plugin.server.application.bus", "BusQueryService"),
    "BusSubscriptionService": ("plugin.server.application.bus", "BusSubscriptionService"),
    "AdminCommandService": ("plugin.server.application.admin", "AdminCommandService"),
    "AdminQueryService": ("plugin.server.application.admin", "AdminQueryService"),
    "LogQueryService": ("plugin.server.application.logs", "LogQueryService"),
    "PluginQueryService": ("plugin.server.application.plugins", "PluginQueryService"),
    "PluginLifecycleService": ("plugin.server.application.plugins", "PluginLifecycleService"),
    "PluginUiQueryService": ("plugin.server.application.plugins", "PluginUiQueryService"),
    "PluginRouterQueryService": ("plugin.server.application.plugins", "PluginRouterQueryService"),
    "PluginDispatchService": ("plugin.server.application.plugins", "PluginDispatchService"),
    "ConfigQueryService": ("plugin.server.application.config", "ConfigQueryService"),
    "ConfigCommandService": ("plugin.server.application.config", "ConfigCommandService"),
    "RunService": ("plugin.server.application.runs", "RunService"),
    "RunIpcService": ("plugin.server.application.runs", "RunIpcService"),
    "MessageQueryService": ("plugin.server.application.messages", "MessageQueryService"),
    "UserContextQueryService": ("plugin.server.application.messages", "UserContextQueryService"),
    "MemoryQueryService": ("plugin.server.application.messages", "MemoryQueryService"),
    "MetricsQueryService": ("plugin.server.application.monitoring", "MetricsQueryService"),
}


def __getattr__(name: str) -> object:
    export = _EXPORT_MAP.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = export
    module = import_module(module_name)
    return getattr(module, attr_name)
