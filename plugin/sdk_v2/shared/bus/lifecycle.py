"""Lifecycle bus facade."""

from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.public.bus.lifecycle import Lifecycle as _ImplLifecycle
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase
from ._facade import BusFacadeMixin
from .types import BusEvent, BusList


class LifecycleRecord(BusEvent):
    pass


class LifecycleList(BusList[LifecycleRecord]):
    pass


class Lifecycle(BusFacadeMixin, BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="lifecycle")
        self._impl = _ImplLifecycle(self._transport)
        self._state = self._impl._state

    async def emit(self, stage: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 5.0) -> Result[None, Exception]:
        if not isinstance(stage, str) or stage.strip() == "":
            return Err(ValueError("stage must be non-empty"))
        return await self._call("bus.lifecycle.emit", self._impl.emit, stage, payload=payload, timeout=timeout)


class LifecycleClient:
    def __init__(self, _transport=None):
        self._impl = Lifecycle(_transport)

    async def get(self, *, max_count: int = 100, timeout: float = 5.0) -> LifecycleList:
        items = [LifecycleRecord(id=event.id, event_type=event.event_type, payload=event.payload, timestamp=event.timestamp) for event in self._impl._state.events if event.event_type.startswith('lifecycle:')]
        return LifecycleList(items[:max_count])

    async def get_async(self, **kwargs) -> LifecycleList:
        return await self.get(**kwargs)


__all__ = ["Lifecycle", "LifecycleRecord", "LifecycleList", "LifecycleClient"]
