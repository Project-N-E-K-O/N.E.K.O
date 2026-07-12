from __future__ import annotations

import asyncio
from contextlib import nullcontext, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar, Union, cast

from plugin.core.bus.bus_list import (
    BusListWatcherCore,
    _compute_watcher_delta,
    _dispatch_watcher_callbacks,
    _infer_bus_from_plan,
    _snapshot_watcher_callbacks,
)

__all__ = [
    "BusListDelta",
    "BusListWatcher",
    "list_subscription",
    "list_Subscription",
]

TRecord = TypeVar("TRecord", bound="BusRecord")
DedupeKey = Tuple[str, Any]
Payload = Dict[str, Any]
ChangeRules = Tuple[str, ...]
WatcherCallback = Callable[["BusListDelta[TRecord]"], None]

if TYPE_CHECKING:
    from plugin.core.bus.types import BusList, BusRecord, BusReplayContext, TraceNode


def _cancel_timer_best_effort(timer: Any) -> None:
    try:
        if timer is not None:
            timer.cancel()
    except Exception:
        return


@dataclass(frozen=True)
class BusListDelta(Generic[TRecord]):
    kind: str
    added: Tuple[TRecord, ...]
    removed: Tuple[DedupeKey, ...]
    current: "BusList[TRecord]"


class BusListWatcher(BusListWatcherCore, Generic[TRecord]):
    def __init__(
        self,
        lst: "BusList[TRecord]",
        ctx: "BusReplayContext",
        *,
        bus: Optional[str] = None,
        debounce_ms: float = 0.0,
    ):
        from plugin.core.bus.types import NonReplayableTraceError

        self._list = lst
        self._ctx = ctx
        self._debounce_ms = float(debounce_ms or 0.0)

        if self._list._plan is None:
            raise NonReplayableTraceError("watcher requires a replayable plan; build list via get()/filter()/sort(by=...)")

        inferred = self._infer_bus(self._list._plan)
        self._bus = str(bus).strip() if isinstance(bus, str) and bus.strip() else inferred
        if self._bus not in ("messages", "events", "lifecycle"):
            raise NonReplayableTraceError(f"watcher cannot infer bus type from plan: {self._bus!r}")

        self._lock = None
        try:
            import threading

            self._lock = threading.Lock()
        except Exception:
            self._lock = None

        self._callbacks: List[Tuple[WatcherCallback[TRecord], ChangeRules]] = []
        self._unsub: Optional[Callable[[], None]] = None
        self._sub_id: Optional[str] = None
        self._last_keys: set[DedupeKey] = {self._list._dedupe_key(x) for x in self._list.dump_records()}

        self._debounce_timer: Any = None
        self._pending_op: Optional[str] = None
        self._pending_payload: Optional[Payload] = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._pending_async_refresh: tuple[str, Optional[Payload]] | None = None
        self._refresh_generation = 0
        self._stopped = True

    def start(self) -> "BusListWatcher[TRecord]":
        if self._unsub is not None or self._sub_id is not None:
            return self
        self._refresh_generation += 1
        self._stopped = False
        return cast("BusListWatcher[TRecord]", super().start())

    def stop(self) -> None:
        self._stopped = True
        self._refresh_generation += 1
        self._pending_async_refresh = None

        refresh_task = self._refresh_task
        self._refresh_task = None
        if refresh_task is not None:
            refresh_task.cancel()

        if self._lock is not None:
            with self._lock:
                debounce_timer = self._debounce_timer
                self._debounce_timer = None
                self._pending_op = None
                self._pending_payload = None
        else:
            debounce_timer = self._debounce_timer
            self._debounce_timer = None
            self._pending_op = None
            self._pending_payload = None
        _cancel_timer_best_effort(debounce_timer)

        super().stop()

    def _schedule_tick(self, op: str, payload: Optional[Payload] = None) -> None:
        if self._stopped:
            return
        if self._debounce_ms <= 0:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                normalized_payload = dict(payload) if isinstance(payload, dict) else None
                self._pending_async_refresh = (str(op), normalized_payload)
                current_task = self._refresh_task
                if current_task is None or current_task.done():
                    self._start_refresh_worker(loop)
                return
        self._schedule_debounced_tick(op, payload)

    def _schedule_debounced_tick(self, op: str, payload: Optional[Payload] = None) -> None:
        generation = self._refresh_generation
        if self._stopped:
            return
        if self._debounce_ms <= 0:
            self._tick(op, payload, generation=generation)
            return

        timer: Any = None
        try:
            import threading

            delay = max(0.0, self._debounce_ms / 1000.0)
            normalized_payload = dict(payload) if isinstance(payload, dict) else None

            def _is_current_generation() -> bool:
                return not self._stopped and self._refresh_generation == generation

            def _fire() -> None:
                with self._lock if self._lock is not None else nullcontext():
                    if not _is_current_generation() or self._debounce_timer is not timer:
                        return
                    pending = self._pending_op
                    pending_payload = self._pending_payload
                    self._pending_op = None
                    self._pending_payload = None
                    self._debounce_timer = None

                with suppress(Exception):
                    self._tick(
                        str(pending or "change"),
                        pending_payload,
                        generation=generation,
                    )

            timer = threading.Timer(delay, _fire)
            timer.daemon = True
            previous_timer: Any = None
            should_start = False
            with self._lock if self._lock is not None else nullcontext():
                if _is_current_generation():
                    previous_timer = self._debounce_timer
                    self._pending_op = str(op)
                    self._pending_payload = normalized_payload
                    self._debounce_timer = timer
                    should_start = True

            if not should_start:
                _cancel_timer_best_effort(timer)
                return
            _cancel_timer_best_effort(previous_timer)
            timer.start()
        except Exception:
            with self._lock if self._lock is not None else nullcontext():
                if self._debounce_timer is timer:
                    self._debounce_timer = None
                    self._pending_op = None
                    self._pending_payload = None
            with suppress(Exception):
                self._tick(op, payload, generation=generation)

    def _start_refresh_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        task = loop.create_task(self._run_refresh_worker(self._refresh_generation))
        self._refresh_task = task
        task.add_done_callback(self._finish_refresh_task)

    def _finish_refresh_task(self, task: asyncio.Task[None]) -> None:
        is_current = self._refresh_task is task
        if is_current:
            self._refresh_task = None
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            pass

        if (
            is_current
            and not self._stopped
            and self._pending_async_refresh is not None
            and not task.get_loop().is_closed()
        ):
            self._start_refresh_worker(task.get_loop())

    def _watcher_set(self, sub_id: str) -> None:
        from plugin.core.bus.rev import _watcher_set

        _watcher_set(sub_id, self)

    def _watcher_pop(self, sub_id: str) -> None:
        from plugin.core.bus.rev import _watcher_pop

        _watcher_pop(sub_id)

    def _on_remote_change(self, *, bus: str, op: str, delta: Payload) -> None:
        try:
            self._schedule_tick(op, delta)
        except Exception:
            return

    def _infer_bus(self, plan: "TraceNode") -> str:
        from plugin.core.bus.types import NonReplayableTraceError

        return _infer_bus_from_plan(plan, conflict_error=NonReplayableTraceError)

    async def _run_refresh_worker(self, generation: int) -> None:
        while not self._stopped and generation == self._refresh_generation:
            pending = self._pending_async_refresh
            self._pending_async_refresh = None
            if pending is None:
                return
            op, _payload = pending
            refreshed = await self._list.reload_with_async(self._ctx)
            if self._stopped or generation != self._refresh_generation:
                return
            self._apply_refresh(op, refreshed)

    def _tick(
        self,
        op: str,
        payload: Optional[Payload] = None,
        *,
        generation: int | None = None,
    ) -> None:
        del payload
        if generation is None:
            generation = self._refresh_generation
        if self._stopped or generation != self._refresh_generation:
            return
        refreshed = self._list.reload(self._ctx)
        if self._stopped or generation != self._refresh_generation:
            return
        self._apply_refresh(op, refreshed)

    def _apply_refresh(self, op: str, refreshed: "BusList[TRecord]") -> None:
        if self._stopped:
            return
        new_items = refreshed.dump_records()
        added_items_raw, removed_keys_raw, new_keys_raw, fired_raw, kind_raw = _compute_watcher_delta(
            op=op,
            refreshed_items=list(new_items),
            last_keys=set(self._last_keys),
            dedupe_key=lambda x: self._list._dedupe_key(cast(TRecord, x)),
        )
        added_items = cast(List[TRecord], added_items_raw)
        removed_keys = cast(Tuple[DedupeKey, ...], removed_keys_raw)
        new_keys = cast(set[DedupeKey], new_keys_raw)
        fired = cast(List[str], fired_raw)
        kind = str(kind_raw)

        if not fired:
            self._last_keys = new_keys
            self._list = refreshed
            return

        delta = BusListDelta(kind=kind, added=tuple(added_items), removed=removed_keys, current=refreshed)

        callbacks = _snapshot_watcher_callbacks(
            cast(List[Tuple[Callable[[Any], None], ChangeRules]], self._callbacks),
            self._lock,
        )
        _dispatch_watcher_callbacks(
            cast(List[Tuple[Callable[[Any], None], ChangeRules]], callbacks),
            cast(List[str], fired),
            delta,
        )

        self._last_keys = new_keys
        self._list = refreshed


def list_subscription(
    watcher: BusListWatcher[TRecord],
    *,
    on: Union[str, Sequence[str]] = ("add",),
) -> Callable[[Callable[[BusListDelta[TRecord]], None]], Callable[[BusListDelta[TRecord]], None]]:
    return watcher.subscribe(on=on)


list_Subscription = list_subscription
