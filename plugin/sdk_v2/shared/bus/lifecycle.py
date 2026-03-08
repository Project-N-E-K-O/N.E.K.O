"""Lifecycle bus contracts."""

from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.shared.models import Result
from ._client_base import BusClientBase


class Lifecycle(BusClientBase):
    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.lifecycle not implemented")

    async def emit(self, stage: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 5.0) -> Result[None, Exception]:
        raise NotImplementedError


__all__ = ["Lifecycle"]
