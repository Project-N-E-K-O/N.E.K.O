from pathlib import Path
from unittest.mock import patch

import pytest

from utils.file_utils import atomic_write_json
from utils.storage_layout import (
    NEKO_STORAGE_ANCHOR_ROOT_ENV,
    NEKO_STORAGE_SELECTED_ROOT_ENV,
)
from utils.cloudsave_runtime import MaintenanceModeError


def _make_config_manager(tmp_path: Path):
    from utils.config_manager import ConfigManager

    standard_root = tmp_path / "anchor-base"
    with patch.object(
        ConfigManager,
        "_get_documents_directory",
        return_value=tmp_path / "runtime-parent",
    ), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[standard_root],
    ):
        return ConfigManager("N.E.K.O")


@pytest.mark.unit
def test_state_paths_are_explicit_in_default_layout(tmp_path, monkeypatch):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    config_manager = _make_config_manager(tmp_path)

    assert config_manager.anchor_state_dir == config_manager.anchor_root / "state"
    assert config_manager.runtime_state_dir == config_manager.app_docs_dir / "state"
    assert config_manager.local_state_dir == config_manager.anchor_state_dir
    assert config_manager.root_state_path == config_manager.anchor_state_dir / "root_state.json"
    assert config_manager.cloudsave_local_state_path == (
        config_manager.anchor_state_dir / "cloudsave_local_state.json"
    )
    assert config_manager.character_tombstones_state_path == (
        config_manager.anchor_state_dir / "character_tombstones.json"
    )
    assert config_manager.cloudsave_staging_dir == config_manager.anchor_root / ".cloudsave_staging"
    assert config_manager.cloudsave_backups_dir == config_manager.anchor_root / "cloudsave_backups"


@pytest.mark.unit
def test_state_paths_follow_custom_effective_root_without_moving_anchor(tmp_path, monkeypatch):
    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(selected_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(anchor_root))

    config_manager = _make_config_manager(tmp_path)

    assert config_manager.app_docs_dir == selected_root
    assert config_manager.anchor_root == anchor_root
    assert config_manager.anchor_state_dir == anchor_root / "state"
    assert config_manager.runtime_state_dir == selected_root / "state"
    assert config_manager.local_state_dir == anchor_root / "state"
    assert config_manager.root_state_path.parent == anchor_root / "state"
    assert config_manager.cloudsave_local_state_path.parent == anchor_root / "state"
    assert config_manager.character_tombstones_state_path.parent == anchor_root / "state"
    assert config_manager.cloudsave_staging_dir.parent == anchor_root
    assert config_manager.cloudsave_backups_dir.parent == anchor_root

    assert config_manager.ensure_runtime_state_directory() is True
    assert (selected_root / "state").is_dir()
    assert not (anchor_root / "state").exists()


@pytest.mark.unit
def test_state_paths_are_explicit_during_unavailable_selected_root_recovery(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    anchor_root = (tmp_path / "anchor-base" / "N.E.K.O").resolve()
    unavailable_selected_root = (tmp_path / "offline-selected" / "N.E.K.O").resolve()
    atomic_write_json(
        anchor_root / "state" / "storage_policy.json",
        {
            "version": 1,
            "anchor_root": str(anchor_root),
            "selected_root": str(unavailable_selected_root),
            "selection_source": "custom",
            "cloudsave_strategy": "fixed_anchor",
            "first_run_completed": True,
        },
        ensure_ascii=False,
        indent=2,
    )

    config_manager = _make_config_manager(tmp_path)

    assert config_manager.recovery_committed_root_unavailable is True
    assert config_manager.app_docs_dir == anchor_root
    assert config_manager.anchor_state_dir == anchor_root / "state"
    assert config_manager.runtime_state_dir == anchor_root / "state"
    assert config_manager.local_state_dir == config_manager.anchor_state_dir

    with pytest.raises(MaintenanceModeError):
        config_manager.ensure_runtime_state_directory()


@pytest.mark.unit
def test_legacy_runtime_state_imports_only_functional_files_and_retains_source(
    tmp_path, monkeypatch
):
    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(selected_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(anchor_root))
    config_manager = _make_config_manager(tmp_path)

    legacy_state = anchor_root / "state"
    legacy_state.mkdir(parents=True)
    functional_files = {
        "voice_identity.profile": b"profile",
        "voice_identity.settings.json": b"{}",
        "initial_personality_prompt.json": b"{}",
        "new_character_greeting_state.json": b"{}",
        "scoped_prompt_locale_forget_cutoffs.json": b"{}",
        "topic_signals.json": b"{}",
        "topic_signals.used_topics.json": b"{}",
    }
    for filename, payload in functional_files.items():
        (legacy_state / filename).write_bytes(payload)
    atomic_write_json(
        legacy_state / "root_state.json",
        {"version": 1, "mode": "normal"},
        ensure_ascii=False,
        indent=2,
    )

    report = config_manager.import_legacy_runtime_state()

    assert report["status"] == "copied"
    assert set(report["copied"]) == set(functional_files)
    for filename, payload in functional_files.items():
        assert (selected_root / "state" / filename).read_bytes() == payload
        assert (legacy_state / filename).read_bytes() == payload
    assert not (selected_root / "state" / "root_state.json").exists()


@pytest.mark.unit
def test_legacy_runtime_state_import_preserves_target_and_is_idempotent(tmp_path, monkeypatch):
    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(selected_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(anchor_root))
    config_manager = _make_config_manager(tmp_path)
    source_path = anchor_root / "state" / "topic_signals.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"legacy")
    target_path = selected_root / "state" / "topic_signals.json"
    target_path.parent.mkdir(parents=True)
    target_path.write_bytes(b"selected")

    first = config_manager.import_legacy_runtime_state()
    second = config_manager.import_legacy_runtime_state()

    assert first["status"] == "conflicts"
    assert first["conflicts"] == ["topic_signals.json"]
    assert second["status"] == "conflicts"
    assert target_path.read_bytes() == b"selected"
    assert source_path.read_bytes() == b"legacy"


@pytest.mark.unit
def test_legacy_runtime_state_import_skips_same_root_and_recovery(tmp_path, monkeypatch):
    same_root = (tmp_path / "same" / "N.E.K.O").resolve()
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(same_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(same_root))
    config_manager = _make_config_manager(tmp_path)
    same_source = config_manager.anchor_state_dir / "topic_signals.json"
    same_source.parent.mkdir(parents=True)
    same_source.write_bytes(b"legacy")

    same_root_report = config_manager.import_legacy_runtime_state()

    assert same_root_report["status"] == "same_root"
    assert not (config_manager.runtime_state_dir / "topic_signals.json").is_symlink()

    unavailable_selected_root = (tmp_path / "offline-selected" / "N.E.K.O").resolve()
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)
    recovery_anchor = (tmp_path / "anchor-base" / "N.E.K.O").resolve()
    atomic_write_json(
        recovery_anchor / "state" / "storage_policy.json",
        {
            "version": 1,
            "anchor_root": str(recovery_anchor),
            "selected_root": str(unavailable_selected_root),
            "selection_source": "custom",
            "cloudsave_strategy": "fixed_anchor",
            "first_run_completed": True,
        },
        ensure_ascii=False,
        indent=2,
    )
    recovery_manager = _make_config_manager(tmp_path)
    recovery_report = recovery_manager.import_legacy_runtime_state()

    assert recovery_manager.recovery_committed_root_unavailable is True
    assert recovery_report["status"] == "deferred"
    assert not unavailable_selected_root.exists()
