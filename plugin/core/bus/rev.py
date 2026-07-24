from __future__ import annotations

from contextlib import suppress
from threading import Lock
from typing import Dict, Optional, Protocol

__all__ = [
    "_watcher_set",
    "_watcher_pop",
    "dispatch_bus_change",
]


class _WatcherSink(Protocol):
    def _on_remote_change(self, *, bus: str, op: str, delta: Dict[str, object]) -> None: ...


_WATCHER_REGISTRY: Dict[str, _WatcherSink] = {}
_WATCHER_REGISTRY_LOCK = Lock()


def _watcher_get(sub_id: str) -> Optional[_WatcherSink]:
    with _WATCHER_REGISTRY_LOCK:
        return _WATCHER_REGISTRY.get(sub_id)


def _watcher_set(sub_id: str, watcher: _WatcherSink) -> None:
    with _WATCHER_REGISTRY_LOCK:
        _WATCHER_REGISTRY[sub_id] = watcher


def _watcher_pop(sub_id: str) -> None:
    with _WATCHER_REGISTRY_LOCK:
        _WATCHER_REGISTRY.pop(sub_id, None)


def dispatch_bus_change(
    *,
    sub_id: str,
    bus: str,
    op: str,
    delta: Optional[Dict[str, object]] = None,
) -> None:
    sub_id_norm = str(sub_id).strip()
    if not sub_id_norm:
        return
    watcher = _watcher_get(sub_id_norm)
    if watcher is None:
        return
    with suppress(Exception):
        watcher._on_remote_change(bus=str(bus), op=str(op), delta=dict(delta or {}))
