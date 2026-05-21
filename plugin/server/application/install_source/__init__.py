"""Plugin install source lock — see manager.py.

Public API re-exports plus the global-manager singleton (set once at
lifespan startup by ``StartupReconciler``).

The singleton pattern is used because ``PluginCliService`` and
``market_bridge`` are both module-level instances; a DI container would be
out of scope for this feature, so we expose a minimal get/set API.
"""

from __future__ import annotations

import threading

from plugin.server.application.install_source.manager import (
    InstallSourceError,
    InstallSourceManager,
    _DEFAULT_INSTALL_SOURCE,
    classify_plugin_path,
    resolve_lock_path,
)
from plugin.server.application.install_source.models import (
    LockEntry,
    LockFile,
    SourceDetailImported,
    SourceDetailMarket,
)
from plugin.server.application.install_source.reconciler import StartupReconciler
from plugin.server.application.install_source.scanner import (
    DiscoveredPlugin,
    PluginDirectoryScanner,
)

_GLOBAL_MANAGER: InstallSourceManager | None = None
_GLOBAL_LOCK: threading.RLock = threading.RLock()


def set_global_manager(mgr: InstallSourceManager | None) -> None:
    """Publish (or clear) the global manager singleton.

    Called once from the FastAPI lifespan after
    :class:`StartupReconciler` has run. Passing ``None`` puts every
    subsequent :func:`get_install_source_manager` caller into the
    degraded-default branch.
    """

    global _GLOBAL_MANAGER
    with _GLOBAL_LOCK:
        _GLOBAL_MANAGER = mgr


def get_install_source_manager() -> InstallSourceManager | None:
    """Return the current global manager or ``None`` if not initialised."""

    return _GLOBAL_MANAGER


def build_install_source_manager() -> InstallSourceManager:
    """Factory: build an :class:`InstallSourceManager` using default roots.

    ``plugin.settings`` is imported lazily so this module stays side-effect
    free at import time and tests can override ``PLUGIN_CONFIG_ROOT``
    at runtime.
    """

    from plugin.settings import (
        get_builtin_plugin_config_root,
        get_user_plugin_config_root,
    )

    builtin_root = get_builtin_plugin_config_root()
    user_root = get_user_plugin_config_root()
    scanner = PluginDirectoryScanner(builtin_root, user_root)
    lock_path = resolve_lock_path()
    return InstallSourceManager(
        lock_path=lock_path,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=scanner,
    )


__all__ = [
    "DiscoveredPlugin",
    "InstallSourceError",
    "InstallSourceManager",
    "LockEntry",
    "LockFile",
    "PluginDirectoryScanner",
    "SourceDetailImported",
    "SourceDetailMarket",
    "StartupReconciler",
    "_DEFAULT_INSTALL_SOURCE",
    "build_install_source_manager",
    "classify_plugin_path",
    "get_install_source_manager",
    "resolve_lock_path",
    "set_global_manager",
]
