"""Revision bus contracts."""

from __future__ import annotations

from plugin.sdk_v2.shared.models import Result

from ._client_base import BusClientBase


class Revision(BusClientBase):
    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.rev not implemented")

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[int, Exception]:
        raise NotImplementedError

    async def compare(self, namespace: str, record_id: str, expected: int, *, timeout: float = 10.0) -> Result[bool, Exception]:
        raise NotImplementedError


__all__ = ["Revision"]
