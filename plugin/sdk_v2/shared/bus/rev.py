"""Revision bus facade and change-listener helpers."""

from __future__ import annotations

from plugin.sdk_v2.shared.models import Ok, Result
from plugin.sdk_v2.shared.models.exceptions import BusErrorLike

from ._facade import BusFacadeMixin


class Revision(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup(_transport, namespace="revision")

    async def _do_get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[int, BusErrorLike]:
        return Ok(int(self._state.revisions.get((namespace, record_id), 0)))

    async def _do_compare(self, namespace: str, record_id: str, expected: int, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        return Ok(int(self._state.revisions.get((namespace, record_id), 0)) == expected)

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[int, BusErrorLike]:
        namespace_ok = self._require_non_empty_str("namespace", namespace)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id)
        if not self._is_ok(record_ok):
            return record_ok
        return await self._call("bus.revision.get", self._do_get, namespace_ok.value, record_ok.value, timeout=timeout)

    async def compare(self, namespace: str, record_id: str, expected: int, *, timeout: float = 10.0) -> Result[bool, BusErrorLike]:
        namespace_ok = self._require_non_empty_str("namespace", namespace)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id)
        if not self._is_ok(record_ok):
            return record_ok
        expected_ok = self._require_int("expected", expected)
        if not self._is_ok(expected_ok):
            return expected_ok
        return await self._call("bus.revision.compare", self._do_compare, namespace_ok.value, record_ok.value, expected_ok.value, timeout=timeout)


__all__ = ["Revision"]
