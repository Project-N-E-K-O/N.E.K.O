"""Process-local temporary image storage shared by plugin media transports and HTTP."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass


DEFAULT_IMAGE_STORE_MAX_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageRecord:
    data: bytes
    mime: str


class ImageStore:
    """Thread-safe, byte-bounded LRU store for the current server lifetime."""

    def __init__(self, *, max_bytes: int = DEFAULT_IMAGE_STORE_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = int(max_bytes)
        self._size_bytes = 0
        self._records: OrderedDict[str, ImageRecord] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, data: bytes, *, mime: str) -> str:
        payload = bytes(data)
        if not payload:
            raise ValueError("image data must not be empty")
        if len(payload) > self._max_bytes:
            raise ValueError("image exceeds the temporary store byte budget")
        image_id = hashlib.sha256(payload).hexdigest()
        record = ImageRecord(data=payload, mime=str(mime or "application/octet-stream"))
        with self._lock:
            existing = self._records.pop(image_id, None)
            if existing is not None:
                self._size_bytes -= len(existing.data)
            self._records[image_id] = record
            self._size_bytes += len(payload)
            while self._size_bytes > self._max_bytes:
                _evicted_id, evicted = self._records.popitem(last=False)
                self._size_bytes -= len(evicted.data)
        return image_id

    def get(self, image_id: str) -> ImageRecord | None:
        key = str(image_id)
        with self._lock:
            record = self._records.pop(key, None)
            if record is None:
                return None
            self._records[key] = record
            return record

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._size_bytes = 0


_IMAGE_STORE = ImageStore()


def get_image_store() -> ImageStore:
    return _IMAGE_STORE


__all__ = ["ImageRecord", "ImageStore", "get_image_store"]
