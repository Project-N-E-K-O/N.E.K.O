from __future__ import annotations

import asyncio
import threading
from collections import deque

import ormsgpack
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from plugin.logging_config import get_logger

from plugin.core.state import state
from plugin.settings import PLUGIN_LOG_BUS_SUBSCRIPTIONS, PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS

logger = get_logger("server.messaging.bus_subscriptions")

_RUNTIME_ERRORS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError)


@dataclass(frozen=True)
class BusDelta:
    bus: str
    op: str
    payload: dict[str, object]
    at: float


class BusSubscriptionManager:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._queue: asyncio.Queue[BusDelta] = asyncio.Queue(maxsize=1000)
        self._unsubs: list[object] = []
        self._last_log_key: Optional[tuple] = None
        self._last_log_time: float = 0.0
        self._last_log_repeat_count: int = 0
        # Dispatch control: bounded concurrency + slow subscriber isolation.
        self._dispatch_sem = asyncio.Semaphore(64)
        self._push_timeout_s: float = 1.0
        self._fail_threshold: int = 3
        self._pause_seconds: float = 5.0
        self._sub_failures: dict[tuple[str, str], int] = {}
        self._sub_paused_until: dict[tuple[str, str], float] = {}
        self._conversation_lock = threading.Lock()
        self._conversation_ingress: deque[tuple[str, dict, int]] = deque()
        self._conversation_ingress_bytes = 0
        self._conversation_overflow = False
        self._conversation_wakeup_scheduled = False
        self._conversation_loop: asyncio.AbstractEventLoop | None = None
        self._conversation_wakeup: asyncio.Event | None = None
        self._conversation_task: asyncio.Task | None = None
        self._conversation_workers: dict[str, asyncio.Task] = {}
        self._conversation_queues: dict[str, deque[tuple[str, str, dict, int, int]]] = {}
        self._conversation_enqueue_seq = 0
        self._conversation_pending_invalidations: dict[str, set[str]] = {}
        self._conversation_queue_bytes: dict[str, int] = {}
        self._conversation_total_count = 0
        self._conversation_total_bytes = 0
        self._conversation_send_sem = asyncio.Semaphore(64)
        self._conversation_record_observer = None
        self._conversation_fact_observer = None

    async def start(self) -> None:
        if self._task is not None:
            return
        # A stopped service can be started by another embedded/standalone loop.
        # Queue waiters and contended semaphores must not retain the old loop.
        self._queue = asyncio.Queue(maxsize=1000)
        self._dispatch_sem = asyncio.Semaphore(64)

        def _on_change_factory(bus: str):
            def _on_change(op: str, payload: dict[str, object]) -> None:
                try:
                    self._queue.put_nowait(BusDelta(bus=bus, op=str(op), payload=dict(payload or {}), at=time.time()))
                except asyncio.QueueFull:
                    return

            return _on_change

        try:
            self._unsubs.append(state.bus_change_hub.subscribe("messages", _on_change_factory("messages")))
            self._unsubs.append(state.bus_change_hub.subscribe("events", _on_change_factory("events")))
            self._unsubs.append(state.bus_change_hub.subscribe("lifecycle", _on_change_factory("lifecycle")))
            self._unsubs.append(state.bus_change_hub.subscribe("runs", _on_change_factory("runs")))
            self._unsubs.append(state.bus_change_hub.subscribe("export", _on_change_factory("export")))
        except _RUNTIME_ERRORS as err:
            logger.opt(exception=True).exception("Failed to subscribe bus_change_hub: {}", err)

        self._task = asyncio.create_task(self._loop())
        self._conversation_send_sem = asyncio.Semaphore(64)
        with self._conversation_lock:
            self._conversation_loop = asyncio.get_running_loop()
            self._conversation_wakeup = asyncio.Event()
        self._conversation_task = asyncio.create_task(self._conversation_dispatch_loop())

    async def stop(self) -> None:
        for u in list(self._unsubs):
            try:
                u()
            except _RUNTIME_ERRORS:
                pass
        self._unsubs.clear()
        with self._conversation_lock:
            self._conversation_loop = None
            self._conversation_wakeup = None
            self._conversation_ingress.clear()
            self._conversation_ingress_bytes = 0
            self._conversation_overflow = False
            self._conversation_wakeup_scheduled = False
        tasks = list(self._conversation_workers.values())
        if self._conversation_task is not None:
            tasks.append(self._conversation_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._conversation_task = None
        self._conversation_workers.clear()
        self._conversation_queues.clear()
        self._conversation_pending_invalidations.clear()
        self._conversation_queue_bytes.clear()
        self._conversation_total_count = 0
        self._conversation_total_bytes = 0

        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except _RUNTIME_ERRORS:
            pass
        finally:
            self._task = None

    def set_conversation_observers(self, on_record=None, on_fact=None) -> None:
        self._conversation_record_observer = on_record
        self._conversation_fact_observer = on_fact

    def accept_raw_conversation_fact(self, op: str, payload: dict[str, object]) -> None:
        self._accept_conversation("raw:" + op, payload)

    def accept_conversation_record(self, event: dict[str, object]) -> None:
        if event.get("store") == "conversations" and event.get("topic") == "all":
            op = event.get("conversation_op", "record")
            if op in ("record", "message.updated", "view.invalidated"):
                self._accept_conversation(str(op), event)

    def accept_conversation_fact(self, op: str, payload: dict[str, object]) -> None:
        self._accept_conversation(op, payload)

    def _accept_conversation(self, op: str, payload: dict[str, object]) -> None:
        if not isinstance(op, str) or not isinstance(payload, dict):
            return
        try:
            size = len(ormsgpack.packb(payload))
        except Exception:
            return
        with self._conversation_lock:
            loop = self._conversation_loop
            wakeup = self._conversation_wakeup
            if loop is None or wakeup is None:
                return
            while self._conversation_ingress and (
                len(self._conversation_ingress) >= 1000
                or self._conversation_ingress_bytes + size > 4 * 1024 * 1024
            ):
                _, _, dropped_size = self._conversation_ingress.popleft()
                self._conversation_ingress_bytes -= dropped_size
                self._conversation_overflow = True
            if size <= 4 * 1024 * 1024:
                self._conversation_ingress.append((op, payload, size))
                self._conversation_ingress_bytes += size
            else:
                self._conversation_overflow = True
            if self._conversation_wakeup_scheduled:
                return
            self._conversation_wakeup_scheduled = True
            try:
                loop.call_soon_threadsafe(wakeup.set)
            except RuntimeError:
                self._conversation_wakeup_scheduled = False

    async def _conversation_dispatch_loop(self) -> None:
        from plugin.sdk.shared.contracts.conversations import message_from_event

        while True:
            wakeup = self._conversation_wakeup
            if wakeup is None:
                return
            await wakeup.wait()
            with self._conversation_lock:
                items = list(self._conversation_ingress)
                self._conversation_ingress.clear()
                self._conversation_ingress_bytes = 0
                overflow = self._conversation_overflow
                self._conversation_overflow = False
                self._conversation_wakeup_scheduled = False
                wakeup.clear()
            subs = state.get_bus_subscriptions("conversations")
            if overflow:
                for sid, info in subs.items():
                    if info.get("contract") == "conversations_v2" and info.get("view"):
                        self._conversation_invalidate(str(sid), info)
            for offset, (op, payload, _) in enumerate(items):
                if offset and offset % 64 == 0:
                    await asyncio.sleep(0)
                try:
                    if op in ("record", "message.updated") and "index" in payload:
                        observer = self._conversation_record_observer
                        if observer is not None:
                            try:
                                observer(payload)
                            except Exception:
                                logger.exception("conversation record observer failed")
                        data = message_from_event(payload)
                        data["_revision"] = payload.get("_revision")
                    elif op.startswith("raw:"):
                        observer = self._conversation_fact_observer
                        result = observer(op[4:], payload) if observer is not None else None
                        if result is None:
                            continue
                        op, data = result
                    else:
                        data = dict(payload)
                except Exception:
                    logger.exception("conversation observation could not be processed")
                    continue
                for sid, info in subs.items():
                    if info.get("contract") != "conversations_v2":
                        continue
                    if op in ("message.updated", "view.invalidated") and info.get("view"):
                        self._conversation_invalidate(str(sid), info)
                    if op == "view.invalidated":
                        continue
                    if op not in info.get("rules", ()) or not self._conversation_matches(op, data, info.get("filters", {})):
                        continue
                    self._conversation_enqueue(str(sid), info, op, data)

    @staticmethod
    def _conversation_matches(op: str, data: dict, filters: object) -> bool:
        if not isinstance(filters, dict):
            return False
        candidate = data
        for key in ("submission", "reply", "playback"):
            if isinstance(data.get(key), dict):
                candidate = data[key]
                break
        role = candidate.get("role")
        if op == "input.submitted":
            role = "user"
        elif op.startswith("reply."):
            role = "assistant"
        for key, value in filters.items():
            if key == "role":
                actual = role
            elif key == "origin_kind":
                origin = candidate.get("origin")
                actual = origin.get("kind") if isinstance(origin, dict) else None
            else:
                actual = candidate.get(key)
            if actual != value:
                return False
        return True

    def _mark_conversation_pending(self, plugin_id: str, sid: str) -> bool:
        pending = self._conversation_pending_invalidations.get(plugin_id)
        if pending is None:
            if len(self._conversation_pending_invalidations) >= 128:
                return False
            pending = self._conversation_pending_invalidations[plugin_id] = set()
        if sid not in pending and len(pending) >= 128:
            return False
        pending.add(sid)
        return True

    def _conversation_invalidate(self, sid: str, info: dict) -> None:
        plugin_id = info.get("from_plugin")
        if not isinstance(plugin_id, str) or not plugin_id:
            return
        if plugin_id not in self._conversation_workers and len(self._conversation_workers) >= 128:
            return
        if self._mark_conversation_pending(plugin_id, sid):
            self._ensure_conversation_worker(plugin_id)

    def _conversation_enqueue(self, sid: str, info: dict, op: str, payload: dict) -> None:
        plugin_id = info.get("from_plugin")
        if not isinstance(plugin_id, str) or not plugin_id:
            return
        if plugin_id not in self._conversation_workers and len(self._conversation_workers) >= 128:
            return
        size = len(ormsgpack.packb(payload))
        queue = self._conversation_queues.setdefault(plugin_id, deque())
        used = self._conversation_queue_bytes.get(plugin_id, 0)
        while queue and (len(queue) >= 128 or used + size > 256 * 1024):
            old_sid, _, _, old_size, _ = queue.popleft()
            used -= old_size
            self._conversation_total_count -= 1
            self._conversation_total_bytes -= old_size
            self._mark_conversation_pending(plugin_id, old_sid)
        while size <= 256 * 1024 and (
            self._conversation_total_count >= 2048
            or self._conversation_total_bytes + size > 8 * 1024 * 1024
        ):
            candidates = [(items[0][4], key) for key, items in self._conversation_queues.items() if items]
            if not candidates:
                break
            _, oldest_plugin = min(candidates)
            oldest = self._conversation_queues[oldest_plugin]
            old_sid, _, _, old_size, _ = oldest.popleft()
            if oldest_plugin == plugin_id:
                used -= old_size
            else:
                self._conversation_queue_bytes[oldest_plugin] -= old_size
            self._conversation_total_count -= 1
            self._conversation_total_bytes -= old_size
            self._mark_conversation_pending(oldest_plugin, old_sid)
        if size > 256 * 1024:
            if info.get("view"):
                self._mark_conversation_pending(plugin_id, sid)
        else:
            self._conversation_enqueue_seq += 1
            queue.append((sid, op, payload, size, self._conversation_enqueue_seq))
            used += size
            self._conversation_total_count += 1
            self._conversation_total_bytes += size
        self._conversation_queue_bytes[plugin_id] = used
        self._ensure_conversation_worker(plugin_id)

    def _ensure_conversation_worker(self, plugin_id: str) -> None:
        if plugin_id not in self._conversation_workers:
            self._conversation_workers[plugin_id] = asyncio.create_task(self._conversation_worker(plugin_id))

    async def _conversation_worker(self, plugin_id: str) -> None:
        invalidation_failures = 0
        try:
            while True:
                queue = self._conversation_queues.get(plugin_id)
                pending = self._conversation_pending_invalidations.get(plugin_id)
                if queue:
                    sid, op, payload, size, _ = queue.popleft()
                    self._conversation_queue_bytes[plugin_id] -= size
                    self._conversation_total_count -= 1
                    self._conversation_total_bytes -= size
                elif pending:
                    sid = pending.pop()
                    op, payload = "view.invalidated", {}
                else:
                    break
                info = state.get_bus_subscriptions("conversations").get(sid)
                if not info or info.get("from_plugin") != plugin_id:
                    continue
                if op == "view.invalidated" and not info.get("view"):
                    continue
                host = state.get_plugin_hosts_snapshot_cached(timeout=1.0).get(plugin_id)
                if host is None:
                    continue
                try:
                    async with self._conversation_send_sem:
                        await asyncio.wait_for(
                            host.push_bus_change(sub_id=sid, bus="conversations", op=op, delta=payload),
                            timeout=self._push_timeout_s,
                        )
                    if op == "view.invalidated":
                        invalidation_failures = 0
                except _RUNTIME_ERRORS:
                    if info.get("view"):
                        self._mark_conversation_pending(plugin_id, sid)
                    if op == "view.invalidated":
                        invalidation_failures += 1
                        if invalidation_failures >= 2:
                            break
                        await asyncio.sleep(0.25)
        finally:
            self._conversation_workers.pop(plugin_id, None)
            queue = self._conversation_queues.get(plugin_id)
            if not queue:
                self._conversation_queues.pop(plugin_id, None)
                self._conversation_queue_bytes.pop(plugin_id, None)
            if not self._conversation_pending_invalidations.get(plugin_id):
                self._conversation_pending_invalidations.pop(plugin_id, None)

    async def invalidate_conversations(self, reason: str) -> None:
        """Close subscriptions when their message-plane backing is retired."""
        subscriptions = state.get_bus_subscriptions("conversations")
        for sid in subscriptions:
            state.remove_bus_subscription("conversations", sid)
        with self._conversation_lock:
            self._conversation_ingress.clear()
            self._conversation_ingress_bytes = 0
            self._conversation_overflow = False
        tasks = list(self._conversation_workers.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._conversation_workers.clear()
        self._conversation_queues.clear()
        self._conversation_queue_bytes.clear()
        self._conversation_pending_invalidations.clear()
        self._conversation_total_count = 0
        self._conversation_total_bytes = 0

        async def notify(sid: str, info: dict) -> None:
            if info.get("contract") != "conversations_v2":
                return
            plugin_id = info.get("from_plugin")
            host = state.get_plugin_hosts_snapshot_cached(timeout=1.0).get(plugin_id)
            if host is None:
                return
            try:
                await asyncio.wait_for(
                    host.push_bus_change(
                        sub_id=sid, bus="conversations", op="subscription.closed",
                        delta={"reason": reason},
                    ),
                    timeout=self._push_timeout_s,
                )
            except _RUNTIME_ERRORS:
                pass

        entries = list(subscriptions.items())
        for offset in range(0, len(entries), 64):
            await asyncio.gather(*(notify(str(sid), info) for sid, info in entries[offset:offset + 64]), return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                delta = await self._queue.get()
                try:
                    await self._dispatch(delta)
                except _RUNTIME_ERRORS:
                    logger.exception("Error dispatching bus delta")
            except asyncio.CancelledError:
                break

    async def _dispatch(self, delta: BusDelta) -> None:
        subs = state.get_bus_subscriptions(delta.bus)
        if not subs:
            return

        async def _send_one(sub_id: str, info: dict[str, object]) -> None:
            plugin_id = info.get("from_plugin")
            if not isinstance(plugin_id, str) or not plugin_id:
                return

            key2 = (plugin_id, str(sub_id))
            now_m = time.monotonic()
            try:
                until = float(self._sub_paused_until.get(key2, 0.0))
            except _RUNTIME_ERRORS:
                until = 0.0
            if until > now_m:
                return
            if until > 0.0:
                self._sub_paused_until.pop(key2, None)

            # Use a cached snapshot to avoid lock contention.
            hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
            host = hosts_snapshot.get(plugin_id)
            if not host:
                return

            d: dict[str, object] = dict(delta.payload or {})
            try:
                if "rev" not in d:
                    d["rev"] = int(state.get_bus_rev(delta.bus))
            except _RUNTIME_ERRORS:
                pass

            async with self._dispatch_sem:
                try:
                    await asyncio.wait_for(
                        host.push_bus_change(
                            sub_id=str(sub_id or ""),
                            bus=str(delta.bus or ""),
                            op=str(delta.op or ""),
                            delta=d,
                        ),
                        timeout=float(self._push_timeout_s),
                    )
                except _RUNTIME_ERRORS:
                    # Failure tracking + circuit breaker
                    try:
                        nfail = int(self._sub_failures.get(key2, 0)) + 1
                        self._sub_failures[key2] = nfail
                        if nfail >= int(self._fail_threshold):
                            self._sub_paused_until[key2] = time.monotonic() + float(self._pause_seconds)
                            self._sub_failures.pop(key2, None)
                    except _RUNTIME_ERRORS:
                        pass
                    return

            # Success -> reset failures
            try:
                self._sub_failures.pop(key2, None)
                self._sub_paused_until.pop(key2, None)
            except _RUNTIME_ERRORS:
                pass

            if PLUGIN_LOG_BUS_SUBSCRIPTIONS:
                try:
                    window = PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS
                    if window and window > 0:
                        now_ts = time.monotonic()
                        key = (plugin_id, sub_id, delta.bus, delta.op)
                        last_key = self._last_log_key
                        last_ts = self._last_log_time
                        if last_key == key and last_ts > 0.0 and (now_ts - last_ts) <= window:
                            self._last_log_repeat_count += 1
                            return

                        if last_key is not None and self._last_log_repeat_count > 0:
                            logger.info(
                                "Pushed bus.change (suppressed {} duplicate entries for plugin={} sub_id={} bus={} op={})",
                                self._last_log_repeat_count,
                                last_key[0],
                                last_key[1],
                                last_key[2],
                                last_key[3],
                            )

                        self._last_log_key = key
                        self._last_log_time = now_ts
                        self._last_log_repeat_count = 0

                    logger.info(
                        "Pushed bus.change to plugin={} sub_id={} bus={} op={}",
                        plugin_id,
                        sub_id,
                        delta.bus,
                        delta.op,
                    )
                except _RUNTIME_ERRORS:
                    pass

        tasks = []
        for sid, info in subs.items():
            if not isinstance(info, dict):
                continue
            tasks.append(asyncio.create_task(_send_one(str(sid), dict(info)), name="bus-dispatch-one"))
        if not tasks:
            return
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except _RUNTIME_ERRORS:
            return


bus_subscription_manager = BusSubscriptionManager()


def new_sub_id() -> str:
    return str(uuid.uuid4())
