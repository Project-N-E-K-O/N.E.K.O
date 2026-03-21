from __future__ import annotations

from collections import deque
from contextlib import suppress
from typing import Any, Callable, Dict, Optional, Protocol

__all__ = [
    "_BUS_LATEST_REV",
    "_get_bus_rev",
    "_get_recent_deltas",
    "_watcher_set",
    "_watcher_pop",
    "_ensure_bus_rev_subscription",
    "register_bus_change_listener",
    "dispatch_bus_change",
]

_WATCHER_REGISTRY: Dict[str, "_WatcherSink"] = {}
_WATCHER_REGISTRY_LOCK = None
try:
    import threading

    _WATCHER_REGISTRY_LOCK = threading.Lock()
except Exception:
    _WATCHER_REGISTRY_LOCK = None

_BUS_LATEST_REV: Dict[str, int] = {
    "messages": 0,
    "events": 0,
    "lifecycle": 0,
    "conversations": 0,
}
_BUS_LATEST_REV_LOCK = _WATCHER_REGISTRY_LOCK

_BUS_RECENT_DELTAS: Dict[str, "deque[tuple[int, str, Dict[str, Any]]]"] = {}

_BUS_CHANGE_LISTENERS: Dict[str, "list[Callable[[str, str, Dict[str, Any]], None]]"] = {
    "messages": [],
    "events": [],
    "lifecycle": [],
    "conversations": [],
}


def _locked(fn: Callable[..., Any]) -> Any:
    if _BUS_LATEST_REV_LOCK is not None:
        with _BUS_LATEST_REV_LOCK:
            return fn()
    return fn()


def _get_bus_rev(bus: str) -> int:
    return _locked(lambda: int(_BUS_LATEST_REV.get(bus, 0)))


def _update_bus_rev(bus: str, rev: int) -> None:
    def _do() -> None:
        prev = int(_BUS_LATEST_REV.get(bus, 0))
        if rev > prev:
            _BUS_LATEST_REV[bus] = rev

    _locked(_do)


def _get_bus_listeners(bus: str) -> "list[Callable[[str, str, Dict[str, Any]], None]]":
    return _locked(lambda: list(_BUS_CHANGE_LISTENERS.get(bus, [])))


def _get_recent_deltas(bus: str) -> "list[tuple[int, str, Dict[str, Any]]]":
    return _locked(lambda: list(_BUS_RECENT_DELTAS.get(bus, [])))


def _append_recent_delta(bus: str, rev: int, op: str, delta: Dict[str, Any]) -> None:
    def _do() -> None:
        q = _BUS_RECENT_DELTAS.get(bus)
        if q is None:
            q = deque(maxlen=512)
            _BUS_RECENT_DELTAS[bus] = q
        q.append((rev, str(op), dict(delta)))

    _locked(_do)


def _watcher_get(sub_id: str) -> "Optional[_WatcherSink]":
    return _locked(lambda: _WATCHER_REGISTRY.get(sub_id))


def _watcher_set(sub_id: str, watcher: "_WatcherSink") -> None:
    _locked(lambda: _WATCHER_REGISTRY.__setitem__(sub_id, watcher))


def _watcher_pop(sub_id: str) -> None:
    _locked(lambda: _WATCHER_REGISTRY.pop(sub_id, None))


def register_bus_change_listener(bus: str, fn: "Callable[[str, str, Dict[str, Any]], None]") -> Callable[[], None]:
    b = str(bus).strip()
    if b not in _BUS_CHANGE_LISTENERS:
        raise ValueError(f"invalid bus: {bus!r}")
    if not callable(fn):
        raise ValueError("listener must be callable")
    _locked(lambda: _BUS_CHANGE_LISTENERS[b].append(fn))

    def _unsub() -> None:
        try:
            def _do() -> None:
                lst = _BUS_CHANGE_LISTENERS.get(b)
                if lst is not None:
                    try:
                        lst.remove(fn)
                    except ValueError:
                        pass

            _locked(_do)
        except Exception:
            return

    return _unsub


_BUS_REV_SUB_ID: Dict[str, str] = {}


class _BusRevSink:
    def _on_remote_change(self, *, bus: str, op: str, delta: Dict[str, Any]) -> None:
        _ = (bus, op, delta)
        return


class _WatcherSink(Protocol):
    def _on_remote_change(self, *, bus: str, op: str, delta: Dict[str, Any]) -> None: ...


def _ensure_bus_rev_subscription(ctx: Any, bus: str) -> None:
    b = str(bus).strip()
    if b not in ("messages", "events", "lifecycle"):
        return
    if getattr(ctx, "_plugin_comm_queue", None) is None or not hasattr(ctx, "_send_request_and_wait"):
        return

    sid0 = None
    with suppress(Exception):
        sid0 = _locked(lambda: _BUS_REV_SUB_ID.get(b))
    if isinstance(sid0, str) and sid0:
        return

    try:
        res = ctx._send_request_and_wait(
            method_name="bus_subscribe",
            request_type="BUS_SUBSCRIBE",
            request_data={
                "bus": b,
                "rules": ["add", "del", "change"],
                "deliver": "delta",
                "plan": None,
            },
            timeout=5.0,
            wrap_result=True,
        )
    except Exception:
        return

    sub_id = None
    cur_rev = None
    try:
        if isinstance(res, dict):
            sub_id = res.get("sub_id")
            cur_rev = res.get("rev")
    except Exception:
        sub_id = None

    if not isinstance(sub_id, str) or not sub_id:
        return

    sink = _BusRevSink()

    def _register() -> None:
        _WATCHER_REGISTRY[sub_id] = sink
        _BUS_REV_SUB_ID[b] = sub_id

    _locked(_register)

    if cur_rev is not None:
        try:
            r = int(cur_rev)
        except Exception:
            r = None
        if r is not None:
            _update_bus_rev(b, r)


def _notify_bus_listeners(bus: str, op: str, delta: Dict[str, Any]) -> None:
    bus_name = str(bus).strip()
    if bus_name not in _BUS_CHANGE_LISTENERS:
        return
    listeners = _get_bus_listeners(bus_name)
    payload = dict(delta or {})
    for fn in listeners:
        try:
            fn(bus_name, str(op), payload)
        except Exception:
            continue


def dispatch_bus_change(*, sub_id: str, bus: str, op: str, delta: Optional[Dict[str, Any]] = None) -> None:
    sub_id_norm = str(sub_id).strip()
    if not sub_id_norm:
        return
    with suppress(Exception):
        bus_name = str(bus).strip()
        payload = dict(delta or {})
        rev = payload.get("rev")
        if bus_name in _BUS_LATEST_REV and rev is not None:
            try:
                revision = int(rev)
            except Exception:
                revision = None
            if revision is not None:
                with suppress(Exception):
                    _append_recent_delta(bus_name, revision, op, payload)
                _update_bus_rev(bus_name, revision)
    watcher = _watcher_get(sub_id_norm)
    if watcher is None:
        with suppress(Exception):
            _notify_bus_listeners(bus, op, dict(delta or {}))
        return
    try:
        watcher._on_remote_change(bus=str(bus), op=str(op), delta=dict(delta or {}))
    except Exception:
        return

    try:
        _notify_bus_listeners(bus, op, dict(delta or {}))
    except Exception:
        return
