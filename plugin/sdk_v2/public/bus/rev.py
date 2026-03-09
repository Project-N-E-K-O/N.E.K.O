from __future__ import annotations

from plugin.sdk_v2.shared.models import Ok, Result

from ._client_base import BusClientBase


class Revision(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="revision")

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[int, Exception]:
        return Ok(int(self._state.revisions.get((namespace, record_id), 0)))

    async def compare(self, namespace: str, record_id: str, expected: int, *, timeout: float = 10.0) -> Result[bool, Exception]:
        return Ok(int(self._state.revisions.get((namespace, record_id), 0)) == expected)


__all__ = ["Revision"]
