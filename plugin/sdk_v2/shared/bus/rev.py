"""Revision bus facade."""

from __future__ import annotations

from plugin.sdk_v2.public.bus.rev import Revision as _ImplRevision
from plugin.sdk_v2.shared.models import Err, Result

from ._client_base import BusClientBase


class Revision(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="revision")
        self._impl = _ImplRevision(self._transport)
        self._state = self._impl._state

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[int, Exception]:
        if not isinstance(namespace, str) or namespace.strip() == "":
            return Err(ValueError("namespace must be non-empty"))
        if not isinstance(record_id, str) or record_id.strip() == "":
            return Err(ValueError("record_id must be non-empty"))
        return await self._forward_result("bus.revision.get", self._impl.get, namespace, record_id, timeout=timeout)

    async def compare(self, namespace: str, record_id: str, expected: int, *, timeout: float = 10.0) -> Result[bool, Exception]:
        if not isinstance(expected, int):
            return Err(ValueError("expected must be int"))
        return await self._forward_result("bus.revision.compare", self._impl.compare, namespace, record_id, expected, timeout=timeout)


__all__ = ["Revision"]
