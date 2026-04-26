from pathlib import Path
from unittest.mock import patch

import pytest

from utils.storage_layout import NEKO_STORAGE_ANCHOR_ROOT_ENV, NEKO_STORAGE_SELECTED_ROOT_ENV, resolve_storage_layout
from utils.storage_policy import save_storage_policy
from utils.file_utils import atomic_write_json


def _make_config_manager(tmp_path: Path):
    from utils.config_manager import ConfigManager

    standard_root = tmp_path / "anchor-base"
    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path / "runtime-parent"),
        patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard_root]),
    ]
    with patchers[0], patchers[1]:
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


@pytest.mark.unit
def test_config_manager_uses_committed_storage_policy_for_selected_and_anchor_roots(tmp_path, monkeypatch):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    config_manager = _make_config_manager(tmp_path)
    selected_root = tmp_path / "custom-selected" / "N.E.K.O"
    selected_root.mkdir(parents=True, exist_ok=True)
    save_storage_policy(
        config_manager,
        selected_root=selected_root,
        selection_source="custom",
    )

    reloaded_manager = _make_config_manager(tmp_path)

    assert reloaded_manager.app_docs_dir == selected_root.resolve()
    assert reloaded_manager.anchor_root == (tmp_path / "anchor-base" / "N.E.K.O").resolve()
    assert reloaded_manager.cloudsave_dir == reloaded_manager.anchor_root / "cloudsave"
    assert reloaded_manager.local_state_dir == reloaded_manager.anchor_root / "state"


@pytest.mark.unit
def test_config_manager_env_overrides_committed_layout(tmp_path, monkeypatch):
    override_selected_root = (tmp_path / "override-selected" / "N.E.K.O").resolve()
    override_anchor_root = (tmp_path / "override-anchor" / "N.E.K.O").resolve()
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(override_selected_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(override_anchor_root))

    config_manager = _make_config_manager(tmp_path)

    assert config_manager.app_docs_dir == override_selected_root
    assert config_manager.anchor_root == override_anchor_root
    assert config_manager.cloudsave_dir == override_anchor_root / "cloudsave"
    assert config_manager.local_state_dir == override_anchor_root / "state"


@pytest.mark.unit
def test_config_manager_env_anchor_takes_precedence_over_policy_anchor(tmp_path, monkeypatch):
    override_selected_root = (tmp_path / "override-selected" / "N.E.K.O").resolve()
    override_anchor_root = (tmp_path / "override-anchor" / "N.E.K.O").resolve()
    stale_policy_anchor = (tmp_path / "stale-policy-anchor" / "N.E.K.O").resolve()
    atomic_write_json(
        override_anchor_root / "state" / "storage_policy.json",
        {
            "version": 1,
            "anchor_root": str(stale_policy_anchor),
            "selected_root": str(override_selected_root),
            "selection_source": "custom",
            "cloudsave_strategy": "fixed_anchor",
            "first_run_completed": True,
        },
        ensure_ascii=False,
        indent=2,
    )
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(override_selected_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(override_anchor_root))

    config_manager = _make_config_manager(tmp_path)

    assert config_manager.app_docs_dir == override_selected_root
    assert config_manager.anchor_root == override_anchor_root
    assert config_manager.anchor_root != stale_policy_anchor


@pytest.mark.unit
def test_resolve_storage_layout_keeps_default_anchor_when_policy_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    config_manager = _make_config_manager(tmp_path)
    layout = resolve_storage_layout(config_manager)

    assert layout["selected_root"] == str((tmp_path / "runtime-parent" / "N.E.K.O").resolve())
    assert layout["anchor_root"] == str((tmp_path / "anchor-base" / "N.E.K.O").resolve())
    assert layout["source"] == "runtime_default"


@pytest.mark.unit
def test_config_manager_uses_anchor_runtime_layout_when_committed_selected_root_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    config_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        config_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    reloaded_manager = _make_config_manager(tmp_path)
    anchor_root = (tmp_path / "anchor-base" / "N.E.K.O").resolve()

    assert reloaded_manager.recovery_committed_root_unavailable is True
    assert reloaded_manager.app_docs_dir == anchor_root
    assert reloaded_manager.anchor_root == anchor_root
    assert reloaded_manager.selected_root == unavailable_selected_root.resolve()
    assert reloaded_manager.reported_current_root == unavailable_selected_root.resolve()

    root_state = reloaded_manager.load_root_state()
    assert root_state["mode"] == "deferred_init"
    assert root_state["current_root"] == str(unavailable_selected_root.resolve())
    assert root_state["last_known_good_root"] == str(unavailable_selected_root.resolve())
    assert root_state["last_migration_backup"] == ""
    assert root_state["legacy_cleanup_pending"] is False

    layout = resolve_storage_layout(reloaded_manager)
    assert layout["selected_root"] == str(anchor_root)
    assert layout["anchor_root"] == str(anchor_root)
    assert layout["source"] == "recovery_runtime"


@pytest.mark.unit
def test_config_manager_uses_env_anchor_when_policy_selected_root_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    env_anchor_root = (tmp_path / "env-anchor" / "N.E.K.O").resolve()
    unavailable_selected_root = (tmp_path / "offline-selected" / "N.E.K.O").resolve()
    stale_policy_anchor = (tmp_path / "stale-policy-anchor" / "N.E.K.O").resolve()
    atomic_write_json(
        env_anchor_root / "state" / "storage_policy.json",
        {
            "version": 1,
            "anchor_root": str(stale_policy_anchor),
            "selected_root": str(unavailable_selected_root),
            "selection_source": "custom",
            "cloudsave_strategy": "fixed_anchor",
            "first_run_completed": True,
        },
        ensure_ascii=False,
        indent=2,
    )
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(env_anchor_root))

    reloaded_manager = _make_config_manager(tmp_path)

    assert reloaded_manager.recovery_committed_root_unavailable is True
    assert reloaded_manager.app_docs_dir == env_anchor_root
    assert reloaded_manager.anchor_root == env_anchor_root
    assert reloaded_manager.selected_root == unavailable_selected_root
    assert reloaded_manager.reported_current_root == unavailable_selected_root


@pytest.mark.unit
def test_config_manager_recovery_state_persist_failure_is_best_effort(
    tmp_path,
    monkeypatch,
):
    from utils.config_manager import ConfigManager

    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    initial_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        initial_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    with patch.object(ConfigManager, "save_root_state", side_effect=OSError("disk unavailable")):
        reloaded_manager = _make_config_manager(tmp_path)

    assert reloaded_manager.recovery_committed_root_unavailable is True
    assert reloaded_manager.selected_root == unavailable_selected_root.resolve()
    assert reloaded_manager.reported_current_root == unavailable_selected_root.resolve()


@pytest.mark.unit
def test_config_manager_preserves_recovery_context_when_launcher_exports_anchor_runtime_layout(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    initial_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        initial_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    recovery_manager = _make_config_manager(tmp_path)
    layout = resolve_storage_layout(recovery_manager)
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, layout["selected_root"])
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, layout["anchor_root"])

    reloaded_manager = _make_config_manager(tmp_path)
    anchor_root = (tmp_path / "anchor-base" / "N.E.K.O").resolve()

    assert reloaded_manager.app_docs_dir == anchor_root
    assert reloaded_manager.anchor_root == anchor_root
    assert reloaded_manager.committed_selected_root == unavailable_selected_root.resolve()
    assert reloaded_manager.reported_current_root == unavailable_selected_root.resolve()
    assert reloaded_manager.recovery_committed_root_unavailable is True


@pytest.mark.unit
def test_get_config_manager_skips_runtime_file_migration_while_recovery_layout_is_active(
    tmp_path,
    monkeypatch,
):
    from utils.config_manager import ConfigManager, get_config_manager, reset_config_manager_cache

    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    initial_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        initial_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    reset_config_manager_cache()
    standard_root = tmp_path / "anchor-base"
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path / "runtime-parent"), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[standard_root],
    ):
        manager = get_config_manager("N.E.K.O")

    try:
        assert manager.recovery_committed_root_unavailable is True
        assert not (unavailable_selected_root / "config").exists()
        assert not (unavailable_selected_root / "memory").exists()
        manager.recovery_committed_root_unavailable = False
        with patch.object(manager, "migrate_config_files") as migrate_config, patch.object(
            manager,
            "migrate_memory_files",
        ) as migrate_memory:
            assert get_config_manager("N.E.K.O") is manager
        migrate_config.assert_called_once()
        migrate_memory.assert_called_once()
    finally:
        reset_config_manager_cache()
