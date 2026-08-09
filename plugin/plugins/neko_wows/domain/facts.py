"""Deterministic facts derived from one frame.

Every value here is either a measurement or `None`. `None` means "not knowable
from this frame", never "zero" and never "false" -- detectors branch on that
distinction, and the wording rules downstream depend on it (you cannot claim a
ship is showing its side if no heading was reported).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .snapshot import (
    DOMAIN_BALLISTICS,
    DOMAIN_DAMAGE,
    DOMAIN_MAP_BOUNDS,
    DOMAIN_OBJECTS,
    DOMAIN_ROSTER,
    DOMAIN_SELF,
    Ship,
    WowsSnapshot,
)


@dataclass(frozen=True)
class ThreatBearing:
    ship: Ship
    distance_m: float
    bearing_deg: float


@dataclass(frozen=True)
class WowsFacts:
    """One frame's measurements, shared by every detector."""

    seq: int = 0
    at: float = 0.0
    battle_id: str | None = None

    own_hp_ratio: float | None = None
    own_health: float | None = None
    own_max_health: float | None = None
    own_alive: bool | None = None
    own_speed: float | None = None
    own_heading_deg: float | None = None

    alive_allies: int | None = None
    alive_enemies: int | None = None
    visible_enemies: int | None = None

    nearest_enemy: ThreatBearing | None = None
    nearest_ally_distance_m: float | None = None
    threats_in_scan_range: tuple[ThreatBearing, ...] = ()
    threat_bearing_spread_deg: float | None = None

    distance_to_boundary_m: float | None = None
    heading_towards_boundary: bool | None = None

    own_broadside_angle_deg: float | None = None
    exposed_target: ThreatBearing | None = None
    exposed_target_angle_deg: float | None = None

    best_target: ThreatBearing | None = None
    lowest_hp_target: ThreatBearing | None = None

    damage_inflicted: float | None = None
    ammo_type: str | None = None
    penetration_mm: float | None = None

    # Which domains actually backed these numbers, for the panel timeline.
    sourced_domains: tuple[str, ...] = ()
    notes: dict[str, object] = field(default_factory=dict)


def _distance(ax: float, az: float, bx: float, bz: float) -> float:
    return math.hypot(bx - ax, bz - az)


def _bearing_deg(from_x: float, from_z: float, to_x: float, to_z: float) -> float:
    """Compass-style bearing in degrees, 0 = +Z (north), clockwise."""
    return math.degrees(math.atan2(to_x - from_x, to_z - from_z)) % 360.0


def _angle_between(a_deg: float, b_deg: float) -> float:
    """Smallest absolute separation between two bearings, 0..180."""
    delta = abs(a_deg - b_deg) % 360.0
    return 360.0 - delta if delta > 180.0 else delta


def _bearing_spread(bearings: tuple[float, ...]) -> float | None:
    """Widest gap-complement across bearings: how spread out the threats are.

    Sorting the bearings and taking 360 minus the largest empty arc gives the
    angular width of the cone the threats actually occupy, which is what
    "coming from several directions" means.
    """
    if len(bearings) < 2:
        return None
    ordered = sorted(b % 360.0 for b in bearings)
    largest_gap = (ordered[0] + 360.0) - ordered[-1]
    for first, second in zip(ordered, ordered[1:]):
        largest_gap = max(largest_gap, second - first)
    return 360.0 - largest_gap


def _yaw_to_deg(yaw: float | None) -> float | None:
    if yaw is None:
        return None
    return math.degrees(float(yaw)) % 360.0


def _broadside_angle(heading_deg: float, bearing_deg: float) -> float:
    """0 = bow/stern on to the bearing, 90 = fully broadside to it."""
    offset = _angle_between(heading_deg, bearing_deg)
    return 90.0 - abs(offset - 90.0) if offset <= 180.0 else 0.0


class FactBuilder:
    """Turns a snapshot into `WowsFacts`. Pure: no memory between frames."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def build(self, snapshot: WowsSnapshot) -> WowsFacts:
        sourced: list[str] = []
        own = snapshot.self_ship if snapshot.is_available(DOMAIN_SELF) else None
        if own is not None:
            sourced.append(DOMAIN_SELF)

        objects_ok = snapshot.is_available(DOMAIN_OBJECTS)
        roster_ok = snapshot.is_available(DOMAIN_ROSTER)
        if objects_ok:
            sourced.append(DOMAIN_OBJECTS)
        if roster_ok:
            sourced.append(DOMAIN_ROSTER)

        # Alive counts come from objects (incl. last-known) and/or the full
        # match roster in meta. Threat geometry stays visible-only: a dark
        # last-known x/z must not become nearest_enemy or a closing edge.
        count_ok = objects_ok or roster_ok
        known_enemies = snapshot.enemies(visible_only=False) if count_ok else ()
        own_side = snapshot.own_side(visible_only=False) if count_ok else ()
        visible_enemies = snapshot.enemies(visible_only=True) if objects_ok else ()
        visible_allies = snapshot.allies(visible_only=True) if objects_ok else ()

        own_heading = _yaw_to_deg(own.yaw) if own is not None else None

        threats: tuple[ThreatBearing, ...] = ()
        nearest_enemy: ThreatBearing | None = None
        nearest_ally_distance: float | None = None
        if own is not None and own.has_position and objects_ok:
            visible_enemy_bearings = self._enemy_bearings(own, visible_enemies)
            # Exact telemetry covers every visible enemy; only tactical threat
            # consumers are intentionally capped by the configured scan range.
            nearest_enemy = (
                visible_enemy_bearings[0] if visible_enemy_bearings else None
            )
            threats = tuple(
                bearing for bearing in visible_enemy_bearings
                if bearing.distance_m <= self.cfg.threat_scan_range_m
            )
            nearest_ally_distance = self._nearest_ally_distance(own, visible_allies)

        bearings = tuple(t.bearing_deg for t in threats)
        spread = _bearing_spread(bearings) if len(bearings) >= 2 else None

        boundary_distance, heading_out = self._boundary(snapshot, own, own_heading)
        if boundary_distance is not None:
            sourced.append(DOMAIN_MAP_BOUNDS)

        own_broadside = None
        if own_heading is not None and nearest_enemy is not None:
            own_broadside = _broadside_angle(own_heading, nearest_enemy.bearing_deg)

        exposed, exposed_angle = self._exposed_target(own, threats)

        damage = snapshot.damage_inflicted if snapshot.is_available(DOMAIN_DAMAGE) else None
        if damage is not None:
            sourced.append(DOMAIN_DAMAGE)

        ammo_type = None
        penetration = None
        if snapshot.is_available(DOMAIN_BALLISTICS):
            sourced.append(DOMAIN_BALLISTICS)
            raw_type = snapshot.ballistics.get("ammoType")
            ammo_type = str(raw_type) if isinstance(raw_type, str) else None
            raw_pen = snapshot.ballistics.get("penetration")
            if isinstance(raw_pen, (int, float)) and not isinstance(raw_pen, bool):
                penetration = float(raw_pen)

        return WowsFacts(
            seq=snapshot.seq,
            at=snapshot.received_at,
            battle_id=snapshot.battle_id,
            own_hp_ratio=own.hp_ratio if own is not None else None,
            own_health=own.health if own is not None else None,
            own_max_health=own.max_health if own is not None else None,
            own_alive=(own.health is not None and own.health > 0) if own is not None else None,
            own_speed=own.speed if own is not None else None,
            own_heading_deg=own_heading,
            alive_allies=len(own_side) if count_ok else None,
            alive_enemies=len(known_enemies) if count_ok else None,
            visible_enemies=len(visible_enemies) if objects_ok else None,
            nearest_enemy=nearest_enemy,
            nearest_ally_distance_m=nearest_ally_distance,
            threats_in_scan_range=threats,
            threat_bearing_spread_deg=spread,
            distance_to_boundary_m=boundary_distance,
            heading_towards_boundary=heading_out,
            own_broadside_angle_deg=own_broadside,
            exposed_target=exposed,
            exposed_target_angle_deg=exposed_angle,
            best_target=self._best_target(threats),
            lowest_hp_target=self._lowest_hp_target(threats),
            damage_inflicted=damage,
            ammo_type=ammo_type,
            penetration_mm=penetration,
            sourced_domains=tuple(sourced),
        )

    # ------------------------------------------------------------------
    def _enemy_bearings(self, own, enemies) -> tuple[ThreatBearing, ...]:
        """Return every positioned visible enemy, nearest first."""
        found: list[ThreatBearing] = []
        for enemy in enemies:
            if not enemy.has_position:
                continue
            distance = _distance(own.x, own.z, enemy.x, enemy.z)
            found.append(ThreatBearing(
                ship=enemy,
                distance_m=distance,
                bearing_deg=_bearing_deg(own.x, own.z, enemy.x, enemy.z),
            ))
        found.sort(key=lambda t: t.distance_m)
        return tuple(found)

    @staticmethod
    def _nearest_ally_distance(own, allies) -> float | None:
        distances = [
            _distance(own.x, own.z, ally.x, ally.z)
            for ally in allies
            if ally.has_position and ally.player_id != own.player_id
        ]
        return min(distances) if distances else None

    def _boundary(self, snapshot, own, own_heading):
        if snapshot.bounds is None or own is None or not own.has_position:
            return None, None
        if not snapshot.is_available(DOMAIN_MAP_BOUNDS):
            return None, None
        min_x, max_x, min_z, max_z = snapshot.bounds
        margins = {
            0.0: max_z - own.z,      # north edge
            90.0: max_x - own.x,     # east edge
            180.0: own.z - min_z,    # south edge
            270.0: own.x - min_x,    # west edge
        }
        nearest_bearing, distance = min(margins.items(), key=lambda item: item[1])
        heading_out = None
        if own_heading is not None:
            heading_out = _angle_between(own_heading, nearest_bearing) <= 60.0
        return max(0.0, distance), heading_out

    def _exposed_target(self, own, threats):
        """The nearest enemy that is currently showing us its side.

        Requires both a heading for the enemy and a position for us, which is why
        it stays `None` far more often than the distance-only facts.
        """
        if own is None or not own.has_position:
            return None, None
        best: ThreatBearing | None = None
        best_angle: float | None = None
        for threat in threats:
            enemy_heading = _yaw_to_deg(threat.ship.yaw)
            if enemy_heading is None:
                continue
            # Bearing from the enemy back to us.
            reverse = (threat.bearing_deg + 180.0) % 360.0
            angle = _broadside_angle(enemy_heading, reverse)
            if best_angle is None or angle > best_angle:
                best, best_angle = threat, angle
        return best, best_angle

    @staticmethod
    def _best_target(threats) -> ThreatBearing | None:
        """Closest low-health enemy first; distance decides ties.

        This is a candidate for the model to mention, not an order: the plugin
        has no idea what the player's guns are actually doing.
        """
        scored = [t for t in threats if t.ship.hp_ratio is not None]
        if not scored:
            return threats[0] if threats else None
        scored.sort(key=lambda t: (t.ship.hp_ratio, t.distance_m))
        return scored[0]

    @staticmethod
    def _lowest_hp_target(threats) -> ThreatBearing | None:
        scored = [t for t in threats if t.ship.hp_ratio is not None]
        if not scored:
            return None
        return min(scored, key=lambda t: t.ship.hp_ratio)
