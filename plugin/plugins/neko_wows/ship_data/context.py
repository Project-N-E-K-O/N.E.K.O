"""Battle-frozen ship reference context, counting, batching, and bookkeeping."""

from __future__ import annotations

import hashlib
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..domain.snapshot import (
    RELATION_ALLY,
    RELATION_ENEMY,
    RELATION_SELF,
    STATUS_LIVE,
    Ship,
    WowsSnapshot,
)
from .models import CatalogMeta, ShipCounts, ShipResolution
from .renderer import ShipReferenceRenderer
from .resolver import ShipResolver, normalize_ship_alias
from .store import NullCatalogSnapshot

_MAX_GAME_INFO_BYTES = 1024 * 1024
_VERSION_TAGS = frozenset({"version", "clientversion", "gameversion"})


@dataclass(frozen=True)
class ShipCatalogEvent:
    outcome: str
    detail: Mapping[str, Any]


@dataclass(frozen=True)
class ContextObservation:
    state: str
    submitted_ship_ids: tuple[int, ...] = ()
    updated_ship_ids: tuple[int, ...] = ()
    pending_ship_ids: tuple[int, ...] = ()
    preview_batches: tuple[str, ...] = ()
    unresolved_reasons: Mapping[str, int] | None = None
    events: tuple[ShipCatalogEvent, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class _PendingBlock:
    ship_id: int
    text: str
    count_update: bool


class BattleShipContextManager:
    """Pin one catalog per battle and submit exact ship profiles as read context."""

    def __init__(
        self,
        plugin: Any,
        store: Any,
        cfg: Any,
        *,
        renderer: ShipReferenceRenderer | None = None,
        logger: Any = None,
        clock=time.monotonic,
    ) -> None:
        self._plugin = plugin
        self._store = store
        self._cfg = cfg
        self._renderer = renderer or ShipReferenceRenderer()
        self._logger = logger
        self._clock = clock
        self._lock = threading.RLock()

        self._identity: tuple[str, str | None] | None = None
        self._catalog: Any = None
        self._frozen_meta: CatalogMeta | None = None
        self._state = "idle"
        self._client_game_version = ""
        self._version_status = "unknown"
        self._seen_objects: set[tuple[Any, ...]] = set()
        self._resolutions: dict[int, ShipResolution] = {}
        self._counts: dict[int, ShipCounts] = {}
        self._submitted: set[int] = set()
        self._submitted_counts: dict[int, ShipCounts] = {}
        self._unresolved_by_object: dict[tuple[Any, ...], str] = {}
        self._render_failures: set[int] = set()
        self._unresolved_reasons: Counter[str] = Counter()
        self._batch_sequence = 0
        self._retry_failures = 0
        self._retry_after = 0.0
        self._last_error = ""

    def apply_config(self, cfg: Any) -> None:
        with self._lock:
            was_enabled = bool(getattr(self._cfg, "ship_catalog_enabled", True))
            self._cfg = cfg
            is_enabled = bool(getattr(cfg, "ship_catalog_enabled", True))
            if was_enabled and not is_enabled:
                self._reset_locked("config_disabled")

    def observe(
        self,
        snapshot: WowsSnapshot,
        *,
        dry_run: bool,
    ) -> ContextObservation:
        with self._lock:
            events: list[ShipCatalogEvent] = []
            try:
                return self._observe_locked(snapshot, dry_run=dry_run, events=events)
            except Exception as exc:
                self._last_error = "context_observation_failed"
                self._warn(f"ship catalog observation failed: {type(exc).__name__}")
                events.append(ShipCatalogEvent(
                    "observation_failed", {"error": type(exc).__name__}))
                return self._observation(
                    events=events, error="context_observation_failed")

    def reset(self, reason: str = "battle_end") -> None:
        with self._lock:
            self._reset_locked(str(reason or "battle_end"))

    def stats(self) -> dict[str, Any]:
        with self._lock:
            meta = self._frozen_meta
            active_info: Mapping[str, Any] = {}
            try:
                read_active = getattr(self._store, "active_manifest_info", None)
                candidate = read_active() if callable(read_active) else {}
                if isinstance(candidate, Mapping):
                    active_info = candidate
            except Exception:
                active_info = {}
            active_catalog_version = active_info.get("catalog_version")
            active_game_version = active_info.get("game_version")
            active_schema_version = active_info.get("schema_version")
            pending = self._pending_ship_ids()
            return {
                "enabled": bool(getattr(
                    self._cfg, "ship_catalog_enabled", True)),
                "state": self._state,
                "active_catalog_version": (
                    active_catalog_version
                    if isinstance(active_catalog_version, str) else ""),
                "frozen_catalog_version": meta.catalog_version if meta else "",
                "catalog_game_version": (
                    active_game_version
                    if isinstance(active_game_version, str) else ""),
                "client_game_version": self._client_game_version,
                "version_status": self._version_status,
                "source_commit": meta.source_commit if meta else "",
                "schema_version": (
                    active_schema_version
                    if type(active_schema_version) is int else None),
                "observed_objects": (
                    len(self._seen_objects) + len(self._unresolved_by_object)),
                "resolved_ship_types": len(self._resolutions),
                "unresolved_objects": sum(self._unresolved_reasons.values()),
                "pending_ship_types": len(pending),
                "submitted_ship_types": len(self._submitted),
                "unresolved_reasons": dict(sorted(self._unresolved_reasons.items())),
                "last_error": self._last_error,
            }

    def _observe_locked(
        self,
        snapshot: WowsSnapshot,
        *,
        dry_run: bool,
        events: list[ShipCatalogEvent],
    ) -> ContextObservation:
        if not bool(getattr(self._cfg, "ship_catalog_enabled", True)):
            self._state = "disabled"
            return self._observation(events=events)
        if snapshot.status != STATUS_LIVE or not snapshot.active:
            return self._observation(events=events)

        identity = snapshot.identity
        if self._identity is not None and identity != self._identity:
            self._reset_locked("identity_changed")
            events.append(ShipCatalogEvent(
                "battle_reset", {"reason": "identity_changed"}))
        if self._identity is None:
            self._freeze(snapshot, events)

        if self._state in {"null_catalog", "version_rejected"}:
            return self._observation(events=events)

        self._observe_ships(snapshot, events)
        blocks = self._pending_blocks(events)
        batches = self._pack_batches(blocks)
        previews = tuple(text for text, _ in batches)
        if dry_run:
            for text, batch_blocks in batches:
                events.append(ShipCatalogEvent("batch_preview", {
                    "characters": len(text),
                    "ship_ids": [item.ship_id for item in batch_blocks],
                }))
            return self._observation(events=events, preview_batches=previews)

        now = float(self._clock())
        if blocks and now < self._retry_after:
            events.append(ShipCatalogEvent("batch_retry_wait", {
                "retry_in_seconds": round(self._retry_after - now, 3),
            }))
            return self._observation(events=events)

        submitted: list[int] = []
        updated: list[int] = []
        failed = False
        push_error = ""
        for text, batch_blocks in batches:
            self._batch_sequence += 1
            batch_id = self._batch_sequence
            full_ids = tuple(
                item.ship_id for item in batch_blocks if not item.count_update)
            update_ids = tuple(
                item.ship_id for item in batch_blocks if item.count_update)
            accepted, error = self._push_batch(
                text,
                batch_id=batch_id,
                ship_ids=tuple(item.ship_id for item in batch_blocks),
                update_ship_ids=update_ids,
            )
            if accepted:
                for ship_id in full_ids:
                    self._submitted.add(ship_id)
                    self._submitted_counts[ship_id] = self._counts[ship_id]
                    submitted.append(ship_id)
                for ship_id in update_ids:
                    self._submitted_counts[ship_id] = self._counts[ship_id]
                    updated.append(ship_id)
                events.append(ShipCatalogEvent("batch_submitted", {
                    "batch_id": batch_id,
                    "ship_ids": list(full_ids),
                    "count_update_ship_ids": list(update_ids),
                }))
            else:
                failed = True
                push_error = error or push_error
                events.append(ShipCatalogEvent("batch_declined", {
                    "batch_id": batch_id,
                    "ship_ids": [item.ship_id for item in batch_blocks],
                    "reason": error or "submission_declined",
                }))
                break

        if failed:
            self._retry_failures += 1
            delay = min(30.0, 0.5 * (2 ** min(self._retry_failures - 1, 6)))
            self._retry_after = now + delay
        elif batches:
            self._retry_failures = 0
            self._retry_after = 0.0
            self._last_error = ""
        if push_error:
            self._last_error = push_error
        return self._observation(
            events=events,
            submitted_ship_ids=tuple(sorted(set(submitted))),
            updated_ship_ids=tuple(sorted(set(updated))),
            error=push_error,
        )

    def _freeze(
        self,
        snapshot: WowsSnapshot,
        events: list[ShipCatalogEvent],
    ) -> None:
        self._identity = snapshot.identity
        try:
            catalog = self._store.snapshot(
                language=getattr(self._cfg, "ship_catalog_language", None))
        except Exception as exc:
            self._warn(f"ship catalog open failed: {type(exc).__name__}")
            catalog = NullCatalogSnapshot("catalog_open_failed")
        self._catalog = catalog
        self._frozen_meta = getattr(catalog, "meta", None)
        self._client_game_version = self._client_version(snapshot)
        meta = self._frozen_meta
        self._version_status = self._compare_versions(
            self._client_game_version,
            meta.game_version if meta is not None else "",
        )
        if meta is None:
            self._state = "null_catalog"
            events.append(ShipCatalogEvent("null_catalog", {
                "reason": getattr(catalog, "reason", "catalog_unavailable"),
            }))
            return

        policy = str(getattr(
            self._cfg, "ship_catalog_version_policy", "warn"))
        if policy == "strict" and self._version_status != "match":
            try:
                catalog.close()
            except Exception:
                pass
            self._catalog = NullCatalogSnapshot(
                f"version_{self._version_status}")
            self._state = "version_rejected"
            events.append(ShipCatalogEvent("version_rejected", {
                "version_status": self._version_status,
            }))
            return

        self._state = "loaded"
        events.extend((
            ShipCatalogEvent("loaded", {
                "catalog_version": meta.catalog_version,
            }),
            ShipCatalogEvent("version_" + self._version_status, {}),
            ShipCatalogEvent("battle_frozen", {
                "catalog_version": meta.catalog_version,
            }),
        ))

    def _observe_ships(
        self,
        snapshot: WowsSnapshot,
        events: list[ShipCatalogEvent],
    ) -> None:
        if self._catalog is None:
            return
        resolver = ShipResolver(self._catalog)
        fallback_occurrences: Counter[tuple[Any, ...]] = Counter()
        own_player_id = (
            snapshot.self_ship.player_id
            if snapshot.self_ship is not None else None
        )
        for ship in snapshot.ships:
            if not isinstance(ship.name, str) or not ship.name.strip():
                continue
            object_key = self._object_key(ship, fallback_occurrences)
            if object_key in self._seen_objects:
                continue
            resolution = resolver.resolve(
                ship.name, tier=ship.tier, ship_type=ship.ship_type)
            if not resolution.resolved or resolution.ship is None:
                self._set_unresolved_reason(object_key, resolution.reason)
                events.append(ShipCatalogEvent("unresolved", {
                    "reason": resolution.reason,
                }))
                continue
            self._set_unresolved_reason(object_key, None)
            self._seen_objects.add(object_key)
            ship_id = resolution.ship.ship_id
            self._resolutions.setdefault(ship_id, resolution)
            counts = self._counts.get(ship_id, ShipCounts())
            relation = ship.relation
            if own_player_id is not None and ship.player_id == own_player_id:
                relation = RELATION_SELF
            if relation == RELATION_SELF:
                counts = replace(counts, self_count=counts.self_count + 1)
            elif relation == RELATION_ALLY:
                counts = replace(counts, ally_count=counts.ally_count + 1)
            elif relation == RELATION_ENEMY:
                counts = replace(counts, enemy_count=counts.enemy_count + 1)
            self._counts[ship_id] = counts
            events.append(ShipCatalogEvent("resolved", {"ship_id": ship_id}))

    def _set_unresolved_reason(
        self,
        object_key: tuple[Any, ...],
        reason: str | None,
    ) -> None:
        previous = self._unresolved_by_object.get(object_key)
        if previous == reason:
            return
        if previous is not None:
            del self._unresolved_by_object[object_key]
            self._unresolved_reasons[previous] -= 1
            if self._unresolved_reasons[previous] <= 0:
                del self._unresolved_reasons[previous]
        if reason is not None:
            self._unresolved_by_object[object_key] = reason
            self._unresolved_reasons[reason] += 1

    @staticmethod
    def _object_key(
        ship: Ship,
        fallback_occurrences: Counter[tuple[Any, ...]],
    ) -> tuple[Any, ...]:
        if isinstance(ship.ui_id, int) and not isinstance(ship.ui_id, bool):
            return ("ui", ship.ui_id)
        if isinstance(ship.player_id, int) and not isinstance(ship.player_id, bool):
            return ("player", ship.player_id)
        base = (
            "fallback",
            ship.team_id,
            ship.relation,
            normalize_ship_alias(ship.name),
        )
        ordinal = fallback_occurrences[base]
        fallback_occurrences[base] += 1
        return base + (ordinal,)

    def _pending_blocks(
        self,
        events: list[ShipCatalogEvent],
    ) -> list[_PendingBlock]:
        blocks: list[_PendingBlock] = []
        for ship_id in sorted(self._resolutions):
            resolution = self._resolutions[ship_id]
            counts = self._counts.get(ship_id, ShipCounts())
            try:
                if ship_id not in self._submitted:
                    text = self._renderer.render(
                        resolution, counts, version_status=self._version_status)
                    blocks.append(_PendingBlock(ship_id, text, False))
                elif self._submitted_counts.get(ship_id) != counts:
                    text = self._renderer.render_count_update(
                        resolution, counts, version_status=self._version_status)
                    blocks.append(_PendingBlock(ship_id, text, True))
                    events.append(ShipCatalogEvent(
                        "count_update", {"ship_id": ship_id}))
                else:
                    continue
                if ship_id in self._render_failures:
                    self._render_failures.remove(ship_id)
                    self._unresolved_reasons["render_failed"] -= 1
                    if self._unresolved_reasons["render_failed"] <= 0:
                        del self._unresolved_reasons["render_failed"]
            except Exception as exc:
                if ship_id not in self._render_failures:
                    self._render_failures.add(ship_id)
                    self._unresolved_reasons["render_failed"] += 1
                self._warn(
                    f"ship reference render failed: {type(exc).__name__}")
                events.append(ShipCatalogEvent("unresolved", {
                    "reason": "render_failed",
                    "ship_id": ship_id,
                }))
        return blocks

    def _pack_batches(
        self,
        blocks: list[_PendingBlock],
    ) -> list[tuple[str, tuple[_PendingBlock, ...]]]:
        limit = int(getattr(
            self._cfg, "ship_catalog_context_batch_chars", 12_000))
        batches: list[tuple[str, tuple[_PendingBlock, ...]]] = []
        current: list[_PendingBlock] = []
        current_size = 0
        for block in blocks:
            separator_size = 2 if current else 0
            if current and current_size + separator_size + len(block.text) > limit:
                batches.append((
                    "\n\n".join(item.text for item in current),
                    tuple(current),
                ))
                current = []
                current_size = 0
                separator_size = 0
            current.append(block)
            current_size += separator_size + len(block.text)
        if current:
            batches.append((
                "\n\n".join(item.text for item in current),
                tuple(current),
            ))
        return batches

    def _push_batch(
        self,
        text: str,
        *,
        batch_id: int,
        ship_ids: tuple[int, ...],
        update_ship_ids: tuple[int, ...],
    ) -> tuple[bool, str]:
        identity = self._identity or ("", None)
        battle_key = hashlib.sha256(
            repr(identity).encode("utf-8", "replace")).hexdigest()[:16]
        meta = self._frozen_meta
        try:
            receipt = self._plugin.push_message(
                source="neko_wows",
                visibility=[],
                ai_behavior="read",
                parts=[{"type": "text", "text": text}],
                priority=0,
                coalesce_key=(
                    f"wows_ship_reference:{battle_key}:{batch_id}"),
                metadata={
                    "plugin": "neko_wows",
                    "kind": "ship_reference",
                    "battle_identity": list(identity),
                    "catalog_version": (
                        meta.catalog_version if meta is not None else ""),
                    "batch_id": batch_id,
                    "ship_ids": list(ship_ids),
                    "count_update_ship_ids": list(update_ship_ids),
                },
            )
        except Exception as exc:
            self._warn(f"ship reference push failed: {type(exc).__name__}")
            return False, "host_push_failed"
        if isinstance(receipt, Mapping) and receipt.get("submitted") is True:
            return True, ""
        return False, "submission_declined"

    def _observation(
        self,
        *,
        events: list[ShipCatalogEvent],
        submitted_ship_ids: tuple[int, ...] = (),
        updated_ship_ids: tuple[int, ...] = (),
        preview_batches: tuple[str, ...] = (),
        error: str = "",
    ) -> ContextObservation:
        return ContextObservation(
            state=self._state,
            submitted_ship_ids=submitted_ship_ids,
            updated_ship_ids=updated_ship_ids,
            pending_ship_ids=self._pending_ship_ids(),
            preview_batches=preview_batches,
            unresolved_reasons=dict(sorted(self._unresolved_reasons.items())),
            events=tuple(events),
            error=error,
        )

    def _pending_ship_ids(self) -> tuple[int, ...]:
        pending = []
        for ship_id in self._resolutions:
            counts = self._counts.get(ship_id, ShipCounts())
            if (
                ship_id not in self._submitted
                or self._submitted_counts.get(ship_id) != counts
            ):
                pending.append(ship_id)
        return tuple(sorted(pending))

    def _reset_locked(self, reason: str) -> None:
        catalog = self._catalog
        self._catalog = None
        if catalog is not None:
            try:
                catalog.close()
            except Exception:
                pass
        self._identity = None
        self._frozen_meta = None
        self._state = "idle"
        self._client_game_version = ""
        self._version_status = "unknown"
        self._seen_objects.clear()
        self._resolutions.clear()
        self._counts.clear()
        self._submitted.clear()
        self._submitted_counts.clear()
        self._unresolved_by_object.clear()
        self._render_failures.clear()
        self._unresolved_reasons.clear()
        self._retry_failures = 0
        self._retry_after = 0.0
        self._last_error = ""
        self._log("debug", f"ship catalog battle context reset ({reason})")

    def _client_version(self, snapshot: WowsSnapshot) -> str:
        explicit = self._normalize_version(snapshot.game_version)
        if explicit:
            return explicit
        game_dir = str(getattr(self._cfg, "game_dir", "") or "").strip()
        if not game_dir:
            return ""
        path = Path(game_dir) / "game_info.xml"
        try:
            if not path.is_file() or path.stat().st_size > _MAX_GAME_INFO_BYTES:
                return ""
            root = ET.fromstring(path.read_bytes())
        except (OSError, ET.ParseError, ValueError):
            return ""
        for element in root.iter():
            local_name = str(element.tag).rsplit("}", 1)[-1].casefold()
            if local_name in _VERSION_TAGS:
                normalized = self._normalize_version(element.text)
                if normalized:
                    return normalized
        return ""

    @staticmethod
    def _normalize_version(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        normalized = value.strip().lstrip("vV").replace(",", ".")
        normalized = "".join(normalized.split())
        parts = normalized.split(".")
        if len(parts) < 2 or any(not part.isdigit() for part in parts):
            return ""
        return ".".join(str(int(part)) for part in parts)

    @classmethod
    def _compare_versions(cls, client: str, catalog: str) -> str:
        client_version = cls._normalize_version(client)
        catalog_version = cls._normalize_version(catalog)
        if not client_version or not catalog_version:
            return "unknown"
        return "match" if client_version == catalog_version else "mismatch"

    def _warn(self, message: str) -> None:
        self._log("warning", message)

    def _log(self, level: str, message: str) -> None:
        method = getattr(self._logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


__all__ = [
    "BattleShipContextManager",
    "ContextObservation",
    "ShipCatalogEvent",
]
