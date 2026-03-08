"""Record bus contracts."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Result

from ._client_base import BusClientBase
from .types import BusRecord


class RecordConflictError(RuntimeError):
    """Record revision conflict or invalid write."""


class Records(BusClientBase):
    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.records not implemented")

    async def list(self, namespace: str, *, limit: int = 100, timeout: float = 10.0) -> Result[list[BusRecord], Exception]:
        raise NotImplementedError

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        raise NotImplementedError

    async def put(self, namespace: str, record_id: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[BusRecord, Exception]:
        raise NotImplementedError

    async def delete(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        raise NotImplementedError


__all__ = ["Records", "RecordConflictError"]
