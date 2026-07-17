"""Idle-hosting beat material accessors for NEKO Live."""

from __future__ import annotations

from typing import Any

from .live_content_host_catalog import IDLE_HOSTING_BEAT_CANDIDATES


def idle_hosting_beat_candidates() -> list[dict[str, Any]]:
    return [dict(candidate) for candidate in IDLE_HOSTING_BEAT_CANDIDATES]
