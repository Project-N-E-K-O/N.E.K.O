"""Keep each running plugin process's ``ctx.permissions`` copy current.

Every write to the permission store reports the plugins it changed (see
``plugin_permissions.add_change_listener``). This service collects them and,
on the server loop, sends each running plugin a fresh snapshot built from the
store at send time.

Ordering: a snapshot is numbered before it reads the store and the plugin
process keeps only the highest number it has seen, and every write schedules
a push that starts after the write. So whatever order pushes run or arrive
in, the last snapshot a plugin keeps was read after the last write. Writers
therefore do not need to hold any lock while pushing, and the start path
(which sends its snapshot with the launch options, before the host is
registered here) pushes once more after registering.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.infrastructure import plugin_permissions

logger = get_logger("server.plugins.permission_push")

PUSH_TIMEOUT_SECONDS = 5.0


async def push_permissions(plugin_id: str, host: Any | None = None) -> bool:
    """Send ``plugin_id``'s current snapshot to its running process.

    Returns whether it was sent; a plugin that is not running is not an
    error. A failed send is logged and never raises.
    """
    from plugin.server.application.plugins.permission_service import permission_snapshot

    if host is None:
        host = state.get_plugin_hosts_snapshot_cached(timeout=1.0).get(plugin_id)
    push = getattr(host, "push_permissions", None)
    if host is None or not callable(push):
        return False
    try:
        if not host.is_alive():
            return False
    except Exception:
        return False
    snapshot = await asyncio.to_thread(permission_snapshot, plugin_id)
    try:
        await asyncio.wait_for(push(snapshot), timeout=PUSH_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "permission update was not delivered to the plugin process: plugin_id={}, revision={}, error={}",
            plugin_id,
            snapshot.get("revision"),
            type(exc).__name__,
        )
        return False
    return True


class PermissionPushService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._all = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wakeup: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start on the running (server) loop; idempotent."""
        if self._task is not None and not self._task.done():
            return
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop
            self._wakeup = asyncio.Event()
        self._task = loop.create_task(self._run(), name="plugin-permission-push")
        plugin_permissions.add_change_listener(self.notify)

    async def stop(self) -> None:
        plugin_permissions.remove_change_listener(self.notify)
        task, self._task = self._task, None
        with self._lock:
            self._loop = None
            self._pending.clear()
            self._all = False
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def notify(self, plugin_ids: frozenset[str] | None) -> None:
        """Store change listener: may run on any thread, only schedules."""
        with self._lock:
            loop, wakeup = self._loop, self._wakeup
            if loop is None or wakeup is None:
                return
            if plugin_ids is None:
                self._all = True
            else:
                self._pending.update(plugin_ids)
        try:
            loop.call_soon_threadsafe(wakeup.set)
        except RuntimeError:
            # The loop is closing; the plugins are going down with it.
            logger.warning("permission update not scheduled: server loop is closed")

    def _take(self) -> set[str]:
        with self._lock:
            pending, self._pending = self._pending, set()
            everyone, self._all = self._all, False
        if everyone:
            pending |= set(state.get_plugin_hosts_snapshot_cached(timeout=1.0, force=True))
        return pending

    async def _run(self) -> None:
        wakeup = self._wakeup
        assert wakeup is not None
        while True:
            await wakeup.wait()
            wakeup.clear()
            for plugin_id in sorted(self._take()):
                try:
                    await push_permissions(plugin_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "permission update failed: plugin_id={}, error={}",
                        plugin_id,
                        type(exc).__name__,
                    )


permission_push_service = PermissionPushService()
