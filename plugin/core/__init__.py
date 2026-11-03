"""
Plugin Core module

Provides core runtime state, context, process management, and plugin registration.
Incorporates the former runtime/ module's functionality.

Import package-level names on demand (PEP 562): plugin subprocesses entering through ``plugin.core.host`` execute this
file first; eagerly importing registry / communication etc. here adds import time and memory to every plugin process.
"""

from __future__ import annotations

import importlib
from typing import Any

# ``state`` shares its name with submodule plugin.core.state and must import eagerly: otherwise
# ``import plugin.core.state`` binds the package's ``state`` attribute to the submodule,
# and ``from plugin.core import state`` no longer returns the GlobalState instance.
from plugin.core.state import GlobalState, state

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "PluginContext": ("plugin.core.context", "PluginContext"),
    # State manager.
    "status_manager": ("plugin.core.status", "status_manager"),
    "PluginStatusManager": ("plugin.core.status", "PluginStatusManager"),
    # Registry.
    "load_plugins_from_toml": ("plugin.core.registry", "load_plugins_from_toml"),
    "get_plugins": ("plugin.core.registry", "get_plugins"),
    "register_plugin": ("plugin.core.registry", "register_plugin"),
    "scan_static_metadata": ("plugin.core.registry", "scan_static_metadata"),
    # Process management.
    "PluginHost": ("plugin.core.host", "PluginHost"),
    "PluginProcessHost": ("plugin.core.host", "PluginProcessHost"),
    "PluginCommunicationResourceManager": ("plugin.core.communication", "PluginCommunicationResourceManager"),
}

__all__ = [
    # State management.
    "state",
    "GlobalState",
    *_LAZY_EXPORTS,
]


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
