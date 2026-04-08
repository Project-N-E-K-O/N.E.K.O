import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.file_utils import atomic_write_json


def _make_config_manager(tmp_path, platform: str | None = None):
    from utils.config_manager import ConfigManager

    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path),
    ]
    if platform is not None:
        patchers.append(patch("utils.config_manager.sys.platform", platform))

    with patchers[0]:
        if len(patchers) == 1:
            config_manager = ConfigManager("N.E.K.O")
            config_manager.get_legacy_app_root_candidates = lambda: []
            return config_manager
        with patchers[1]:
            config_manager = ConfigManager("N.E.K.O")
            config_manager.get_legacy_app_root_candidates = lambda: []
            return config_manager


def _write_runtime_state(cm, *, character_name="小满"):
    from utils.config_manager import set_reserved

    characters = cm.get_default_characters()
    characters["猫娘"] = {
        character_name: characters["猫娘"][next(iter(characters["猫娘"]))]
    }
    characters["当前猫娘"] = character_name
    set_reserved(characters["猫娘"][character_name], "touch_set", {"default": {"tap": "wave"}})
    set_reserved(characters["猫娘"][character_name], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"][character_name], "avatar", "asset_source", "steam_workshop")
    set_reserved(characters["猫娘"][character_name], "avatar", "asset_source_id", "123456")
    set_reserved(characters["猫娘"][character_name], "avatar", "live2d", "model_path", "example/example.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    prefs_path = Path(cm.get_config_path("user_preferences.json"))
    atomic_write_json(
        prefs_path,
        [
            {
                "model_path": "/user_live2d/example.model3.json",
                "position": {"x": 1, "y": 2, "z": 3},
                "scale": {"x": 1, "y": 1, "z": 1},
            },
            {
                "model_path": "__global_conversation__",
                "userLanguage": "zh-CN",
                "noiseReductionEnabled": True,
            },
        ],
        ensure_ascii=False,
        indent=2,
    )

    character_memory_dir = Path(cm.memory_dir) / character_name
    character_memory_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(character_memory_dir / "recent.json", [{"role": "user", "content": "你好"}], ensure_ascii=False, indent=2)
    atomic_write_json(character_memory_dir / "settings.json", {"mood": "calm"}, ensure_ascii=False, indent=2)
    atomic_write_json(character_memory_dir / "facts.json", [{"id": "fact-1", "content": "喜欢鱼"}], ensure_ascii=False, indent=2)
    atomic_write_json(character_memory_dir / "persona.json", {"traits": ["温柔"]}, ensure_ascii=False, indent=2)
    (character_memory_dir / "time_indexed.db").write_bytes(b"sqlite-placeholder")
    workshop_model_dir = Path(cm.workshop_dir) / "123456" / "example"
    workshop_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(workshop_model_dir / "example.model3.json", {"Version": 3}, ensure_ascii=False, indent=2)

    return characters


@pytest.mark.unit
def test_bootstrap_creates_manifest_and_legacy_state(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    result = bootstrap_local_cloudsave_environment(cm)

    manifest = result["manifest"]
    root_state = result["root_state"]
    cloud_state = result["cloudsave_local_state"]

    assert cm.cloudsave_manifest_path.is_file()
    assert manifest["client_id"] == cloud_state["client_id"]
    assert manifest["schema_version"] == 1
    assert root_state["current_root"] == str(cm.app_docs_dir)
    assert root_state["last_migration_result"] in {"no_legacy_root_found", "bootstrap_initialized"}


@pytest.mark.unit
def test_bootstrap_imports_legacy_root_after_seed_migration(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)
    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    legacy_config_dir = legacy_root / "config"
    legacy_memory_dir = legacy_root / "memory" / "旧角色"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_memory_dir.mkdir(parents=True, exist_ok=True)

    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_config_dir / "user_preferences.json", [{"model_path": "/legacy.model3.json", "scale": {"x": 2, "y": 2}}], ensure_ascii=False, indent=2)
    atomic_write_json(legacy_config_dir / "voice_storage.json", {"legacy_bucket": {"voice_a": {"name": "旧音色"}}}, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_config_dir / "workshop_config.json", {"default_workshop_folder": "/legacy/workshop"}, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_config_dir / "core_config.json", {"recent_memory_auto_review": False}, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_memory_dir / "recent.json", [{"role": "user", "content": "旧记忆"}], ensure_ascii=False, indent=2)
    (legacy_root / "live2d" / "legacy_model").mkdir(parents=True, exist_ok=True)
    atomic_write_json(legacy_root / "live2d" / "legacy_model" / "legacy_model.model3.json", {"Version": 3}, ensure_ascii=False, indent=2)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]

    # Simulate the real phase-0 startup order: ConfigManager seeds the new root first,
    # then bootstrap decides whether to import a historical runtime root.
    cm.migrate_config_files()
    cm.migrate_memory_files()

    assert (cm.config_dir / "characters.json").is_file()
    assert not cm.root_state_path.exists()
    assert cm.load_characters()["当前猫娘"] != "旧角色"

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is True
    assert result["legacy_import"]["source"] == str(legacy_root)
    assert result["legacy_import"]["result"] == "legacy_root_repaired_target"
    assert cm.load_characters()["当前猫娘"] == "旧角色"
    assert (Path(cm.memory_dir) / "旧角色" / "recent.json").is_file()
    assert Path(cm.get_config_path("user_preferences.json")).is_file()
    assert Path(cm.get_config_path("voice_storage.json")).is_file()
    assert Path(cm.get_config_path("workshop_config.json")).is_file()
    assert Path(cm.get_config_path("core_config.json")).is_file()
    assert (cm.live2d_dir / "legacy_model" / "legacy_model.model3.json").is_file()
    assert cm.root_state_path.is_file()


@pytest.mark.unit
def test_bootstrap_repairs_existing_seeded_install_with_backup_and_merged_preferences(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)

    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    legacy_config_dir = legacy_root / "config"
    legacy_memory_dir = legacy_root / "memory" / "旧角色"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_memory_dir.mkdir(parents=True, exist_ok=True)

    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_config_dir / "user_preferences.json", [{"model_path": "/legacy.model3.json", "position": {"x": 1, "y": 2}}], ensure_ascii=False, indent=2)
    atomic_write_json(legacy_config_dir / "voice_storage.json", {"legacy_bucket": {"voice_a": {"name": "旧音色"}}}, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_memory_dir / "recent.json", [{"role": "user", "content": "旧记忆"}], ensure_ascii=False, indent=2)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()
    cm.ensure_cloudsave_state_files()

    atomic_write_json(
        cm.config_dir / "user_preferences.json",
        [{"model_path": "/current.model3.json", "position": {"x": 9, "y": 9}}],
        ensure_ascii=False,
        indent=2,
    )
    pre_repair_characters = cm.load_characters()
    root_state = cm.load_root_state()
    root_state["last_migration_result"] = "launcher_phase0_bootstrap_ok"
    root_state["last_successful_boot_at"] = "2026-04-08T00:00:00Z"
    cm.save_root_state(root_state)

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is True
    assert result["legacy_import"]["result"] == "legacy_root_repaired_target"
    assert result["legacy_import"]["backup_path"]
    backup_path = Path(result["legacy_import"]["backup_path"])
    assert backup_path.is_dir()
    backup_characters = json.loads((backup_path / "config" / "characters.json").read_text(encoding="utf-8"))
    assert backup_characters["当前猫娘"] == pre_repair_characters["当前猫娘"]

    merged_characters = cm.load_characters()
    assert "旧角色" in merged_characters["猫娘"]

    merged_preferences = json.loads((cm.config_dir / "user_preferences.json").read_text(encoding="utf-8"))
    merged_model_paths = {entry.get("model_path") for entry in merged_preferences if isinstance(entry, dict)}
    assert {"/legacy.model3.json", "/current.model3.json"}.issubset(merged_model_paths)

    merged_voice_storage = json.loads((cm.config_dir / "voice_storage.json").read_text(encoding="utf-8"))
    assert "legacy_bucket" in merged_voice_storage


@pytest.mark.unit
def test_bootstrap_repairs_legacy_root_while_launcher_fence_is_active(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)

    from utils.cloudsave_runtime import ROOT_MODE_BOOTSTRAP_IMPORTING, bootstrap_local_cloudsave_environment, cloud_apply_fence

    legacy_config_dir = legacy_root / "config"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()

    with cloud_apply_fence(cm, mode=ROOT_MODE_BOOTSTRAP_IMPORTING, reason="launcher_phase0_bootstrap"):
        result = bootstrap_local_cloudsave_environment(cm)
        assert result["legacy_import"]["migrated"] is True
        assert result["root_state"]["mode"] == ROOT_MODE_BOOTSTRAP_IMPORTING

    assert cm.load_characters()["当前猫娘"] == "旧角色"


@pytest.mark.unit
def test_bootstrap_skips_legacy_repair_when_target_is_already_richer(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)

    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    legacy_config_dir = legacy_root / "config"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    _write_runtime_state(cm, character_name="当前角色")
    cm.ensure_cloudsave_state_files()

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is False
    assert result["legacy_import"]["result"] == "target_root_already_initialized"
    assert cm.load_characters()["当前猫娘"] == "当前角色"


@pytest.mark.unit
def test_bootstrap_does_not_reimport_same_legacy_root_after_local_deletion(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)

    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    legacy_config_dir = legacy_root / "config"
    legacy_memory_dir = legacy_root / "memory" / "旧角色"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_memory_dir.mkdir(parents=True, exist_ok=True)

    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_memory_dir / "recent.json", [{"role": "user", "content": "旧记忆"}], ensure_ascii=False, indent=2)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()

    first_result = bootstrap_local_cloudsave_environment(cm)
    assert first_result["legacy_import"]["migrated"] is True
    assert cm.load_characters()["当前猫娘"] == "旧角色"

    root_state = cm.load_root_state()
    root_state["last_successful_boot_at"] = "2026-04-08T00:00:00Z"
    cm.save_root_state(root_state)

    characters = cm.load_characters()
    characters["猫娘"] = {}
    characters["当前猫娘"] = ""
    cm.save_characters(characters, bypass_write_fence=True)

    second_result = bootstrap_local_cloudsave_environment(cm)

    assert second_result["legacy_import"]["migrated"] is False
    assert second_result["legacy_import"]["result"] == "target_root_already_initialized"
    assert cm.load_characters()["猫娘"] == {}
    assert cm.load_characters()["当前猫娘"] == ""


@pytest.mark.unit
def test_bootstrap_does_not_reimport_after_non_launcher_boot_success_marker(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)

    from utils.cloudsave_runtime import ROOT_MODE_NORMAL, bootstrap_local_cloudsave_environment, set_root_mode

    legacy_config_dir = legacy_root / "config"
    legacy_memory_dir = legacy_root / "memory" / "旧角色"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_memory_dir.mkdir(parents=True, exist_ok=True)

    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_memory_dir / "recent.json", [{"role": "user", "content": "旧记忆"}], ensure_ascii=False, indent=2)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()

    first_result = bootstrap_local_cloudsave_environment(cm)
    assert first_result["legacy_import"]["migrated"] is True

    set_root_mode(
        cm,
        ROOT_MODE_NORMAL,
        current_root=str(cm.app_docs_dir),
        last_known_good_root=str(cm.app_docs_dir),
        last_successful_boot_at="2026-04-08T00:00:00Z",
    )

    characters = cm.load_characters()
    characters["猫娘"] = {}
    characters["当前猫娘"] = ""
    cm.save_characters(characters, bypass_write_fence=True)

    second_result = bootstrap_local_cloudsave_environment(cm)

    assert second_result["legacy_import"]["migrated"] is False
    assert cm.load_characters()["猫娘"] == {}
    assert cm.load_characters()["当前猫娘"] == ""


@pytest.mark.unit
def test_legacy_repair_respects_local_tombstones_even_if_launcher_result_was_overwritten(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)

    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    legacy_config_dir = legacy_root / "config"
    legacy_memory_dir = legacy_root / "memory" / "旧角色"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_memory_dir.mkdir(parents=True, exist_ok=True)

    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)
    atomic_write_json(legacy_memory_dir / "recent.json", [{"role": "user", "content": "旧记忆"}], ensure_ascii=False, indent=2)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()
    cm.ensure_cloudsave_state_files()

    root_state = cm.load_root_state()
    root_state["last_migration_result"] = "launcher_phase0_bootstrap_ok"
    root_state["last_successful_boot_at"] = "2026-04-08T00:00:00Z"
    cm.save_root_state(root_state)

    tombstones = cm.load_character_tombstones_state()
    tombstones["tombstones"] = [
        {
            "character_name": "旧角色",
            "deleted_at": "2026-04-08T00:00:00Z",
            "sequence_number": 5,
        }
    ]
    cm.save_character_tombstones_state(tombstones)

    characters = cm.load_characters()
    characters["猫娘"] = {}
    characters["当前猫娘"] = ""
    cm.save_characters(characters, bypass_write_fence=True)

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is True
    assert "旧角色" not in cm.load_characters()["猫娘"]
    assert cm.load_characters()["当前猫娘"] == ""


@pytest.mark.unit
def test_runtime_root_summary_ignores_dotfiles_in_memory(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import _runtime_root_has_user_content, _runtime_root_summary

    (cm.memory_dir).mkdir(parents=True, exist_ok=True)
    (Path(cm.memory_dir) / ".DS_Store").write_text("macOS metadata", encoding="utf-8")
    (cm.memory_dir / ".gitkeep").write_text("", encoding="utf-8")

    summary = _runtime_root_summary(cm, Path(cm.app_docs_dir))

    assert summary["memory_character_names"] == set()
    assert summary["has_user_content"] is False
    assert _runtime_root_has_user_content(Path(cm.app_docs_dir)) is False


@pytest.mark.unit
def test_bootstrap_recovers_stale_blocking_mode(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import ROOT_MODE_BOOTSTRAP_IMPORTING, bootstrap_local_cloudsave_environment

    cm.ensure_cloudsave_state_files()
    root_state = cm.load_root_state()
    root_state["mode"] = ROOT_MODE_BOOTSTRAP_IMPORTING
    root_state["last_migration_result"] = "interrupted_import"
    cm.save_root_state(root_state)

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["root_state"]["mode"] == "normal"
    assert result["root_state"]["last_migration_result"] == f"recovered_stale_mode:{ROOT_MODE_BOOTSTRAP_IMPORTING}"


@pytest.mark.unit
def test_bootstrap_does_not_clear_active_fence_in_same_process(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import (
        ROOT_MODE_BOOTSTRAP_IMPORTING,
        bootstrap_local_cloudsave_environment,
        cloud_apply_fence,
    )

    with cloud_apply_fence(cm, mode=ROOT_MODE_BOOTSTRAP_IMPORTING, reason="test_active_fence"):
        result = bootstrap_local_cloudsave_environment(cm)
        assert result["root_state"]["mode"] == ROOT_MODE_BOOTSTRAP_IMPORTING


@pytest.mark.unit
def test_cloud_apply_fence_blocks_core_writes(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import MaintenanceModeError, cloud_apply_fence
    import utils.preferences as preferences

    _write_runtime_state(cm)

    with patch.object(preferences, "_config_manager", cm), patch.object(
        preferences,
        "PREFERENCES_FILE",
        str(cm.get_config_path("user_preferences.json")),
    ):
        with cloud_apply_fence(cm):
            with pytest.raises(MaintenanceModeError):
                cm.save_characters({"猫娘": {}, "主人": {}, "当前猫娘": ""})
            with pytest.raises(MaintenanceModeError):
                cm.save_json_config("core_config.json", {"recent_memory_auto_review": False})
            with pytest.raises(MaintenanceModeError):
                cm.save_workshop_config({"default_workshop_folder": "/tmp/workshop", "auto_create_folder": True})
            with pytest.raises(MaintenanceModeError):
                preferences.save_global_conversation_settings({"userLanguage": "en-US"})


@pytest.mark.unit
def test_cloud_apply_fence_releases_lock_when_mode_restore_fails(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils import cloudsave_runtime

    original_set_root_mode = cloudsave_runtime.set_root_mode
    call_count = 0

    def _flaky_set_root_mode(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("restore failed")
        return original_set_root_mode(*args, **kwargs)

    with patch.object(cloudsave_runtime, "set_root_mode", side_effect=_flaky_set_root_mode):
        with pytest.raises(RuntimeError, match="restore failed"):
            with cloudsave_runtime.cloud_apply_fence(cm):
                pass

    assert cloudsave_runtime.acquire_cloud_apply_lock(cm) is True
    cloudsave_runtime.release_cloud_apply_lock(cm)


@pytest.mark.unit
def test_local_cloudsave_round_trip_restores_runtime_truth(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot, import_local_cloudsave_snapshot

    expected_characters = _write_runtime_state(cm)

    export_result = export_local_cloudsave_snapshot(cm)
    assert export_result["manifest"]["sequence_number"] == 1
    assert (cm.cloudsave_dir / "profiles" / "characters.json").is_file()
    assert (cm.cloudsave_dir / "memory" / "小满" / "recent.json").is_file()
    assert (cm.cloudsave_dir / "bindings" / "小满.json").is_file()
    assert (cm.cloudsave_dir / "catalog" / "character_tombstones.json").is_file()

    binding_payload = json.loads((cm.cloudsave_dir / "bindings" / "小满.json").read_text(encoding="utf-8"))
    assert binding_payload["model_type"] == "live2d"
    assert binding_payload["asset_source"] == "steam_workshop"
    assert binding_payload["asset_source_id"] == "123456"
    assert binding_payload["asset_state"] == "ready"
    assert binding_payload["experience_overrides"]["touch_set"]["default"]["tap"] == "wave"

    catalog_payload = json.loads((cm.cloudsave_dir / "catalog" / "catgirls_index.json").read_text(encoding="utf-8"))
    assert catalog_payload["characters"][0]["character_name"] == "小满"
    assert catalog_payload["characters"][0]["entry_sequence_number"] == 1

    shutil_targets = [
        cm.get_config_path("characters.json"),
        cm.get_config_path("user_preferences.json"),
    ]
    for target in shutil_targets:
        path = Path(target)
        if path.exists():
            path.unlink()
    if Path(cm.memory_dir).exists():
        import shutil
        shutil.rmtree(cm.memory_dir)

    import_result = import_local_cloudsave_snapshot(cm)

    assert import_result["applied_character_count"] == 1
    assert cm.load_characters() == expected_characters

    with open(cm.get_config_path("user_preferences.json"), "r", encoding="utf-8") as file_obj:
        preferences = file_obj.read()
    assert "__global_conversation__" in preferences
    assert "noiseReductionEnabled" in preferences

    restored_recent = Path(cm.memory_dir) / "小满" / "recent.json"
    restored_db = Path(cm.memory_dir) / "小满" / "time_indexed.db"
    assert restored_recent.is_file()
    assert restored_db.read_bytes() == b"sqlite-placeholder"

    cloud_state = cm.load_cloudsave_local_state()
    assert cloud_state["next_sequence_number"] == 2
    assert cloud_state["last_applied_manifest_fingerprint"] == export_result["manifest"]["fingerprint"]
    assert cloud_state["last_successful_import_at"]


@pytest.mark.unit
def test_export_persists_local_tombstones_into_catalog_and_import_state(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot, import_local_cloudsave_snapshot

    _write_runtime_state(cm)
    cm.save_character_tombstones_state(
        {
            "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
            "tombstones": [
                {
                    "character_name": "已删除角色",
                    "deleted_at": "2026-04-08T00:00:00Z",
                    "sequence_number": 11,
                }
            ],
        }
    )

    export_local_cloudsave_snapshot(cm)

    tombstones_catalog = json.loads((cm.cloudsave_dir / "catalog" / "character_tombstones.json").read_text(encoding="utf-8"))
    assert tombstones_catalog["tombstones"][0]["character_name"] == "已删除角色"

    cm.save_character_tombstones_state({"version": 1, "tombstones": []})
    import_local_cloudsave_snapshot(cm)

    restored_tombstones = cm.load_character_tombstones_state()
    assert restored_tombstones["tombstones"][0]["character_name"] == "已删除角色"


@pytest.mark.unit
def test_export_rejects_casefold_name_conflicts(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot

    characters = cm.get_default_characters()
    template_character = next(iter(characters["猫娘"].values()))
    characters["猫娘"] = {
        "Alice": template_character,
        "alice": template_character,
    }
    characters["当前猫娘"] = "Alice"
    cm.save_characters(characters, bypass_write_fence=True)

    with pytest.raises(ValueError, match="character name audit failed"):
        export_local_cloudsave_snapshot(cm)


@pytest.mark.unit
def test_import_rolls_back_runtime_on_apply_failure(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils import cloudsave_runtime

    _write_runtime_state(cm, character_name="旧角色")
    cloudsave_runtime.export_local_cloudsave_snapshot(cm)

    original_characters = cm.load_characters()
    original_recent = (Path(cm.memory_dir) / "旧角色" / "recent.json").read_text(encoding="utf-8")

    original_atomic_copy = cloudsave_runtime._atomic_copy_file

    def _failing_atomic_copy(source_path, target_path):
        if str(target_path).endswith("user_preferences.json"):
            raise RuntimeError("boom")
        return original_atomic_copy(source_path, target_path)

    with patch.object(cloudsave_runtime, "_atomic_copy_file", side_effect=_failing_atomic_copy):
        with pytest.raises(RuntimeError):
            cloudsave_runtime.import_local_cloudsave_snapshot(cm)

    assert cm.load_characters() == original_characters
    assert (Path(cm.memory_dir) / "旧角色" / "recent.json").read_text(encoding="utf-8") == original_recent


@pytest.mark.unit
def test_standard_data_candidates_on_unix_platforms(tmp_path):
    from utils.config_manager import ConfigManager

    fake_home = tmp_path / "home"
    fake_home.mkdir()

    with patch("utils.config_manager.Path.home", return_value=fake_home), patch(
        "utils.config_manager.sys.platform",
        "darwin",
    ):
        cm = ConfigManager("N.E.K.O")
        assert cm._get_standard_data_directory_candidates()[0] == fake_home / "Library" / "Application Support"

    with patch("utils.config_manager.Path.home", return_value=fake_home), patch(
        "utils.config_manager.sys.platform",
        "linux",
    ), patch.dict("os.environ", {"XDG_DATA_HOME": str(fake_home / ".xdg-data")}, clear=False):
        cm = ConfigManager("N.E.K.O")
        candidates = cm._get_standard_data_directory_candidates()
        assert candidates[0] == fake_home / ".xdg-data"
        assert fake_home / ".local" / "share" in candidates
