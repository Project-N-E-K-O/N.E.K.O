from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.application.plugins.inventory_store import InventoryResolution
from plugin.server.application.plugins.resolver import (
    PluginCandidate,
    resolve_plugin_candidates,
)


pytestmark = pytest.mark.plugin_unit


def _candidate(plugin_id: str, root_id: str, directory: str) -> PluginCandidate:
    return PluginCandidate(
        logical_plugin_id=plugin_id,
        root_id=root_id,  # type: ignore[arg-type]
        directory_name=directory,
        config_path=Path(root_id) / directory / "plugin.toml",
    )


def _inventory(
    *,
    deleted: set[str] | None = None,
    active: dict[str, str] | None = None,
) -> InventoryResolution:
    return InventoryResolution(
        deleted_plugin_ids=frozenset(deleted or set()),
        active_user_directories=dict(active or {}),
    )


def test_builtin_is_default_and_residual_user_copy_is_rejected() -> None:
    builtin = _candidate("demo", "builtin", "demo")
    residual = _candidate("demo", "user", "demo-old")

    resolution = resolve_plugin_candidates(
        [builtin, residual],
        inventory=_inventory(),
    )[0]

    assert resolution.status == "selected"
    assert resolution.selected == builtin
    assert resolution.rejected == (residual,)
    assert resolution.reason == "builtin_default"


def test_logical_plugin_ids_differing_only_by_case_are_blocked() -> None:
    resolutions = resolve_plugin_candidates(
        [
            _candidate("demo", "builtin", "demo"),
            _candidate("Demo", "user", "Demo"),
        ],
        inventory=_inventory(),
    )

    assert len(resolutions) == 1
    resolution = resolutions[0]
    assert resolution.status == "blocked"
    assert resolution.selected is None
    assert {candidate.logical_plugin_id for candidate in resolution.rejected} == {"demo", "Demo"}
    assert resolution.reason == "logical_plugin_id_case_collision"


def test_case_only_manifest_rename_cannot_bypass_deletion_claim() -> None:
    resolution = resolve_plugin_candidates(
        [_candidate("Demo", "user", "demo")],
        inventory=_inventory(deleted={"demo"}),
    )[0]

    assert resolution.status == "deleted"
    assert resolution.selected is None
    assert resolution.reason == "user_deleted"


def test_explicit_user_installation_replaces_builtin_without_suffix() -> None:
    builtin = _candidate("demo", "builtin", "demo")
    installed = _candidate("demo", "user", "demo")

    resolution = resolve_plugin_candidates(
        [builtin, installed],
        inventory=_inventory(active={"demo": "demo"}),
    )[0]

    assert resolution.status == "selected"
    assert resolution.selected == installed
    assert resolution.rejected == (builtin,)
    assert resolution.reason == "explicit_user_installation"


def test_explicit_installation_with_residual_user_copy_fails_closed() -> None:
    resolution = resolve_plugin_candidates(
        [
            _candidate("demo", "builtin", "demo"),
            _candidate("demo", "user", "demo"),
            _candidate("demo", "user", "demo-old"),
        ],
        inventory=_inventory(active={"demo": "demo"}),
    )[0]

    assert resolution.status == "blocked"
    assert resolution.selected is None
    assert resolution.reason == "unexpected_user_installation_candidates"


def test_multiple_unclaimed_user_copies_fail_closed() -> None:
    resolution = resolve_plugin_candidates(
        [
            _candidate("demo", "user", "demo-a"),
            _candidate("demo", "user", "demo-b"),
        ],
        inventory=_inventory(),
    )[0]

    assert resolution.status == "blocked"
    assert resolution.selected is None
    assert resolution.reason == "multiple_unclaimed_installations"


def test_missing_claimed_user_copy_falls_back_to_single_builtin() -> None:
    builtin = _candidate("demo", "builtin", "demo")

    resolution = resolve_plugin_candidates(
        [builtin],
        inventory=_inventory(active={"demo": "missing"}),
    )[0]

    assert resolution.selected == builtin
    assert resolution.reason == "missing_user_installation_fallback_builtin"


def test_deleted_plugin_has_no_selected_candidate() -> None:
    resolution = resolve_plugin_candidates(
        [_candidate("demo", "builtin", "demo")],
        inventory=_inventory(deleted={"demo"}),
    )[0]

    assert resolution.status == "deleted"
    assert resolution.selected is None
