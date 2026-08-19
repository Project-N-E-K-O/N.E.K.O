"""Canonical local inventory for installed and logically deleted plugins.

Built-in plugin payloads are part of the signed application distribution and
may be read-only or restored by the desktop updater.  Deleting one therefore
means recording that the logical plugin must stay hidden, not removing files
from the application directory.

Schema version 2 distinguishes managed payloads from legacy/unmanaged plugin
directories.  Version 1 remains readable migration input; it is upgraded only
when a real inventory mutation is committed.  The legacy ``plugins.lock.json``
remains migration input only.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from plugin.server.application.plugins.package_ownership import (
    validate_package_state_files,
)


_SCHEMA_VERSION = 2
_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_STORE_LOCK = threading.RLock()


@dataclass(frozen=True)
class ActiveInstallation:
    installation_key: str
    installation_kind: Literal["managed", "legacy"]
    directory_name: str
    source: str = "manual"
    installed_at: str | None = None


@dataclass(frozen=True)
class InventoryResolution:
    deleted_plugin_ids: frozenset[str]
    active_user_directories: dict[str, str]
    authoritative: bool = True
    active_installations: dict[str, ActiveInstallation] = field(default_factory=dict)


@dataclass(frozen=True)
class InventoryFileSnapshot:
    path: Path
    existed: bool
    payload: bytes | None


@dataclass(frozen=True)
class InventoryInstallationSlot:
    installation_key: str
    logical_plugin_id: str
    installation_kind: Literal["managed", "legacy"]
    directory_name: str
    package_id: str
    source: str
    installed_at: str | None


@dataclass(frozen=True)
class PluginInstallationState:
    generation: int
    deleted: bool
    active_installation_key: str | None
    installations: tuple[InventoryInstallationSlot, ...]


class PluginInventoryError(RuntimeError):
    """Raised when the canonical plugin inventory cannot be read or saved."""


def resolve_inventory_path() -> Path:
    configured = os.environ.get("NEKO_PLUGIN_INSTALLATIONS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    from plugin.settings import get_user_plugin_config_root

    return (get_user_plugin_config_root().parent / "plugin-installations.json").resolve()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "generation": 0,
        "updated_at": None,
        "installations": [],
        "activation_claims": {},
    }


def _validate_plugin_id(plugin_id: str) -> str:
    normalized = plugin_id.strip()
    if not _PLUGIN_ID_PATTERN.fullmatch(normalized):
        raise PluginInventoryError(f"invalid plugin id: {plugin_id!r}")
    return normalized.casefold()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PluginInventoryError(f"cannot read plugin inventory: {exc}") from exc
    if not isinstance(raw, dict):
        raise PluginInventoryError("plugin inventory must be a JSON object")

    schema_version = raw.get("schema_version")
    if schema_version not in {1, _SCHEMA_VERSION}:
        raise PluginInventoryError(
            f"unsupported plugin inventory schema: {schema_version!r}"
        )
    generation = raw.get("generation")
    if not isinstance(generation, int) or generation < 0:
        raise PluginInventoryError("plugin inventory generation is invalid")
    installations = raw.get("installations")
    if not isinstance(installations, list):
        raise PluginInventoryError("installations must be a JSON array")
    activation_claims = raw.get("activation_claims")
    if not isinstance(activation_claims, dict):
        raise PluginInventoryError("activation_claims must be a JSON object")
    return raw


def _state_for_current_schema(state: dict[str, Any]) -> dict[str, Any]:
    """Return a write-ready v2 state without mutating the loaded document."""

    updated = dict(state)
    installations: list[dict[str, Any]] = []
    for raw_slot in state["installations"]:
        if not isinstance(raw_slot, dict):
            raise PluginInventoryError("installations contains an invalid slot")
        slot = dict(raw_slot)
        if slot.get("root_id") == "user":
            slot["root_id"] = "legacy"
        installations.append(slot)
    updated["installations"] = installations
    updated["schema_version"] = _SCHEMA_VERSION
    return updated


def _atomic_write_payload(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with temp_path.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: OSError | None = None
        for delay_ms in (0, 50, 100, 200):
            if delay_ms:
                time.sleep(delay_ms / 1000)
            try:
                os.replace(temp_path, path)
                return
            except OSError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
    except OSError as exc:
        raise PluginInventoryError(f"cannot save plugin inventory: {exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write(path: Path, state: dict[str, Any]) -> None:
    payload = (
        json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write_payload(path, payload)


def capture_inventory_snapshot(*, path: Path | None = None) -> InventoryFileSnapshot:
    """Capture an exact, validated inventory file for transaction rollback."""

    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        _load_state(state_path)
        if not state_path.exists():
            return InventoryFileSnapshot(path=state_path, existed=False, payload=None)
        try:
            payload = state_path.read_bytes()
        except OSError as exc:
            raise PluginInventoryError(f"cannot read plugin inventory: {exc}") from exc
        return InventoryFileSnapshot(path=state_path, existed=True, payload=payload)


def restore_inventory_snapshot(snapshot: InventoryFileSnapshot) -> None:
    """Restore a prior inventory byte-for-byte, including file absence."""

    with _STORE_LOCK:
        if snapshot.existed:
            assert snapshot.payload is not None
            _atomic_write_payload(snapshot.path, snapshot.payload)
            return
        try:
            snapshot.path.unlink(missing_ok=True)
        except OSError as exc:
            raise PluginInventoryError(
                f"cannot restore plugin inventory absence: {exc}"
            ) from exc


def load_inventory_resolution_for_registry(
    *, path: Path | None = None
) -> tuple[InventoryResolution, str | None]:
    """Load inventory without allowing one corrupt file to stop discovery.

    Corrupt current-schema files are moved aside for audit. Unknown future
    schemas are never renamed or rewritten; registry runs without claims and
    reports a distinct failure instead.
    """

    state_path = path or resolve_inventory_path()
    if not state_path.exists() and any(
        state_path.parent.glob(f"{state_path.name}.corrupt-*")
    ):
        return (
            InventoryResolution(frozenset(), {}, authoritative=False),
            "plugin_inventory_quarantined",
        )
    try:
        return get_inventory_resolution(path=state_path), None
    except PluginInventoryError as exc:
        if str(exc).startswith("unsupported plugin inventory schema:"):
            return (
                InventoryResolution(frozenset(), {}, authoritative=False),
                "plugin_inventory_unsupported_schema",
            )

        with _STORE_LOCK:
            if state_path.exists():
                quarantine_path = state_path.with_name(
                    f"{state_path.name}.corrupt-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
                )
                try:
                    os.replace(state_path, quarantine_path)
                except OSError as quarantine_exc:
                    raise PluginInventoryError(
                        f"cannot quarantine corrupt plugin inventory: {quarantine_exc}"
                    ) from quarantine_exc
        return (
            InventoryResolution(frozenset(), {}, authoritative=False),
            "plugin_inventory_quarantined",
        )


def get_inventory_resolution(*, path: Path | None = None) -> InventoryResolution:
    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        state = _load_state(state_path)
        schema_version = int(state["schema_version"])
        slots_by_key: dict[str, tuple[str, str, str, str, str | None]] = {}
        for slot in state["installations"]:
            if not isinstance(slot, dict):
                raise PluginInventoryError("installations contains an invalid slot")
            installation_key = slot.get("installation_key")
            plugin_id = slot.get("logical_plugin_id")
            root_id = slot.get("root_id")
            directory_name = slot.get("directory_name")
            source = slot.get("source")
            installed_at = slot.get("installed_at")
            installation_kind: str | None
            if schema_version == 1 and root_id == "user":
                installation_kind = "legacy"
            elif schema_version == 2 and root_id in {"managed", "legacy"}:
                installation_kind = root_id
            else:
                installation_kind = None
            if (
                not isinstance(installation_key, str)
                or not installation_key
                or not isinstance(plugin_id, str)
                or not _PLUGIN_ID_PATTERN.fullmatch(plugin_id)
                or installation_kind is None
                or not isinstance(directory_name, str)
                or not directory_name
                or directory_name in {".", ".."}
                or "/" in directory_name
                or "\\" in directory_name
            ):
                raise PluginInventoryError("installations contains an invalid slot")
            if installation_key in slots_by_key:
                raise PluginInventoryError("installation_key must be unique")
            slots_by_key[installation_key] = (
                plugin_id.casefold(),
                installation_kind,
                directory_name,
                source if isinstance(source, str) else "manual",
                installed_at if isinstance(installed_at, str) else None,
            )

        deleted: set[str] = set()
        active_user_directories: dict[str, str] = {}
        active_installations: dict[str, ActiveInstallation] = {}
        activation_claims = state["activation_claims"]
        canonical_claim_ids: set[str] = set()
        for plugin_id, record in activation_claims.items():
            if not isinstance(plugin_id, str) or not _PLUGIN_ID_PATTERN.fullmatch(plugin_id):
                raise PluginInventoryError("activation_claims contains an invalid plugin id")
            canonical_plugin_id = plugin_id.casefold()
            if canonical_plugin_id in canonical_claim_ids:
                raise PluginInventoryError(
                    "activation_claims contains duplicate case-insensitive plugin ids"
                )
            canonical_claim_ids.add(canonical_plugin_id)
            if not isinstance(record, dict):
                raise PluginInventoryError(
                    f"activation_claims[{plugin_id!r}] is invalid"
                )
            claim_state = record.get("state")
            if claim_state == "deleted":
                deleted.add(canonical_plugin_id)
                continue
            if claim_state != "active":
                raise PluginInventoryError(
                    f"activation_claims[{plugin_id!r}] has an invalid state"
                )
            installation_key = record.get("installation_key")
            if not isinstance(installation_key, str):
                raise PluginInventoryError(
                    f"activation_claims[{plugin_id!r}] has no installation key"
                )
            slot = slots_by_key.get(installation_key)
            if slot is None or slot[0] != canonical_plugin_id:
                raise PluginInventoryError(
                    f"activation_claims[{plugin_id!r}] references an invalid installation"
                )
            active_user_directories[canonical_plugin_id] = slot[2]
            active_installations[canonical_plugin_id] = ActiveInstallation(
                installation_key=installation_key,
                installation_kind=slot[1],  # type: ignore[arg-type]
                directory_name=slot[2],
                source=slot[3],
                installed_at=slot[4],
            )
        return InventoryResolution(
            deleted_plugin_ids=frozenset(deleted),
            active_user_directories=active_user_directories,
            active_installations=active_installations,
        )


def get_deleted_plugin_ids(*, path: Path | None = None) -> frozenset[str]:
    return get_inventory_resolution(path=path).deleted_plugin_ids


def get_plugin_installation_state(
    plugin_id: str,
    *,
    path: Path | None = None,
) -> PluginInstallationState:
    """Return the switchable installation records for one logical plugin."""

    normalized = _validate_plugin_id(plugin_id)
    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        state = _load_state(state_path)
        current = _state_for_current_schema(state)
        slots: list[InventoryInstallationSlot] = []
        for raw_slot in current["installations"]:
            if not isinstance(raw_slot, dict):
                raise PluginInventoryError("installations contains an invalid slot")
            raw_plugin_id = raw_slot.get("logical_plugin_id")
            if not isinstance(raw_plugin_id, str) or raw_plugin_id.casefold() != normalized:
                continue
            installation_key = raw_slot.get("installation_key")
            installation_kind = raw_slot.get("root_id")
            directory_name = raw_slot.get("directory_name")
            if (
                not isinstance(installation_key, str)
                or installation_kind not in {"managed", "legacy"}
                or not isinstance(directory_name, str)
                or not directory_name
            ):
                raise PluginInventoryError("installations contains an invalid slot")
            package_id = raw_slot.get("package_id")
            source = raw_slot.get("source")
            installed_at = raw_slot.get("installed_at")
            slots.append(
                InventoryInstallationSlot(
                    installation_key=installation_key,
                    logical_plugin_id=normalized,
                    installation_kind=installation_kind,
                    directory_name=directory_name,
                    package_id=package_id if isinstance(package_id, str) else "",
                    source=source if isinstance(source, str) else "manual",
                    installed_at=installed_at if isinstance(installed_at, str) else None,
                )
            )

        deleted = False
        active_installation_key: str | None = None
        for claim_plugin_id, raw_claim in current["activation_claims"].items():
            if not isinstance(claim_plugin_id, str) or claim_plugin_id.casefold() != normalized:
                continue
            if not isinstance(raw_claim, dict):
                raise PluginInventoryError("activation claim is invalid")
            if raw_claim.get("state") == "deleted":
                deleted = True
            elif raw_claim.get("state") == "active":
                raw_key = raw_claim.get("installation_key")
                if not isinstance(raw_key, str):
                    raise PluginInventoryError("activation claim has no installation key")
                active_installation_key = raw_key
            else:
                raise PluginInventoryError("activation claim has an invalid state")

        return PluginInstallationState(
            generation=int(current["generation"]),
            deleted=deleted,
            active_installation_key=active_installation_key,
            installations=tuple(slots),
        )


def select_plugin_installation(
    plugin_id: str,
    *,
    installation_key: str | None,
    expected_generation: int,
    path: Path | None = None,
) -> bool:
    """Select a recorded overlay, or clear the claim to use the built-in.

    ``installation_key=None`` deliberately keeps the installation slot and
    only removes the active claim.  The Resolver can then select the built-in
    candidate while the managed payload remains available for a later switch.
    """

    normalized = _validate_plugin_id(plugin_id)
    if not isinstance(expected_generation, int) or expected_generation < 0:
        raise PluginInventoryError("expected inventory generation is invalid")
    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        state = _load_state(state_path)
        current = _state_for_current_schema(state)
        actual_generation = int(current["generation"])
        if actual_generation != expected_generation:
            raise PluginInventoryError("plugin inventory generation changed")

        if installation_key is not None:
            matching_slots = [
                raw_slot
                for raw_slot in current["installations"]
                if isinstance(raw_slot, dict)
                and raw_slot.get("installation_key") == installation_key
                and isinstance(raw_slot.get("logical_plugin_id"), str)
                and str(raw_slot["logical_plugin_id"]).casefold() == normalized
            ]
            if len(matching_slots) != 1:
                raise PluginInventoryError("requested plugin installation is unavailable")

        activation_claims = dict(current["activation_claims"])
        equivalent_keys = [
            key
            for key in activation_claims
            if isinstance(key, str) and key.casefold() == normalized
        ]
        existing_claim = next(
            (
                activation_claims[key]
                for key in equivalent_keys
                if isinstance(activation_claims[key], dict)
            ),
            None,
        )
        already_selected = (
            installation_key is None
            and existing_claim is None
        ) or (
            installation_key is not None
            and isinstance(existing_claim, dict)
            and existing_claim.get("state") == "active"
            and existing_claim.get("installation_key") == installation_key
        )
        if already_selected:
            return False

        for key in equivalent_keys:
            activation_claims.pop(key, None)
        timestamp = _now_iso()
        if installation_key is not None:
            activation_claims[normalized] = {
                "state": "active",
                "installation_key": installation_key,
                "reason": "user_selected",
                "updated_at": timestamp,
            }

        updated = dict(current)
        updated["generation"] = actual_generation + 1
        updated["updated_at"] = timestamp
        updated["activation_claims"] = activation_claims
        _atomic_write(state_path, updated)
        return True


def get_user_installation_package_state_files(
    plugin_id: str,
    *,
    directory_name: str,
    path: Path | None = None,
) -> dict[str, str] | None:
    """Return the last managed package receipt, or ``None`` for legacy installs."""

    normalized = _validate_plugin_id(plugin_id)
    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        state = _load_state(state_path)
        for raw_slot in state["installations"]:
            if not isinstance(raw_slot, dict):
                raise PluginInventoryError("installations contains an invalid slot")
            raw_plugin_id = raw_slot.get("logical_plugin_id")
            if (
                isinstance(raw_plugin_id, str)
                and raw_plugin_id.casefold() == normalized
                and raw_slot.get("directory_name") == directory_name
            ):
                raw_files = raw_slot.get("package_state_files")
                if raw_files is None:
                    return None
                if not isinstance(raw_files, dict):
                    raise PluginInventoryError(
                        "installation package_state_files is invalid"
                    )
                try:
                    return validate_package_state_files(raw_files)
                except ValueError as exc:
                    raise PluginInventoryError(
                        "installation package_state_files is invalid"
                    ) from exc
    return None


def mark_plugin_deleted(
    plugin_id: str,
    *,
    path: Path | None = None,
) -> bool:
    """Persist a logical deletion while retaining plugin config/data/cache."""

    normalized = _validate_plugin_id(plugin_id)
    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        state = _load_state(state_path)
        activation_claims = dict(state["activation_claims"])
        equivalent_claim_keys = [
            key
            for key in activation_claims
            if isinstance(key, str) and key.casefold() == normalized
        ]
        existing_claim = next(
            (
                activation_claims[key]
                for key in equivalent_claim_keys
                if isinstance(activation_claims[key], dict)
            ),
            None,
        )
        if isinstance(existing_claim, dict) and existing_claim.get("state") == "deleted":
            return False
        for key in equivalent_claim_keys:
            activation_claims.pop(key, None)
        timestamp = _now_iso()
        activation_claims[normalized] = {
            "state": "deleted",
            "deleted_at": timestamp,
            "retain_user_data": True,
        }
        updated = _state_for_current_schema(state)
        updated["generation"] = int(state["generation"]) + 1
        updated["updated_at"] = timestamp
        updated["installations"] = [
            slot
            for slot in state["installations"]
            if not isinstance(slot, dict)
            or not isinstance(slot.get("logical_plugin_id"), str)
            or str(slot.get("logical_plugin_id")).casefold() != normalized
        ]
        updated["activation_claims"] = activation_claims
        updated = _state_for_current_schema(updated)
        _atomic_write(state_path, updated)
        return True


def record_user_installation(
    plugin_id: str,
    *,
    directory_name: str,
    package_id: str,
    source: str,
    package_state_files: dict[str, str] | None = None,
    path: Path | None = None,
    installation_kind: Literal["managed", "legacy"] = "legacy",
) -> None:
    """Record a successful install and select its exact installation slot.

    Existing callers default to ``legacy`` because they still install payload
    and mutable state beneath the historical user plugin root.  The managed
    code-root slice opts into ``managed`` explicitly.
    """

    normalized = _validate_plugin_id(plugin_id)
    if (
        not directory_name
        or directory_name in {".", ".."}
        or "/" in directory_name
        or "\\" in directory_name
    ):
        raise PluginInventoryError(f"invalid plugin directory: {directory_name!r}")
    if source not in {"manual", "imported", "market"}:
        raise PluginInventoryError(f"invalid installation source: {source!r}")
    if installation_kind not in {"managed", "legacy"}:
        raise PluginInventoryError(
            f"invalid installation kind: {installation_kind!r}"
        )
    try:
        validated_package_state_files = (
            validate_package_state_files(package_state_files)
            if package_state_files is not None
            else None
        )
    except ValueError as exc:
        raise PluginInventoryError("invalid package state ownership receipt") from exc

    state_path = path or resolve_inventory_path()
    installation_key = f"{installation_kind}:{directory_name}"
    with _STORE_LOCK:
        state = _load_state(state_path)
        timestamp = _now_iso()
        installations: list[dict[str, Any]] = []
        installed_at = timestamp
        previous_package_state_files: dict[str, str] | None = None
        for raw_slot in state["installations"]:
            if not isinstance(raw_slot, dict):
                raise PluginInventoryError("installations contains an invalid slot")
            if raw_slot.get("installation_key") == installation_key:
                prior_installed_at = raw_slot.get("installed_at")
                if isinstance(prior_installed_at, str) and prior_installed_at:
                    installed_at = prior_installed_at
                raw_files = raw_slot.get("package_state_files")
                if isinstance(raw_files, dict):
                    try:
                        previous_package_state_files = validate_package_state_files(
                            raw_files
                        )
                    except ValueError as exc:
                        raise PluginInventoryError(
                            "installation package_state_files is invalid"
                        ) from exc
                continue
            raw_plugin_id = raw_slot.get("logical_plugin_id")
            if isinstance(raw_plugin_id, str) and raw_plugin_id.casefold() == normalized:
                continue
            installations.append(dict(raw_slot))
        installation = {
            "installation_key": installation_key,
            "logical_plugin_id": normalized,
            "root_id": installation_kind,
            "directory_name": directory_name,
            "package_id": package_id,
            "source": source,
            "installed_at": installed_at,
            "updated_at": timestamp,
        }
        resolved_package_state_files = (
            validated_package_state_files
            if validated_package_state_files is not None
            else previous_package_state_files
        )
        if resolved_package_state_files is not None:
            installation["package_state_files"] = resolved_package_state_files
        installations.append(installation)
        activation_claims = dict(state["activation_claims"])
        for key in tuple(activation_claims):
            if isinstance(key, str) and key.casefold() == normalized:
                activation_claims.pop(key, None)
        activation_claims[normalized] = {
            "state": "active",
            "installation_key": installation_key,
            "reason": "user_installed",
            "updated_at": timestamp,
        }
        updated = _state_for_current_schema(state)
        updated["generation"] = int(state["generation"]) + 1
        updated["updated_at"] = timestamp
        updated["installations"] = installations
        updated["activation_claims"] = activation_claims
        updated = _state_for_current_schema(updated)
        _atomic_write(state_path, updated)


def record_managed_installation(
    plugin_id: str,
    *,
    directory_name: str,
    package_id: str,
    source: str,
    package_state_files: dict[str, str] | None = None,
    path: Path | None = None,
) -> None:
    """Record a payload installed in the dedicated managed code root."""

    record_user_installation(
        plugin_id,
        directory_name=directory_name,
        package_id=package_id,
        source=source,
        package_state_files=package_state_files,
        path=path,
        installation_kind="managed",
    )


def remove_user_installation(
    plugin_id: str,
    *,
    path: Path | None = None,
) -> bool:
    """Remove a user overlay claim without disabling the logical plugin.

    Once the user payload is gone the resolver may select a remaining built-in
    candidate.  This is deliberately different from ``mark_plugin_deleted``,
    which represents the user's intent to disable the whole logical plugin.
    """

    normalized = _validate_plugin_id(plugin_id)
    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        state = _load_state(state_path)
        installations: list[dict[str, Any]] = []
        for raw_slot in state["installations"]:
            if not isinstance(raw_slot, dict):
                raise PluginInventoryError("installations contains an invalid slot")
            raw_plugin_id = raw_slot.get("logical_plugin_id")
            if isinstance(raw_plugin_id, str) and raw_plugin_id.casefold() == normalized:
                continue
            installations.append(dict(raw_slot))
        activation_claims = dict(state["activation_claims"])
        removed_claim = False
        for key in tuple(activation_claims):
            if isinstance(key, str) and key.casefold() == normalized:
                record = activation_claims.get(key)
                if isinstance(record, dict) and record.get("state") == "active":
                    activation_claims.pop(key, None)
                    removed_claim = True
        removed_slot = len(installations) != len(state["installations"])
        if not removed_slot and not removed_claim:
            return False

        timestamp = _now_iso()
        updated = _state_for_current_schema(state)
        updated["generation"] = int(state["generation"]) + 1
        updated["updated_at"] = timestamp
        updated["installations"] = installations
        updated["activation_claims"] = activation_claims
        updated = _state_for_current_schema(updated)
        _atomic_write(state_path, updated)
        return True


def clear_plugin_deleted(
    plugin_id: str,
    *,
    path: Path | None = None,
) -> bool:
    """Clear deletion intent after a replacement installation succeeds."""

    normalized = _validate_plugin_id(plugin_id)
    state_path = path or resolve_inventory_path()
    with _STORE_LOCK:
        state = _load_state(state_path)
        activation_claims = dict(state["activation_claims"])
        equivalent_claim_keys = [
            key
            for key in activation_claims
            if isinstance(key, str) and key.casefold() == normalized
        ]
        if not equivalent_claim_keys:
            return False
        for key in equivalent_claim_keys:
            activation_claims.pop(key, None)
        timestamp = _now_iso()
        updated = _state_for_current_schema(state)
        updated["generation"] = int(state["generation"]) + 1
        updated["updated_at"] = timestamp
        updated["activation_claims"] = activation_claims
        updated = _state_for_current_schema(updated)
        _atomic_write(state_path, updated)
        return True
