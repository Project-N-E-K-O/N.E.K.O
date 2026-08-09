"""Normalizes a service payload into `WowsSnapshot`.

Two input shapes are accepted:

* **v1** -- the payload declares `apiVersion` and carries the envelope
  (`instanceId`, `seq`, `battleId`, `source`, `capabilities`, `availability`).
* **legacy** -- a pre-envelope flat `schema: 1` snapshot. The envelope is derived
  locally so nothing downstream needs a second code path.

Wire positions and map bounds arrive in BigWorld units from `8111_for_wows`
and are converted to metres here (`BW_TO_METERS`). Downstream facts, detectors
and prompts all speak metres.

Derived values are honest about their limits: a synthesized `battleId` only
guarantees "it changes between battles", and a synthesized `seq` only guarantees
"it advances when the content changes". That is exactly what the cursor and the
detector reset rules need.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from .service_manager import SERVICE_ID
from ..domain.snapshot import (
    AVAIL_AVAILABLE,
    AVAIL_STALE,
    AVAIL_UNKNOWN,
    AVAIL_UNSUPPORTED,
    CORE_DOMAINS,
    DOMAIN_BALLISTICS,
    DOMAIN_DAMAGE,
    DOMAIN_MAP_BOUNDS,
    DOMAIN_OBJECTS,
    DOMAIN_ROSTER,
    DOMAIN_SELF,
    FUTURE_DOMAINS,
    STATUS_ENDED,
    STATUS_LIVE,
    STATUS_STALE,
    STATUS_WAITING,
    SelfShip,
    Ship,
    WowsSnapshot,
)

SUPPORTED_API_MAJOR = 1

# 8111_for_wows emits world x/z and map bounds in BigWorld units. The engine
# constant is 1 BW = 30 metres; every distance downstream is labelled `_m`.
BW_TO_METERS = 30.0

# Legacy frames are considered stale once the last content change is older than
# this; it mirrors the service-side rule so both paths age data the same way.
LEGACY_STALE_SECONDS = 2.0

# Meta-derived domains do not go stale mid-battle the way the ~10 Hz state file
# does, so a stale frame must not invalidate them.
META_DOMAINS = (DOMAIN_ROSTER, DOMAIN_MAP_BOUNDS)


class UnsupportedApiVersion(Exception):
    """Raised for an envelope whose major version we cannot interpret."""


class UnexpectedServiceIdentity(Exception):
    """Raised when a v1 envelope belongs to a different telemetry service."""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _bw_to_m(value: Any) -> float | None:
    """Convert a BigWorld length from the wire into metres, or `None`."""
    number = _number(value)
    return None if number is None else number * BW_TO_METERS


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _api_major(version: str) -> int | None:
    head = str(version or "").split(".", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


class WowsSchemaAdapter:
    """Stateful only where legacy payloads force it.

    v1 payloads pass straight through. Legacy payloads need memory to synthesize
    a cursor and a battle id, so one adapter instance must stay bound to one
    transport for its lifetime.
    """

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._legacy_instance_id = "legacy-" + hashlib.sha1(
            f"{time.time()}".encode("utf-8")).hexdigest()[:12]
        self._legacy_seq = 0
        self._legacy_fingerprint: str | None = None
        self._legacy_battle_id: str | None = None
        self._legacy_battles = 0
        self._legacy_was_active = False
        self._legacy_last_change = 0.0
        self._legacy_battle_seen = False
        # playerIds seen with alive=False in this battle. Corpses often leave
        # `objects` while remaining on the roster; stubs must not revive them.
        self._dead_player_ids: set[int] = set()
        self._death_battle_key: tuple[str, str | None] | None = None

    # ------------------------------------------------------------------
    def parse(self, raw: Mapping[str, Any], *, transport: str = "",
              epoch: int = 0, received_at: float | None = None) -> WowsSnapshot:
        payload = dict(raw or {})
        now = self._clock() if received_at is None else received_at
        api_version = _text(payload.get("apiVersion"))

        if api_version is None:
            return self._parse_legacy(payload, transport, epoch, now)

        service_id = str(payload.get("serviceId") or "")
        if service_id != SERVICE_ID:
            raise UnexpectedServiceIdentity(
                f"serviceId {service_id!r} does not match {SERVICE_ID!r}")

        major = _api_major(api_version)
        if major != SUPPORTED_API_MAJOR:
            raise UnsupportedApiVersion(
                f"apiVersion {api_version!r} is outside the supported major "
                f"version {SUPPORTED_API_MAJOR}")
        return self._parse_v1(payload, api_version, transport, epoch, now)

    # ------------------------------------------------------------------
    def _parse_v1(self, payload, api_version, transport, epoch, now) -> WowsSnapshot:
        source = payload.get("source")
        source = source if isinstance(source, dict) else {}
        capabilities = self._read_capabilities(payload.get("capabilities"))
        availability = self._read_availability(payload.get("availability"))
        seq = payload.get("seq")
        instance_id = str(payload.get("instanceId") or "")
        battle_id = _text(payload.get("battleId"))
        self._remember_battle(instance_id, battle_id)
        return WowsSnapshot(
            service_id=str(payload.get("serviceId") or ""),
            api_version=api_version,
            game_version=(
                _text(payload.get("gameVersion"))
                or _text(payload.get("game_version"))
                or ""
            ),
            instance_id=instance_id,
            seq=int(seq) if isinstance(seq, int) and not isinstance(seq, bool) else 0,
            battle_id=battle_id,
            status=self._read_status(source.get("status"), payload),
            source_kind=str(source.get("kind") or ""),
            source_mode=str(source.get("mode") or ""),
            updated_at=_number(source.get("updatedAt")),
            legacy=False,
            capabilities=capabilities,
            availability=availability,
            extensions=dict(payload.get("extensions") or {}),
            **self._body(payload),
            received_at=now,
            transport=transport,
            epoch=epoch,
        )

    def _parse_legacy(self, payload, transport, epoch, now) -> WowsSnapshot:
        fingerprint = self._fingerprint(payload)
        if fingerprint != self._legacy_fingerprint:
            self._legacy_fingerprint = fingerprint
            self._legacy_seq += 1
            self._legacy_last_change = now

        active = bool(payload.get("active"))
        if active:
            if not self._legacy_was_active or self._legacy_battle_id is None:
                self._legacy_battles += 1
                self._legacy_battle_id = (
                    f"{self._legacy_instance_id}-b{self._legacy_battles}")
            self._legacy_battle_seen = True
        self._legacy_was_active = active
        self._remember_battle(self._legacy_instance_id, self._legacy_battle_id)
        body = self._body(payload)

        if not payload:
            status = STATUS_WAITING
        elif active:
            age = now - (self._legacy_last_change or now)
            status = STATUS_STALE if age > LEGACY_STALE_SECONDS else STATUS_LIVE
        else:
            status = STATUS_ENDED if self._legacy_battle_seen else STATUS_WAITING

        capabilities = {domain: True for domain in CORE_DOMAINS}
        capabilities.update({domain: False for domain in FUTURE_DOMAINS})
        availability = self._derive_availability(body, status)

        return WowsSnapshot(
            service_id="",
            api_version="",
            game_version=(
                _text(payload.get("gameVersion"))
                or _text(payload.get("game_version"))
                or ""
            ),
            instance_id=self._legacy_instance_id,
            seq=self._legacy_seq,
            battle_id=self._legacy_battle_id,
            status=status,
            source_kind="legacy",
            source_mode="legacy",
            updated_at=_number(payload.get("ts")),
            legacy=True,
            capabilities=capabilities,
            availability=availability,
            extensions={},
            **body,
            received_at=now,
            transport=transport,
            epoch=epoch,
        )

    def _remember_battle(self, instance_id: str, battle_id: str | None) -> None:
        key = (instance_id, battle_id)
        if key == self._death_battle_key:
            return
        self._death_battle_key = key
        self._dead_player_ids.clear()

    # ------------------------------------------------------------------
    def _body(self, payload) -> dict[str, Any]:
        damage = payload.get("damage")
        damage = damage if isinstance(damage, dict) else {}
        ballistics = payload.get("ballistics")
        ballistics = ballistics if isinstance(ballistics, dict) else {}
        map_info = payload.get("map")
        map_info = map_info if isinstance(map_info, dict) else {}
        return {
            "active": bool(payload.get("active")),
            "ts": _number(payload.get("ts")),
            "battle_type": _text(payload.get("battleType")),
            "game_mode": _text(payload.get("gameMode")),
            "map_name": _text(map_info.get("name")) or _text(map_info.get("id")),
            "bounds": self._read_bounds(payload.get("bounds")),
            "self_ship": self._read_self(payload.get("self")),
            "ships": self._read_ships(payload.get("objects"), payload.get("roster")),
            "damage_inflicted": _sum_table(damage.get("inflicted")),
            "damage_received": _sum_table(damage.get("received")),
            "damage_team_total": _sum_table(damage.get("teamTotal")),
            "ballistics": ballistics,
        }

    @staticmethod
    def _read_status(value: Any, payload: Mapping[str, Any]) -> str:
        text = _text(value)
        if text in (STATUS_WAITING, STATUS_LIVE, STATUS_STALE, STATUS_ENDED):
            return text
        # A v1 payload without a usable status is treated by activity alone; we
        # must not invent `ended` from a missing field.
        return STATUS_LIVE if payload.get("active") else STATUS_WAITING

    @staticmethod
    def _read_capabilities(raw: Any) -> dict[str, bool]:
        capabilities: dict[str, bool] = {}
        if isinstance(raw, Mapping):
            for name, entry in raw.items():
                if not isinstance(name, str):
                    continue
                if isinstance(entry, Mapping):
                    capabilities[name] = bool(entry.get("supported"))
                else:
                    capabilities[name] = bool(entry)
        for domain in CORE_DOMAINS:
            capabilities.setdefault(domain, False)
        for domain in FUTURE_DOMAINS:
            capabilities.setdefault(domain, False)
        return capabilities

    @staticmethod
    def _read_availability(raw: Any) -> dict[str, str]:
        allowed = (AVAIL_AVAILABLE, AVAIL_UNKNOWN, AVAIL_STALE, AVAIL_UNSUPPORTED)
        availability: dict[str, str] = {}
        if isinstance(raw, Mapping):
            for name, value in raw.items():
                if isinstance(name, str) and value in allowed:
                    availability[name] = value
        for domain in CORE_DOMAINS:
            availability.setdefault(domain, AVAIL_UNKNOWN)
        for domain in FUTURE_DOMAINS:
            availability.setdefault(domain, AVAIL_UNSUPPORTED)
        return availability

    @staticmethod
    def _derive_availability(body: Mapping[str, Any], status: str) -> dict[str, str]:
        """Infer per-domain availability for a service that does not report it."""
        ships = body.get("ships") or ()
        # Roster-only stubs (no uiId/position/alive flag) must not make the
        # objects domain look populated when the wire `objects` list was empty.
        object_ships = any(
            ship.ui_id is not None
            or ship.has_position
            or ship.visible
            or ship.alive is not None
            for ship in ships
        )
        present = {
            DOMAIN_SELF: body.get("self_ship") is not None,
            DOMAIN_OBJECTS: object_ships,
            DOMAIN_ROSTER: any(
                ship.player_name or ship.tier is not None
                for ship in ships
            ),
            DOMAIN_DAMAGE: any(
                body.get(key) for key in
                ("damage_inflicted", "damage_received", "damage_team_total")
            ),
            DOMAIN_BALLISTICS: bool((body.get("ballistics") or {}).get("available")),
            DOMAIN_MAP_BOUNDS: body.get("bounds") is not None,
        }
        availability: dict[str, str] = {}
        for domain in CORE_DOMAINS:
            if not present.get(domain):
                availability[domain] = AVAIL_UNKNOWN
            elif status == STATUS_STALE and domain not in META_DOMAINS:
                availability[domain] = AVAIL_STALE
            else:
                availability[domain] = AVAIL_AVAILABLE
        for domain in FUTURE_DOMAINS:
            availability[domain] = AVAIL_UNSUPPORTED
        return availability

    @staticmethod
    def _read_bounds(raw: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        values = [_bw_to_m(item) for item in raw]
        if any(v is None for v in values):
            return None
        min_x, max_x, min_z, max_z = values  # type: ignore[misc]
        if max_x <= min_x or max_z <= min_z:
            return None
        return (min_x, max_x, min_z, max_z)

    @staticmethod
    def _read_self(raw: Any) -> SelfShip | None:
        if not isinstance(raw, Mapping) or not raw:
            return None
        position = raw.get("position")
        x = z = None
        if isinstance(position, (list, tuple)) and len(position) >= 3:
            x, z = _bw_to_m(position[0]), _bw_to_m(position[2])
        player_id = raw.get("playerId")
        team_id = raw.get("teamId")
        return SelfShip(
            player_id=player_id if isinstance(player_id, int) else None,
            team_id=team_id if isinstance(team_id, int) else None,
            health=_number(raw.get("health")),
            max_health=_number(raw.get("maxHealth")),
            yaw=_number(raw.get("yaw")),
            # Speed is already a game-facing knot-scale reading, not BW/s.
            speed=_number(raw.get("speed")),
            x=x,
            z=z,
            is_observer=bool(raw.get("isObserver")),
        )

    def _read_ships(self, raw_objects: Any, raw_roster: Any) -> tuple[Ship, ...]:
        roster: dict[int, Mapping[str, Any]] = {}
        if isinstance(raw_roster, (list, tuple)):
            for entry in raw_roster:
                if isinstance(entry, Mapping) and isinstance(entry.get("playerId"), int):
                    roster[entry["playerId"]] = entry

        ships: list[Ship] = []
        seen_player_ids: set[int] = set()
        if isinstance(raw_objects, (list, tuple)):
            for entry in raw_objects:
                if not isinstance(entry, Mapping):
                    continue
                player_id = entry.get("playerId")
                meta = roster.get(player_id) if isinstance(player_id, int) else None
                meta = meta if isinstance(meta, Mapping) else {}
                health = _number(entry.get("health"))
                max_health = _number(entry.get("maxHealth"))
                hp_ratio = _number(entry.get("hpRatio"))
                if hp_ratio is None and health is not None and max_health:
                    hp_ratio = max(0.0, min(1.0, health / max_health))
                relation = entry.get("relation")
                tier = (
                    entry.get("tier") if entry.get("tier") is not None
                    else meta.get("shipTier")
                )
                alive = (
                    entry.get("alive") if isinstance(entry.get("alive"), bool)
                    else None
                )
                if isinstance(player_id, int):
                    seen_player_ids.add(player_id)
                    if alive is False:
                        self._dead_player_ids.add(player_id)
                    elif alive is True:
                        self._dead_player_ids.discard(player_id)
                ships.append(Ship(
                    ui_id=entry.get("uiId") if isinstance(entry.get("uiId"), int) else None,
                    player_id=player_id if isinstance(player_id, int) else None,
                    team_id=(
                        entry.get("teamId") if isinstance(entry.get("teamId"), int)
                        else None
                    ),
                    relation=relation if isinstance(relation, int) else None,
                    ship_type=_text(entry.get("type")) or _text(meta.get("shipType")),
                    name=_text(entry.get("name")) or _text(meta.get("shipName")),
                    player_name=(
                        _text(entry.get("playerName")) or _text(meta.get("name"))
                    ),
                    tier=tier if isinstance(tier, int) else None,
                    alive=alive,
                    visible=bool(entry.get("visible")),
                    x=_bw_to_m(entry.get("x")),
                    z=_bw_to_m(entry.get("z")),
                    yaw=_number(entry.get("yaw")),
                    health=health,
                    max_health=max_health,
                    hp_ratio=hp_ratio,
                    stale_seconds=_number(entry.get("staleSeconds")),
                ))

        # Roster lists the full match even before anyone is spotted. Emit
        # position-less stubs so alive counts are not stuck at zero at the
        # opening, while objects remain authoritative for death/visibility.
        for player_id, meta in roster.items():
            if player_id in seen_player_ids or player_id in self._dead_player_ids:
                continue
            relation = meta.get("relation")
            team_id = meta.get("teamId")
            tier = meta.get("shipTier")
            ships.append(Ship(
                player_id=player_id,
                team_id=team_id if isinstance(team_id, int) else None,
                relation=relation if isinstance(relation, int) else None,
                ship_type=_text(meta.get("shipType")),
                name=_text(meta.get("shipName")),
                player_name=_text(meta.get("name")),
                tier=tier if isinstance(tier, int) else None,
                alive=None,
                visible=False,
            ))
        return tuple(ships)

    @staticmethod
    def _fingerprint(payload: Mapping[str, Any]) -> str:
        """Content identity for legacy frames, which carry no cursor.

        Only the fields that can move within a battle are hashed, so a repeated
        REST read of an unchanged frame does not look like new data.
        """
        try:
            material = json.dumps(
                {
                    "active": payload.get("active"),
                    "ts": payload.get("ts"),
                    "self": payload.get("self"),
                    "objects": payload.get("objects"),
                    "damage": payload.get("damage"),
                    "ballistics": payload.get("ballistics"),
                },
                sort_keys=True,
                default=str,
            )
        except (TypeError, ValueError):
            material = repr(sorted(payload.items(), key=lambda kv: kv[0]))
        return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _sum_table(raw: Any) -> float | None:
    """Total a `{playerId: amount}` damage table, or `None` when absent."""
    if not isinstance(raw, Mapping):
        return None
    total = 0.0
    seen = False
    for value in raw.values():
        number = _number(value)
        if number is not None:
            total += number
            seen = True
    return total if seen else None


__all__ = [
    "BW_TO_METERS",
    "LEGACY_STALE_SECONDS",
    "SUPPORTED_API_MAJOR",
    "UnexpectedServiceIdentity",
    "UnsupportedApiVersion",
    "WowsSchemaAdapter",
]
