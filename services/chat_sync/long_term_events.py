"""Process-local hints for successfully persisted learned-memory organizations."""

import threading

_lock = threading.Lock()
_listeners = set()


def subscribe(callback):
    with _lock:
        _listeners.add(callback)
    def unsubscribe():
        with _lock:
            _listeners.discard(callback)
    return unsubscribe


def changed():
    with _lock:
        listeners = tuple(_listeners)
    for callback in listeners:
        try:
            callback()
        except Exception:
            pass
