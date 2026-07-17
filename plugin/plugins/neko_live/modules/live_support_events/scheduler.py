from __future__ import annotations

import asyncio
import heapq
import time
from collections import OrderedDict
from enum import IntEnum
from typing import Any, Awaitable, Callable


class SupportPriority(IntEnum):
    MILESTONE = 0
    HIGH = 1
    MEDIUM = 2
    LIGHT = 3


class _QueueAdmission(IntEnum):
    REJECTED = 0
    QUEUED = 1
    AGGREGATED = 2


def classify_support_priority(payload: dict[str, Any]) -> SupportPriority:
    event_type = str(payload.get("event_type") or "").strip().lower()
    if event_type in {"super_chat", "guard"}:
        return SupportPriority.MILESTONE
    if event_type != "gift" or str(payload.get("coin_type") or "").lower() != "gold":
        return SupportPriority.LIGHT
    value = payload.get("gift_value", payload.get("gift_total_coin", 0))
    try:
        total_coin = int(value or 0)
    except (TypeError, ValueError):
        total_coin = 0
    if total_coin >= 10_000:
        return SupportPriority.HIGH
    if total_coin >= 1_000:
        return SupportPriority.MEDIUM
    return SupportPriority.LIGHT


class SupportEventScheduler:
    def __init__(
        self,
        *,
        dispatch: Callable[[dict[str, Any]], Awaitable[None]],
        audit: Any = None,
        queue_limit: int = 64,
        combo_idle_seconds: float = 1.0,
        finalized_combo_seconds: float = 600.0,
        finalized_combo_limit: int = 4096,
    ) -> None:
        self._dispatch = dispatch
        self._audit = audit
        self._queue_limit = max(1, min(100, int(queue_limit)))
        self._combo_limit = self._queue_limit
        self._combo_idle_seconds = max(0.0, float(combo_idle_seconds))
        self._queue: list[tuple[int, int, dict[str, Any]]] = []
        self._combos: dict[tuple[str, ...], dict[str, Any]] = {}
        self._combo_deadlines: dict[tuple[str, ...], float] = {}
        self._combo_tasks: dict[tuple[str, ...], asyncio.Task[None]] = {}
        self._finalized_combos: OrderedDict[tuple[str, ...], float] = OrderedDict()
        self._finalized_combo_limit = max(
            self._queue_limit,
            min(65_536, int(finalized_combo_limit)),
        )
        self._finalized_combo_seconds = max(
            self._combo_idle_seconds,
            min(3_600.0, float(finalized_combo_seconds)),
        )
        self._retired_tasks: set[asyncio.Task[None]] = set()
        self._retired_task_limit = self._combo_limit + 1
        self._sequence = 0
        self._worker: asyncio.Task[None] | None = None
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        self._processed_id_limit = 65_536
        self._overflow_count = 0
        self._dropped_count = 0
        self._aggregated_count = 0
        self._closed = False
        self._idle = asyncio.Event()
        self._idle.set()

    def submit(self, payload: dict[str, Any]) -> bool:
        if self._closed:
            return False
        if len(self._retired_tasks) >= self._retired_task_limit:
            self._dropped_count += 1
            return False
        item = dict(payload)
        if self._combo_key(item) is not None:
            return self._submit_combo(item)
        event_id = str(item.get("provider_event_id") or "").strip()
        if event_id:
            if event_id in self._processed_ids:
                return False
        accepted = self._enqueue(item)
        if accepted and event_id:
            self._remember_event_id(event_id)
        return accepted

    def _enqueue(self, item: dict[str, Any]) -> bool:
        priority = classify_support_priority(item)
        if len(self._queue) >= self._queue_limit:
            admission = self._make_room(priority, item)
            if admission is _QueueAdmission.REJECTED:
                return False
            if admission is _QueueAdmission.AGGREGATED:
                return True
        self._sequence += 1
        heapq.heappush(self._queue, (int(priority), self._sequence, item))
        self._idle.clear()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        return True

    def _make_room(
        self,
        priority: SupportPriority,
        item: dict[str, Any],
    ) -> _QueueAdmission:
        if priority is SupportPriority.LIGHT:
            incoming_key = self._light_aggregation_key(item)
            light_entries = [
                entry
                for entry in self._queue
                if entry[0] == int(SupportPriority.LIGHT)
                and incoming_key is not None
                and self._light_aggregation_key(entry[2]) == incoming_key
            ]
            if light_entries:
                target = min(light_entries, key=lambda entry: entry[1])[2]
                count = self._non_negative_int(target.get("aggregated_event_count")) or 1
                target["aggregated_event_count"] = count + 1
                target.pop("provider_event_id", None)
                target["gift_value"] = 0
                self._aggregated_count += 1
                return _QueueAdmission.AGGREGATED
            else:
                self._dropped_count += 1
            return _QueueAdmission.REJECTED

        allowed = {
            int(candidate)
            for candidate in SupportPriority
            if candidate > priority
        }
        candidates = [entry for entry in self._queue if entry[0] in allowed]
        if candidates:
            victim = max(candidates, key=lambda entry: (entry[0], -entry[1]))
            self._queue.remove(victim)
            heapq.heapify(self._queue)
            self._release_evicted_dedupe(victim[2])
            self._dropped_count += 1
            return _QueueAdmission.QUEUED
        if priority in {SupportPriority.HIGH, SupportPriority.MILESTONE}:
            self._overflow_count += 1
            return _QueueAdmission.REJECTED
        self._dropped_count += 1
        return _QueueAdmission.REJECTED

    @staticmethod
    def _light_aggregation_key(payload: dict[str, Any]) -> tuple[str, ...] | None:
        if str(payload.get("event_type") or "").strip().lower() != "gift":
            return None
        if str(payload.get("provider_event_id") or "").strip():
            return None
        room = str(payload.get("room_ref") or payload.get("room_id") or "").strip()
        uid = str(payload.get("uid") or "").strip()
        gift_name = str(payload.get("gift_name") or "").strip()
        if not room or not uid or not gift_name:
            return None
        return (
            room,
            uid,
            gift_name,
            str(payload.get("coin_type") or "").strip().lower(),
            str(payload.get("provider_event_type") or "").strip().upper(),
        )

    def _remember_event_id(self, event_id: str) -> None:
        self._processed_ids[event_id] = None
        self._processed_ids.move_to_end(event_id)
        while len(self._processed_ids) > self._processed_id_limit:
            self._processed_ids.popitem(last=False)

    def _release_evicted_dedupe(self, payload: dict[str, Any]) -> None:
        event_id = str(payload.get("provider_event_id") or "").strip()
        if event_id:
            self._processed_ids.pop(event_id, None)
        combo_key = self._combo_key(payload)
        if combo_key is not None:
            self._finalized_combos.pop(combo_key, None)

    def _submit_combo(self, item: dict[str, Any]) -> bool:
        key = self._combo_key(item)
        if key is None:
            return self._enqueue(item)
        now = time.monotonic()
        self._prune_finalized_combos(now)
        if key in self._finalized_combos:
            return False
        current = self._combos.get(key)
        if current is None:
            if len(self._combos) >= self._combo_limit:
                self._dropped_count += 1
                return False
            current = item
            self._combos[key] = current
            previous_count = 0
            previous_value = 0
        else:
            conflict_field = self._combo_identity_conflict(current, item)
            if conflict_field:
                self._dropped_count += 1
                self._record_audit(
                    "support.combo_conflict",
                    "conflicting combo update rejected",
                    level="warning",
                    detail={"field": conflict_field},
                )
                return False
            previous_count = max(
                self._non_negative_int(current.get("gift_count")),
                self._non_negative_int(current.get("combo_count")),
            )
            previous_value = self._non_negative_int(current.get("gift_value"))
        count = max(
            previous_count,
            self._non_negative_int(item.get("gift_count")),
            self._non_negative_int(item.get("combo_count")),
        )
        value = max(
            previous_value,
            self._non_negative_int(item.get("gift_value")),
        )
        combo_end = item.get("combo_end") is True
        if current is not item and count == previous_count and value == previous_value and not combo_end:
            return False
        current["gift_count"] = count
        current["combo_count"] = count
        current["gift_value"] = value
        current["provider_timestamp_ms"] = max(
            self._non_negative_int(current.get("provider_timestamp_ms")),
            self._non_negative_int(item.get("provider_timestamp_ms")),
        )
        if combo_end:
            current["combo_end"] = True
        self._idle.clear()

        if combo_end:
            self._combo_deadlines.pop(key, None)
            timer = self._combo_tasks.pop(key, None)
            if timer is not None:
                timer.cancel()
                self._retire_task(timer)
            self._combos.pop(key, None)
            return self._enqueue_finalized_combo(key, current, now)
        self._combo_deadlines[key] = now + self._combo_idle_seconds
        timer = self._combo_tasks.get(key)
        if timer is None or timer.done():
            self._combo_tasks[key] = asyncio.create_task(self._finalize_combo_after_idle(key))
        return True

    async def _finalize_combo_after_idle(self, key: tuple[str, ...]) -> None:
        task = asyncio.current_task()
        try:
            while True:
                deadline = self._combo_deadlines.get(key)
                if deadline is None:
                    return
                delay = deadline - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                self._combo_deadlines.pop(key, None)
                payload = self._combos.pop(key, None)
                if payload is not None:
                    self._enqueue_finalized_combo(key, payload, time.monotonic())
                return
        except asyncio.CancelledError:
            raise
        finally:
            if self._combo_tasks.get(key) is task:
                self._combo_tasks.pop(key, None)
            self._refresh_idle()

    def _prune_finalized_combos(self, now: float) -> None:
        while self._finalized_combos:
            first_key = next(iter(self._finalized_combos))
            if self._finalized_combos[first_key] > now:
                break
            self._finalized_combos.popitem(last=False)

    def _mark_combo_finalized(self, key: tuple[str, ...], now: float) -> None:
        self._finalized_combos[key] = now + self._finalized_combo_seconds
        self._finalized_combos.move_to_end(key)
        while len(self._finalized_combos) > self._finalized_combo_limit:
            self._finalized_combos.popitem(last=False)

    def _enqueue_finalized_combo(
        self,
        key: tuple[str, ...],
        payload: dict[str, Any],
        now: float,
    ) -> bool:
        accepted = self._enqueue(payload)
        if accepted:
            self._mark_combo_finalized(key, now)
        return accepted

    @staticmethod
    def _combo_key(payload: dict[str, Any]) -> tuple[str, ...] | None:
        if str(payload.get("provider_event_type") or "").upper() != "COMBO_SEND":
            return None
        room = str(payload.get("room_ref") or payload.get("room_id") or "")
        uid = str(payload.get("uid") or "")
        combo_id = str(payload.get("combo_id") or "")
        if combo_id:
            return (room, uid, combo_id)
        gift_name = str(payload.get("gift_name") or "")[:80]
        return (room, uid, "COMBO_SEND", gift_name)

    @staticmethod
    def _combo_identity_conflict(current: dict[str, Any], item: dict[str, Any]) -> str:
        for field in (
            "event_type",
            "provider_event_type",
            "uid",
            "gift_name",
            "coin_type",
            "combo_id",
        ):
            existing = str(current.get(field) or "").strip()
            incoming = str(item.get(field) or "").strip()
            if existing and incoming and existing != incoming:
                return field
        existing_room = str(current.get("room_ref") or current.get("room_id") or "").strip()
        incoming_room = str(item.get("room_ref") or item.get("room_id") or "").strip()
        if existing_room and incoming_room and existing_room != incoming_room:
            return "room_ref"
        return ""

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    async def _run(self) -> None:
        worker = asyncio.current_task()
        try:
            while self._queue:
                _priority, _sequence, payload = heapq.heappop(self._queue)
                await self._dispatch_with_retry(payload)
        finally:
            if self._worker is worker:
                self._worker = None
            self._refresh_idle()

    async def _dispatch_with_retry(self, payload: dict[str, Any]) -> None:
        for attempt in range(2):
            try:
                await self._dispatch(payload)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt == 0:
                    continue
                self._record_audit(
                    "support.dispatch_failed",
                    "support event dispatch failed",
                    level="error",
                    detail={
                        "event_type": str(payload.get("event_type") or "unknown")[:32],
                        "error_type": type(exc).__name__,
                    },
                )

    def _record_audit(
        self,
        op: str,
        message: str,
        *,
        level: str,
        detail: dict[str, Any],
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit.record(op, message, level=level, detail=detail)
        except Exception:
            return

    async def wait_idle(self) -> None:
        self._refresh_idle()
        await self._idle.wait()

    def _retire_task(self, task: asyncio.Task[None]) -> None:
        if task.done() or task in self._retired_tasks:
            return
        self._retired_tasks.add(task)
        self._idle.clear()
        task.add_done_callback(self._retired_task_done)

    def _retired_task_done(self, task: asyncio.Task[None]) -> None:
        self._retired_tasks.discard(task)
        self._refresh_idle()

    def _refresh_idle(self) -> None:
        idle = (
            not self._queue
            and not self._combos
            and self._worker is None
            and not self._combo_tasks
            and not self._retired_tasks
        )
        if idle:
            self._idle.set()
        else:
            self._idle.clear()

    def reset(self) -> None:
        self._queue.clear()
        self._combos.clear()
        self._combo_deadlines.clear()
        self._finalized_combos.clear()
        self._processed_ids.clear()
        self._overflow_count = 0
        self._dropped_count = 0
        self._aggregated_count = 0
        for task in self._combo_tasks.values():
            task.cancel()
            self._retire_task(task)
        self._combo_tasks.clear()
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            self._retire_task(self._worker)
        self._worker = None
        self._refresh_idle()

    async def close(self) -> None:
        self._closed = True
        self.reset()
        while self._retired_tasks:
            await asyncio.gather(*list(self._retired_tasks), return_exceptions=True)
        self._refresh_idle()

    def status(self) -> dict[str, int | float]:
        return {
            "queue_limit": self._queue_limit,
            "pending_count": len(self._queue),
            "active_combo_count": len(self._combos),
            "active_combo_task_count": len(self._combo_tasks),
            "retired_task_count": len(self._retired_tasks),
            "processed_id_count": len(self._processed_ids),
            "processed_id_limit": self._processed_id_limit,
            "finalized_combo_count": len(self._finalized_combos),
            "finalized_combo_limit": self._finalized_combo_limit,
            "finalized_combo_seconds": self._finalized_combo_seconds,
            "overflow_count": self._overflow_count,
            "dropped_count": self._dropped_count,
            "aggregated_count": self._aggregated_count,
        }
