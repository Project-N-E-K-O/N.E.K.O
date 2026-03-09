"""Lifecycle bus facade."""

from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.public.bus.lifecycle import Lifecycle as _ImplLifecycle
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase


class Lifecycle(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="lifecycle")
        self._impl = _ImplLifecycle(self._transport)
        self._state = self._impl._state

    async def emit(self, stage: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 5.0) -> Result[None, Exception]:
        if not isinstance(stage, str) or stage.strip() == "":
            return Err(ValueError("stage must be non-empty"))
        return await self._forward_result("bus.lifecycle.emit", self._impl.emit, stage, payload=payload, timeout=timeout)


__all__ = ["Lifecycle"]
