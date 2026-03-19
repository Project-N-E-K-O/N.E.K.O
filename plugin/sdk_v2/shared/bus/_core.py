"""Shared core helpers for bus list/watcher structures."""

from __future__ import annotations

from plugin.sdk_v2.shared.bus.types import BusList


class BusListCore(BusList):
    """Compatibility-oriented core list type for shared bus."""
    pass


__all__ = ["BusListCore"]
