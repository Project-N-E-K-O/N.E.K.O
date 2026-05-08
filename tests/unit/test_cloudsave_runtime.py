import copy
import contextlib
import json
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.file_utils import atomic_write_json


def _make_config_manager(
    tmp_path,
    platform: str | None = None,
    legacy_candidates: list[str] | None = None,
):
    from utils.config_manager import ConfigManager

    if legacy_candidates is None:
        legacy_candidates = []

    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path),
        patch.object(
            ConfigManager,
            "_get_standard_data_directory_candidates",
            return_value=[tmp_path],
        ),
        patch.object(
            ConfigManager,
            "get_legacy_app_root_candidates",
            return_value=list(legacy_candidates),
        ),
    ]
    if platform is not None:
        patchers.append(patch("utils.config_manager.sys.platform", platform))

    with contextlib.ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        config_manager = ConfigManager("N.E.K.O")

    config_manager.get_legacy_app_root_candidates = lambda: list(legacy_candidates)
    config_manager._get_standard_data_directory_candidates = lambda: [tmp_path]
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
def test_resolve_managed_target_path_rejects_traversal(tmp_path):
    from utils.cloudsave_runtime import _resolve_managed_target_path

    cm = _make_config_manager(tmp_path)

    with pytest.raises(ValueError):
        _resolve_managed_target_path(cm, "anchor/../../outside.txt")
    with pytest.raises(ValueError):
        _resolve_managed_target_path(cm, "/absolute/outside.txt")

    resolved = _resolve_managed_target_path(cm, "runtime/config/characters.json")
    assert resolved == (cm.app_docs_dir / "config" / "characters.json").resolve(strict=False)


@pytest.mark.unit
def test_managed_target_relative_path_prefers_nested_anchor_root(tmp_path):
    from utils.cloudsave_runtime import _managed_target_relative_path

    cm = _make_config_manager(tmp_path)
    cm.anchor_root = cm.app_docs_dir / "anchor" / "N.E.K.O"
    target_path = cm.anchor_root / "state" / "storage_policy.json"

    assert _managed_target_relative_path(cm, target_path) == Path("anchor/state/storage_policy.json")


def _add_runtime_character(cm, character_name: str, *, recent_text: str) -> None:
    from utils.config_manager import set_reserved

    characters = cm.load_characters()
    template_payload = copy.deepcopy(next(iter(characters["猫娘"].values())))
    template_payload["档案名"] = character_name
    set_reserved(template_payload, "avatar", "model_type", "live2d")
    set_reserved(template_payload, "avatar", "asset_source", "steam_workshop")
    set_reserved(template_payload, "avatar", "asset_source_id", "123456")
    set_reserved(template_payload, "avatar", "live2d", "model_path", "example/example.model3.json")
    characters["猫娘"][character_name] = template_payload
    cm.save_characters(characters, bypass_write_fence=True)

    character_memory_dir = Path(cm.memory_dir) / character_name
    character_memory_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        character_memory_dir / "recent.json",
        [{"role": "user", "content": recent_text}],
        ensure_ascii=False,
        indent=2,
    )


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
    atomic_write_json(
        legacy_config_dir / "workshop_config.json",
        {"default_workshop_folder": str(legacy_root / "workshop")},
        ensure_ascii=False,
        indent=2,
    )
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
    migrated_workshop_config = json.loads(Path(cm.get_config_path("workshop_config.json")).read_text(encoding="utf-8"))
    assert migrated_workshop_config["default_workshop_folder"] == str(cm.workshop_dir)
    assert Path(cm.get_config_path("core_config.json")).is_file()
    assert (cm.live2d_dir / "legacy_model" / "legacy_model.model3.json").is_file()
    assert cm.root_state_path.is_file()


@pytest.mark.unit
def test_bootstrap_preserves_staged_cloudsave_snapshot_before_legacy_runtime_import(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    snapshot_source_base = tmp_path / "snapshot_source"
    cm = _make_config_manager(new_root_base)
    snapshot_source_cm = _make_config_manager(snapshot_source_base)

    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment, export_local_cloudsave_snapshot

    legacy_config_dir = legacy_root / "config"
    legacy_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_characters = cm.get_default_characters()
    template_character = next(iter(legacy_characters["猫娘"].values()))
    legacy_characters["猫娘"] = {"旧角色": template_character}
    legacy_characters["当前猫娘"] = "旧角色"
    atomic_write_json(legacy_config_dir / "characters.json", legacy_characters, ensure_ascii=False, indent=2)

    bootstrap_local_cloudsave_environment(snapshot_source_cm)
    _write_runtime_state(snapshot_source_cm, character_name="云端角色")
    export_local_cloudsave_snapshot(snapshot_source_cm)

    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    shutil.copytree(snapshot_source_cm.cloudsave_dir, cm.cloudsave_dir, dirs_exist_ok=True)

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is False
    assert result["legacy_import"]["result"] == "target_root_preserves_staged_cloudsave_snapshot"
    assert json.loads(cm.cloudsave_manifest_path.read_text(encoding="utf-8")).get("files")
    assert cm.load_characters()["当前猫娘"] != "旧角色"


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
def test_bootstrap_skips_legacy_character_merge_when_target_has_non_seeded_user_content(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_source_base = tmp_path / "legacy_source_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)
    legacy_cm = _make_config_manager(legacy_source_base)

    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    _write_runtime_state(cm, character_name="当前角色")
    _write_runtime_state(legacy_cm, character_name="旧角色")
    _add_runtime_character(legacy_cm, "旧角色二", recent_text="更多旧记忆")

    shutil.copytree(legacy_cm.app_docs_dir, legacy_root, dirs_exist_ok=True)
    cm.get_legacy_app_root_candidates = lambda: [legacy_root]

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is False
    assert result["legacy_import"]["result"] == "target_root_already_initialized"
    characters = cm.load_characters()
    assert set(characters["猫娘"]) == {"当前角色"}
    assert characters["当前猫娘"] == "当前角色"


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
def test_bootstrap_preserves_deferred_init_mode(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import ROOT_MODE_DEFERRED_INIT, bootstrap_local_cloudsave_environment

    cm.ensure_cloudsave_state_files()
    root_state = cm.load_root_state()
    unavailable_root = tmp_path / "offline-selected" / "N.E.K.O"
    root_state["mode"] = ROOT_MODE_DEFERRED_INIT
    root_state["current_root"] = str(unavailable_root)
    root_state["last_known_good_root"] = str(unavailable_root)
    root_state["last_migration_result"] = f"selected_root_unavailable:{unavailable_root}"
    cm.save_root_state(root_state)

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["root_state"]["mode"] == ROOT_MODE_DEFERRED_INIT
    assert result["root_state"]["current_root"] == str(unavailable_root)
    assert result["root_state"]["last_known_good_root"] == str(unavailable_root)
    assert result["root_state"]["last_migration_result"] == f"selected_root_unavailable:{unavailable_root}"


@pytest.mark.unit
def test_bootstrap_preserves_restart_pending_maintenance_mode(tmp_path):
    cm = _make_config_manager(tmp_path)
    anchor_base = tmp_path / "anchor-base"
    anchor_base.mkdir(parents=True, exist_ok=True)
    cm._get_standard_data_directory_candidates = lambda: [anchor_base]

    from utils.cloudsave_runtime import (
        ROOT_MODE_MAINTENANCE_READONLY,
        bootstrap_local_cloudsave_environment,
        set_root_mode,
    )
    from utils.storage_migration import create_pending_storage_migration

    create_pending_storage_migration(
        cm,
        source_root=cm.app_docs_dir,
        target_root=tmp_path / "target-root" / "N.E.K.O",
        selection_source="custom",
    )
    set_root_mode(
        cm,
        ROOT_MODE_MAINTENANCE_READONLY,
        last_migration_source=str(cm.app_docs_dir),
        last_migration_result=f"restart_pending:{tmp_path / 'target-root' / 'N.E.K.O'}",
    )

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["root_state"]["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    assert result["root_state"]["last_migration_result"].startswith("restart_pending:")


@pytest.mark.unit
def test_write_blocking_recovery_fails_closed_when_migration_checkpoint_cannot_load(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils import cloudsave_runtime as cloudsave_runtime_module
    from utils.cloudsave_runtime import ROOT_MODE_MAINTENANCE_READONLY

    root_state = {
        "mode": ROOT_MODE_MAINTENANCE_READONLY,
        "last_migration_result": "restart_pending_missing_marker",
    }

    with patch("utils.storage_migration.load_storage_migration", side_effect=OSError("unreadable")):
        assert cloudsave_runtime_module._should_preserve_write_blocking_mode(cm, root_state) is True


@pytest.mark.unit
def test_should_write_root_mode_normal_after_startup_only_when_mode_is_normal():
    from utils.cloudsave_runtime import (
        ROOT_MODE_DEFERRED_INIT,
        ROOT_MODE_MAINTENANCE_READONLY,
        should_write_root_mode_normal_after_startup,
    )

    assert should_write_root_mode_normal_after_startup({"mode": "normal"}) is True
    assert should_write_root_mode_normal_after_startup({"mode": ROOT_MODE_DEFERRED_INIT}) is False
    assert should_write_root_mode_normal_after_startup({"mode": ROOT_MODE_MAINTENANCE_READONLY}) is False


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
def test_cloudsave_summary_marks_exported_character_as_matched(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import (
        build_cloudsave_character_detail,
        build_cloudsave_summary,
        export_local_cloudsave_snapshot,
    )

    _write_runtime_state(cm, character_name="小满")
    export_local_cloudsave_snapshot(cm)

    summary = build_cloudsave_summary(cm)

    assert summary["success"] is True
    assert summary["provider_available"] is True
    assert summary["current_character_name"] == "小满"
    assert len(summary["items"]) == 1
    assert summary["items"][0]["character_name"] == "小满"
    assert summary["items"][0]["relation_state"] == "matched"
    assert summary["items"][0]["available_actions"] == []

    detail = build_cloudsave_character_detail(cm, "小满")
    assert detail is not None
    assert detail["item"]["relation_state"] == "matched"


@pytest.mark.unit
def test_cloudsave_summary_marks_exported_character_as_matched_with_live_sqlite_memory_db(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit

    _write_runtime_state(cm, character_name="小满")
    db_path = Path(cm.memory_dir) / "小满" / "time_indexed.db"
    db_path.unlink(missing_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE memory_events (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO memory_events (content) VALUES (?)", ("first",))
        conn.commit()
        conn.execute("INSERT INTO memory_events (content) VALUES (?)", ("second",))
        conn.commit()

        export_cloudsave_character_unit(cm, "小满")
        summary = build_cloudsave_summary(cm)
    finally:
        conn.close()

    assert summary["items"][0]["character_name"] == "小满"
    assert summary["items"][0]["relation_state"] == "matched"


@pytest.mark.unit
def test_cloudsave_summary_returns_empty_items_without_characters(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary

    cm.save_characters({"猫娘": {}, "主人": {}, "当前猫娘": ""}, bypass_write_fence=True)
    summary = build_cloudsave_summary(cm)

    assert summary["success"] is True
    assert summary["provider_available"] is True
    assert summary["current_character_name"] == ""
    assert summary["items"] == []


@pytest.mark.unit
def test_cloudsave_summary_classifies_local_cloud_and_diverged_states(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import build_cloudsave_summary, export_local_cloudsave_snapshot

    _write_runtime_state(source_cm, character_name="共同角色")
    _add_runtime_character(source_cm, "云端独有", recent_text="cloud-only-memory")
    export_local_cloudsave_snapshot(source_cm)

    _write_runtime_state(target_cm, character_name="共同角色")
    common_recent_path = Path(target_cm.memory_dir) / "共同角色" / "recent.json"
    atomic_write_json(
        common_recent_path,
        [{"role": "user", "content": "target-diverged-memory"}],
        ensure_ascii=False,
        indent=2,
    )
    _add_runtime_character(target_cm, "本地独有", recent_text="local-only-memory")
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    summary = build_cloudsave_summary(target_cm)
    items_by_name = {item["character_name"]: item for item in summary["items"]}

    assert items_by_name["共同角色"]["relation_state"] == "diverged"
    assert items_by_name["共同角色"]["available_actions"] == ["upload", "download"]
    assert items_by_name["本地独有"]["relation_state"] == "local_only"
    assert items_by_name["本地独有"]["available_actions"] == ["upload"]
    assert items_by_name["云端独有"]["relation_state"] == "cloud_only"
    assert items_by_name["云端独有"]["available_actions"] == ["download"]


@pytest.mark.unit
def test_cloudsave_summary_merges_legacy_and_sharded_cloud_characters(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit

    _write_runtime_state(cm, character_name="新角色")
    legacy_payload = copy.deepcopy(cm.load_characters()["猫娘"]["新角色"])
    legacy_payload["档案名"] = "旧角色"
    atomic_write_json(
        cm.cloudsave_profiles_dir / "characters.json",
        {"猫娘": {"旧角色": legacy_payload}},
        ensure_ascii=False,
        indent=2,
    )

    export_cloudsave_character_unit(cm, "新角色")

    summary = build_cloudsave_summary(cm)
    items_by_name = {item["character_name"]: item for item in summary["items"]}

    assert items_by_name["旧角色"]["relation_state"] == "cloud_only"
    assert items_by_name["旧角色"]["cloud_exists"] is True
    assert items_by_name["新角色"]["relation_state"] == "matched"
    assert items_by_name["新角色"]["cloud_exists"] is True


@pytest.mark.unit
def test_cloudsave_summary_prefers_sharded_binding_payload_over_stale_legacy_binding(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit

    _write_runtime_state(cm, character_name="小满")
    export_cloudsave_character_unit(cm, "小满")

    stale_binding_path = cm.cloudsave_bindings_dir / "小满.json"
    stale_binding_payload = json.loads(stale_binding_path.read_text(encoding="utf-8"))
    stale_binding_payload["model_ref"] = "stale/stale.model3.json"
    stale_binding_payload["asset_source"] = "local_imported"
    stale_binding_payload["asset_source_id"] = ""
    atomic_write_json(stale_binding_path, stale_binding_payload, ensure_ascii=False, indent=2)

    summary = build_cloudsave_summary(cm)

    assert summary["items"][0]["character_name"] == "小满"
    assert summary["items"][0]["relation_state"] == "matched"


@pytest.mark.unit
def test_cloudsave_summary_prefers_sharded_memory_over_stale_legacy_memory(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit

    _write_runtime_state(cm, character_name="小满")
    export_cloudsave_character_unit(cm, "小满")

    stale_recent_path = cm.cloudsave_memory_dir / "小满" / "recent.json"
    atomic_write_json(
        stale_recent_path,
        [{"role": "user", "content": "stale-legacy-memory"}],
        ensure_ascii=False,
        indent=2,
    )

    summary = build_cloudsave_summary(cm)

    assert summary["items"][0]["character_name"] == "小满"
    assert summary["items"][0]["relation_state"] == "matched"


@pytest.mark.unit
def test_cloudsave_summary_uses_configured_workshop_root_for_local_asset_resolution(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary

    _write_runtime_state(cm, character_name="小满")

    default_workshop_model_dir = Path(cm.workshop_dir) / "123456" / "example"
    shutil.rmtree(default_workshop_model_dir.parent, ignore_errors=True)

    custom_workshop_root = tmp_path / "external_workshop_root"
    custom_workshop_model_dir = custom_workshop_root / "123456" / "example"
    custom_workshop_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        custom_workshop_model_dir / "example.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )
    cm.save_workshop_path(str(custom_workshop_root))

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["local_asset_state"] == "ready"
    assert item["warnings"] == []


@pytest.mark.unit
def test_cloudsave_summary_resolves_workshop_model_from_item_scan_when_stored_path_is_stale(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary
    from utils.config_manager import set_reserved

    _write_runtime_state(cm, character_name="Tian")

    characters = cm.load_characters()
    set_reserved(characters["猫娘"]["Tian"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["Tian"], "avatar", "asset_source", "steam_workshop")
    set_reserved(characters["猫娘"]["Tian"], "avatar", "asset_source_id", "123456")
    set_reserved(characters["猫娘"]["Tian"], "avatar", "live2d", "model_path", "legacy/legacy.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    legacy_item_root = Path(cm.workshop_dir) / "123456"
    shutil.rmtree(legacy_item_root, ignore_errors=True)
    actual_model_dir = legacy_item_root / "current-layout" / "tian"
    actual_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        actual_model_dir / "tian.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["character_name"] == "Tian"
    assert item["local_asset_state"] == "ready"
    assert item["warnings"] == []


@pytest.mark.unit
def test_cloudsave_summary_resolves_stale_local_live2d_filename_from_existing_folder(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary
    from utils.config_manager import set_reserved

    _write_runtime_state(cm, character_name="水水")

    characters = cm.load_characters()
    set_reserved(characters["猫娘"]["水水"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source", "local")
    set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source_id", "")
    set_reserved(characters["猫娘"]["水水"], "avatar", "live2d", "model_path", "yui-export/yui-export.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    actual_model_dir = Path(cm.live2d_dir) / "yui-export"
    actual_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        actual_model_dir / "0313YUI03.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["character_name"] == "水水"
    assert item["local_asset_state"] == "ready"
    assert item["local_asset_source"] == "local_imported"
    assert item["local_asset_source_id"] == ""
    assert item["warnings"] == []


@pytest.mark.unit
def test_cloudsave_summary_infers_workshop_source_from_resolved_workshop_file_when_metadata_is_stale(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary
    from utils.config_manager import set_reserved

    _write_runtime_state(cm, character_name="工坊角色")

    characters = cm.load_characters()
    set_reserved(characters["猫娘"]["工坊角色"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["工坊角色"], "avatar", "asset_source", "local")
    set_reserved(characters["猫娘"]["工坊角色"], "avatar", "asset_source_id", "")
    set_reserved(characters["猫娘"]["工坊角色"], "avatar", "live2d", "model_path", "Blue cat/Blue cat.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    workshop_model_dir = Path(cm.workshop_dir) / "3671939765" / "Blue cat"
    workshop_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        workshop_model_dir / "Blue cat 2.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["character_name"] == "工坊角色"
    assert item["local_asset_state"] == "ready"
    assert item["local_asset_source"] == "steam_workshop"
    assert item["local_asset_source_id"] == "3671939765"
    assert item["warnings"] == []


@pytest.mark.unit
def test_cloudsave_summary_preserves_explicit_workshop_role_origin_even_when_current_model_is_local(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary
    from utils.config_manager import set_reserved

    _write_runtime_state(cm, character_name="水水")

    characters = cm.load_characters()
    set_reserved(characters["猫娘"]["水水"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source", "local")
    set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source_id", "")
    set_reserved(characters["猫娘"]["水水"], "avatar", "live2d", "model_path", "猫娘-YUI-洛丽塔-导出03/0313YUI03.model3.json")
    set_reserved(characters["猫娘"]["水水"], "character_origin", "source", "steam_workshop")
    set_reserved(characters["猫娘"]["水水"], "character_origin", "source_id", "3671939765")
    set_reserved(characters["猫娘"]["水水"], "character_origin", "display_name", "Blue cat")
    set_reserved(
        characters["猫娘"]["水水"],
        "character_origin",
        "model_ref",
        "Blue cat/Blue cat.model3.json",
    )
    cm.save_characters(characters, bypass_write_fence=True)

    local_model_dir = Path(cm.live2d_dir) / "猫娘-YUI-洛丽塔-导出03"
    local_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        local_model_dir / "0313YUI03.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["character_name"] == "水水"
    assert item["local_asset_state"] == "ready"
    assert item["local_asset_source"] == "local_imported"
    assert item["local_asset_source_id"] == ""
    assert item["local_origin_source"] == "steam_workshop"
    assert item["local_origin_source_id"] == "3671939765"
    assert item["local_origin_display_name"] == "Blue cat"
    assert item["warnings"] == []


@pytest.mark.unit
def test_cloudsave_summary_backfills_workshop_role_origin_only_when_profile_payload_matches(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary
    from utils.config_manager import set_reserved

    characters = cm.get_default_characters()
    characters["猫娘"] = {
        "工坊旧角色": {
            "昵称": "海盐",
            "口头禅": "今天也要加油",
        }
    }
    characters["当前猫娘"] = "工坊旧角色"
    set_reserved(characters["猫娘"]["工坊旧角色"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["工坊旧角色"], "avatar", "asset_source", "local")
    set_reserved(characters["猫娘"]["工坊旧角色"], "avatar", "asset_source_id", "")
    set_reserved(characters["猫娘"]["工坊旧角色"], "avatar", "live2d", "model_path", "manual/manual.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    local_model_dir = Path(cm.live2d_dir) / "manual"
    local_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        local_model_dir / "manual.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )

    workshop_item_root = Path(cm.workshop_dir) / "3671939765"
    workshop_item_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        workshop_item_root / "工坊旧角色.chara.json",
        {
            "档案名": "工坊旧角色",
            "昵称": "海盐",
            "口头禅": "今天也要加油",
            "model_type": "live2d",
            "live2d": "Blue cat",
        },
        ensure_ascii=False,
        indent=2,
    )

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["local_asset_source"] == "local_imported"
    assert item["local_origin_source"] == "steam_workshop"
    assert item["local_origin_source_id"] == "3671939765"
    assert item["local_origin_display_name"] == "Blue cat"


@pytest.mark.unit
def test_cloudsave_summary_does_not_backfill_workshop_role_origin_from_name_only_match(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary
    from utils.config_manager import set_reserved

    _write_runtime_state(cm, character_name="水水")

    characters = cm.load_characters()
    characters["猫娘"]["水水"]["昵称"] = "本地创建"
    set_reserved(characters["猫娘"]["水水"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source", "local")
    set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source_id", "")
    set_reserved(characters["猫娘"]["水水"], "avatar", "live2d", "model_path", "猫娘-YUI-洛丽塔-导出03/猫娘-YUI-洛丽塔-导出03.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    local_model_dir = Path(cm.live2d_dir) / "猫娘-YUI-洛丽塔-导出03"
    local_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        local_model_dir / "0313YUI03.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )

    workshop_item_root = Path(cm.workshop_dir) / "3671939765"
    workshop_item_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        workshop_item_root / "水水.chara.json",
        {
            "档案名": "水水",
            "昵称": "来自工坊",
            "model_type": "live2d",
            "live2d": "Blue cat",
        },
        ensure_ascii=False,
        indent=2,
    )

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["local_asset_state"] == "ready"
    assert item["local_asset_source"] == "local_imported"
    assert item["local_origin_source"] == ""
    assert item["local_origin_source_id"] == ""
    assert item["local_origin_display_name"] == ""


@pytest.mark.unit
def test_cloudsave_summary_does_not_treat_workshop_model_binding_as_workshop_role_origin(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary

    _write_runtime_state(cm, character_name="普通角色")

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]

    assert item["local_asset_state"] == "ready"
    assert item["local_asset_source"] == "steam_workshop"
    assert item["local_asset_source_id"] == "123456"
    assert item["local_origin_source"] == ""
    assert item["local_origin_source_id"] == ""


@pytest.mark.unit
def test_cloudsave_summary_keeps_missing_relative_local_model_as_local_imported(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit
    from utils.config_manager import set_reserved

    _write_runtime_state(cm, character_name="缺资源本地导入")

    characters = cm.load_characters()
    set_reserved(characters["猫娘"]["缺资源本地导入"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["缺资源本地导入"], "avatar", "asset_source", "local")
    set_reserved(characters["猫娘"]["缺资源本地导入"], "avatar", "asset_source_id", "")
    set_reserved(
        characters["猫娘"]["缺资源本地导入"],
        "avatar",
        "live2d",
        "model_path",
        "missing-local/missing-local.model3.json",
    )
    cm.save_characters(characters, bypass_write_fence=True)

    summary = build_cloudsave_summary(cm)
    item = summary["items"][0]
    assert item["local_asset_source"] == "local_imported"
    assert item["local_asset_state"] == "import_required"
    assert item["warnings"] == ["local_resource_missing_on_this_device"]

    export_cloudsave_character_unit(cm, "缺资源本地导入")
    binding_payload = json.loads(
        (cm.cloudsave_dir / "characters" / "缺资源本地导入" / "binding.json").read_text(encoding="utf-8")
    )
    assert binding_payload["asset_source"] == "local_imported"
    assert binding_payload["asset_state"] == "import_required"


@pytest.mark.unit
def test_cloudsave_summary_prefers_local_warnings_for_existing_character(tmp_path):
    from utils import cloudsave_runtime

    local_summary = {
        "character_name": "Tian",
        "display_name": "Tian",
        "model_type": "live2d",
        "asset_source": "steam_workshop",
        "asset_source_id": "123456",
        "asset_state": "ready",
        "updated_at_utc": "2026-04-09T00:00:00Z",
        "fingerprint": "sha256:local",
        "warnings": [],
    }
    cloud_summary = {
        "character_name": "Tian",
        "display_name": "Tian",
        "model_type": "live2d",
        "asset_source": "steam_workshop",
        "asset_source_id": "123456",
        "asset_state": "downloadable",
        "updated_at_utc": "2026-04-09T00:00:00Z",
        "fingerprint": "sha256:cloud",
        "warnings": ["cloud_resource_may_be_missing_after_download"],
    }

    item = cloudsave_runtime._merge_character_summary_item(
        character_name="Tian",
        local_summary=local_summary,
        cloud_summary=cloud_summary,
    )

    assert item["relation_state"] == "diverged"
    assert item["warnings"] == []


@pytest.mark.unit
def test_cloudsave_summary_keeps_cloud_warning_for_cloud_only_character(tmp_path):
    from utils import cloudsave_runtime

    cloud_summary = {
        "character_name": "云端角色",
        "display_name": "云端角色",
        "model_type": "live2d",
        "asset_source": "steam_workshop",
        "asset_source_id": "123456",
        "asset_state": "downloadable",
        "updated_at_utc": "2026-04-09T00:00:00Z",
        "fingerprint": "sha256:cloud",
        "warnings": ["cloud_resource_may_be_missing_after_download"],
    }

    item = cloudsave_runtime._merge_character_summary_item(
        character_name="云端角色",
        local_summary=None,
        cloud_summary=cloud_summary,
    )

    assert item["relation_state"] == "cloud_only"
    assert item["warnings"] == ["cloud_resource_may_be_missing_after_download"]


@pytest.mark.unit
def test_cloudsave_summary_keeps_local_warning_for_local_existing_character(tmp_path):
    from utils import cloudsave_runtime

    local_summary = {
        "character_name": "本地角色",
        "display_name": "本地角色",
        "model_type": "live2d",
        "asset_source": "local_imported",
        "asset_source_id": "",
        "asset_state": "import_required",
        "updated_at_utc": "2026-04-09T00:00:00Z",
        "fingerprint": "sha256:local",
        "warnings": ["local_resource_missing_on_this_device"],
    }

    item = cloudsave_runtime._merge_character_summary_item(
        character_name="本地角色",
        local_summary=local_summary,
        cloud_summary=None,
    )

    assert item["relation_state"] == "local_only"
    assert item["warnings"] == ["local_resource_missing_on_this_device"]


@pytest.mark.unit
def test_cloudsave_summary_preserves_separate_local_and_cloud_asset_sources(tmp_path):
    from utils import cloudsave_runtime

    local_summary = {
        "character_name": "共享角色",
        "display_name": "共享角色",
        "model_type": "live2d",
        "asset_source": "local_imported",
        "asset_source_id": "",
        "asset_state": "ready",
        "updated_at_utc": "2026-04-09T00:00:00Z",
        "fingerprint": "sha256:local",
        "warnings": [],
    }
    cloud_summary = {
        "character_name": "共享角色",
        "display_name": "共享角色",
        "model_type": "live2d",
        "asset_source": "steam_workshop",
        "asset_source_id": "123456",
        "asset_state": "downloadable",
        "updated_at_utc": "2026-04-09T01:00:00Z",
        "fingerprint": "sha256:cloud",
        "warnings": ["cloud_resource_may_be_missing_after_download"],
    }

    item = cloudsave_runtime._merge_character_summary_item(
        character_name="共享角色",
        local_summary=local_summary,
        cloud_summary=cloud_summary,
    )

    assert item["asset_source"] == "local_imported"
    assert item["local_asset_source"] == "local_imported"
    assert item["local_asset_source_id"] == ""
    assert item["cloud_asset_source"] == "steam_workshop"
    assert item["cloud_asset_source_id"] == "123456"


@pytest.mark.unit
def test_cloudsave_summary_uses_single_character_meta_updated_at(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit

    _write_runtime_state(cm, character_name="角色A")
    _add_runtime_character(cm, "角色B", recent_text="b-memory")

    export_cloudsave_character_unit(cm, "角色A")
    export_cloudsave_character_unit(cm, "角色B")

    expected_times = {
        "角色A": "2026-04-08T10:00:00Z",
        "角色B": "2026-04-09T11:30:00Z",
    }
    for character_name, updated_at in expected_times.items():
        meta_path = cm.cloudsave_dir / "characters" / character_name / "meta.json"
        meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        meta_payload["updated_at_utc"] = updated_at
        atomic_write_json(meta_path, meta_payload, ensure_ascii=False, indent=2)

    summary = build_cloudsave_summary(cm)
    items_by_name = {item["character_name"]: item for item in summary["items"]}

    assert items_by_name["角色A"]["cloud_updated_at_utc"] == expected_times["角色A"]
    assert items_by_name["角色B"]["cloud_updated_at_utc"] == expected_times["角色B"]


@pytest.mark.unit
def test_cloudsave_summary_hides_cloud_entries_when_provider_is_unavailable(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary, export_local_cloudsave_snapshot

    _write_runtime_state(cm, character_name="小满")
    export_local_cloudsave_snapshot(cm)
    cm.cloudsave_provider_available = False

    summary = build_cloudsave_summary(cm)

    assert summary["provider_available"] is False
    assert len(summary["items"]) == 1
    assert summary["items"][0]["character_name"] == "小满"
    assert summary["items"][0]["relation_state"] == "local_only"
    assert summary["items"][0]["cloud_exists"] is False


@pytest.mark.unit
def test_export_snapshot_emits_single_character_shards_and_meta(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot

    _write_runtime_state(cm, character_name="小满")
    export_local_cloudsave_snapshot(cm)

    assert (cm.cloudsave_dir / "characters" / "小满" / "profile.json").is_file()
    assert (cm.cloudsave_dir / "characters" / "小满" / "binding.json").is_file()
    assert (cm.cloudsave_dir / "characters" / "小满" / "memory" / "recent.json").is_file()
    meta_payload = json.loads((cm.cloudsave_dir / "characters" / "小满" / "meta.json").read_text(encoding="utf-8"))
    assert meta_payload["character_name"] == "小满"
    assert meta_payload["payload_fingerprint"].startswith("sha256:")


@pytest.mark.unit
def test_export_cloudsave_character_unit_updates_only_single_character_scope(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import export_cloudsave_character_unit

    _write_runtime_state(cm, character_name="小满")

    result = export_cloudsave_character_unit(cm, "小满")

    assert result["character_name"] == "小满"
    assert result["detail"]["item"]["relation_state"] == "matched"
    assert (cm.cloudsave_dir / "profiles" / "characters.json").is_file()
    assert (cm.cloudsave_dir / "bindings" / "小满.json").is_file()
    assert (cm.cloudsave_dir / "memory" / "小满" / "recent.json").is_file()
    assert (cm.cloudsave_dir / "characters" / "小满" / "meta.json").is_file()
    assert not (cm.cloudsave_dir / "profiles" / "conversation_settings.json").exists()
    assert not (cm.cloudsave_dir / "catalog" / "current_character.json").exists()

    manifest_payload = json.loads(cm.cloudsave_manifest_path.read_text(encoding="utf-8"))
    assert "characters/小满/profile.json" in manifest_payload["files"]
    assert "profiles/characters.json" in manifest_payload["files"]


@pytest.mark.unit
def test_local_cloudsave_snapshot_roundtrip_supports_embedded_dot_character_names(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot, import_local_cloudsave_snapshot

    _write_runtime_state(source_cm, character_name="N.E.K.O")

    export_local_cloudsave_snapshot(source_cm)

    assert (source_cm.cloudsave_dir / "characters" / "N.E.K.O" / "profile.json").is_file()
    assert (source_cm.cloudsave_dir / "bindings" / "N.E.K.O.json").is_file()

    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    import_local_cloudsave_snapshot(target_cm)

    imported_characters = target_cm.load_characters()
    assert "N.E.K.O" in (imported_characters.get("猫娘") or {})
    assert (Path(target_cm.memory_dir) / "N.E.K.O" / "recent.json").is_file()


@pytest.mark.unit
def test_single_character_cloudsave_operations_support_embedded_dot_names(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import export_cloudsave_character_unit, import_cloudsave_character_unit

    _write_runtime_state(source_cm, character_name="N.E.K.O")

    export_result = export_cloudsave_character_unit(source_cm, "N.E.K.O")

    assert export_result["character_name"] == "N.E.K.O"
    assert (source_cm.cloudsave_dir / "bindings" / "N.E.K.O.json").is_file()
    assert (source_cm.cloudsave_dir / "characters" / "N.E.K.O" / "meta.json").is_file()

    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    import_result = import_cloudsave_character_unit(target_cm, "N.E.K.O")

    assert import_result["character_name"] == "N.E.K.O"
    assert "N.E.K.O" in (target_cm.load_characters().get("猫娘") or {})
    assert (Path(target_cm.memory_dir) / "N.E.K.O" / "recent.json").is_file()


@pytest.mark.unit
def test_single_character_upload_rebuilds_legacy_mirrors_from_sharded_cloud_union(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import export_cloudsave_character_unit, import_local_cloudsave_snapshot

    _write_runtime_state(source_cm, character_name="角色A")
    _add_runtime_character(source_cm, "角色B", recent_text="b-memory")

    export_cloudsave_character_unit(source_cm, "角色A")
    export_cloudsave_character_unit(source_cm, "角色B")

    role_a_profile = json.loads(
        (source_cm.cloudsave_dir / "characters" / "角色A" / "profile.json").read_text(encoding="utf-8")
    )
    atomic_write_json(
        source_cm.cloudsave_profiles_dir / "characters.json",
        {"猫娘": {"角色A": role_a_profile}},
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_json(
        source_cm.cloudsave_catalog_dir / "catgirls_index.json",
        {
            "schema_version": 1,
            "sequence_number": 1,
            "exported_at_utc": "2026-04-09T00:00:00Z",
            "characters": [{"character_name": "角色A"}],
        },
        ensure_ascii=False,
        indent=2,
    )

    export_cloudsave_character_unit(source_cm, "角色A", overwrite=True)

    repaired_profiles = json.loads((source_cm.cloudsave_profiles_dir / "characters.json").read_text(encoding="utf-8"))
    repaired_catalog = json.loads((source_cm.cloudsave_catalog_dir / "catgirls_index.json").read_text(encoding="utf-8"))
    assert set((repaired_profiles.get("猫娘") or {}).keys()) == {"角色A", "角色B"}
    assert {entry.get("character_name") for entry in repaired_catalog.get("characters") or []} == {"角色A", "角色B"}

    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    result = import_local_cloudsave_snapshot(target_cm)

    assert result["applied_character_count"] == 2
    assert set((target_cm.load_characters().get("猫娘") or {}).keys()) == {"角色A", "角色B"}


@pytest.mark.unit
def test_load_cloudsave_character_unit_respects_tombstones_for_sharded_characters(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import _load_cloudsave_character_unit, export_cloudsave_character_unit

    _write_runtime_state(cm, character_name="小满")
    export_cloudsave_character_unit(cm, "小满")
    atomic_write_json(
        cm.cloudsave_catalog_dir / "character_tombstones.json",
        {
            "schema_version": 1,
            "sequence_number": 3,
            "exported_at_utc": "2026-04-09T00:00:00Z",
            "tombstones": [
                {
                    "character_name": "小满",
                    "deleted_at": "2026-04-09T00:00:00Z",
                    "sequence_number": 3,
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

    assert _load_cloudsave_character_unit(cm, "小满") is None


@pytest.mark.unit
def test_import_cloudsave_character_unit_restores_only_target_character_and_preserves_globals(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import export_cloudsave_character_unit, import_cloudsave_character_unit

    _write_runtime_state(source_cm, character_name="云端角色")
    export_cloudsave_character_unit(source_cm, "云端角色")

    _write_runtime_state(target_cm, character_name="本地角色")
    target_characters = target_cm.load_characters()
    target_characters["当前猫娘"] = "本地角色"
    target_cm.save_characters(target_characters, bypass_write_fence=True)
    target_cm.save_character_tombstones_state(
        {
            "version": target_cm.CHARACTER_TOMBSTONES_STATE_VERSION,
            "tombstones": [
                {
                    "character_name": "云端角色",
                    "deleted_at": "2026-04-08T00:00:00Z",
                    "sequence_number": 7,
                }
            ],
        }
    )
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    import_result = import_cloudsave_character_unit(target_cm, "云端角色")

    assert import_result["character_name"] == "云端角色"
    imported_characters = target_cm.load_characters()
    assert "本地角色" in imported_characters["猫娘"]
    assert "云端角色" in imported_characters["猫娘"]
    assert imported_characters["当前猫娘"] == "本地角色"
    assert (Path(target_cm.memory_dir) / "云端角色" / "recent.json").is_file()
    restored_tombstones = target_cm.load_character_tombstones_state()
    assert restored_tombstones["tombstones"] == []


@pytest.mark.unit
def test_single_character_cloudsave_operations_preserve_reflections_archive(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import export_cloudsave_character_unit, import_cloudsave_character_unit

    _write_runtime_state(source_cm, character_name="云端角色")
    archive_payload = [{"id": "reflection-1", "text": "历史观察"}]
    atomic_write_json(
        Path(source_cm.memory_dir) / "云端角色" / "reflections_archive.json",
        archive_payload,
        ensure_ascii=False,
        indent=2,
    )

    export_cloudsave_character_unit(source_cm, "云端角色")
    assert (source_cm.cloudsave_dir / "characters" / "云端角色" / "memory" / "reflections_archive.json").is_file()

    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)
    import_cloudsave_character_unit(target_cm, "云端角色")

    restored_archive = json.loads(
        (Path(target_cm.memory_dir) / "云端角色" / "reflections_archive.json").read_text(encoding="utf-8")
    )
    assert restored_archive == archive_payload


@pytest.mark.unit
def test_single_character_cloudsave_operations_raise_conflict_errors(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import CloudsaveOperationError, export_cloudsave_character_unit, import_cloudsave_character_unit

    _write_runtime_state(source_cm, character_name="小满")
    export_cloudsave_character_unit(source_cm, "小满")
    with pytest.raises(CloudsaveOperationError, match="cloud character already exists") as upload_exc:
        export_cloudsave_character_unit(source_cm, "小满", overwrite=False)
    assert upload_exc.value.code == "CLOUD_CHARACTER_EXISTS"

    _write_runtime_state(target_cm, character_name="小满")
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)
    with pytest.raises(CloudsaveOperationError, match="local character already exists") as download_exc:
        import_cloudsave_character_unit(target_cm, "小满", overwrite=False)
    assert download_exc.value.code == "LOCAL_CHARACTER_EXISTS"


@pytest.mark.unit
def test_import_cloudsave_character_unit_rolls_back_on_apply_failure(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils import cloudsave_runtime

    _write_runtime_state(source_cm, character_name="云端角色")
    cloudsave_runtime.export_cloudsave_character_unit(source_cm, "云端角色")

    _write_runtime_state(target_cm, character_name="本地角色")
    original_characters = target_cm.load_characters()
    original_recent = (Path(target_cm.memory_dir) / "本地角色" / "recent.json").read_text(encoding="utf-8")
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    original_apply_runtime_file = cloudsave_runtime._apply_runtime_file

    def _failing_apply_runtime_file(source_path, target_path):
        if str(target_path).endswith("character_tombstones.json"):
            raise RuntimeError("single import apply failed")
        return original_apply_runtime_file(source_path, target_path)

    with patch.object(cloudsave_runtime, "_apply_runtime_file", side_effect=_failing_apply_runtime_file):
        with pytest.raises(RuntimeError, match="single import apply failed"):
            cloudsave_runtime.import_cloudsave_character_unit(target_cm, "云端角色")

    assert target_cm.load_characters() == original_characters
    assert (Path(target_cm.memory_dir) / "本地角色" / "recent.json").read_text(encoding="utf-8") == original_recent
    assert not (Path(target_cm.memory_dir) / "云端角色").exists()


@pytest.mark.unit
def test_restore_cloudsave_operation_backup_restores_previous_character_state(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")

    from utils.cloudsave_runtime import (
        export_cloudsave_character_unit,
        import_cloudsave_character_unit,
        restore_cloudsave_operation_backup,
    )

    _write_runtime_state(source_cm, character_name="小满")
    source_characters = source_cm.load_characters()
    source_characters["猫娘"]["小满"]["喜欢的食物"] = "鱼干"
    source_cm.save_characters(source_characters, bypass_write_fence=True)
    atomic_write_json(
        Path(source_cm.memory_dir) / "小满" / "recent.json",
        [{"role": "assistant", "content": "来自云端"}],
        ensure_ascii=False,
        indent=2,
    )
    export_cloudsave_character_unit(source_cm, "小满")

    _write_runtime_state(target_cm, character_name="小满")
    target_characters = target_cm.load_characters()
    target_characters["猫娘"]["小满"]["喜欢的食物"] = "罐头"
    target_cm.save_characters(target_characters, bypass_write_fence=True)
    original_characters = target_cm.load_characters()
    atomic_write_json(
        Path(target_cm.memory_dir) / "小满" / "recent.json",
        [{"role": "assistant", "content": "来自本地"}],
        ensure_ascii=False,
        indent=2,
    )
    original_recent = (Path(target_cm.memory_dir) / "小满" / "recent.json").read_text(encoding="utf-8")
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    import_result = import_cloudsave_character_unit(target_cm, "小满", overwrite=True)

    assert target_cm.load_characters()["猫娘"]["小满"]["喜欢的食物"] == "鱼干"
    assert (Path(target_cm.memory_dir) / "小满" / "recent.json").read_text(encoding="utf-8") != original_recent

    restore_cloudsave_operation_backup(target_cm, import_result["backup_path"])

    assert target_cm.load_characters() == original_characters
    assert (Path(target_cm.memory_dir) / "小满" / "recent.json").read_text(encoding="utf-8") == original_recent


@pytest.mark.unit
def test_export_creates_valid_sqlite_shadow_copy_for_time_indexed_db(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot

    _write_runtime_state(cm)

    runtime_db_path = Path(cm.memory_dir) / "小满" / "time_indexed.db"
    runtime_db_path.unlink()

    with sqlite3.connect(str(runtime_db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO entries(content) VALUES (?)", ("来自 WAL 的长期记忆",))
        conn.commit()

        assert Path(f"{runtime_db_path}-wal").exists()
        export_local_cloudsave_snapshot(cm)

    exported_db_path = cm.cloudsave_dir / "memory" / "小满" / "time_indexed.db"
    with sqlite3.connect(str(exported_db_path)) as conn:
        row = conn.execute("SELECT content FROM entries").fetchone()
        quick_check = conn.execute("PRAGMA quick_check").fetchone()

    assert row == ("来自 WAL 的长期记忆",)
    assert quick_check == ("ok",)


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
def test_cross_device_import_overwrites_existing_runtime_without_duplicates_or_partial_loss(tmp_path):
    source_cm = _make_config_manager(tmp_path / "device_a")
    target_cm = _make_config_manager(tmp_path / "device_b")

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot, import_local_cloudsave_snapshot

    character_name = "\u5c0f\u6ee1"
    extra_name = "\u672c\u5730\u591a\u4f59\u89d2\u8272"

    _write_runtime_state(source_cm, character_name=character_name)
    source_memory_dir = Path(source_cm.memory_dir) / character_name
    atomic_write_json(
        source_memory_dir / "recent.json",
        [{"role": "user", "content": "from-device-a"}],
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_json(
        source_memory_dir / "facts.json",
        [{"id": "fact-a", "content": "source-fact"}],
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_json(
        source_memory_dir / "persona.json",
        {"traits": ["source-persona"]},
        ensure_ascii=False,
        indent=2,
    )
    source_settings_path = source_memory_dir / "settings.json"
    if source_settings_path.exists():
        source_settings_path.unlink()
    source_db_path = source_memory_dir / "time_indexed.db"
    source_db_path.unlink()
    with sqlite3.connect(str(source_db_path)) as conn:
        conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO entries(content) VALUES (?)", ("source-db-entry",))
        conn.commit()

    export_local_cloudsave_snapshot(source_cm)

    _write_runtime_state(target_cm, character_name=character_name)
    target_memory_dir = Path(target_cm.memory_dir) / character_name
    atomic_write_json(
        target_memory_dir / "recent.json",
        [{"role": "user", "content": "from-device-b"}],
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_json(
        target_memory_dir / "settings.json",
        {"mood": "stale-target-state"},
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_json(
        target_memory_dir / "facts.json",
        [{"id": "fact-b", "content": "target-fact"}],
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_json(
        target_memory_dir / "persona.json",
        {"traits": ["target-persona"]},
        ensure_ascii=False,
        indent=2,
    )
    target_db_path = target_memory_dir / "time_indexed.db"
    target_db_path.unlink()
    with sqlite3.connect(str(target_db_path)) as conn:
        conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO entries(content) VALUES (?)", ("target-db-entry",))
        conn.commit()

    target_characters = target_cm.load_characters()
    template_character = copy.deepcopy(next(iter(target_characters["\u732b\u5a18"].values())))
    target_characters["\u732b\u5a18"][extra_name] = template_character
    target_characters["\u5f53\u524d\u732b\u5a18"] = extra_name
    target_cm.save_characters(target_characters, bypass_write_fence=True)
    extra_memory_dir = Path(target_cm.memory_dir) / extra_name
    extra_memory_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        extra_memory_dir / "recent.json",
        [{"role": "user", "content": "extra-local-character"}],
        ensure_ascii=False,
        indent=2,
    )

    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    import_result = import_local_cloudsave_snapshot(target_cm)

    assert import_result["applied_character_count"] == 1
    assert target_cm.load_characters() == source_cm.load_characters()
    assert not extra_memory_dir.exists()
    assert not (target_memory_dir / "settings.json").exists()

    restored_recent = json.loads((target_memory_dir / "recent.json").read_text(encoding="utf-8"))
    restored_facts = json.loads((target_memory_dir / "facts.json").read_text(encoding="utf-8"))
    restored_persona = json.loads((target_memory_dir / "persona.json").read_text(encoding="utf-8"))
    assert restored_recent[0]["content"] == "from-device-a"
    assert restored_facts[0]["content"] == "source-fact"
    assert restored_persona["traits"] == ["source-persona"]

    with sqlite3.connect(str(target_memory_dir / "time_indexed.db")) as conn:
        rows = conn.execute("SELECT content FROM entries ORDER BY id").fetchall()
    assert rows == [("source-db-entry",)]


@pytest.mark.unit
def test_cross_device_import_applies_remote_tombstones_without_recreating_deleted_character(tmp_path):
    source_cm = _make_config_manager(tmp_path / "device_a")
    target_cm = _make_config_manager(tmp_path / "device_b")

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot, import_local_cloudsave_snapshot

    kept_name = "\u4fdd\u7559\u89d2\u8272"
    deleted_name = "\u5df2\u5220\u9664\u89d2\u8272"

    _write_runtime_state(source_cm, character_name=kept_name)
    source_cm.save_character_tombstones_state(
        {
            "version": source_cm.CHARACTER_TOMBSTONES_STATE_VERSION,
            "tombstones": [
                {
                    "character_name": deleted_name,
                    "deleted_at": "2026-04-08T00:00:00Z",
                    "sequence_number": 9,
                }
            ],
        }
    )
    export_local_cloudsave_snapshot(source_cm)

    _write_runtime_state(target_cm, character_name=kept_name)
    target_characters = target_cm.load_characters()
    template_character = copy.deepcopy(next(iter(target_characters["\u732b\u5a18"].values())))
    target_characters["\u732b\u5a18"][deleted_name] = template_character
    target_characters["\u5f53\u524d\u732b\u5a18"] = deleted_name
    target_cm.save_characters(target_characters, bypass_write_fence=True)
    deleted_memory_dir = Path(target_cm.memory_dir) / deleted_name
    deleted_memory_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        deleted_memory_dir / "recent.json",
        [{"role": "user", "content": "stale-local-data"}],
        ensure_ascii=False,
        indent=2,
    )

    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

    import_result = import_local_cloudsave_snapshot(target_cm)

    assert import_result["applied_character_count"] == 1
    imported_characters = target_cm.load_characters()
    assert deleted_name not in imported_characters.get("\u732b\u5a18", {})
    assert imported_characters["\u5f53\u524d\u732b\u5a18"] == kept_name
    assert not deleted_memory_dir.exists()

    restored_tombstones = target_cm.load_character_tombstones_state()
    assert restored_tombstones["tombstones"][0]["character_name"] == deleted_name


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
def test_export_allows_normal_words_that_only_contain_sensitive_substrings(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import export_local_cloudsave_snapshot
    from utils.config_manager import set_reserved

    characters = cm.get_default_characters()
    payload = characters["猫娘"][next(iter(characters["猫娘"]))]
    payload["喜欢的食物"] = "cookies"
    characters["猫娘"] = {"普通角色": payload}
    characters["当前猫娘"] = "普通角色"
    set_reserved(characters["猫娘"]["普通角色"], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"]["普通角色"], "avatar", "asset_source", "local")
    set_reserved(characters["猫娘"]["普通角色"], "avatar", "asset_source_id", "")
    set_reserved(characters["猫娘"]["普通角色"], "avatar", "live2d", "model_path", "demo/demo.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    local_model_dir = Path(cm.live2d_dir) / "demo"
    local_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        local_model_dir / "demo.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )

    result = export_local_cloudsave_snapshot(cm)
    assert result["manifest"]["sequence_number"] >= 1


@pytest.mark.unit
def test_scan_for_sensitive_values_detects_secret_like_strings_without_flagging_plain_words():
    from utils.cloudsave_runtime import scan_for_sensitive_values

    assert scan_for_sensitive_values({"喜欢的食物": "cookies"}, path="profiles.characters") == []
    assert scan_for_sensitive_values({"note": "Authorization: Bearer abcdefghijklmnop"}, path="profiles.characters") == [
        "profiles.characters.note"
    ]


@pytest.mark.unit
def test_cloudsave_summary_does_not_persist_default_workshop_config_when_missing(tmp_path):
    cm = _make_config_manager(tmp_path)

    from utils.cloudsave_runtime import build_cloudsave_summary

    _write_runtime_state(cm, character_name="小满")
    workshop_config_path = Path(cm.get_runtime_config_path("workshop_config.json"))
    if workshop_config_path.exists():
        workshop_config_path.unlink()

    summary = build_cloudsave_summary(cm)
    assert summary["success"] is True
    assert not workshop_config_path.exists()


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
