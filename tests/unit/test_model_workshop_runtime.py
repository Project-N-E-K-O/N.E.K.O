"""N3 read isolation and existing-delete coordination, with real N2 transactions."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.model_workshop import runtime
from services.model_workshop.paths import WorkshopError
from tests.unit.test_model_workshop_install import (
    bundle_path,
    config as config,
    service as service,
    install,
    fail_registration,
    journal,
)
from tests.unit.test_model_workshop_formats import live2d_files, make_native_bundle


@pytest.fixture
def owned(service, monkeypatch):
    monkeypatch.setattr(runtime, "_services", {str(service.paths.root): service})
    monkeypatch.setattr(runtime, "_stopped", False)
    return service


def test_read_snapshot_does_not_initialize_workshop(config):
    state = runtime.snapshot(config)
    assert state.records == () and not state.blocked
    assert list(config.app_docs_dir.iterdir()) == []


@pytest.mark.parametrize("tag,allowed", [(0x9000001A, True), (0xA0000003, False), (0xA000000C, False)])
def test_identity_distinguishes_cloud_placeholders_from_redirecting_reparse_points(tmp_path, monkeypatch, tag, allowed):
    from pathlib import Path
    from types import SimpleNamespace
    from services.model_workshop.paths import identity

    path = tmp_path / "file"
    path.write_bytes(b"model")
    info = path.lstat()
    fake = SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino,
                           st_file_attributes=0x400, st_reparse_tag=tag)
    monkeypatch.setattr(Path, "lstat", lambda self: fake)
    if allowed:
        assert identity(path)[:2] == [info.st_dev, info.st_ino]
    else:
        with pytest.raises(WorkshopError, match="unsafe_path"):
            identity(path)


def test_shared_parameters_remain_editable_after_managed_install(owned, tmp_path):
    files = live2d_files()
    files["parameters.json"] = b'{"param_0":1}'
    package = make_native_bundle(tmp_path, files=files)
    row = install(owned, package)["result"]
    target = (owned.paths.root / row["local_entrypoint"]).parent / "parameters.json"
    assert json.loads(target.read_text()) == {"param_0": 1}
    assert runtime.write_managed_json(owned.config, target, {"param_0": 2.5}) is True
    assert owned.installer.registry.installed()[0]["status"] == "installed"
    assert install(owned, package)["result"]["already_installed"]
    assert json.loads(target.read_text()) == {"param_0": 2.5}


def test_root_alias_keeps_managed_write_and_delete_protection(owned, tmp_path):
    path, _ = bundle_path(tmp_path)
    row = install(owned, path)["result"]
    alias = tmp_path / "runtime-alias"
    try:
        alias.symlink_to(owned.paths.root, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    cfg = owned.config
    cfg.app_docs_dir = alias
    for kind in ("live2d", "vrm", "mmd"):
        setattr(cfg, kind + "_dir", alias / kind)
    target = alias / row["target"]
    state = runtime.snapshot(cfg)
    assert state.record_for(target)["install_id"] == row["install_id"]
    with pytest.raises(WorkshopError, match="install_conflict"):
        runtime.reject_managed_upload(cfg, target)
    assert runtime.write_managed_blob(cfg, target / "extra.motion3.json", b"{}") is True
    assert runtime.write_managed_json(cfg, alias / row["local_entrypoint"], {"local": True}) is True
    runtime.remove_managed_model(cfg, row)
    assert not target.exists()


@pytest.mark.parametrize("mutation", ["json", "blob", "upload"])
def test_unreadable_registry_never_falls_back_to_an_ordinary_writer(owned, tmp_path, mutation):
    path, _ = bundle_path(tmp_path)
    row = install(owned, path)["result"]
    (owned.paths.private / "installed.json").write_bytes(b"{")
    target = owned.paths.root / row["local_entrypoint"]
    before = target.read_bytes()
    # Pure queries continue tolerating this error; mutating adapters must not.
    assert runtime.snapshot(owned.config).errors
    with pytest.raises(WorkshopError, match="unavailable"):
        if mutation == "json":
            runtime.write_managed_json(owned.config, target, {})
        elif mutation == "blob":
            runtime.write_managed_blob(owned.config, target.parent / "new.json", b"{}")
        else:
            runtime.reject_managed_upload(owned.config, target.parent)
    assert target.read_bytes() == before


def test_main_maintenance_does_not_retry_unchanged_recovery_conflicts(owned, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path, "vrm")
    save = fail_registration(owned, monkeypatch)
    failed = install(owned, path)
    row = journal(owned, failed["task_id"])["record"]
    (owned.paths.root / row["target"]).write_bytes(b"changed after crash")
    monkeypatch.setattr(owned.installer.registry, "save", save)
    original = owned.installer.recover
    calls = []

    def recovering(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(owned.installer, "recover", recovering)
    for _ in range(5):
        runtime.maintain(owned.config)
    assert len(calls) == 1
    log = owned.installer.journal_path(failed["task_id"])
    log.write_bytes(log.read_bytes())  # A new metadata version permits another explicit attempt.
    runtime.maintain(owned.config)
    assert len(calls) == 2


def test_maintenance_retries_a_previously_active_task_after_its_worker_fails(owned, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path, "vrm")
    entered, release = threading.Event(), threading.Event()
    save = owned.installer.registry.save

    def failed_save(rows, attempts=None):
        if not rows:
            return save(rows, attempts)  # Durable intent precedes publication.
        entered.set()
        assert release.wait(5)
        raise OSError("registration interrupted")

    monkeypatch.setattr(owned.installer.registry, "save", failed_save)
    with path.open("rb") as source:
        attempt = owned.receive(source)
    assert entered.wait(5)
    try:
        # This recovery skips the active journal, then remembers its version.
        runtime.maintain(owned.config)
    finally:
        release.set()
    assert owned.wait(attempt, 5)["phase"] == "failed"
    monkeypatch.setattr(owned.installer.registry, "save", save)
    assert runtime.maintain(owned.config)[attempt] == "completed"


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx"])
def test_only_known_unfinished_resources_are_unavailable(
    owned, tmp_path, monkeypatch, kind
):
    path, _ = bundle_path(tmp_path, kind)
    original = fail_registration(owned, monkeypatch)
    result = install(owned, path)
    row = journal(owned, result["task_id"])["record"]
    state = runtime.snapshot(owned.config)
    with pytest.raises(WorkshopError, match="unavailable"):
        state.require(owned.paths.root / row["local_entrypoint"])
    state.require(owned.paths.root / "live2d/neko_model_unowned/foreign.model3.json")
    state.require(owned.paths.root / "live2d/normal/Avatar.model3.json")
    monkeypatch.setattr(owned.installer.registry, "save", original)
    assert runtime.maintain(owned.config)[result["task_id"]] == "completed"
    runtime.snapshot(owned.config).require(owned.paths.root / row["local_entrypoint"])


def test_registry_corruption_preserves_ordinary_models_and_blocks_trusted_pending_paths(
    owned, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    fail_registration(owned, monkeypatch)
    result = install(owned, path)
    row = journal(owned, result["task_id"])["record"]
    (owned.paths.private / "installed.json").write_bytes(b"{")
    state = runtime.snapshot(owned.config)
    assert state.errors
    with pytest.raises(WorkshopError, match="unavailable"):
        state.require(owned.paths.root / row["target"] / "textures/face.png")
    state.require(owned.paths.root / "live2d/ordinary/model.model3.json")


def test_completed_model_metadata_preserves_local_identity_and_unverified_attribution(
    owned, tmp_path
):
    path, _ = bundle_path(tmp_path)
    row = install(owned, path)["result"]
    state = runtime.snapshot(owned.config)
    result = state.record_for(owned.paths.root / row["local_entrypoint"])
    assert result["install_id"] == row["install_id"]
    assert (
        result["display_name"] == "Native fixture" and result["license"] == "Test only"
    )
    assert "item_id" not in result and "market_source" not in result


def test_delete_rechecks_the_captured_attempt_and_preserves_a_new_reinstall(
    owned, tmp_path
):
    path, _ = bundle_path(tmp_path, "vrm")
    old = install(owned, path)["result"]
    runtime.remove_managed_model(owned.config, old)
    new = install(owned, path)["result"]
    with pytest.raises(WorkshopError, match="conflict"):
        runtime.remove_managed_model(owned.config, old)
    assert (owned.paths.root / new["target"]).exists()


def test_delete_holds_lifecycle_through_associated_config_cleanup(owned, tmp_path):
    path, _ = bundle_path(tmp_path, "vrm")
    row = install(owned, path)["result"]
    entered, release = threading.Event(), threading.Event()

    def auxiliary():
        entered.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(
            runtime.remove_managed_model, owned.config, row, auxiliary
        )
        assert entered.wait(5)

        def receive():
            with path.open("rb") as source:
                return owned.receive(source)

        receiving = pool.submit(receive)
        try:
            with pytest.raises(TimeoutError):
                receiving.result(0.05)
        finally:
            release.set()
        deletion.result(5)
        attempt = receiving.result(5)
    assert owned.wait(attempt, 5)["phase"] == "completed"
    assert (
        owned.installer.registry.load()[row["package_sha256"]]["install_id"]
        != row["install_id"]
    )


def test_partial_delete_keeps_registration_and_reports_actual_failure(owned, tmp_path):
    path, _ = bundle_path(tmp_path, "vrm")
    row = install(owned, path)["result"]

    def fail():
        raise PermissionError("auxiliary mapping is busy")

    with pytest.raises(WorkshopError, match="delete_partial"):
        runtime.remove_managed_model(owned.config, row, fail)
    assert (
        owned.installer.registry.load()[row["package_sha256"]]["install_id"]
        == row["install_id"]
    )
    assert owned.installer.registry.installed()[0]["status"] == "missing"


def test_read_queries_and_mutations_apply_the_current_runtime_root(owned, tmp_path):
    path, _ = bundle_path(tmp_path)
    row = install(owned, path)["result"]
    assert runtime.snapshot(owned.config).record_for(owned.paths.root / row["target"])
    # A copied completed registry needs logical paths, not obsolete physical IDs.
    import shutil
    from types import SimpleNamespace

    new_root = tmp_path / "new-root"
    # Real storage migration runs after the owner stops. Windows denies copying
    # the locked writer file; do not bypass that protection in the test.
    owned.close()
    shutil.copytree(owned.paths.root, new_root)
    cfg = SimpleNamespace(
        app_docs_dir=new_root,
        live2d_dir=new_root / "live2d",
        vrm_dir=new_root / "vrm",
        mmd_dir=new_root / "mmd",
    )
    state = runtime.snapshot(cfg)
    state.require(new_root / row["local_entrypoint"])
    assert (
        state.record_for(new_root / row["local_entrypoint"])["install_id"]
        == row["install_id"]
    )
