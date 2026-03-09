from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.shared.models import Ok, Result

from ._client_base import BusClientBase


class Lifecycle(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="lifecycle")

    async def emit(self, stage: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 5.0) -> Result[None, Exception]:
        await self._transport.publish(f"bus.lifecycle.{stage}", {"stage": stage, "payload": dict(payload or {})}, timeout=timeout)
        return Ok(None)


__all__ = ["Lifecycle"]
