import json
from unittest.mock import patch

import pytest
from utils.file_utils import atomic_write_json


def _make_config_manager(tmp_path):
    from utils.config_manager import ConfigManager

    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ):
        return ConfigManager("N.E.K.O")


@pytest.mark.unit
def test_cloudsave_paths_follow_app_dir(tmp_path):
    cm = _make_config_manager(tmp_path)

    assert cm.cloudsave_dir == tmp_path / "N.E.K.O" / "cloudsave"
    assert cm.cloudsave_manifest_path == cm.cloudsave_dir / "manifest.json"
    assert cm.cloudsave_staging_dir == tmp_path / "N.E.K.O" / ".cloudsave_staging"
    assert cm.cloudsave_backups_dir == tmp_path / "N.E.K.O" / "cloudsave_backups"
    assert cm.root_state_path == tmp_path / "N.E.K.O" / "state" / "root_state.json"
    assert cm.cloudsave_local_state_path == tmp_path / "N.E.K.O" / "state" / "cloudsave_local_state.json"
    assert cm.character_tombstones_state_path == tmp_path / "N.E.K.O" / "state" / "character_tombstones.json"


@pytest.mark.unit
def test_ensure_cloudsave_structure_creates_expected_directories(tmp_path):
    cm = _make_config_manager(tmp_path)

    assert cm.ensure_cloudsave_structure() is True

    expected_dirs = [
        cm.cloudsave_dir,
        cm.cloudsave_catalog_dir,
        cm.cloudsave_profiles_dir,
        cm.cloudsave_bindings_dir,
        cm.cloudsave_memory_dir,
        cm.cloudsave_overrides_dir,
        cm.cloudsave_meta_dir,
        cm.cloudsave_workshop_meta_dir,
        cm.cloudsave_staging_dir,
        cm.cloudsave_backups_dir,
    ]
    for directory in expected_dirs:
        assert directory.is_dir(), f"expected directory to exist: {directory}"


@pytest.mark.unit
def test_ensure_cloudsave_state_files_creates_defaults(tmp_path):
    cm = _make_config_manager(tmp_path)

    created = cm.ensure_cloudsave_state_files()

    assert created is True
    assert cm.root_state_path.is_file()
    assert cm.cloudsave_local_state_path.is_file()
    assert cm.character_tombstones_state_path.is_file()

    root_state = cm.load_root_state()
    cloud_state = cm.load_cloudsave_local_state()
    tombstone_state = cm.load_character_tombstones_state()

    assert root_state["version"] == cm.ROOT_STATE_VERSION
    assert root_state["current_root"] == str(cm.app_docs_dir)
    assert root_state["last_known_good_root"] == str(cm.app_docs_dir)
    assert cloud_state["version"] == cm.CLOUDSAVE_LOCAL_STATE_VERSION
    assert cloud_state["next_sequence_number"] == 1
    assert isinstance(cloud_state["client_id"], str) and cloud_state["client_id"]
    assert tombstone_state["version"] == cm.CHARACTER_TOMBSTONES_STATE_VERSION
    assert tombstone_state["tombstones"] == []


@pytest.mark.unit
def test_cloudsave_state_round_trip_preserves_data(tmp_path):
    cm = _make_config_manager(tmp_path)
    cm.ensure_cloudsave_state_files()

    original_cloud_state = cm.load_cloudsave_local_state()
    client_id = original_cloud_state["client_id"]

    root_state = cm.load_root_state()
    root_state["mode"] = "bootstrap_importing"
    root_state["last_successful_boot_at"] = "2026-04-08T00:00:00Z"
    cm.save_root_state(root_state)

    original_cloud_state["next_sequence_number"] = 7
    original_cloud_state["last_applied_manifest_fingerprint"] = "fp-test"
    cm.save_cloudsave_local_state(original_cloud_state)

    reloaded_root_state = cm.load_root_state()
    reloaded_cloud_state = cm.load_cloudsave_local_state()

    assert reloaded_root_state["mode"] == "bootstrap_importing"
    assert reloaded_root_state["last_successful_boot_at"] == "2026-04-08T00:00:00Z"
    assert reloaded_cloud_state["client_id"] == client_id
    assert reloaded_cloud_state["next_sequence_number"] == 7
    assert reloaded_cloud_state["last_applied_manifest_fingerprint"] == "fp-test"


@pytest.mark.unit
def test_ensure_cloudsave_state_files_raises_when_local_state_directory_init_fails(tmp_path):
    cm = _make_config_manager(tmp_path)

    with patch.object(cm, "ensure_local_state_directory", return_value=False):
        with pytest.raises(RuntimeError, match="root_state.json"):
            cm.ensure_cloudsave_state_files()


@pytest.mark.unit
def test_get_documents_directory_preserves_first_readable_legacy_candidate(tmp_path):
    from utils.config_manager import ConfigManager

    standard_dir = tmp_path / "standard"
    legacy_dir = tmp_path / "legacy"
    standard_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir.mkdir(parents=True, exist_ok=True)

    cm = object.__new__(ConfigManager)
    cm._log = lambda *_args, **_kwargs: None
    cm._get_standard_data_directory_candidates = lambda: [standard_dir]
    cm._get_legacy_storage_candidates = lambda: [legacy_dir]
    cm._dedupe_paths = lambda paths: list(dict.fromkeys(paths))

    chosen = ConfigManager._get_documents_directory(cm)

    assert chosen == standard_dir
    assert cm._first_readable_candidate == legacy_dir


@pytest.mark.unit
def test_persist_user_workshop_folder_retries_after_save_failure(tmp_path):
    cm = _make_config_manager(tmp_path)
    workshop_dir = tmp_path / "workshop"
    workshop_dir.mkdir(parents=True, exist_ok=True)

    save_side_effects = [OSError("disk full"), None]

    with patch.object(cm, "load_workshop_config", return_value={}), patch.object(
        cm,
        "save_workshop_config",
        side_effect=save_side_effects,
    ) as save_mock:
        cm.persist_user_workshop_folder(str(workshop_dir))
        assert cm._user_workshop_folder_persisted is False

        cm.persist_user_workshop_folder(str(workshop_dir))
        assert cm._user_workshop_folder_persisted is True

    assert save_mock.call_count == 2


@pytest.mark.unit
def test_load_workshop_config_does_not_delete_invalid_file_on_read(tmp_path):
    cm = _make_config_manager(tmp_path)
    config_path = cm.config_dir / "workshop_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        config_path,
        {"default_workshop_folder": str(tmp_path / "missing-workshop")},
        ensure_ascii=False,
        indent=2,
    )

    loaded = cm.load_workshop_config()

    assert loaded["default_workshop_folder"] == str(tmp_path / "missing-workshop")
    assert config_path.is_file()


@pytest.mark.unit
def test_save_characters_writes_runtime_root_even_when_project_fallback_exists(tmp_path):
    cm = _make_config_manager(tmp_path)

    project_characters_path = cm.project_config_dir / "characters.json"
    runtime_characters_path = cm.config_dir / "characters.json"
    project_characters_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(project_characters_path, cm.get_default_characters(), ensure_ascii=False, indent=2)
    assert not runtime_characters_path.exists()

    characters = cm.load_characters()
    template_name = next(iter(characters["猫娘"]))
    characters["猫娘"]["运行时角色"] = json.loads(json.dumps(characters["猫娘"][template_name], ensure_ascii=False))
    characters["当前猫娘"] = "运行时角色"
    cm.save_characters(characters, bypass_write_fence=True)

    assert runtime_characters_path.is_file()
    project_payload = json.loads(project_characters_path.read_text(encoding="utf-8"))
    runtime_payload = json.loads(runtime_characters_path.read_text(encoding="utf-8"))
    assert runtime_payload["当前猫娘"] == characters["当前猫娘"]
    assert project_payload["当前猫娘"] != characters["当前猫娘"]


@pytest.mark.unit
def test_save_json_config_writes_runtime_root_even_when_project_fallback_exists(tmp_path):
    cm = _make_config_manager(tmp_path)

    project_core_config_path = cm.project_config_dir / "core_config.json"
    runtime_core_config_path = cm.config_dir / "core_config.json"
    project_core_config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        project_core_config_path,
        {"recent_memory_auto_review": False, "coreApi": "legacy"},
        ensure_ascii=False,
        indent=2,
    )
    assert not runtime_core_config_path.exists()

    loaded = cm.load_json_config("core_config.json", default_value={})
    loaded["recent_memory_auto_review"] = True
    loaded["coreApi"] = "runtime"
    cm.save_json_config("core_config.json", loaded)

    assert runtime_core_config_path.is_file()
    assert json.loads(runtime_core_config_path.read_text(encoding="utf-8"))["coreApi"] == "runtime"
    assert json.loads(project_core_config_path.read_text(encoding="utf-8"))["coreApi"] == "legacy"


@pytest.mark.unit
def test_save_user_preferences_writes_runtime_root_even_when_project_fallback_exists(tmp_path):
    cm = _make_config_manager(tmp_path)

    project_preferences_path = cm.project_config_dir / "user_preferences.json"
    runtime_preferences_path = cm.config_dir / "user_preferences.json"
    project_preferences_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        project_preferences_path,
        [{"model_path": "/legacy.model3.json", "position": {"x": 1}, "scale": {"x": 1}}],
        ensure_ascii=False,
        indent=2,
    )
    assert not runtime_preferences_path.exists()

    with patch("utils.config_manager._config_manager", cm):
        from utils import preferences as preferences_module

        with patch.object(preferences_module, "_config_manager", cm):
            saved = preferences_module.save_user_preferences(
                [{"model_path": "/runtime.model3.json", "position": {"x": 2}, "scale": {"x": 2}}]
            )

    assert saved is True
    assert runtime_preferences_path.is_file()
    assert json.loads(runtime_preferences_path.read_text(encoding="utf-8"))[0]["model_path"] == "/runtime.model3.json"
    assert json.loads(project_preferences_path.read_text(encoding="utf-8"))[0]["model_path"] == "/legacy.model3.json"


@pytest.mark.unit
def test_load_root_state_reraises_corrupt_json_even_with_default_value(tmp_path):
    cm = _make_config_manager(tmp_path)
    cm.root_state_path.parent.mkdir(parents=True, exist_ok=True)
    cm.root_state_path.write_text("{not-valid-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        cm.load_root_state()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("save_call", "target_name"),
    (
        (lambda cm: cm.save_characters({"猫娘": {}, "主人": {}, "当前猫娘": ""}), "characters.json"),
        (lambda cm: cm.save_json_config("core_config.json", {"coreApi": "demo"}), "core_config.json"),
        (lambda cm: cm.save_workshop_config({"default_workshop_folder": "/tmp/workshop"}), "workshop_config.json"),
    ),
)
def test_config_save_entrypoints_check_write_fence_before_ensuring_config_dir(tmp_path, save_call, target_name):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import MaintenanceModeError

    maintenance_error = MaintenanceModeError("maintenance_readonly", operation="save", target=target_name)

    with patch("utils.cloudsave_runtime.assert_cloudsave_writable", side_effect=maintenance_error), patch.object(
        cm,
        "ensure_config_directory",
        side_effect=AssertionError("ensure_config_directory should not run before the write fence"),
    ):
        with pytest.raises(MaintenanceModeError):
            save_call(cm)


@pytest.mark.unit
def test_preferences_save_entrypoints_check_write_fence_before_ensuring_config_dir(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils import preferences as preferences_module
    from utils.cloudsave_runtime import MaintenanceModeError

    maintenance_error = MaintenanceModeError("maintenance_readonly", operation="save", target="user_preferences.json")

    with patch("utils.config_manager._config_manager", cm), patch.object(
        preferences_module,
        "_config_manager",
        cm,
    ), patch.object(
        preferences_module,
        "assert_cloudsave_writable",
        side_effect=maintenance_error,
    ), patch.object(
        cm,
        "ensure_config_directory",
        side_effect=AssertionError("ensure_config_directory should not run before the write fence"),
    ):
        with pytest.raises(MaintenanceModeError):
            preferences_module.save_user_preferences(
                [{"model_path": "/runtime.model3.json", "position": {"x": 2}, "scale": {"x": 2}}]
            )
        with pytest.raises(MaintenanceModeError):
            preferences_module.save_global_conversation_settings({"focusModeEnabled": True})
