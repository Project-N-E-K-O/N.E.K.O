from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugin.server.application.plugins.inventory_store import (
    PluginInventoryError,
    clear_plugin_deleted,
    get_deleted_plugin_ids,
    get_inventory_resolution,
    get_user_installation_package_state_files,
    mark_plugin_deleted,
    record_user_installation,
    remove_user_installation,
)


pytestmark = pytest.mark.plugin_unit


def test_mark_and_clear_plugin_deletion_is_persistent(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin-installations.json"

    assert mark_plugin_deleted("demo_plugin", path=state_path) is True
    assert mark_plugin_deleted("demo_plugin", path=state_path) is False
    assert get_deleted_plugin_ids(path=state_path) == frozenset({"demo_plugin"})

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 2
    assert state["generation"] == 1
    assert state["installations"] == []
    assert state["activation_claims"]["demo_plugin"]["retain_user_data"] is True

    assert clear_plugin_deleted("demo_plugin", path=state_path) is True
    assert clear_plugin_deleted("demo_plugin", path=state_path) is False
    assert get_deleted_plugin_ids(path=state_path) == frozenset()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["generation"] == 2


def test_store_preserves_unknown_fields_in_current_schema(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin-installations.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generation": 4,
                "updated_at": "2026-01-01T00:00:00.000Z",
                "installations": [],
                "activation_claims": {},
                "future_metadata": {"keep": True},
            }
        ),
        encoding="utf-8",
    )

    mark_plugin_deleted("demo", path=state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["generation"] == 5
    assert state["future_metadata"] == {"keep": True}


def test_successful_install_replaces_deletion_with_active_user_claim(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin-installations.json"
    mark_plugin_deleted("demo", path=state_path)

    record_user_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="market",
        path=state_path,
    )

    resolution = get_inventory_resolution(path=state_path)
    assert resolution.deleted_plugin_ids == frozenset()
    assert resolution.active_user_directories == {"demo": "demo"}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["generation"] == 2
    assert state["installations"][0]["source"] == "market"
    assert state["activation_claims"]["demo"]["state"] == "active"


def test_installation_persists_package_state_file_ownership(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin-installations.json"
    owned_files = {
        "data/defaults.json": "a" * 64,
        "config/schema.json": "b" * 64,
    }

    record_user_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="imported",
        package_state_files=owned_files,
        path=state_path,
    )

    assert get_user_installation_package_state_files(
        "demo",
        directory_name="demo",
        path=state_path,
    ) == owned_files
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["installations"][0]["package_state_files"] == owned_files


def test_installation_rejects_unsafe_package_state_ownership_path(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin-installations.json"

    with pytest.raises(PluginInventoryError, match="ownership"):
        record_user_installation(
            "demo",
            directory_name="demo",
            package_id="demo-package",
            source="imported",
            package_state_files={"data/../outside.txt": "a" * 64},
            path=state_path,
        )

    assert not state_path.exists()


def test_remove_user_installation_clears_overlay_without_hiding_builtin(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "plugin-installations.json"
    record_user_installation(
        "demo",
        directory_name="demo",
        package_id="demo-package",
        source="market",
        path=state_path,
    )

    assert remove_user_installation("demo", path=state_path) is True
    assert remove_user_installation("demo", path=state_path) is False

    resolution = get_inventory_resolution(path=state_path)
    assert resolution.deleted_plugin_ids == frozenset()
    assert resolution.active_user_directories == {}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["installations"] == []
    assert state["activation_claims"] == {}


def test_future_schema_is_never_downgraded(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin-installations.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "generation": 9,
                "updated_at": None,
                "installations": [],
                "activation_claims": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PluginInventoryError, match="unsupported"):
        mark_plugin_deleted("demo", path=state_path)

    assert json.loads(state_path.read_text(encoding="utf-8"))["schema_version"] == 3


def test_inventory_persists_and_reads_logical_ids_case_insensitively(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "plugin-installations.json"

    mark_plugin_deleted("Demo", path=state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state["activation_claims"]) == {"demo"}
    assert get_deleted_plugin_ids(path=state_path) == frozenset({"demo"})

    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generation": 3,
                "updated_at": None,
                "installations": [
                    {
                        "installation_key": "user:Demo",
                        "logical_plugin_id": "Demo",
                        "root_id": "user",
                        "directory_name": "Demo",
                    }
                ],
                "activation_claims": {
                    "Demo": {
                        "state": "active",
                        "installation_key": "user:Demo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    resolution = get_inventory_resolution(path=state_path)
    assert resolution.active_user_directories == {"demo": "Demo"}
