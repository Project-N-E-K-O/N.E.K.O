"""Shared core helpers for bus list/watcher structures."""

from __future__ import annotations

from plugin.sdk_v2.shared.bus.types import BusList


class BusListCore(BusList):
    """Compatibility-oriented core list type for shared bus."""

    def reload_with(self, factory) -> "BusListCore":
        base = super().reload_with(factory)
        return BusListCore(base.items, trace=base.trace, fast_mode=base.fast_mode, _reload_factory=base._reload_factory)

    def reload(self, ctx=None) -> "BusListCore":
        base = super().reload(ctx)
        return BusListCore(base.items, trace=base.trace, fast_mode=base.fast_mode, _reload_factory=base._reload_factory)

    async def reload_async(self, ctx=None) -> "BusListCore":
        return self.reload(ctx)

    async def reload_with_async(self, factory) -> "BusListCore":
        return self.reload_with(factory)


__all__ = ["BusListCore"]
