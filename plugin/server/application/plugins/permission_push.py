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
NOT_RUNNING = "not_running"
HOSTS_BUSY = "hosts_busy"
FAILED = "failed"


def _running_hosts() -> dict[str, Any] | None:
    """A fresh copy of the host table, or None if it stayed locked.

    Read under the hosts lock every time: the shared snapshot cache may hand
    back a table from before a start or stop while the lock is held, which
    would make a running plugin look stopped. Blocks up to a second, so call
    it off the event loop.
    """
    try:
        with state.acquire_plugin_hosts_read_lock(timeout=1.0):
            return dict(state.plugin_hosts)
    except TimeoutError:
        return None


async def push_permissions(plugin_id: str, host: Any | None = None) -> bool:
    """Send ``plugin_id``'s current snapshot to its running process.

    Returns whether it was sent; a plugin that is not running is not an
    error. A failed send is logged and never raises.
    """
    outcome, _read_failed = await _deliver(plugin_id, host)
    return outcome == SENT


async def _deliver(plugin_id: str, host: Any | None = None) -> tuple[str, bool]:
    """Push once; returns the outcome and whether the snapshot had to be
    built without reading the grant store."""
    from plugin.server.application.plugins.permission_service import permission_snapshot

    if host is None:
        hosts = await asyncio.to_thread(_running_hosts)
        if hosts is None:
            return HOSTS_BUSY, False
        host = hosts.get(plugin_id)
    push = getattr(host, "push_permissions", None)
    if host is None or not callable(push):
        return NOT_RUNNING, False
    try:
        if not host.is_alive():
            return NOT_RUNNING, False
    except Exception:
        return NOT_RUNNING, False
    snapshot = await asyncio.to_thread(permission_snapshot, plugin_id)
    read_failed = bool(snapshot.get("read_failed"))
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
        return FAILED, read_failed
    return SENT, read_failed


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
        # At most one pending retry per plugin ("*" = every running plugin).
        self._timers: dict[str, asyncio.TimerHandle] = {}

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
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
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

    def _retry_later(self, key: str, delay: float) -> None:
        """Schedule one retry for ``key``; an earlier pending one is kept."""
        loop = self._loop
        if loop is None:
            return
        existing = self._timers.get(key)
        if existing is not None:
            if existing.when() <= loop.time() + delay:
                return
            existing.cancel()
        ids = None if key == "*" else frozenset({key})
        self._timers[key] = loop.call_later(delay, self._fire, key, ids)

    def _fire(self, key: str, ids: frozenset[str] | None) -> None:
        self._timers.pop(key, None)
        self.notify(ids)

    def _cancel_retry(self, key: str) -> None:
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    async def _take(self) -> set[str]:
        with self._lock:
            pending, self._pending = self._pending, set()
            everyone, self._all = self._all, False
        if everyone:
            hosts = await asyncio.to_thread(_running_hosts)
            if hosts is None:
                self._busy("*")
            else:
                self._busy_retries.pop("*", None)
                pending |= set(hosts)
        return pending

    def _busy(self, key: str) -> None:
        count = self._busy_retries.get(key, 0) + 1
        self._busy_retries[key] = count
        if count <= HOSTS_BUSY_MAX_RETRIES:
            self._retry_later(key, HOSTS_BUSY_RETRY_SECONDS)
            return
        self._busy_retries.pop(key, None)
        if key == "*":
            logger.error("permission update to every running plugin dropped: host table stayed busy")
        else:
            logger.error("permission update dropped: host table stayed busy, plugin_id={}", key)

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
            outcome, read_failed = await _deliver(plugin_id)
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
            self._busy(plugin_id)
            return
        self._busy_retries.pop(plugin_id, None)
        if outcome == NOT_RUNNING:
            self._cancel_retry(plugin_id)
            self._read_retry_delay.pop(plugin_id, None)
            return
        if read_failed:
            # Sent or not, the plugin needs a snapshot read from the store:
            # rebuild later with a growing delay until a read works.
            delay = self._read_retry_delay.get(plugin_id, READ_RETRY_SECONDS)
            self._read_retry_delay[plugin_id] = min(delay * 2, READ_RETRY_MAX_SECONDS)
            self._retry_later(plugin_id, delay)
        else:
            self._read_retry_delay.pop(plugin_id, None)
            if outcome == SENT:
                self._cancel_retry(plugin_id)

    async def _run(self) -> None:
        wakeup = self._wakeup
        assert wakeup is not None
        while True:
            await wakeup.wait()
            wakeup.clear()
            try:
                for plugin_id in sorted(await self._take()):
                    self._spawn(plugin_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep serving later changes; this batch is reported, not retried.
                logger.error("permission update batch failed: error={}", type(exc).__name__)


permission_push_service = PermissionPushService()
