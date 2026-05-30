"""Startup reconciler for the install-source subsystem (design §4.5 / §6.1).

:class:`StartupReconciler` is the single lifespan hook that bridges the
rest of the install-source module into the FastAPI application lifecycle.
It owns exactly one side-effect: call :meth:`InstallSourceManager.load`
followed by :meth:`InstallSourceManager.reconcile`, in that order.

The **critical invariant** this module enforces is Req 17.1: failures in
the install-source subsystem must never propagate out of startup. A
broken lock file, an unreadable config directory, or even an unexpected
bug in the parser must degrade the subsystem silently — the rest of the
plugin server (which can happily serve without install-source
attribution) has to keep booting. Any exception raised by ``load`` or
``reconcile`` is therefore caught, logged at ERROR with a traceback, and
swallowed.

Note on blocking: ``load`` / ``reconcile`` / ``save`` inside
:class:`~plugin.server.application.install_source.manager.InstallSourceManager`
are synchronous — they acquire a :class:`threading.RLock` and perform
blocking filesystem IO. We offload them to a worker thread via
``asyncio.to_thread`` so the FastAPI event loop is not blocked during
``lifespan`` startup (design §4.6 and the existing ``PluginCliService``
pattern both use the same offload convention).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from plugin.logging_config import get_logger

if TYPE_CHECKING:
    # Imported only for type-checking to avoid pulling the full manager
    # module at import time (startup order sensitivity — the reconciler
    # module itself must be cheap to import).
    from plugin.server.application.install_source.manager import InstallSourceManager

logger = get_logger("server.application.install_source")


class StartupReconciler:
    """Run ``manager.load()`` then ``manager.reconcile()`` during lifespan.

    Usage (see design §6.1)::

        mgr = build_install_source_manager()
        await StartupReconciler(mgr).run()
        set_global_manager(mgr)

    The caller is responsible for constructing the manager and publishing
    it as the global singleton afterwards — the reconciler only drives
    the ``load → reconcile`` sequence on an instance it is handed.
    """

    def __init__(self, manager: "InstallSourceManager") -> None:
        self.manager = manager

    async def run(self) -> None:
        """Drive the ``load → reconcile`` sequence; swallow all errors.

        Covers:

        * Req 6.1 / 7.1 / 7.5 — the lock file must be loaded and the
          disk must be reconciled against it at lifespan startup.
        * Req 14.1 / 14.2 — parser-level corruption is already handled
          inside ``manager.load`` (it renames the bad file and rebuilds
          via First_Startup), but any *further* unexpected failure here
          must still not crash startup.
        * Req 17.1 — the subsystem must never block application startup.
          Everything except ``asyncio.CancelledError`` is caught; cancel
          is re-raised so a real cancellation of the lifespan task still
          propagates.

        The two manager calls are offloaded via ``asyncio.to_thread``
        because they are synchronous and grab a ``threading.RLock``; we
        don't want them to pin the event loop during startup.
        """

        try:
            await asyncio.to_thread(self.manager.load)
            await asyncio.to_thread(self.manager.reconcile)
        except asyncio.CancelledError:
            # A cancellation of the lifespan task is not a subsystem
            # failure — let it bubble so the shutdown path sees it.
            raise
        except Exception as exc:  # noqa: BLE001 — Req 17.1 demands a blanket catch
            logger.error(
                "StartupReconciler: load/reconcile failed, install-source "
                "subsystem will run in degraded mode: %s",
                exc,
                exc_info=True,
            )
