"""Lifecycle bus facade."""

from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.public.bus.lifecycle import Lifecycle as _ImplLifecycle
from plugin.sdk_v2.shared.models import Err, Result

from ._facade import BusFacadeMixin
from .types import BusEvent, BusList


class LifecycleRecord(BusEvent):
    pass


class LifecycleList(BusList[LifecycleRecord]):
    pass


class Lifecycle(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplLifecycle, _transport, namespace="lifecycle")

    async def emit(self, stage: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 5.0) -> Result[None, Exception]:
        stage_ok = self._require_non_empty_str("stage", stage)
        if isinstance(stage_ok, Err):
            return stage_ok
        return await self._call("bus.lifecycle.emit", self._impl.emit, stage_ok, payload=payload, timeout=timeout)


class LifecycleClient:
    def __init__(self, _transport=None):
        self._impl = Lifecycle(_transport)

    async def get(self, *, max_count: int = 100, timeout: float = 5.0) -> LifecycleList:
        items = [LifecycleRecord(id=event.id, event_type=event.event_type, payload=event.payload, timestamp=event.timestamp) for event in self._impl._state.events if event.event_type.startswith('lifecycle:')]
        return LifecycleList(items[:max_count])

    async def get_async(self, **kwargs) -> LifecycleList:
        return await self.get(**kwargs)


__all__ = ["Lifecycle", "LifecycleRecord", "LifecycleList", "LifecycleClient"]
