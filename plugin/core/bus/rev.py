from __future__ import annotations

import asyncio
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
_RECEIVER_LOOP: asyncio.AbstractEventLoop | None = None
_READ_CAPACITY_NOTIFY_PENDING = False


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


def notify_receiver_ready() -> None:
    """Hand newly registered async sinks to the persistent receive loop."""
    global _RECEIVER_LOOP, _READ_CAPACITY_NOTIFY_PENDING
    with _WATCHER_REGISTRY_LOCK:
        _RECEIVER_LOOP = asyncio.get_running_loop()
        _READ_CAPACITY_NOTIFY_PENDING = False
        watchers = tuple(_WATCHER_REGISTRY.values())
    for watcher in watchers:
        ready = getattr(watcher, "_on_receiver_ready", None)
        if callable(ready):
            with suppress(Exception):
                ready()


def activate_watcher(watcher: object) -> None:
    """Schedule only on the host's established receiver, never a startup loop."""
    with _WATCHER_REGISTRY_LOCK:
        loop = _RECEIVER_LOOP
    ready = getattr(watcher, "_on_receiver_ready", None)
    if loop is not None and not loop.is_closed() and callable(ready):
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(ready)


def notify_conversation_read_capacity() -> None:
    """Coalesce successful-read capacity hints on the persistent receiver."""
    global _READ_CAPACITY_NOTIFY_PENDING
    with _WATCHER_REGISTRY_LOCK:
        loop = _RECEIVER_LOOP
        if (
            not _WATCHER_REGISTRY or _READ_CAPACITY_NOTIFY_PENDING
            or loop is None or loop.is_closed() or not loop.is_running()
        ):
            return
        _READ_CAPACITY_NOTIFY_PENDING = True
    try:
        loop.call_soon_threadsafe(_dispatch_conversation_read_capacity)
    except RuntimeError:
        with _WATCHER_REGISTRY_LOCK:
            _READ_CAPACITY_NOTIFY_PENDING = False


def _dispatch_conversation_read_capacity() -> None:
    global _READ_CAPACITY_NOTIFY_PENDING
    with _WATCHER_REGISTRY_LOCK:
        _READ_CAPACITY_NOTIFY_PENDING = False
        watchers = tuple(_WATCHER_REGISTRY.values())
    for watcher in watchers:
        ready = getattr(watcher, "_on_read_capacity_available", None)
        if callable(ready):
            with suppress(Exception):
                ready()
