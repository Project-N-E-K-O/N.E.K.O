from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugin.server.application.plugins.inventory_store import (
    get_inventory_resolution,
    mark_plugin_deleted,
    record_managed_installation,
    record_user_installation,
    remove_user_installation,
)
from plugin.server.application.plugins.resolver import (
    PluginCandidate,
    resolve_plugin_candidates,
)


pytestmark = pytest.mark.plugin_unit


def _candidate(root: Path, *, root_id: str) -> PluginCandidate:
    plugin_id = "neko_warthunder"
    return PluginCandidate(
        logical_plugin_id=plugin_id,
        root_id=root_id,  # type: ignore[arg-type]
        directory_name=plugin_id,
        config_path=root / plugin_id / "plugin.toml",
    )


def test_managed_claim_selects_exact_same_id_over_builtin_and_legacy_after_reload(
    tmp_path: Path,
) -> None:
    inventory_path = tmp_path / "plugin-installations.json"
    record_managed_installation(
        "neko_warthunder",
        directory_name="neko_warthunder",
        package_id="market-release-42",
        source="market",
        path=inventory_path,
    )

    persisted = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 2
    assert persisted["installations"][0]["root_id"] == "managed"
    assert (
        persisted["activation_claims"]["neko_warthunder"]["installation_key"]
        == "managed:neko_warthunder"
    )

    builtin = _candidate(tmp_path / "builtin", root_id="builtin")
    managed = _candidate(tmp_path / "plugin-installations", root_id="managed")
    legacy = _candidate(tmp_path / "plugins", root_id="legacy")
    resolution = resolve_plugin_candidates(
        [builtin, legacy, managed],
        inventory=get_inventory_resolution(path=inventory_path),
    )[0]

    assert resolution.status == "selected"
    assert resolution.selected == managed
    assert resolution.reason == "explicit_managed_installation"
    assert set(resolution.rejected) == {builtin, legacy}


def test_removing_managed_claim_safely_falls_back_to_builtin(
    tmp_path: Path,
) -> None:
    inventory_path = tmp_path / "plugin-installations.json"
    record_managed_installation(
        "neko_warthunder",
        directory_name="neko_warthunder",
        package_id="market-release-42",
        source="market",
        path=inventory_path,
    )
    assert remove_user_installation("neko_warthunder", path=inventory_path) is True

    builtin = _candidate(tmp_path / "builtin", root_id="builtin")
    managed = _candidate(tmp_path / "plugin-installations", root_id="managed")
    resolution = resolve_plugin_candidates(
        [managed, builtin],
        inventory=get_inventory_resolution(path=inventory_path),
    )[0]

    assert resolution.status == "selected"
    assert resolution.selected == builtin
    assert resolution.reason == "builtin_default"


def test_single_legacy_directory_remains_compatible_without_becoming_managed(
    tmp_path: Path,
) -> None:
    legacy = _candidate(tmp_path / "plugins", root_id="legacy")

    resolution = resolve_plugin_candidates(
        [legacy],
        inventory=get_inventory_resolution(
            path=tmp_path / "missing-plugin-installations.json"
        ),
    )[0]

    assert resolution.status == "selected"
    assert resolution.selected == legacy
    assert resolution.reason == "single_legacy_user_installation"


def test_current_user_root_claim_remains_selected_during_shadow_phase(
    tmp_path: Path,
) -> None:
    inventory_path = tmp_path / "plugin-installations.json"
    record_user_installation(
        "neko_warthunder",
        directory_name="neko_warthunder",
        package_id="legacy-layout-package",
        source="imported",
        path=inventory_path,
    )
    builtin = _candidate(tmp_path / "builtin", root_id="builtin")
    current_user_root = _candidate(tmp_path / "plugins", root_id="user")

    resolution = resolve_plugin_candidates(
        [builtin, current_user_root],
        inventory=get_inventory_resolution(path=inventory_path),
    )[0]

    assert resolution.status == "selected"
    assert resolution.selected == current_user_root
    assert resolution.reason == "explicit_user_installation"


def test_v1_inventory_is_readable_and_only_upgraded_on_mutation(tmp_path: Path) -> None:
    inventory_path = tmp_path / "plugin-installations.json"
    inventory_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generation": 7,
                "updated_at": None,
                "installations": [
                    {
                        "installation_key": "user:neko_warthunder",
                        "logical_plugin_id": "neko_warthunder",
                        "root_id": "user",
                        "directory_name": "neko_warthunder",
                    },
                    {
                        "installation_key": "user:other_plugin",
                        "logical_plugin_id": "other_plugin",
                        "root_id": "user",
                        "directory_name": "other_plugin",
                    }
                ],
                "activation_claims": {
                    "neko_warthunder": {
                        "state": "active",
                        "installation_key": "user:neko_warthunder",
                    },
                    "other_plugin": {
                        "state": "active",
                        "installation_key": "user:other_plugin",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    resolution = get_inventory_resolution(path=inventory_path)
    assert resolution.active_installations[
        "neko_warthunder"
    ].installation_kind == "legacy"
    assert json.loads(inventory_path.read_text(encoding="utf-8"))["schema_version"] == 1

    mark_plugin_deleted("neko_warthunder", path=inventory_path)

    persisted = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 2
    assert persisted["generation"] == 8
    assert persisted["installations"] == [
        {
            "installation_key": "user:other_plugin",
            "logical_plugin_id": "other_plugin",
            "root_id": "legacy",
            "directory_name": "other_plugin",
        }
    ]
    assert get_inventory_resolution(path=inventory_path).active_installations[
        "other_plugin"
    ].installation_kind == "legacy"
