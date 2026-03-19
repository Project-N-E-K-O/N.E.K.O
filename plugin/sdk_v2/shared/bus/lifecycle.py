"""Lifecycle bus facade."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import BusError, BusErrorLike, InvalidArgumentError, TransportError

from ._facade import BusFacadeMixin
from .types import BusEvent, BusList


class LifecycleRecord(BusEvent):
    pass


class LifecycleList(BusList[LifecycleRecord]):
    pass


class Lifecycle(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup(_transport, namespace="lifecycle")

    async def _do_emit(self, stage: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 5.0) -> Result[None, BusErrorLike]:
        trimmed_stage = stage.strip() if isinstance(stage, str) else ""
        if trimmed_stage == "":
            return Err(InvalidArgumentError("stage must be non-empty"))
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return Err(InvalidArgumentError("timeout must be > 0"))
        if payload is not None and not isinstance(payload, Mapping):
            return Err(InvalidArgumentError("payload must be an object"))
        payload_obj = dict(payload or {})
        try:
            sent = await self._transport.publish(
                f"bus.lifecycle.{trimmed_stage}",
                {"stage": trimmed_stage, "payload": payload_obj},
                timeout=timeout,
            )
            if isinstance(sent, Err):
                error = sent.error if isinstance(sent.error, Exception) else TransportError(
                    str(sent.error),
                    op_name="bus.lifecycle.emit",
                    channel=f"bus.lifecycle.{trimmed_stage}",
                    timeout=timeout,
                )
                if isinstance(error, (BusError, InvalidArgumentError, TransportError)):
                    return Err(error)
                return Err(
                    TransportError(
                        str(error),
                        op_name="bus.lifecycle.emit",
                        channel=f"bus.lifecycle.{trimmed_stage}",
                        timeout=timeout,
                    )
                )
            return Ok(None)
        except Exception as error:
            if isinstance(error, (BusError, InvalidArgumentError, TransportError)):
                return Err(error)
            return Err(
                TransportError(
                    str(error),
                    op_name="bus.lifecycle.emit",
                    channel=f"bus.lifecycle.{trimmed_stage}",
                    timeout=timeout,
                )
            )

    async def emit(self, stage: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 5.0) -> Result[None, BusErrorLike]:
        stage_ok = self._require_non_empty_str("stage", stage)
        if isinstance(stage_ok, Err):
            return stage_ok
        return await self._call("bus.lifecycle.emit", self._do_emit, stage_ok.value, payload=payload, timeout=timeout)


class LifecycleClient:
    def __init__(self, _transport=None):
        self._impl = Lifecycle(_transport)

    async def get(self, *, max_count: int = 100, timeout: float = 5.0) -> LifecycleList:
        if max_count < 0:
            raise ValueError("max_count must be >= 0")
        await asyncio.wait_for(asyncio.sleep(0), timeout=timeout)
        items = [LifecycleRecord(id=event.id, event_type=event.event_type, payload=event.payload, timestamp=event.timestamp) for event in self._impl._state.events if event.event_type.startswith('lifecycle:')]
        return LifecycleList(items[:max_count])

__all__ = ["Lifecycle", "LifecycleClient", "LifecycleList", "LifecycleRecord"]
