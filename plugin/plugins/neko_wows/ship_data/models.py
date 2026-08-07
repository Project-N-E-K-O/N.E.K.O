"""Immutable public value objects for the offline ship catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CatalogMeta:
    schema_version: int
    catalog_version: str
    game_version: str
    channel: str
    source_repo: str
    source_commit: str
    content_sha256: str
    default_language: str
    ship_count: int
    profile_count: int


@dataclass(frozen=True)
class CatalogShip:
    ship_id: int
    ship_index: str
    name_key: str
    display_name: str
    nation: str
    ship_class: str
    tier: int
    is_premium: bool = False
    is_special: bool = False
    is_paper: bool = False
    availability_group: str = ""


@dataclass(frozen=True)
class ShipProfile:
    profile_id: str
    ship_id: int
    configuration: str
    variant_key: str
    is_primary: bool
    profile_schema_version: int
    data: Mapping[str, Any]
    profile_sha256: str


@dataclass(frozen=True)
class ShipCounts:
    """Observed instances of one exact ship type in the current battle."""

    self_count: int = 0
    ally_count: int = 0
    enemy_count: int = 0


@dataclass(frozen=True)
class ShipResolution:
    """One deterministic catalog lookup, successful or unresolved."""

    query: str
    normalized_query: str
    reason: str
    meta: CatalogMeta | None = None
    ship: CatalogShip | None = None
    profile: ShipProfile | None = None

    @property
    def resolved(self) -> bool:
        return self.reason == "resolved" and self.ship is not None and self.profile is not None


__all__ = [
    "CatalogMeta",
    "CatalogShip",
    "ShipCounts",
    "ShipProfile",
    "ShipResolution",
]
