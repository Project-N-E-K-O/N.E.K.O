"""Revision bus facade and change-listener helpers."""

from __future__ import annotations

from plugin.sdk_v2.public.bus._changes import dispatch_bus_change, register_bus_change_listener
from plugin.sdk_v2.public.bus.rev import Revision as _ImplRevision
from plugin.sdk_v2.shared.models import Result

from ._facade import BusFacadeMixin


class Revision(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplRevision, _transport, namespace="revision")

    async def get(self, namespace: str, record_id: str, *, timeout: float = 10.0) -> Result[int, Exception]:
        namespace_ok = self._require_non_empty_str("namespace", namespace)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id)
        if not self._is_ok(record_ok):
            return record_ok
        return await self._call("bus.revision.get", self._impl.get, namespace_ok, record_ok, timeout=timeout)

    async def compare(self, namespace: str, record_id: str, expected: int, *, timeout: float = 10.0) -> Result[bool, Exception]:
        namespace_ok = self._require_non_empty_str("namespace", namespace)
        if not self._is_ok(namespace_ok):
            return namespace_ok
        record_ok = self._require_non_empty_str("record_id", record_id)
        if not self._is_ok(record_ok):
            return record_ok
        expected_ok = self._require_int("expected", expected)
        if not self._is_ok(expected_ok):
            return expected_ok
        return await self._call("bus.revision.compare", self._impl.compare, namespace_ok, record_ok, expected_ok, timeout=timeout)


__all__ = ["Revision", "register_bus_change_listener", "dispatch_bus_change"]
