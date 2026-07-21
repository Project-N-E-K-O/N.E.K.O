"""Small in-memory avatar cache."""

from __future__ import annotations

from collections import deque


class AvatarCache:
    def __init__(self, max_items: int = 128) -> None:
        self.max_items = max(1, max_items)
        self._items: dict[str, tuple[bytes, str]] = {}
        self._order: deque[str] = deque()

    def get(self, key: str) -> tuple[bytes, str] | None:
        item = self._items.get(key)
        if item is None:
            return None
        try:
            self._order.remove(key)
        except ValueError:
            pass
        self._order.append(key)
        return item

    def put(self, key: str, data: bytes, mime: str) -> None:
        if not key or not data:
            return
        if key in self._items:
            try:
                self._order.remove(key)
            except ValueError:
                pass
        self._order.append(key)
        self._items[key] = (data, mime)
        while len(self._order) > self.max_items:
            old = self._order.popleft()
            self._items.pop(old, None)

    def status(self) -> dict[str, int]:
        return {"items": len(self._items), "max_items": self.max_items}
