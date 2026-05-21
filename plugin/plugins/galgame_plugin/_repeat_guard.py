from __future__ import annotations

from enum import Enum
from typing import Any

from ._gateway_utils import (
    _REPEAT_GUARD_MAX_ITEMS,
    _json_payload_copy,
    _response_similarity,
    _stable_json_fingerprint,
)


class PluginErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    BUSY = "busy"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    ENTRY_UNAVAILABLE = "entry_unavailable"
    INTERNAL_ERROR = "internal_error"



class ResponseRepeatGuard:
    def __init__(self, *, max_items: int = _REPEAT_GUARD_MAX_ITEMS) -> None:
        self._max_items = max(1, int(max_items))
        self._recent: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._recent.clear()

    def is_repeat(self, response: dict[str, Any], *, threshold: float) -> bool:
        threshold = max(0.0, min(float(threshold), 1.0))
        fingerprint = _stable_json_fingerprint(response)
        for item in self._recent:
            if fingerprint == str(item.get("fingerprint") or ""):
                return True
            previous = item.get("response")
            if _response_similarity(response, previous) >= threshold:
                return True
        return False

    def record(self, response: dict[str, Any]) -> None:
        payload = _json_payload_copy(response)
        self._recent.append(
            {
                "fingerprint": _stable_json_fingerprint(payload),
                "response": payload,
            }
        )
        if len(self._recent) > self._max_items:
            del self._recent[: len(self._recent) - self._max_items]
