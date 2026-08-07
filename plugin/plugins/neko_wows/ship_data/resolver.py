"""Deterministic exact-name resolution for telemetry ship objects."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import CatalogShip, ShipResolution

_SPACE_RE = re.compile(r"\s+")

_CLASS_ALIASES = {
    "battleship": "Battleship",
    "battle ship": "Battleship",
    "战列舰": "Battleship",
    "戰列艦": "Battleship",
    "cruiser": "Cruiser",
    "巡洋舰": "Cruiser",
    "巡洋艦": "Cruiser",
    "destroyer": "Destroyer",
    "驱逐舰": "Destroyer",
    "驅逐艦": "Destroyer",
    "aircarrier": "AirCarrier",
    "air carrier": "AirCarrier",
    "aircraft carrier": "AirCarrier",
    "航空母舰": "AirCarrier",
    "航空母艦": "AirCarrier",
    "submarine": "Submarine",
    "潜艇": "Submarine",
    "潛艇": "Submarine",
}


def normalize_ship_alias(value: Any) -> str:
    """Return the exact catalog lookup key used by both builder and runtime."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _SPACE_RE.sub(" ", normalized).strip()


def _canonical_ship_class(value: Any) -> str | None:
    normalized = normalize_ship_alias(value)
    return _CLASS_ALIASES.get(normalized)


class ShipResolver:
    """Resolve only exact aliases, using tier/class solely for disambiguation."""

    def __init__(self, snapshot: Any) -> None:
        self.snapshot = snapshot

    def resolve(
        self,
        name: Any,
        *,
        tier: int | None = None,
        ship_type: str | None = None,
    ) -> ShipResolution:
        query = name if isinstance(name, str) else ""
        normalized = normalize_ship_alias(name)
        meta = getattr(self.snapshot, "meta", None)
        if not normalized:
            return ShipResolution(query, normalized, "alias_not_found", meta=meta)

        candidates = self._unique_candidates(
            self.snapshot.alias_candidates(normalized))
        if not candidates:
            return ShipResolution(query, normalized, "alias_not_found", meta=meta)

        if len(candidates) > 1 and isinstance(tier, int) and not isinstance(tier, bool):
            candidates = tuple(ship for ship in candidates if ship.tier == tier)
        if not candidates:
            return ShipResolution(query, normalized, "alias_not_found", meta=meta)

        canonical_class = _canonical_ship_class(ship_type)
        if len(candidates) > 1 and canonical_class is not None:
            candidates = tuple(
                ship for ship in candidates if ship.ship_class == canonical_class)
        if not candidates:
            return ShipResolution(query, normalized, "alias_not_found", meta=meta)
        if len(candidates) != 1:
            return ShipResolution(query, normalized, "ambiguous_alias", meta=meta)

        ship = candidates[0]
        profile = self.snapshot.primary_profile(ship.ship_id)
        if profile is None:
            return ShipResolution(
                query,
                normalized,
                "profile_not_found",
                meta=meta,
                ship=ship,
            )
        return ShipResolution(
            query,
            normalized,
            "resolved",
            meta=meta,
            ship=ship,
            profile=profile,
        )

    @staticmethod
    def _unique_candidates(candidates: Any) -> tuple[CatalogShip, ...]:
        unique: dict[int, CatalogShip] = {}
        for candidate in candidates or ():
            if isinstance(candidate, CatalogShip):
                unique.setdefault(candidate.ship_id, candidate)
        return tuple(unique[key] for key in sorted(unique))


__all__ = ["ShipResolver", "normalize_ship_alias"]
