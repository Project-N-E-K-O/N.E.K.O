"""Shared structured routing contract for analyzer events."""

from __future__ import annotations

from typing import Any


ANALYZE_ROUTE_OWNER_PUBLIC_KNOWLEDGE = "public_knowledge"
_ANALYZE_ROUTE_OWNERS = frozenset({ANALYZE_ROUTE_OWNER_PUBLIC_KNOWLEDGE})


def normalize_analyze_route_owner(value: Any) -> str | None:
    """Return a supported route owner, failing open for absent/unknown values."""
    return value if isinstance(value, str) and value in _ANALYZE_ROUTE_OWNERS else None
