"""Neutral, synchronous capture capability; composition owns its provider.

The provider returns a snapshot, never performs network or file IO while a
recommendation/credential transaction is held.  Missing authority is held, not
an implicit license to assign today's identity to an old renderer event.
"""
from __future__ import annotations

from copy import deepcopy
from threading import RLock

_lock = RLock()
_provider = None


def install_control_provider(provider):
    global _provider
    with _lock:
        _provider = provider


def get_control_snapshot(owner: dict) -> dict | None:
    with _lock:
        provider = _provider
    result = provider(owner) if provider is not None else None
    if not isinstance(result, dict) or result.get("owner_key") != owner.get("key"):
        return None
    return deepcopy(result)
