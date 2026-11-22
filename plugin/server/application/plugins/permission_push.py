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

A write that is later undone (the consent flow mirrors its decision before
writing the consent file and restores the record if that fails) is pushed
too, so a plugin may briefly see the undone state; the restore is a write
of its own and is pushed after it.

Retries: when the host table is busy the plugin is looked up again shortly;
when the snapshot had to fall back because the grant store could not be
read, it is rebuilt later with a growing delay until a read succeeds. Each
plugin is pushed by its own task, so a plugin whose downlink is stuck holds
up only its own later updates.
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
# Host table busy (a start or stop holds it): look again after this long, up
# to this many times in a row before giving up on that update.
HOSTS_BUSY_RETRY_SECONDS = 0.2
HOSTS_BUSY_MAX_RETRIES = 25
# Grant store unreadable: rebuild the snapshot after this long, doubling up to
# the cap, until a read succeeds.
READ_RETRY_SECONDS = 5.0
READ_RETRY_MAX_SECONDS = 60.0

SENT = "sent"
SENT_FALLBACK = "sent_fallback"  # sent, but built without reading the store
NOT_RUNNING = "not_running"
HOSTS_BUSY = "hosts_busy"
FAILED = "failed"


async def push_permissions(plugin_id: str, host: Any | None = None) -> bool:
    """Send ``plugin_id``'s current snapshot to its running process.

    Returns whether it was sent; a plugin that is not running is not an
    error. A failed send is logged and never raises.
    """
    return await _deliver(plugin_id, host) in (SENT, SENT_FALLBACK)


async def _deliver(plugin_id: str, host: Any | None = None) -> str:
    from plugin.server.application.plugins.permission_service import permission_snapshot

    if host is None:
        # Never waits (safe on the loop) and never mistakes a busy host table
        # for "no plugins running": None means the lock was not available.
        hosts = state.get_plugin_hosts_snapshot_nowait()
        if hosts is None:
            return HOSTS_BUSY
        host = hosts.get(plugin_id)
    push = getattr(host, "push_permissions", None)
    if host is None or not callable(push):
        return NOT_RUNNING
    try:
        if not host.is_alive():
            return NOT_RUNNING
    except Exception:
        return NOT_RUNNING
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
        return FAILED
    return SENT_FALLBACK if snapshot.get("read_failed") else SENT


class PermissionPushService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._all = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wakeup: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        # Loop-thread only.
        self._inflight: dict[str, asyncio.Task] = {}
        self._rerun: set[str] = set()
        self._busy_retries: dict[str, int] = {}
        self._read_retry_delay: dict[str, float] = {}

    def start(self) -> None:
        """Start on the running (server) loop; idempotent."""
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._loop is loop:
            return
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
        tasks = [t for t in (task, *self._inflight.values()) if t is not None and not t.done()]
        self._inflight.clear()
        self._rerun.clear()
        self._busy_retries.clear()
        self._read_retry_delay.clear()
        for pending in tasks:
            pending.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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

    def _notify_later(self, delay: float, plugin_ids: frozenset[str] | None) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_later(delay, self.notify, plugin_ids)

    def _take(self) -> set[str] | None:
        """Pending ids, or None while "every plugin" cannot be resolved yet."""
        with self._lock:
            pending, self._pending = self._pending, set()
            everyone, self._all = self._all, False
        if everyone:
            hosts = state.get_plugin_hosts_snapshot_nowait()
            if hosts is None:
                count = self._busy_retries.get("*", 0) + 1
                self._busy_retries["*"] = count
                if count <= HOSTS_BUSY_MAX_RETRIES:
                    self._notify_later(HOSTS_BUSY_RETRY_SECONDS, None)
                else:
                    self._busy_retries.pop("*", None)
                    logger.error("permission update to every running plugin dropped: host table stayed busy")
            else:
                self._busy_retries.pop("*", None)
                pending |= set(hosts)
        return pending

    def _spawn(self, plugin_id: str) -> None:
        running = self._inflight.get(plugin_id)
        if running is not None and not running.done():
            # The running push may have read the store before this change.
            self._rerun.add(plugin_id)
            return
        task = asyncio.get_running_loop().create_task(self._push_one(plugin_id))
        self._inflight[plugin_id] = task
        task.add_done_callback(lambda done, p=plugin_id: self._finished(p, done))

    def _finished(self, plugin_id: str, task: asyncio.Task) -> None:
        if self._inflight.get(plugin_id) is task:
            del self._inflight[plugin_id]
        if plugin_id in self._rerun and self._task is not None:
            self._rerun.discard(plugin_id)
            self._spawn(plugin_id)

    async def _push_one(self, plugin_id: str) -> None:
        try:
            outcome = await _deliver(plugin_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "permission update failed: plugin_id={}, error={}",
                plugin_id,
                type(exc).__name__,
            )
            return
        if outcome == HOSTS_BUSY:
            count = self._busy_retries.get(plugin_id, 0) + 1
            self._busy_retries[plugin_id] = count
            if count <= HOSTS_BUSY_MAX_RETRIES:
                self._notify_later(HOSTS_BUSY_RETRY_SECONDS, frozenset({plugin_id}))
            else:
                self._busy_retries.pop(plugin_id, None)
                logger.error(
                    "permission update dropped: host table stayed busy, plugin_id={}", plugin_id
                )
            return
        self._busy_retries.pop(plugin_id, None)
        if outcome == SENT_FALLBACK:
            delay = self._read_retry_delay.get(plugin_id, READ_RETRY_SECONDS)
            self._read_retry_delay[plugin_id] = min(delay * 2, READ_RETRY_MAX_SECONDS)
            self._notify_later(delay, frozenset({plugin_id}))
        else:
            self._read_retry_delay.pop(plugin_id, None)

    async def _run(self) -> None:
        wakeup = self._wakeup
        assert wakeup is not None
        while True:
            await wakeup.wait()
            wakeup.clear()
            try:
                pending = self._take()
                for plugin_id in sorted(pending or ()):
                    self._spawn(plugin_id)
            except Exception as exc:
                # Keep serving later changes; this batch is reported, not retried.
                logger.error("permission update batch failed: error={}", type(exc).__name__)


permission_push_service = PermissionPushService()
