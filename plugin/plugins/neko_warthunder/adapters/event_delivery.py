"""Host-facing delivery envelope for one selected battle event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventDelivery:
    """Immutable delivery plan built before crossing the host push boundary."""

    text: str
    ai_behavior: str
    visibility: tuple[str, ...]
    metadata: dict[str, Any]
    target_lanlan: str = ""

    def push_kwargs(self, *, priority: int, coalesce_key: str) -> dict[str, Any]:
        return {
            "source": "neko_warthunder",
            "visibility": list(self.visibility),
            "ai_behavior": self.ai_behavior,
            "parts": [{"type": "text", "text": self.text}],
            "priority": priority,
            "coalesce_key": coalesce_key,
            "metadata": self.metadata,
            "target_lanlan": self.target_lanlan or None,
        }
