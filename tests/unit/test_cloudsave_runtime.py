import copy


import contextlib


import json


import os


import shutil


import sqlite3


import sys


import threading


import time


from contextlib import contextmanager


from pathlib import Path


from types import SimpleNamespace


from unittest.mock import patch


import pytest


from utils.file_utils import atomic_write_json


@contextmanager
def _isolated_sidecar_stores(memory_dir, config_manager=None):
    """Swap the three sidecar singletons for FRESH instances.

    Saving the module globals and restoring them is not enough. These tests
    mutate ``_cache`` and ``_retired`` on the EXISTING objects, and putting the
    same reference back leaves those mutations in place -- so an entry another
    test also uses is silently dropped and the suite becomes order-dependent.
    """
    import memory.anti_repeat as anti_repeat_module
    import memory.anti_repeat_effects as effects_module
    import memory.startup_greeting_history as greeting_module

    # A real config manager when the test drives a real flush: the write path
    # enters cloudsave_writable_transaction, which needs more than memory_dir.
    if config_manager is None:
        config_manager = SimpleNamespace(memory_dir=str(memory_dir))
    store = effects_module.AntiRepeatEffectStore()
    store._config_manager = config_manager
    corpus = anti_repeat_module.AntiRepeatCorpus()
    corpus._config_manager = config_manager
    greeting = greeting_module.StartupGreetingHistory(config_manager)

    previous = (
        effects_module._GLOBAL_STORE,
        anti_repeat_module._GLOBAL_CORPUS,
        greeting_module._GLOBAL_HISTORY,
    )
    effects_module._GLOBAL_STORE = store
    anti_repeat_module._GLOBAL_CORPUS = corpus
    greeting_module._GLOBAL_HISTORY = greeting
    try:
        yield (store, corpus, greeting)
    finally:
        (
            effects_module._GLOBAL_STORE,
            anti_repeat_module._GLOBAL_CORPUS,
            greeting_module._GLOBAL_HISTORY,
        ) = previous


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
@pytest.mark.unit
@pytest.mark.unit
@pytest.mark.unit
def test_empty_cloudsave_skeleton_does_not_block_legacy_runtime_import(tmp_path):
    cm = _make_config_manager(tmp_path / "new")
    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    legacy_root = tmp_path / "legacy" / "N.E.K.O"
    legacy_model = legacy_root / "live2d" / "legacy-model"
    legacy_model.mkdir(parents=True)
    (legacy_model / "model.json").write_text("legacy", encoding="utf-8")
    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()
    for name in ("overrides", "memory", "catalog", "meta", "bindings", "profiles"):
        (Path(cm.cloudsave_dir) / name).mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        Path(cm.cloudsave_dir) / "manifest.json",
        {"files": {}},
        ensure_ascii=False,
        indent=2,
    )

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is True
    assert (Path(cm.live2d_dir) / "legacy-model" / "model.json").is_file()


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
def test_phase0_preserves_completed_storage_checkpoint_and_does_not_reimport(
    tmp_path,
):
    cm = _make_config_manager(tmp_path / "new")
    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment
    from utils.storage.migration import (
        build_pending_storage_migration_payload,
        get_storage_migration_path,
        save_storage_migration,
    )

    legacy_root = tmp_path / "legacy" / "N.E.K.O"
    legacy_model = legacy_root / "live2d" / "old-model"
    legacy_model.mkdir(parents=True)
    (legacy_model / "model.json").write_text("OLD", encoding="utf-8")
    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()
    current_marker = Path(cm.live2d_dir) / "current-model" / "model.json"
    current_marker.parent.mkdir(parents=True)
    current_marker.write_text("CURRENT", encoding="utf-8")

    checkpoint = build_pending_storage_migration_payload(
        source_root=legacy_root,
        target_root=cm.app_docs_dir,
        selection_source="recommended",
        confirmed_existing_target_content=True,
    )
    checkpoint.update(
        status="completed",
        backup_root=str(legacy_root),
        retained_source_root=str(legacy_root),
        retained_source_mode="manual_retention",
        completed_at="2026-09-16T00:00:00Z",
    )
    save_storage_migration(cm, checkpoint, anchor_root=cm.anchor_root)
    checkpoint_path = get_storage_migration_path(cm, anchor_root=cm.anchor_root)
    checkpoint_before = checkpoint_path.read_bytes()

    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is False
    assert result["legacy_import"]["result"] == "target_root_already_initialized"
    assert checkpoint_path.read_bytes() == checkpoint_before
    assert current_marker.read_text(encoding="utf-8") == "CURRENT"
    assert not (Path(cm.live2d_dir) / "old-model").exists()
    assert (legacy_model / "model.json").read_text(encoding="utf-8") == "OLD"


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


def _tamper_manifest_with_memory_key(cm, hostile_key: str, placement_relative_path: str) -> None:
    placement_path = cm.cloudsave_dir / placement_relative_path
    placement_path.parent.mkdir(parents=True, exist_ok=True)
    placement_path.write_text("{}", encoding="utf-8")

    manifest_path = Path(cm.cloudsave_manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][hostile_key] = {"sha256": "0" * 64, "size": 2}
    # 攻击场景里 manifest 由存档作者产出，fingerprint 留空即可跳过一致性校验，
    # 因此旧版路径约束不能依赖 fingerprint 这道闸。
    manifest["schema_version"] = 1
    manifest["min_reader_schema_version"] = 1
    manifest["fingerprint"] = ""
    atomic_write_json(manifest_path, manifest, ensure_ascii=False, indent=2)

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_legacy_import_rejects_cache_symlink_without_replacing_target(tmp_path):
    new_root_base = tmp_path / "new_root_base"
    legacy_root = tmp_path / "legacy_docs" / "N.E.K.O"
    cm = _make_config_manager(new_root_base)
    from utils.cloudsave_runtime import (
        CloudsaveOperationError,
        bootstrap_local_cloudsave_environment,
    )

    legacy_model = legacy_root / "live2d" / "legacy-model"
    legacy_model.mkdir(parents=True)
    (legacy_model / "legacy.model3.json").write_text('{"Version": 3}', encoding="utf-8")
    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()

    external_cache = tmp_path / "external-cache"
    external_cache.mkdir()
    sentinel = external_cache / "sentinel.bin"
    sentinel.write_bytes(b"outside")
    cache_link = Path(cm.app_docs_dir) / "embedding_models"
    cache_link.symlink_to(external_cache, target_is_directory=True)

    with pytest.raises(CloudsaveOperationError) as caught:
        bootstrap_local_cloudsave_environment(cm)

    assert caught.value.code == "LEGACY_RUNTIME_ENTRY_UNSAFE"
    assert cache_link.is_symlink()
    assert sentinel.read_bytes() == b"outside"
    assert not (cm.live2d_dir / "legacy-model").exists()

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO semantics")
def test_legacy_runtime_copy_rejects_fifo_without_blocking(tmp_path):
    cm = _make_config_manager(tmp_path / "new")
    from utils.cloudsave_runtime import (
        CloudsaveOperationError,
        bootstrap_local_cloudsave_environment,
    )

    legacy_root = tmp_path / "legacy" / "N.E.K.O"
    legacy_model = legacy_root / "live2d" / "legacy-model"
    legacy_model.mkdir(parents=True)
    (legacy_model / "legacy.model3.json").write_text("{}", encoding="utf-8")
    os.mkfifo(legacy_root / "embedding_models")
    cm.get_legacy_app_root_candidates = lambda: [legacy_root]

    errors = []

    def copy_fifo():
        try:
            bootstrap_local_cloudsave_environment(cm)
        except BaseException as exc:  # pragma: no branch - asserted below
            errors.append(exc)

    worker = threading.Thread(target=copy_fifo, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive(), "legacy FIFO inspection must not block startup"
    assert len(errors) == 1
    assert isinstance(errors[0], CloudsaveOperationError)
    assert errors[0].code == "LEGACY_RUNTIME_ENTRY_UNSAFE"
    assert not (cm.live2d_dir / "legacy-model").exists()

@pytest.mark.unit
def test_phase0_retries_shared_checkpoint_without_rewriting_anchor_state(
    tmp_path,
    monkeypatch,
):
    cm = _make_config_manager(tmp_path / "new")
    from utils.cloudsave_runtime import (
        CloudsaveOperationError,
        bootstrap_local_cloudsave_environment,
    )
    from utils.storage import migration as storage_migration_module
    from utils.storage.policy import get_storage_policy_path, save_storage_policy

    legacy_model = tmp_path / "legacy" / "N.E.K.O" / "live2d" / "model"
    legacy_model.mkdir(parents=True)
    (legacy_model / "model.json").write_text("legacy", encoding="utf-8")
    cm.get_legacy_app_root_candidates = lambda: [tmp_path / "legacy" / "N.E.K.O"]
    cm.migrate_config_files()
    cm.migrate_memory_files()
    cm.ensure_cloudsave_state_files()
    save_storage_policy(
        cm,
        selected_root=cm.app_docs_dir,
        selection_source="default",
        anchor_root=cm.anchor_root,
    )
    policy_path = get_storage_policy_path(cm, anchor_root=cm.anchor_root)
    policy_before = policy_path.read_bytes()
    root_state_before = Path(cm.root_state_path).read_bytes()

    real_writable_check = storage_migration_module._ensure_target_root_writable

    def fail_preflight(_target_root):
        raise storage_migration_module.StorageMigrationError(
            "injected_preflight_failure",
            "injected preflight failure",
        )

    monkeypatch.setattr(
        storage_migration_module,
        "_ensure_target_root_writable",
        fail_preflight,
    )
    with pytest.raises(CloudsaveOperationError):
        bootstrap_local_cloudsave_environment(cm)

    checkpoint = storage_migration_module.load_storage_migration(cm)
    assert checkpoint["status"] == "recovery_required"
    assert Path(checkpoint["source_root"]).is_dir()
    assert policy_path.read_bytes() == policy_before
    assert Path(cm.root_state_path).read_bytes() == root_state_before

    monkeypatch.setattr(
        storage_migration_module,
        "_ensure_target_root_writable",
        real_writable_check,
    )
    result = bootstrap_local_cloudsave_environment(cm)

    assert result["legacy_import"]["migrated"] is True
    assert (Path(cm.live2d_dir) / "model" / "model.json").is_file()
    completed = storage_migration_module.load_storage_migration(cm)
    assert completed["status"] == "completed"
    assert completed["retained_source_mode"] == "manual_retention"

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX publication fsync injection")
def test_legacy_publish_fsync_failure_restores_original_target(tmp_path, monkeypatch):
    cm = _make_config_manager(tmp_path / "new")
    from utils import storage_migration as storage_migration_module
    from utils.cloudsave_runtime import (
        CloudsaveOperationError,
        bootstrap_local_cloudsave_environment,
    )

    legacy_root = tmp_path / "legacy" / "N.E.K.O"
    legacy_model = legacy_root / "live2d" / "legacy-model"
    legacy_model.mkdir(parents=True)
    (legacy_model / "legacy.model3.json").write_text("legacy", encoding="utf-8")
    cm.get_legacy_app_root_candidates = lambda: [legacy_root]
    cm.migrate_config_files()
    cm.migrate_memory_files()
    target_sentinel = Path(cm.app_docs_dir) / "target-sentinel.txt"
    target_sentinel.write_text("current", encoding="utf-8")

    real_fsync_opened = storage_migration_module._fsync_opened_migration_directory
    injected = False

    def fail_transaction_flush(fd, path):
        nonlocal injected
        if ".neko-storage-migration-" in str(path) and not injected:
            injected = True
            raise storage_migration_module.StorageMigrationError(
                "target_flush_failed",
                "injected transaction flush failure",
            )
        return real_fsync_opened(fd, path)

    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_opened_migration_directory",
        fail_transaction_flush,
    )

    with pytest.raises((CloudsaveOperationError, storage_migration_module.StorageMigrationError)):
        bootstrap_local_cloudsave_environment(cm)

    assert injected is True
    assert target_sentinel.read_text(encoding="utf-8") == "current"
    assert not (Path(cm.app_docs_dir) / "live2d" / "legacy-model").exists()
