"""Pure selection of one runtime candidate per logical plugin ID."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from plugin.server.application.plugins.inventory_store import (
    ActiveInstallation,
    InventoryResolution,
)


RootId = Literal["builtin", "managed", "legacy", "user"]
ResolutionStatus = Literal["selected", "deleted", "blocked"]


@dataclass(frozen=True, slots=True)
class PluginCandidate:
    logical_plugin_id: str
    root_id: RootId
    directory_name: str
    config_path: Path
    installation_key: str | None = None


@dataclass(frozen=True, slots=True)
class PluginResolution:
    logical_plugin_id: str
    status: ResolutionStatus
    selected: PluginCandidate | None
    rejected: tuple[PluginCandidate, ...]
    reason: str


def resolve_plugin_candidates(
    candidates: list[PluginCandidate],
    *,
    inventory: InventoryResolution,
) -> tuple[PluginResolution, ...]:
    """Resolve candidates without importing plugin code or touching disk."""

    grouped: dict[str, list[PluginCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.logical_plugin_id.casefold(), []).append(candidate)

    resolutions: list[PluginResolution] = []
    for canonical_plugin_id in sorted(grouped):
        group = sorted(
            grouped[canonical_plugin_id],
            key=lambda candidate: (
                {"builtin": 0, "managed": 1, "legacy": 2, "user": 2}[
                    candidate.root_id
                ],
                candidate.directory_name,
                str(candidate.config_path),
            ),
        )
        if not inventory.authoritative:
            resolutions.append(
                PluginResolution(
                    logical_plugin_id=canonical_plugin_id,
                    status="blocked",
                    selected=None,
                    rejected=tuple(group),
                    reason="plugin_inventory_unavailable",
                )
            )
            continue
        plugin_id_spellings = {candidate.logical_plugin_id for candidate in group}
        if len(plugin_id_spellings) != 1:
            resolutions.append(
                PluginResolution(
                    logical_plugin_id=canonical_plugin_id,
                    status="blocked",
                    selected=None,
                    rejected=tuple(group),
                    reason="logical_plugin_id_case_collision",
                )
            )
            continue
        plugin_id = group[0].logical_plugin_id
        if canonical_plugin_id in inventory.deleted_plugin_ids:
            resolutions.append(
                PluginResolution(
                    logical_plugin_id=plugin_id,
                    status="deleted",
                    selected=None,
                    rejected=tuple(group),
                    reason="user_deleted",
                )
            )
            continue

        active_installation = inventory.active_installations.get(canonical_plugin_id)
        if active_installation is None:
            claimed_directory = inventory.active_user_directories.get(
                canonical_plugin_id
            )
            if claimed_directory is not None:
                active_installation = ActiveInstallation(
                    installation_key=f"user:{claimed_directory}",
                    installation_kind="legacy",
                    directory_name=claimed_directory,
                )
        if active_installation is not None:
            def _candidate_kind(candidate: PluginCandidate) -> str | None:
                if candidate.root_id == "managed":
                    return "managed"
                if candidate.root_id in {"legacy", "user"}:
                    return "legacy"
                return None

            def _candidate_keys(candidate: PluginCandidate) -> frozenset[str]:
                if candidate.installation_key is not None:
                    return frozenset({candidate.installation_key})
                if candidate.root_id == "user":
                    return frozenset(
                        {
                            f"user:{candidate.directory_name}",
                            f"legacy:{candidate.directory_name}",
                        }
                    )
                kind = _candidate_kind(candidate)
                if kind is None:
                    return frozenset()
                return frozenset({f"{kind}:{candidate.directory_name}"})

            install_candidates = [
                candidate
                for candidate in group
                if _candidate_kind(candidate)
                == active_installation.installation_kind
            ]
            claimed = [
                candidate
                for candidate in install_candidates
                if candidate.directory_name == active_installation.directory_name
                and active_installation.installation_key in _candidate_keys(candidate)
            ]
            claim_is_unambiguous = len(claimed) == 1 and (
                active_installation.installation_kind == "managed"
                or len(install_candidates) == 1
            )
            if claim_is_unambiguous:
                selected = claimed[0]
                resolutions.append(
                    PluginResolution(
                        logical_plugin_id=plugin_id,
                        status="selected",
                        selected=selected,
                        rejected=tuple(item for item in group if item != selected),
                        reason=(
                            "explicit_managed_installation"
                            if active_installation.installation_kind == "managed"
                            else "explicit_user_installation"
                        ),
                    )
                )
                continue

            if len(claimed) == 1 and len(install_candidates) > 1:
                resolutions.append(
                    PluginResolution(
                        logical_plugin_id=plugin_id,
                        status="blocked",
                        selected=None,
                        rejected=tuple(group),
                        reason="unexpected_user_installation_candidates",
                    )
                )
                continue

            builtin = [candidate for candidate in group if candidate.root_id == "builtin"]
            if len(claimed) == 0 and len(builtin) == 1:
                selected = builtin[0]
                resolutions.append(
                    PluginResolution(
                        logical_plugin_id=plugin_id,
                        status="selected",
                        selected=selected,
                        rejected=tuple(item for item in group if item != selected),
                        reason="missing_user_installation_fallback_builtin",
                    )
                )
                continue

            resolutions.append(
                PluginResolution(
                    logical_plugin_id=plugin_id,
                    status="blocked",
                    selected=None,
                    rejected=tuple(group),
                    reason="claimed_user_installation_missing_or_ambiguous",
                )
            )
            continue

        builtin = [candidate for candidate in group if candidate.root_id == "builtin"]
        managed = [candidate for candidate in group if candidate.root_id == "managed"]
        legacy = [
            candidate for candidate in group if candidate.root_id in {"legacy", "user"}
        ]
        if len(builtin) == 1:
            selected = builtin[0]
            resolutions.append(
                PluginResolution(
                    logical_plugin_id=plugin_id,
                    status="selected",
                    selected=selected,
                    rejected=tuple(item for item in group if item != selected),
                    reason="builtin_default",
                )
            )
            continue
        if not builtin and not managed and len(legacy) == 1:
            resolutions.append(
                PluginResolution(
                    logical_plugin_id=plugin_id,
                    status="selected",
                    selected=legacy[0],
                    rejected=(),
                    reason="single_legacy_user_installation",
                )
            )
            continue

        resolutions.append(
            PluginResolution(
                logical_plugin_id=plugin_id,
                status="blocked",
                selected=None,
                rejected=tuple(group),
                reason="multiple_unclaimed_installations",
            )
        )

    return tuple(resolutions)
