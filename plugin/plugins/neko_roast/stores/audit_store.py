"""Recent audit ring for Neko Roast."""

from __future__ import annotations

from typing import Any

from ..core.contracts import utc_now_iso


class AuditStore:
    def __init__(self, limit: int = 100) -> None:
        self.limit = max(1, limit)
        self._events: list[dict[str, Any]] = []

    def set_limit(self, limit: int) -> None:
        self.limit = max(1, limit)
        if len(self._events) > self.limit:
            self._events = self._events[-self.limit :]

    def record(self, op: str, message: str, *, level: str = "info", detail: dict[str, Any] | None = None) -> None:
        item = {
            "at": utc_now_iso(),
            "op": op,
            "level": level,
            "message": message,
            "detail": detail or {},
        }
        self._events.append(item)
        if len(self._events) > self.limit:
            self._events = self._events[-self.limit :]

    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        cap = limit or self.limit
        return list(reversed(self._events[-cap:]))
