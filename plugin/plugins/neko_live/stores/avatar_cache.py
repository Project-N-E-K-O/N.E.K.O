"""Small in-memory avatar cache."""

from __future__ import annotations

from collections import deque


class AvatarCache:
    def __init__(self, max_items: int = 32, max_bytes: int = 4 * 1024 * 1024) -> None:
        self.max_items = max(1, max_items)
        self.max_bytes = max(1, max_bytes)
        self._items: dict[str, tuple[bytes, str]] = {}
        self._order: deque[str] = deque()
        self._bytes = 0

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
        item_size = len(data)
        if item_size > self.max_bytes:
            return
        if key in self._items:
            old_data, _old_mime = self._items[key]
            self._bytes -= len(old_data)
            try:
                self._order.remove(key)
            except ValueError:
                pass
        self._order.append(key)
        self._items[key] = (data, mime)
        self._bytes += item_size
        while len(self._order) > self.max_items or self._bytes > self.max_bytes:
            old = self._order.popleft()
            removed = self._items.pop(old, None)
            if removed is not None:
                self._bytes -= len(removed[0])

    def status(self) -> dict[str, int]:
        return {
            "items": len(self._items),
            "max_items": self.max_items,
            "bytes": self._bytes,
            "max_bytes": self.max_bytes,
        }
