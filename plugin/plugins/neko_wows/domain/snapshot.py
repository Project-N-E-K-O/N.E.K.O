"""One normalized frame of World of Warships telemetry.

`WowsSnapshot` is the only shape the rest of the plugin sees. Whether the frame
arrived from a v1 service, a pre-envelope service, WebSocket or REST, everything
downstream reads the same fields and the same per-domain availability map.

The availability distinction is load-bearing: a domain that is `unknown` or
`stale` is *not* a negative reading. Detectors that need it are blocked instead
of concluding `false`, which is what keeps a dropped connection from looking
like "all enemies disappeared".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- source status -------------------------------------------------------
STATUS_WAITING = "waiting"
STATUS_LIVE = "live"
STATUS_STALE = "stale"
STATUS_ENDED = "ended"

ALL_STATUSES = (STATUS_WAITING, STATUS_LIVE, STATUS_STALE, STATUS_ENDED)

# --- per-domain availability --------------------------------------------
AVAIL_AVAILABLE = "available"
AVAIL_UNKNOWN = "unknown"
AVAIL_STALE = "stale"
AVAIL_UNSUPPORTED = "unsupported"

# --- capability domains -------------------------------------------------
DOMAIN_SELF = "self"
DOMAIN_OBJECTS = "objects"
DOMAIN_ROSTER = "roster"
DOMAIN_DAMAGE = "damage"
DOMAIN_BALLISTICS = "ballistics"
DOMAIN_MAP_BOUNDS = "mapBounds"

# Declared by the service as not-yet-available. Listed here so a detector that
# wants them can be written now and stay permanently blocked until the matching
# extension ships, rather than silently guessing.
DOMAIN_KILLS = "kills"
DOMAIN_CAPTURE_POINTS = "capturePoints"
DOMAIN_TORPEDOES = "torpedoes"
DOMAIN_CONSUMABLES = "consumables"

CORE_DOMAINS = (
    DOMAIN_SELF,
    DOMAIN_OBJECTS,
    DOMAIN_ROSTER,
    DOMAIN_DAMAGE,
    DOMAIN_BALLISTICS,
    DOMAIN_MAP_BOUNDS,
)

FUTURE_DOMAINS = (
    DOMAIN_KILLS,
    DOMAIN_CAPTURE_POINTS,
    DOMAIN_TORPEDOES,
    DOMAIN_CONSUMABLES,
)

# `relation` values as emitted by the service.
RELATION_SELF = 0
RELATION_ALLY = 1
RELATION_ENEMY = 2


@dataclass(frozen=True)
class Ship:
    """One ship on the minimap, ally or enemy, visible or last-known."""

    ui_id: int | None = None
    player_id: int | None = None
    team_id: int | None = None
    relation: int | None = None
    ship_type: str | None = None
    name: str | None = None
    player_name: str | None = None
    tier: int | None = None
    alive: bool | None = None
    visible: bool = False
    x: float | None = None
    z: float | None = None
    yaw: float | None = None
    health: float | None = None
    max_health: float | None = None
    hp_ratio: float | None = None
    stale_seconds: float | None = None

    @property
    def is_enemy(self) -> bool:
        return self.relation == RELATION_ENEMY

    @property
    def is_ally(self) -> bool:
        return self.relation == RELATION_ALLY

    @property
    def has_position(self) -> bool:
        return self.x is not None and self.z is not None


@dataclass(frozen=True)
class SelfShip:
    player_id: int | None = None
    team_id: int | None = None
    health: float | None = None
    max_health: float | None = None
    yaw: float | None = None
    speed: float | None = None
    x: float | None = None
    z: float | None = None
    is_observer: bool = False

    @property
    def hp_ratio(self) -> float | None:
        if self.health is None or not self.max_health:
            return None
        return max(0.0, min(1.0, float(self.health) / float(self.max_health)))

    @property
    def has_position(self) -> bool:
        return self.x is not None and self.z is not None


@dataclass(frozen=True)
class WowsSnapshot:
    """Normalized frame plus the envelope needed to order and trust it."""

    # --- envelope ---
    service_id: str = ""
    api_version: str = ""
    game_version: str = ""
    instance_id: str = ""
    seq: int = 0
    battle_id: str | None = None
    status: str = STATUS_WAITING
    source_kind: str = ""
    source_mode: str = ""
    updated_at: float | None = None
    # True when the frame came from a service with no envelope and the fields
    # above were derived locally. Kept so the panel can say so out loud.
    legacy: bool = False
    capabilities: dict[str, bool] = field(default_factory=dict)
    availability: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)

    # --- body ---
    active: bool = False
    ts: float | None = None
    battle_type: str | None = None
    game_mode: str | None = None
    map_name: str | None = None
    bounds: tuple[float, float, float, float] | None = None
    self_ship: SelfShip | None = None
    ships: tuple[Ship, ...] = ()
    damage_inflicted: float | None = None
    damage_received: float | None = None
    damage_team_total: float | None = None
    ballistics: dict[str, Any] = field(default_factory=dict)

    # --- local bookkeeping ---
    # Monotonic clock reading for when the plugin accepted this frame. Wall time
    # from the service is not usable for cooldowns: `ts` restarts every battle.
    received_at: float = 0.0
    transport: str = ""
    epoch: int = 0

    # ------------------------------------------------------------------
    @property
    def cursor(self) -> tuple[str, int]:
        return (self.instance_id, self.seq)

    @property
    def identity(self) -> tuple[str, str | None]:
        """What makes detector state comparable across frames."""
        return (self.instance_id, self.battle_id)

    @property
    def is_live(self) -> bool:
        return self.status == STATUS_LIVE

    def availability_of(self, domain: str) -> str:
        return self.availability.get(domain, AVAIL_UNKNOWN)

    def is_available(self, domain: str) -> bool:
        """True only for trustworthy, fresh data in this frame."""
        return self.availability_of(domain) == AVAIL_AVAILABLE

    def missing_domains(self, domains: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(d for d in domains if not self.is_available(d))

    def supports(self, domain: str) -> bool:
        return bool(self.capabilities.get(domain, False))

    # ------------------------------------------------------------------
    @property
    def own_ship(self) -> Ship | None:
        """The roster entry for our own hull, when it can be matched."""
        own = self.self_ship
        if own is None or own.player_id is None:
            return None
        for ship in self.ships:
            if ship.player_id == own.player_id:
                return ship
        return None

    @property
    def own_ship_type(self) -> str | None:
        ship = self.own_ship
        return ship.ship_type if ship is not None else None

    @property
    def own_ship_name(self) -> str | None:
        ship = self.own_ship
        return ship.name if ship is not None else None

    def enemies(self, *, visible_only: bool = True) -> tuple[Ship, ...]:
        return tuple(
            s for s in self.ships
            if s.is_enemy and s.alive is not False and (s.visible or not visible_only)
        )

    def allies(self, *, visible_only: bool = True) -> tuple[Ship, ...]:
        return tuple(
            s for s in self.ships
            if s.is_ally and s.alive is not False and (s.visible or not visible_only)
        )

    def own_side(self, *, visible_only: bool = True) -> tuple[Ship, ...]:
        """Own team including self.

        Wire roster marks the player as ``relation=1`` (ally), while live
        objects often use ``relation=0`` (self). Team-size counts need both.
        """
        return tuple(
            s for s in self.ships
            if (s.is_ally or s.relation == RELATION_SELF)
            and s.alive is not False
            and (s.visible or not visible_only)
        )
