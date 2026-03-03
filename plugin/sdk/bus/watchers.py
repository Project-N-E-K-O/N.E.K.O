from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar, Union, cast

from plugin.sdk.bus.bus_list import (
    BusListWatcherCore,
    _apply_watcher_ops_local,
    _compute_watcher_delta,
    _dispatch_watcher_callbacks,
    _extract_unary_plan_ops,
    _infer_bus_from_plan,
    _record_from_raw_by_bus,
    _resolve_watcher_refresh,
    _schedule_watcher_tick_debounced,
    _snapshot_watcher_callbacks,
    _try_incremental_local,
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
    from plugin.sdk.bus.types import BusList, BusRecord, BusReplayContext, TraceNode


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
        from plugin.sdk.bus.types import NonReplayableTraceError

        self._list = lst
        self._ctx = ctx
        self._debounce_ms = float(debounce_ms or 0.0)

        if self._list._plan is None:
            raise NonReplayableTraceError("watcher requires a replayable plan; build list via get()/filter()/where_*/sort(by=...)")

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

    def _schedule_tick(self, op: str, payload: Optional[Payload] = None) -> None:
        _schedule_watcher_tick_debounced(self, op, payload)

    def _watcher_set(self, sub_id: str) -> None:
        from plugin.sdk.bus.rev import _watcher_set

        _watcher_set(sub_id, self)

    def _watcher_pop(self, sub_id: str) -> None:
        from plugin.sdk.bus.rev import _watcher_pop

        _watcher_pop(sub_id)

    def _on_remote_change(self, *, bus: str, op: str, delta: Payload) -> None:
        try:
            self._schedule_tick(op, delta)
        except Exception:
            return

    def _extract_plan_ops(self) -> Optional[List[Tuple[str, Payload]]]:
        plan = getattr(self._list, "_plan", None)
        return _extract_unary_plan_ops(plan)

    def _infer_bus(self, plan: "TraceNode") -> str:
        from plugin.sdk.bus.types import NonReplayableTraceError

        return _infer_bus_from_plan(plan, conflict_error=NonReplayableTraceError)

    def _apply_ops_local(
        self,
        base_items: List[TRecord],
        ops: List[Tuple[str, Payload]],
    ) -> Optional["BusList[TRecord]"]:
        try:
            base = self._list._construct(base_items, self._list._trace, self._list._plan)
        except Exception:
            from plugin.sdk.bus.types import BusList

            base = BusList(cast(List["BusRecord"], base_items))
        return cast(Optional["BusList[TRecord]"], _apply_watcher_ops_local(base, ops))

    def _record_from_raw(self, raw: Payload) -> Optional[TRecord]:
        return cast(Optional[TRecord], _record_from_raw_by_bus(self._bus, raw))

    def _try_incremental(self, op: str, payload: Optional[Payload]) -> Optional["BusList[TRecord]"]:
        ops = self._extract_plan_ops()
        out = _try_incremental_local(
            op=op,
            payload=payload,
            bus=self._bus,
            ops=ops,
            current_items=list(self._list.dump_records()),
            record_from_raw=lambda raw: self._record_from_raw(raw),
            apply_ops_local=lambda items, op_list: self._apply_ops_local(cast(List[TRecord], items), op_list),
            dedupe_key=lambda x: self._list._dedupe_key(cast(TRecord, x)),
        )
        return cast(Optional["BusList[TRecord]"], out)

    def _tick(self, op: str, payload: Optional[Payload] = None) -> None:
        refreshed = _resolve_watcher_refresh(
            op=op,
            payload=payload,
            try_incremental=lambda op0, payload0: self._try_incremental(op0, payload0),
            reload_full=lambda: self._list.reload(self._ctx),
        )
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
