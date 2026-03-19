from __future__ import annotations

from collections import defaultdict
from contextlib import suppress
from typing import Any, Callable

_BUS_CHANGE_LISTENERS: dict[str, list[Callable[[str, str, dict[str, Any]], None]]] = defaultdict(list)


def register_bus_change_listener(bus: str, fn: Callable[[str, str, dict[str, Any]], None]) -> Callable[[], None]:
    bus_name = str(bus).strip()
    if bus_name and callable(fn):
        _BUS_CHANGE_LISTENERS[bus_name].append(fn)

    def _unsubscribe() -> None:
        listeners = _BUS_CHANGE_LISTENERS.get(bus_name, [])
        with suppress(ValueError):
            listeners.remove(fn)
        if not listeners and bus_name in _BUS_CHANGE_LISTENERS:
            _BUS_CHANGE_LISTENERS.pop(bus_name, None)

    return _unsubscribe


def dispatch_bus_change(*, sub_id: str, bus: str, op: str, delta: dict[str, Any] | None = None) -> None:
    if not str(sub_id).strip():
        return
    bus_name = str(bus).strip()
    if not bus_name:
        return
    payload = dict(delta or {})
    for fn in list(_BUS_CHANGE_LISTENERS.get(bus_name, [])):
        with suppress(Exception):
            fn(bus_name, str(op), payload)
