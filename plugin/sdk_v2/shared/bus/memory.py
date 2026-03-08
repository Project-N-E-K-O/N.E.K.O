"""Memory bus contracts."""

from __future__ import annotations

from typing import Any, Mapping

from plugin.sdk_v2.shared.models import Result
from ._client_base import BusClientBase


class Memory(BusClientBase):
    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.memory not implemented")

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[Any, Exception]:
        raise NotImplementedError

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[Mapping[str, Any]], Exception]:
        raise NotImplementedError


__all__ = ["Memory"]
