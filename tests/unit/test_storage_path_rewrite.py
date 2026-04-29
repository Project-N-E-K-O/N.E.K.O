import pytest

from utils.storage_path_rewrite import rebase_runtime_bound_workshop_config_paths


@pytest.mark.unit
def test_rebase_runtime_bound_workshop_config_paths_only_rewrites_source_root_paths(tmp_path):
    source_root = tmp_path / "old-root" / "N.E.K.O"
    target_root = tmp_path / "new-root" / "N.E.K.O"
    external_mods = tmp_path / "external-mods"
    payload = {
        "default_workshop_folder": str(source_root / "workshop"),
        "user_workshop_folder": str(source_root / "workshop" / "cached"),
        "steam_workshop_path": str(source_root / "steam-workshop"),
        "user_mod_folder": str(external_mods),
        "auto_create_folder": True,
    }

    rewritten = rebase_runtime_bound_workshop_config_paths(
        payload,
        source_root=source_root,
        target_root=target_root,
    )

    assert rewritten is not payload
    assert payload["default_workshop_folder"] == str(source_root / "workshop")
    assert rewritten["default_workshop_folder"] == str(target_root / "workshop")
    assert rewritten["user_workshop_folder"] == str(target_root / "workshop" / "cached")
    assert rewritten["steam_workshop_path"] == str(target_root / "steam-workshop")
    assert rewritten["user_mod_folder"] == str(external_mods)
    assert rewritten["auto_create_folder"] is True


@pytest.mark.unit
def test_rebase_runtime_bound_workshop_config_paths_preserves_unrelated_payload(tmp_path):
    source_root = tmp_path / "old-root" / "N.E.K.O"
    target_root = tmp_path / "new-root" / "N.E.K.O"
    payload = {
        "default_workshop_folder": str(tmp_path / "other-root" / "workshop"),
        "user_mod_folder": "",
        "note": {"path": str(source_root / "not-a-workshop-config-field")},
    }

    rewritten = rebase_runtime_bound_workshop_config_paths(
        payload,
        source_root=source_root,
        target_root=target_root,
    )

    assert rewritten is payload
    assert rewritten == payload


@pytest.mark.unit
def test_rebase_runtime_bound_workshop_config_paths_rewrites_source_root_itself(tmp_path):
    source_root = tmp_path / "old-root" / "N.E.K.O"
    target_root = tmp_path / "new-root" / "N.E.K.O"

    rewritten = rebase_runtime_bound_workshop_config_paths(
        {"default_workshop_folder": str(source_root)},
        source_root=source_root,
        target_root=target_root,
    )

    assert rewritten["default_workshop_folder"] == str(target_root)
