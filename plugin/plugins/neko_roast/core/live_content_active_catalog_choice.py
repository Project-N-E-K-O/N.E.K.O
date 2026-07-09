"""Compatibility aggregate for active-engagement choice fallback topics."""

from __future__ import annotations

from typing import Any

from .live_content_active_catalog_choice_props import (
    PROP_CHOICE_FALLBACK_TOPIC_CANDIDATES,
)
from .live_content_active_catalog_choice_room import (
    ROOM_CHOICE_FALLBACK_TOPIC_CANDIDATES,
)
from .live_content_active_catalog_choice_verdict import (
    VERDICT_CHOICE_FALLBACK_TOPIC_CANDIDATES,
)


CHOICE_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    *ROOM_CHOICE_FALLBACK_TOPIC_CANDIDATES,
    *PROP_CHOICE_FALLBACK_TOPIC_CANDIDATES,
    *VERDICT_CHOICE_FALLBACK_TOPIC_CANDIDATES,
)
