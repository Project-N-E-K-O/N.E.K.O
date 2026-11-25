"""Plugin application services.

Package-level service classes are exported on demand (PEP 562). Importing any submodule, such as the metadata scanner
worker bootstrap (``from plugin.server.application.plugins.metadata_scanner import _worker_main``), executes this file
first; eagerly importing the services here would pull FastAPI and pydantic into every metadata scan subprocess.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "PluginQueryService": ("plugin.server.application.plugins.query_service", "PluginQueryService"),
    "PluginLifecycleService": ("plugin.server.application.plugins.lifecycle_service", "PluginLifecycleService"),
    "PluginRegistryService": ("plugin.server.application.plugins.registry_service", "PluginRegistryService"),
    "PluginUiQueryService": ("plugin.server.application.plugins.ui_query_service", "PluginUiQueryService"),
    "PluginRouterQueryService": (
        "plugin.server.application.plugins.router_query_service",
        "PluginRouterQueryService",
    ),
    "PluginDispatchService": ("plugin.server.application.plugins.dispatch_service", "PluginDispatchService"),
    "PluginHotReloadService": ("plugin.server.application.plugins.hot_reload_service", "PluginHotReloadService"),
}

__all__ = [
    "PluginQueryService",
    "PluginLifecycleService",
    "PluginRegistryService",
    "PluginUiQueryService",
    "PluginRouterQueryService",
    "PluginDispatchService",
    "PluginHotReloadService",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        # AttributeError (not ImportError) lets ``from <package> import <submodule>`` fall back to importing it.
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
