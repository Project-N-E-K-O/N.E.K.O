"""Side-effect-free projection of switchable plugin code installations."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from plugin.server.application.plugins.inventory_store import (
    InventoryInstallationSlot,
    PluginInventoryError,
    get_inventory_resolution,
    get_plugin_installation_state,
)
from plugin.server.application.plugins.resolver import (
    PluginCandidate,
    resolve_plugin_candidates,
)


InstallationKind = Literal["builtin", "managed", "legacy"]


@dataclass(frozen=True, slots=True)
class PluginInstallationRoots:
    builtin_root: Path
    managed_root: Path | None
    legacy_root: Path

    @classmethod
    def from_settings(cls) -> "PluginInstallationRoots":
        from plugin import settings

        managed_root = Path(settings.MANAGED_PLUGIN_INSTALLATIONS_ROOT).resolve()
        legacy_root = Path(settings.USER_PLUGIN_CONFIG_ROOT).resolve()
        return cls(
            builtin_root=Path(settings.BUILTIN_PLUGIN_CONFIG_ROOT).resolve(),
            managed_root=None if managed_root == legacy_root else managed_root,
            legacy_root=legacy_root,
        )

    @property
    def source_roots(self) -> tuple[tuple[str, Path], ...]:
        roots: list[tuple[str, Path]] = [("builtin", self.builtin_root)]
        if self.managed_root is not None:
            roots.append(("managed", self.managed_root))
        roots.append(("user", self.legacy_root))
        return tuple(roots)


@dataclass(frozen=True, slots=True)
class InstallationCandidateView:
    selection_id: str
    installation_key: str | None
    kind: InstallationKind
    directory_name: str
    name: str
    version: str
    source: str
    installed_at: str | None
    active: bool
    selectable: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class PluginInstallationProjection:
    plugin_id: str
    generation: int
    status: str
    reason: str
    active_selection_id: str | None
    candidates: tuple[InstallationCandidateView, ...]


@dataclass(frozen=True, slots=True)
class _ScannedInstallation:
    resolver_candidate: PluginCandidate
    name: str
    version: str
    slot: InventoryInstallationSlot | None


def _read_manifest_summary(config_path: Path) -> tuple[str, str, str] | None:
    try:
        with config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    plugin = raw.get("plugin")
    if not isinstance(plugin, dict):
        return None
    plugin_id = plugin.get("id")
    if not isinstance(plugin_id, str) or not plugin_id:
        return None
    name = plugin.get("name")
    version = plugin.get("version")
    return (
        plugin_id,
        name if isinstance(name, str) and name else plugin_id,
        version if isinstance(version, str) and version else "unknown",
    )


def _slot_for_candidate(
    *,
    slots: tuple[InventoryInstallationSlot, ...],
    kind: str,
    directory_name: str,
) -> InventoryInstallationSlot | None:
    installation_kind = "managed" if kind == "managed" else "legacy"
    matches = [
        slot
        for slot in slots
        if slot.installation_kind == installation_kind
        and slot.directory_name == directory_name
    ]
    return matches[0] if len(matches) == 1 else None


def inspect_plugin_installations(
    plugin_id: str,
    *,
    roots: PluginInstallationRoots | None = None,
    inventory_path: Path | None = None,
) -> PluginInstallationProjection:
    """Inspect installed candidates without importing plugin Python code."""

    normalized_plugin_id = plugin_id.strip().casefold()
    if not normalized_plugin_id:
        raise PluginInventoryError("invalid plugin id")
    selected_roots = roots or PluginInstallationRoots.from_settings()
    state = get_plugin_installation_state(plugin_id, path=inventory_path)
    inventory = get_inventory_resolution(path=inventory_path)
    if not inventory.authoritative:
        raise PluginInventoryError("plugin inventory is unavailable")

    scanned: list[_ScannedInstallation] = []
    for root_id, root in selected_roots.source_roots:
        if not root.exists():
            continue
        for config_path in sorted(root.glob("*/plugin.toml")):
            summary = _read_manifest_summary(config_path)
            if summary is None or summary[0].casefold() != normalized_plugin_id:
                continue
            manifest_plugin_id, name, version = summary
            kind = "managed" if root_id == "managed" else (
                "builtin" if root_id == "builtin" else "legacy"
            )
            slot = None
            if kind != "builtin":
                slot = _slot_for_candidate(
                    slots=state.installations,
                    kind=kind,
                    directory_name=config_path.parent.name,
                )
            scanned.append(
                _ScannedInstallation(
                    resolver_candidate=PluginCandidate(
                        logical_plugin_id=manifest_plugin_id,
                        root_id=root_id,
                        directory_name=config_path.parent.name,
                        config_path=config_path.resolve(strict=False),
                        installation_key=(slot.installation_key if slot is not None else None),
                    ),
                    name=name,
                    version=version,
                    slot=slot,
                )
            )

    if not scanned:
        raise PluginInventoryError("plugin has no available installation")
    resolutions = resolve_plugin_candidates(
        [item.resolver_candidate for item in scanned],
        inventory=inventory,
    )
    if len(resolutions) != 1:
        raise PluginInventoryError("plugin installation resolution is ambiguous")
    resolution = resolutions[0]
    selected_path = (
        resolution.selected.config_path.resolve(strict=False)
        if resolution.selected is not None
        else None
    )

    candidates: list[InstallationCandidateView] = []
    for item in scanned:
        candidate = item.resolver_candidate
        is_active = selected_path is not None and candidate.config_path == selected_path
        if candidate.root_id == "builtin":
            selection_id = f"builtin:{candidate.directory_name}"
            selectable = resolution.status != "blocked"
            reason = None if selectable else resolution.reason
            source = "builtin"
            kind: InstallationKind = "builtin"
            installed_at = None
        else:
            selection_id = (
                item.slot.installation_key
                if item.slot is not None
                else f"{candidate.root_id}:{candidate.directory_name}"
            )
            selectable = item.slot is not None and resolution.status != "blocked"
            reason = None if selectable else (
                resolution.reason if resolution.status == "blocked" else "installation_not_recorded"
            )
            source = item.slot.source if item.slot is not None else "manual"
            kind = "managed" if candidate.root_id == "managed" else "legacy"
            installed_at = item.slot.installed_at if item.slot is not None else None
        candidates.append(
            InstallationCandidateView(
                selection_id=selection_id,
                installation_key=(item.slot.installation_key if item.slot is not None else None),
                kind=kind,
                directory_name=candidate.directory_name,
                name=item.name,
                version=item.version,
                source=source,
                installed_at=installed_at,
                active=is_active,
                selectable=selectable,
                reason=reason,
            )
        )

    active = next((item.selection_id for item in candidates if item.active), None)
    return PluginInstallationProjection(
        plugin_id=resolution.logical_plugin_id,
        generation=state.generation,
        status=resolution.status,
        reason=resolution.reason,
        active_selection_id=active,
        candidates=tuple(candidates),
    )


def serialize_plugin_installation_projection(
    projection: PluginInstallationProjection,
) -> dict[str, object]:
    return {
        "plugin_id": projection.plugin_id,
        "generation": projection.generation,
        "status": projection.status,
        "reason": projection.reason,
        "active_selection_id": projection.active_selection_id,
        "candidates": [
            {
                "selection_id": item.selection_id,
                "kind": item.kind,
                "name": item.name,
                "version": item.version,
                "source": item.source,
                "installed_at": item.installed_at,
                "active": item.active,
                "selectable": item.selectable,
                "reason": item.reason,
            }
            for item in projection.candidates
        ],
    }


__all__ = [
    "InstallationCandidateView",
    "PluginInstallationRoots",
    "PluginInstallationProjection",
    "inspect_plugin_installations",
    "serialize_plugin_installation_projection",
]
