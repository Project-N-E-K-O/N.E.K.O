"""Storage classification and stopped-owner migration of real workshop installs."""

import json
from types import SimpleNamespace

import pytest

from services.model_workshop import runtime
from tests.unit.test_model_workshop_install import (
    bundle_path,
    config as config,
    install,
    service as service,
)
from tests.unit.test_storage_migration import _DummyConfigManager
from utils.storage import migration
from utils.cloudsave_runtime.legacy_migration import runtime_root_has_user_content
from utils.storage.migration import (
    MIGRATED_RUNTIME_ENTRY_NAMES,
    StorageMigrationError,
    _V1_CATCH_UP_ENTRIES,
    require_paired_model_workshop_roots,
    create_pending_storage_migration,
    get_storage_migration_path,
    root_has_migrated_entry_content,
    run_pending_storage_migration,
)


def private(root):
    """Only isolated storage is populated; never initialize a real app singleton."""
    value = root / "model_workshop"
    value.mkdir(parents=True)
    for name in ("locks", "staging", "transactions"):
        (value / name).mkdir()
    (value / "locks/writer.lock").touch()
    return value


@pytest.mark.parametrize("probe", [root_has_migrated_entry_content, runtime_root_has_user_content])
@pytest.mark.parametrize("kind", ["empty", "temporary", "empty_registry", "registry", "unknown", "pending", "symlink"])
def test_content_probe_distinguishes_infrastructure_and_preserves_unknowns(tmp_path, probe, kind):
    directory = private(tmp_path)
    if kind == "temporary":
        stage = directory / "staging" / ("a" * 32)
        stage.mkdir()
        (stage / "input.neko-model.zip").write_bytes(b"unpublished")
    elif kind in ("empty_registry", "registry"):
        (directory / "installed.json").write_text(json.dumps(dict(
            schema="neko.model.installed.v1", installed={} if kind == "empty_registry" else {"preserve": {}}, attempts={}
        )))
    elif kind == "unknown":
        (directory / "unexpected.bin").write_bytes(b"preserve")
    elif kind == "pending":
        (directory / "transactions" / ("a" * 32 + ".json")).write_bytes(b"unknown")
    elif kind == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        # A link itself is evidence, even when its destination is empty.
        try:
            (directory / "staging/link").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert probe(tmp_path) is (kind not in ("empty", "temporary", "empty_registry"))
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


def test_workshop_is_migrated_only_with_the_model_directories():
    assert "model_workshop" in MIGRATED_RUNTIME_ENTRY_NAMES
    assert "model_workshop" not in _V1_CATCH_UP_ENTRIES


@pytest.mark.parametrize("location", ["", "transactions"])
def test_atomic_writer_temp_residue_does_not_claim_user_content_or_block_migration(tmp_path, location):
    from utils.model_workshop_storage import assert_model_workshop_stopped

    directory = private(tmp_path)
    temporary = directory / location / ".nkatmpdeadbeef.tmp"
    temporary.write_bytes(b"uncommitted JSON")
    before = temporary.read_bytes()
    assert root_has_migrated_entry_content(tmp_path) is False
    assert runtime_root_has_user_content(tmp_path) is False
    assert_model_workshop_stopped(tmp_path)
    assert temporary.read_bytes() == before  # A read-only probe never sweeps temporaries.


@pytest.mark.parametrize("kind", ["directory", "symlink", "unrecognized"])
def test_temp_like_unknown_entries_remain_protected(tmp_path, kind):
    from utils.model_workshop_storage import ModelWorkshopStorageError, assert_model_workshop_stopped

    directory = private(tmp_path)
    target = directory / ".nkatmpdeadbeef.tmp"
    if kind == "directory":
        target.mkdir()
    elif kind == "symlink":
        try:
            target.symlink_to(tmp_path / "not-present")
        except OSError:
            pytest.skip("symlink privilege unavailable")
    else:
        (directory / ".nkatmpdead.beef.tmp").write_text("preserve")
    assert root_has_migrated_entry_content(tmp_path) is True
    with pytest.raises(ModelWorkshopStorageError):
        assert_model_workshop_stopped(tmp_path)


@pytest.mark.parametrize("side", ["source", "target"])
def test_checkpoint_refuses_an_owned_workshop_without_writing(service, tmp_path, side):
    config = _DummyConfigManager(tmp_path / "manager")
    source = service.paths.root if side == "source" else tmp_path / "source"
    target = service.paths.root if side == "target" else tmp_path / "target"
    source.mkdir(exist_ok=True)
    anchor = tmp_path / "anchor"
    with pytest.raises(StorageMigrationError, match="model_workshop_busy"):
        create_pending_storage_migration(config, source_root=source, target_root=target,
                                         selection_source="user_selected", anchor_root=anchor)
    assert not get_storage_migration_path(config, anchor_root=anchor).exists()


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx", "pmd"])
def test_actual_migration_keeps_completed_registration_and_local_edits(service, tmp_path, kind):
    path, _ = bundle_path(tmp_path, kind)
    row = install(service, path)["result"]
    extra = service.paths.root / row["target"]
    if extra.is_dir():
        (extra / "local-edit.json").write_text('{"edited": true}')
    service.close()
    config = _DummyConfigManager(tmp_path / "manager")
    config.app_docs_dir = service.paths.root
    target, anchor = tmp_path / "target", tmp_path / "anchor"
    create_pending_storage_migration(config, source_root=config.app_docs_dir, target_root=target,
                                     selection_source="user_selected", anchor_root=anchor)
    result = run_pending_storage_migration(config, anchor_root=anchor)
    assert result["completed"], result
    cfg = SimpleNamespace(app_docs_dir=target, **{name + "_dir": target / name for name in ("live2d", "vrm", "mmd")})
    copied = runtime.snapshot(cfg).record_for(target / row["local_entrypoint"])
    assert copied["install_id"] == row["install_id"]
    assert (target / row["local_entrypoint"]).read_bytes() == (service.paths.root / row["local_entrypoint"]).read_bytes()
    if extra.is_dir():
        assert (target / row["target"] / "local-edit.json").read_text() == '{"edited": true}'


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("fault", ["pending", "unknown", "corrupt", "duplicate", "non_regular"])
def test_checkpoint_preserves_uncertain_storage_and_does_not_create_state(tmp_path, side, fault):
    config = _DummyConfigManager(tmp_path / "manager")
    source, target, anchor = (tmp_path / name for name in ("source", "target", "anchor"))
    source.mkdir()
    directory = private(source if side == "source" else target)
    if fault == "pending":
        (directory / "transactions/pending.json").write_text("{}")
    elif fault == "unknown":
        (directory / "unknown.json").write_text("{}")
    elif fault == "corrupt":
        (directory / "installed.json").write_text("{")
    elif fault == "duplicate":
        (directory / "installed.json").write_text('{"schema":"neko.model.installed.v1","installed":{"keep":{}},"installed":{},"attempts":{}}')
    else:
        (directory / "installed.json").mkdir()
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    with pytest.raises(StorageMigrationError, match="model_workshop_(pending|state_unreadable)"):
        create_pending_storage_migration(config, source_root=source, target_root=target,
                                         selection_source="user_selected", anchor_root=anchor)
    assert not get_storage_migration_path(config, anchor_root=anchor).exists()
    assert {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before


def test_copy_rechecks_writer_ownership_after_scheduling(service, tmp_path):
    from services.model_workshop.tasks import WorkshopTasks

    config = _DummyConfigManager(tmp_path / "manager")
    config.app_docs_dir = service.paths.root
    target, anchor = tmp_path / "target", tmp_path / "anchor"
    service.close()
    create_pending_storage_migration(config, source_root=config.app_docs_dir, target_root=target,
                                     selection_source="user_selected", anchor_root=anchor)
    # Emulate an owner starting between the two phases, using the isolated
    # test gate. The real copy phase must independently reject its OS lock.
    late_owner = WorkshopTasks(service.config)
    late_owner.start()
    try:
        result = run_pending_storage_migration(config, anchor_root=anchor)
        assert result["completed"] is False
        assert result["payload"]["error_code"] == "model_workshop_busy"
        assert not target.exists()
    finally:
        late_owner.close()


@pytest.mark.parametrize("registry_at,reuse,source_model,target_model,conflict", [
    ("source", False, True, False, False),
    ("source", True, True, True, True),
    ("source", False, False, True, True),
    ("target", False, True, False, True),
    ("target", True, True, True, False),
    ("both", False, True, True, False),
    ("both", True, True, True, False),
    ("target_source_empty", False, False, True, True),
])
def test_reuse_never_combines_registry_and_models_from_different_roots(
    tmp_path, registry_at, reuse, source_model, target_model, conflict
):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for root, present in ((source, source_model), (target, target_model)):
        if present:
            (root / "live2d").mkdir()
    for root, present in ((source, registry_at in ("source", "both", "target_source_empty")),
                          (target, registry_at in ("target", "both", "target_source_empty"))):
        if present:
            directory = private(root)
            if not (registry_at == "target_source_empty" and root == source):
                (directory / "installed.json").write_text(json.dumps(dict(
                    schema="neko.model.installed.v1", installed={"preserve": {}}, attempts={}
                )))
    if conflict:
        with pytest.raises(StorageMigrationError, match="model_workshop_root_conflict"):
            require_paired_model_workshop_roots(source, target, reuse_target=reuse)
    else:
        require_paired_model_workshop_roots(source, target, reuse_target=reuse)


def test_real_legacy_reuse_rejects_unpaired_registry_and_keeps_both_trees(service, tmp_path):
    path, _ = bundle_path(tmp_path)
    row = install(service, path)["result"]
    service.close()
    config = _DummyConfigManager(tmp_path / "manager")
    config.app_docs_dir = service.paths.root
    target, anchor = tmp_path / "target", tmp_path / "anchor"
    (target / "live2d").mkdir(parents=True)
    foreign = target / "live2d/foreign.model3.json"
    foreign.write_text("foreign")
    create_pending_storage_migration(config, source_root=config.app_docs_dir, target_root=target,
                                     selection_source="legacy", anchor_root=anchor)
    result = run_pending_storage_migration(config, anchor_root=anchor)
    assert not result["completed"]
    assert result["payload"]["error_code"] == "model_workshop_root_conflict"
    assert foreign.read_text() == "foreign"
    assert not (target / "model_workshop").exists()
    assert (service.paths.root / row["local_entrypoint"]).is_file()


def test_legacy_reuse_rejects_a_model_directory_added_during_staging(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    row = install(service, path)["result"]
    service.close()
    config = _DummyConfigManager(tmp_path / "manager")
    config.app_docs_dir = service.paths.root
    (config.app_docs_dir / "memory").mkdir()
    (config.app_docs_dir / "memory" / "note").write_text("source")
    target, anchor = tmp_path / "target", tmp_path / "anchor"
    (target / "config").mkdir(parents=True)
    (target / "config" / "characters.json").write_text("{}")
    foreign = target / row["local_entrypoint"]
    original_copy = migration._copy_and_verify_entry

    def copy_and_add_model(source, staged, **kwargs):
        result = original_copy(source, staged, **kwargs)
        if source == config.app_docs_dir / "memory":
            foreign.parent.mkdir(parents=True)
            foreign.write_text("foreign")
        return result

    monkeypatch.setattr(migration, "_copy_and_verify_entry", copy_and_add_model)
    create_pending_storage_migration(config, source_root=config.app_docs_dir, target_root=target,
                                     selection_source="legacy", anchor_root=anchor)
    result = run_pending_storage_migration(config, anchor_root=anchor)
    assert not result["completed"]
    assert result["payload"]["error_code"] == "model_workshop_root_conflict"
    assert foreign.read_text() == "foreign"
    assert not (target / "model_workshop").exists()
    assert (config.app_docs_dir / row["local_entrypoint"]).is_file()
