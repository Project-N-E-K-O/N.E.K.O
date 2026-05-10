"""Plugin install source lock — see design §4.

Public API re-exports plus the module-level global-manager singleton and
its 60s self-heal scheduler (design §4.6 / §6.5 / Fix 9).

Callers outside the subsystem should import from ``install_source`` rather
than reaching into the individual modules::

    from plugin.server.application.install_source import (
        build_install_source_manager,
        get_install_source_manager,
        set_global_manager,
        StartupReconciler,
    )

The singleton is used because ``PluginCliService`` and ``market_bridge``
are both module-level instances (design §4.6): retrofitting a DI container
would be out of scope for this feature, so we expose a tiny get/set API
and trust callers to set it at lifespan startup.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from plugin.logging_config import get_logger
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

logger = get_logger("server.application.install_source")

# Retry interval for degrade recovery (design §6.5 / Fix 9). A degraded
# manager attempts ``try_recover`` at most once per interval when
# ``get_install_source_manager`` is called. Kept as a module constant so
# tests can monkeypatch it when they need tighter timing.
_RECOVER_INTERVAL_SECONDS: int = 60

# The global singleton and a lock to serialise writers. Reads are
# intentionally lockless (single attribute dereference is atomic under
# the GIL) so the hot ``get_install_source_manager`` path stays cheap.
_GLOBAL_MANAGER: InstallSourceManager | None = None
_GLOBAL_LOCK: threading.RLock = threading.RLock()


def set_global_manager(mgr: InstallSourceManager | None) -> None:
    """Publish (or clear) the global manager singleton.

    Called once from the FastAPI lifespan after
    :class:`StartupReconciler` has run. Passing ``None`` puts every
    subsequent :func:`get_install_source_manager` caller into the
    degraded-default branch (Req 17.2 / 17.3).
    """

    global _GLOBAL_MANAGER
    with _GLOBAL_LOCK:
        _GLOBAL_MANAGER = mgr


def get_install_source_manager() -> InstallSourceManager | None:
    """Return the current global manager or ``None`` if not initialised.

    Implements the Fix 9 "60s self-heal" window: when the manager is
    degraded we invoke :meth:`InstallSourceManager.try_recover` provided
    at least ``_RECOVER_INTERVAL_SECONDS`` seconds have passed since the
    last attempt. ``try_recover`` itself already takes the manager's
    ``_lock``, stamps ``_last_recover_attempt`` regardless of outcome,
    and catches internal ``load`` errors — our only job here is to
    rate-limit calls and swallow any residual exception so a broken
    subsystem can never take down the caller (usually a hot HTTP
    handler or the ``/plugins`` response builder).
    """

    mgr = _GLOBAL_MANAGER  # single atomic reference read
    if mgr is None:
        return None
    if mgr.is_degraded:
        now = datetime.now(UTC)
        last = mgr._last_recover_attempt  # noqa: SLF001 — internal access by design
        if last is None or (now - last).total_seconds() >= _RECOVER_INTERVAL_SECONDS:
            try:
                mgr.try_recover()
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.warning(
                    "get_install_source_manager: try_recover raised %s", exc
                )
    return mgr


def build_install_source_manager() -> InstallSourceManager:
    """Factory: build an :class:`InstallSourceManager` using default roots.

    Resolves:

    * ``lock_path`` — via :func:`resolve_lock_path` (honours
      ``NEKO_PLUGIN_INSTALL_LOCK_PATH`` or falls back to
      ``<USER_PLUGIN_CONFIG_ROOT parent>/plugins.lock.json``).
    * ``builtin_root`` / ``user_root`` — via the corresponding
      ``plugin.settings`` helpers. Imported lazily so this module does
      not touch settings at import time — matching the
      :func:`resolve_lock_path` convention and letting tests override
      ``PLUGIN_CONFIG_ROOT`` at runtime.
    * ``scanner`` — a fresh :class:`PluginDirectoryScanner` bound to
      the resolved roots.
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
