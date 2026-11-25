"""Internal install transaction regressions, using isolated roots and structural model fixtures."""

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.model_workshop import installer as installer_module
from services.model_workshop.paths import (
    WorkshopError,
    identity,
    publish_directory,
    read_json,
)
from services.model_workshop.registry import STATE_BYTES, make_record
from services.model_workshop.tasks import WorkshopTasks
from tests.unit.test_model_workshop_formats import (
    live2d_files,
    make_native_bundle,
    pmd_bytes,
    pmx_bytes,
    vrm_bytes,
)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Patch the existing storage gate/fence at its boundary, never use the user's data root."""
    from utils import cloudsave_runtime
    from utils.storage import location_bootstrap

    root = tmp_path / "runtime"
    root.mkdir()
    cfg = SimpleNamespace(
        app_docs_dir=root,
        live2d_dir=root / "live2d",
        vrm_dir=root / "vrm",
        mmd_dir=root / "mmd",
        blocked=False,
        transactions=[],
    )

    @contextmanager
    def fence(actual, **kwargs):
        assert actual is cfg
        owner = threading.get_ident()
        cfg.transactions.append(("enter", owner))
        try:
            yield
        finally:
            assert threading.get_ident() == owner
            cfg.transactions.append(("exit", owner))

    monkeypatch.setattr(cloudsave_runtime, "cloudsave_writable_transaction", fence)
    monkeypatch.setattr(
        location_bootstrap,
        "get_storage_startup_blocking_reason_readonly",
        lambda actual: "selection_required" if actual.blocked else "",
    )
    return cfg


@pytest.fixture
def service(config):
    value = WorkshopTasks(config)
    value.start()
    try:
        yield value
    finally:
        value.close()


def bundle_path(tmp_path, kind="live2d"):
    if kind == "vrm":
        entry, files = "Avatar.vrm", {"Avatar.vrm": vrm_bytes()}
    elif kind in ("pmx", "pmd"):
        entry = "Models/Avatar." + kind
        raw = pmx_bytes() if kind == "pmx" else pmd_bytes()
        files = {entry: raw, "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture"}
    else:
        entry, files = "Avatar.model3.json", live2d_files()
    return make_native_bundle(
        tmp_path,
        kind="mmd" if kind in ("pmx", "pmd") else kind,
        entry=entry,
        files=files,
    ), files


def install(service, path):
    with path.open("rb") as source:
        attempt = service.receive(source)
    return service.wait(attempt, 10)


def journal(service, attempt):
    return read_json(service.installer.journal_path(attempt), STATE_BYTES)


def fail_registration(service, monkeypatch):
    """Allow intent metadata, fail the first completed registry write after publication."""
    original = service.installer.registry.save

    def fail(rows, attempts=None):
        if any(row["install_id"] in attempts.values() for row in rows.values()):
            raise OSError("injected registry disk failure")
        return original(rows, attempts)

    monkeypatch.setattr(service.installer.registry, "save", fail)
    return original


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx", "pmd"])
def test_installs_existing_local_layout_and_preserves_resource_bytes(
    service, tmp_path, kind
):
    path, files = bundle_path(tmp_path, kind)
    result = install(service, path)
    assert result["phase"] == "completed", result
    record = result["result"]
    assert record["install_id"] == result["task_id"]
    assert "market_source" not in record and "item_id" not in record
    assert service.installer.registry.installed()[0]["status"] == "installed"
    for item in record["files"]:
        assert (service.paths.root / item["local_path"]).read_bytes() == files[
            item["path"]
        ]
    local = Path(record["local_entrypoint"])
    if kind in ("pmx", "pmd"):
        assert local.parent.name == "Models"
        assert local.stem == Path(record["target"]).name
    if kind == "vrm":
        assert local.parent.as_posix() == "vrm"
    assert not list((service.paths.private / "staging").iterdir())
    assert not list((service.paths.private / "transactions").iterdir())
    assert service.installer.registry.unavailable() == set()


@pytest.mark.parametrize("null_display_info", [False, True])
def test_nonstandard_motion_installs_verbatim_and_remains_registered(service, tmp_path, null_display_info):
    files = live2d_files()
    if null_display_info:
        model = json.loads(files["Avatar.model3.json"])
        model["FileReferences"]["DisplayInfo"] = None
        files["Avatar.model3.json"] = json.dumps(model).encode()
    motion = json.loads(files["motions/test.motion3.json"])
    motion["Curves"][0]["Target"] = "2422911982"
    motion["Meta"].update(TotalSegmentCount=2, TotalPointCount=1)
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    result = install(service, make_native_bundle(tmp_path, files=files))
    assert result["phase"] == "completed", result
    assert service.installer.registry.installed()[0]["status"] == "installed"
    for item in result["result"]["files"]:
        assert (service.paths.root / item["local_path"]).read_bytes() == files[item["path"]]
    assert service.installer.registry.unavailable() == set()


@pytest.mark.parametrize("tool_effects", [False, True])
def test_optional_effect_compatibility_does_not_block_installation(service, tmp_path, tool_effects):
    files = live2d_files()
    files["Motions_Idle#2.json"] = files.pop("motions/test.motion3.json")
    files["Motions_Tap.json"] = files["Motions_Idle#2.json"]
    files["voice.mp3"] = b'\xff\xfb\xb0\x04' + b'\x00' * 24
    files["physics3.json"] = b'{"Version":3,"Meta":{"PhysicsSettingCount":0},"PhysicsSettings":[]}'
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"].update(Physics="Physics.json", Motions={
        "Idle": [{"File": "Motions_Idle#2.json"}], "Tap": [{"File": "Motions_Tap.json", "Sound": "voice.mp3"}],
    })
    if tool_effects:
        model["FileReferences"]["PhysicsV2"] = {"File": "physics3.json"}
        model["FileReferences"]["Motions"]["init"] = [{"PostCommand": "not executed"}, {"Name": "menu", "Choices": []}]
    files["Avatar.model3.json"] = json.dumps(model).encode()
    result = install(service, make_native_bundle(tmp_path, files=files))
    assert result["phase"] == "completed", result
    assert service.installer.registry.installed()[0]["status"] == "installed"
    for item in result["result"]["files"]:
        assert (service.paths.root / item["local_path"]).read_bytes() == files[item["path"]]


def test_live2d_tool_settings_survive_installation_and_registry_verification(service, tmp_path):
    """The native consumer retains the same companion bytes admitted by Market."""
    files = live2d_files()
    files.update({"Avatar.vtube.json": b'{ "ModelID": "fixture" }\n',
                  "parameters.json": b'{"param_0":1,"ParamAngleX":-2.5}\n',
                  "items_pinned_to_model.json": b'{"items": []}\n'})
    result = install(service, make_native_bundle(tmp_path, files=files))
    assert result["phase"] == "completed", result
    record = result["result"]
    assert {item["path"] for item in record["files"]} == set(files)
    for item in record["files"]:
        assert (service.paths.root / item["local_path"]).read_bytes() == files[item["path"]]
    assert service.installer.registry.installed()[0]["status"] == "installed"


def test_construction_and_read_only_queries_do_not_create_directories(config):
    svc = WorkshopTasks(config)
    assert svc.installer.registry.installed() == []
    assert svc.installer.registry.unavailable() == set()
    assert list(config.app_docs_dir.iterdir()) == []


def test_first_storage_gate_blocks_all_initialization(config):
    config.blocked = True
    with pytest.raises(WorkshopError, match="storage_blocked"):
        WorkshopTasks(config).start()
    assert list(config.app_docs_dir.iterdir()) == []
    assert not config.transactions


def test_same_root_second_owner_is_busy_and_lock_inode_is_retained(service, config):
    path = service.paths.private / "locks/writer.lock"
    original = identity(path)
    second = WorkshopTasks(config)
    with pytest.raises(WorkshopError, match="busy"):
        second.start()
    service.close()
    second.start()
    assert identity(path) == original
    second.close()


@pytest.mark.parametrize("replacement", ["symlink", "hardlink", "fifo"])
def test_unsafe_writer_lock_is_never_opened_as_a_valid_owner(
    config, tmp_path, replacement
):
    directory = config.app_docs_dir / "model_workshop/locks"
    directory.mkdir(parents=True)
    lock = directory / "writer.lock"
    external = tmp_path / "foreign"
    external.write_bytes(b"keep")
    if replacement == "symlink":
        try:
            lock.symlink_to(external)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("symlink privilege unavailable")
            raise
    elif replacement == "hardlink":
        os.link(external, lock)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is POSIX-only")
        os.mkfifo(lock)
    with pytest.raises((WorkshopError, OSError)):
        WorkshopTasks(config).start()
    assert external.read_bytes() == b"keep"


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx"])
def test_duplicate_import_preserves_local_edits_and_metadata(service, tmp_path, kind):
    path, _ = bundle_path(tmp_path, kind)
    first = install(service, path)["result"]
    entry = service.paths.root / first["local_entrypoint"]
    entry.write_bytes(b"local changes")
    extra = entry.parent / "parameters.json"
    extra.write_text('{"local":true}')
    result = install(service, path)
    assert result["phase"] == "completed"
    assert result["result"]["already_installed"]
    assert result["result"]["install_id"] == first["install_id"]
    assert entry.read_bytes() == b"local changes" and extra.exists()
    assert result["result"]["license"] == "Test only"


@pytest.mark.parametrize("occupant", ["empty", "file", "dangling_link"])
def test_unowned_final_destination_is_never_overwritten(service, tmp_path, occupant):
    path, _ = bundle_path(tmp_path)
    bundle = installer_module.validate_model_bundle(path)
    record = make_record(bundle, uuid.uuid4().hex)
    target = service.paths.root / record["target"]
    target.parent.mkdir()
    if occupant == "empty":
        target.mkdir()
    elif occupant == "file":
        target.write_bytes(b"foreign")
    else:
        try:
            target.symlink_to(tmp_path / "absent")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("symlink privilege unavailable")
            raise
    before = target.lstat()
    result = install(service, path)
    assert result["phase"] == "failed"
    assert target.lstat().st_ino == before.st_ino
    assert service.installer.registry.load() == {}


@pytest.mark.parametrize(
    "invalid", ["broken_json", "unknown_schema", "null", "duplicate_keys", "too_large"]
)
def test_unreadable_registry_is_preserved_and_never_reinitialized(
    service, tmp_path, invalid
):
    registry = service.paths.private / "installed.json"
    raw = {
        "broken_json": b"{",
        "unknown_schema": b'{"schema":"future","installed":{}}',
        "null": b"null",
        "duplicate_keys": b'{"schema":"x","schema":"y"}',
        "too_large": b" " * (STATE_BYTES + 1),
    }[invalid]
    registry.write_bytes(raw)
    path, _ = bundle_path(tmp_path)
    with pytest.raises(WorkshopError, match="state_unreadable"):
        install(service, path)
    assert registry.read_bytes() == raw
    assert not (service.paths.root / "live2d").exists()
    with pytest.raises(WorkshopError, match="state_unreadable"):
        service.installer.registry.installed()


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx"])
def test_recovery_completes_from_published_identity_and_manifest_without_zip(
    service, tmp_path, monkeypatch, kind
):
    path, _ = bundle_path(tmp_path, kind)
    original = fail_registration(service, monkeypatch)
    failed = install(service, path)
    assert failed["phase"] == "failed"
    data = journal(service, failed["task_id"])
    assert data["phase"] == "committing"
    (service.installer.stage(failed["task_id"]) / "input.neko-model.zip").unlink()
    assert service.installer.registry.unavailable() == {data["record"]["target"]}
    monkeypatch.setattr(service.installer.registry, "save", original)
    assert service.recover()[failed["task_id"]] == "completed"
    assert service.installer.registry.installed()[0]["install_id"] == failed["task_id"]
    assert service.installer.registry.unavailable() == set()


@pytest.mark.parametrize(
    "change", ["copied_identity", "changed_content", "extra_file", "missing_file"]
)
def test_recovery_conflict_preserves_target_and_journal(
    service, tmp_path, monkeypatch, change
):
    path, _ = bundle_path(tmp_path)
    original = fail_registration(service, monkeypatch)
    result = install(service, path)
    attempt = result["task_id"]
    data = journal(service, attempt)
    target = service.paths.root / data["record"]["target"]
    if change == "copied_identity":
        shutil.copytree(target, target.with_name("copy"))
        # Keep the original inode alive: a deleted inode may be reused immediately.
        target.rename(target.with_name("original"))
        target.with_name("copy").rename(target)
    elif change == "changed_content":
        (service.paths.root / data["record"]["local_entrypoint"]).write_bytes(b"edited")
    elif change == "extra_file":
        (target / "unexpected.txt").write_text("keep")
    else:
        (service.paths.root / data["record"]["local_entrypoint"]).unlink()
    monkeypatch.setattr(service.installer.registry, "save", original)
    assert service.recover()[attempt] == "model_workshop_recovery_conflict"
    assert target.exists() and service.installer.journal_path(attempt).exists()
    assert service.installer.registry.load() == {}


def test_missing_published_target_is_interrupted_and_not_reinstalled(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    original = fail_registration(service, monkeypatch)
    result = install(service, path)
    data = journal(service, result["task_id"])
    target = service.paths.root / data["record"]["target"]
    target.unlink()
    monkeypatch.setattr(service.installer.registry, "save", original)
    assert service.recover()[result["task_id"]] == "interrupted"
    assert not target.exists()


def test_completed_record_beats_residual_log_after_local_edits(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    original = service.installer.cleanup_private_stage
    monkeypatch.setattr(
        service.installer,
        "cleanup_private_stage",
        lambda _journal: (_ for _ in ()).throw(OSError("busy")),
    )
    result = install(service, path)
    assert result["phase"] == "completed"
    entry = service.paths.root / result["result"]["local_entrypoint"]
    entry.write_bytes(b"local adjustment")
    monkeypatch.setattr(service.installer, "cleanup_private_stage", original)
    assert service.recover()[result["task_id"]] == "completed"
    assert entry.read_bytes() == b"local adjustment"


def test_reinstall_recovery_does_not_mistake_older_completed_record_for_current_attempt(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    first = install(service, path)["result"]
    target = service.paths.root / first["target"]
    target.unlink()
    original = fail_registration(service, monkeypatch)
    second = install(service, path)
    assert second["phase"] == "failed" and target.exists()
    assert (
        service.installer.registry.load()[first["package_sha256"]]["install_id"]
        == first["install_id"]
    )
    monkeypatch.setattr(service.installer.registry, "save", original)
    assert service.recover()[second["task_id"]] == "completed"
    assert (
        service.installer.registry.load()[first["package_sha256"]]["install_id"]
        == second["task_id"]
    )


def test_superseded_old_log_cannot_touch_new_installation(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    original = service.installer.cleanup_private_stage
    monkeypatch.setattr(
        service.installer,
        "cleanup_private_stage",
        lambda _journal: (_ for _ in ()).throw(OSError("busy")),
    )
    first = install(service, path)
    data = journal(service, first["task_id"])
    monkeypatch.setattr(service.installer, "cleanup_private_stage", original)
    # Remove only this old log while reinstalling, then restore the stale log as a crash fixture.
    service.installer.journal_path(first["task_id"]).unlink()
    target = service.paths.root / first["result"]["target"]
    target.unlink()
    second = install(service, path)
    assert second["phase"] == "completed"
    service.installer.save_journal(data)
    expected = target.read_bytes()
    assert service.recover()[first["task_id"]] == "superseded"
    assert target.read_bytes() == expected
    assert (
        service.installer.registry.load()[first["result"]["package_sha256"]][
            "install_id"
        ]
        == second["task_id"]
    )


def test_forged_recovery_path_is_rejected_without_touching_external_file(
    service, tmp_path
):
    attempt = uuid.uuid4().hex
    data = service.installer.begin(attempt)
    data["phase"] = "committing"
    path, _ = bundle_path(tmp_path)
    data["record"] = make_record(installer_module.validate_model_bundle(path), attempt)
    data["object_identity"] = data["stage_identity"]
    from services.model_workshop.registry import validate_journal
    validate_journal(data, attempt + '.json')
    data["record"]["target"] = "../../foreign"
    service.installer.save_journal(data)
    external = tmp_path / "foreign"
    external.write_text("keep")
    assert service.recover()[attempt] == "model_workshop_state_unreadable"
    assert (
        external.read_text() == "keep"
        and service.installer.journal_path(attempt).exists()
    )


def test_atomic_directory_publish_rejects_an_existing_empty_target(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "resource").write_text("source")
    try:
        with pytest.raises(FileExistsError):
            publish_directory(source, target)
    except WorkshopError as exc:
        pytest.skip(str(exc))
    assert not list(target.iterdir()) and (source / "resource").read_text() == "source"


def test_current_runtime_root_is_rechecked_before_acceptance(service, config, tmp_path):
    old_root = config.app_docs_dir
    new_root = tmp_path / "new"
    new_root.mkdir()
    config.app_docs_dir = new_root
    config.live2d_dir, config.vrm_dir, config.mmd_dir = (
        new_root / name for name in ("live2d", "vrm", "mmd")
    )
    with pytest.raises(WorkshopError, match="root_changed"):
        service.receive(io.BytesIO(b"input"))
    assert not (new_root / "model_workshop").exists()
    config.app_docs_dir = old_root
    config.live2d_dir, config.vrm_dir, config.mmd_dir = (
        old_root / name for name in ("live2d", "vrm", "mmd")
    )


def test_native_admission_and_extraction_require_the_same_archive(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    original = installer_module.validate_model_bundle

    def changed(source):
        admitted = original(source)
        # Container-valid replacement with different content cannot reuse native admission.
        replacement, _ = bundle_path(tmp_path, "vrm")
        source.write_bytes(replacement.read_bytes())
        return admitted

    monkeypatch.setattr(installer_module, "validate_model_bundle", changed)
    result = install(service, path)
    assert result["phase"] == "failed" and result["error"] == "model_bundle_changed"
    assert service.installer.registry.load() == {}


def block_validation(monkeypatch):
    """Hold a real validation worker without replacing its final parsing result."""
    entered, release = threading.Event(), threading.Event()
    original = installer_module.validate_model_bundle

    def blocked(path):
        entered.set()
        assert release.wait(10), "test did not release validation"
        return original(path)

    monkeypatch.setattr(installer_module, "validate_model_bundle", blocked)
    return entered, release


def test_bounded_queue_and_recovery_exclude_all_active_reservations(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    entered, release = block_validation(monkeypatch)
    try:
        with path.open("rb") as source:
            first = service.receive(source)
        assert entered.wait(5)
        with path.open("rb") as source:
            second = service.receive(source)
        with pytest.raises(WorkshopError, match="busy"):
            service.receive(io.BytesIO(b"not accepted"))
        assert service.recover() == {}
        assert journal(service, first)["phase"] == "validating"
        assert journal(service, second)["phase"] == "queued"
    finally:
        release.set()
    assert service.wait(first, 10)["phase"] == "completed"
    assert service.wait(second, 10)["result"]["already_installed"]


def test_cancel_during_validation_waits_for_the_actual_worker(
    service, config, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    entered, release = block_validation(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with path.open("rb") as source:
                attempt = service.receive(source)
            assert entered.wait(5)
            cancelling = pool.submit(service.cancel, attempt)
            assert service._task(attempt).cancel.wait(5)
            with pytest.raises(TimeoutError):
                cancelling.result(0.05)
            with pytest.raises(WorkshopError, match="busy"):
                WorkshopTasks(config).start()
        finally:
            release.set()
        assert cancelling.result(10)["phase"] == "cancelled"
    assert service.installer.registry.load() == {}
    assert not (service.paths.root / "live2d").exists()


def test_cancel_after_commit_begins_returns_the_completed_install(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    entered, release = threading.Event(), threading.Event()
    original = service.installer._publish

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(service.installer, "_publish", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with path.open("rb") as source:
                attempt = service.receive(source)
            assert entered.wait(5)
            assert service.status(attempt)["phase"] == "committing"
            cancelling = pool.submit(service.cancel, attempt)
            with pytest.raises(TimeoutError):
                cancelling.result(0.05)
        finally:
            release.set()
        assert cancelling.result(10)["phase"] == "completed"
    assert service.installer.registry.installed()[0]["status"] == "installed"


def test_repeated_async_shutdown_cancellation_does_not_release_a_live_commit(
    service, config, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()
    original_publish, original_close = service.installer._publish, service._close_owned

    def publish(*args):
        entered.set()
        assert release.wait(10)
        return original_publish(*args)

    def close():
        closing.set()
        return original_close()

    monkeypatch.setattr(service.installer, "_publish", publish)
    monkeypatch.setattr(service, "_close_owned", close)
    with path.open("rb") as source:
        attempt = service.receive(source)
    assert entered.wait(5)

    async def scenario():
        pending = asyncio.create_task(service.aclose())
        assert await asyncio.to_thread(closing.wait, 5)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        try:
            assert not pending.done()
            with pytest.raises(WorkshopError, match="busy"):
                WorkshopTasks(config).start()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert service.status(attempt)["phase"] == "completed"
    replacement = WorkshopTasks(config)
    replacement.start()
    replacement.close()


def test_recovery_and_new_receive_share_an_admission_gate(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = service.installer.recover

    def blocked(active):
        entered.set()
        assert release.wait(10)
        return original(active)

    monkeypatch.setattr(service.installer, "recover", blocked)

    def receive():
        with path.open("rb") as source:
            return service.receive(source)

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovering = pool.submit(service.recover)
        assert entered.wait(5)
        receiving = pool.submit(receive)
        try:
            with pytest.raises(TimeoutError):
                receiving.result(0.05)
            assert not list((service.paths.private / "transactions").iterdir())
        finally:
            release.set()
        assert recovering.result(10) == {}
        attempt = receiving.result(10)
    assert service.wait(attempt, 10)["phase"] == "completed"


def test_runtime_root_change_during_preparation_cannot_publish_into_the_old_root(
    service, config, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    entered, release = block_validation(monkeypatch)
    with path.open("rb") as source:
        attempt = service.receive(source)
    assert entered.wait(5)
    original_root = config.app_docs_dir
    new_root = tmp_path / "new-root"
    new_root.mkdir()
    config.app_docs_dir = new_root
    config.live2d_dir, config.vrm_dir, config.mmd_dir = (
        new_root / name for name in ("live2d", "vrm", "mmd")
    )
    try:
        release.set()
        assert service.wait(attempt, 10)["error"] == "model_workshop_root_changed"
        assert not (original_root / "live2d").exists() and not list(new_root.iterdir())
    finally:
        config.app_docs_dir = original_root
        config.live2d_dir, config.vrm_dir, config.mmd_dir = (
            original_root / name for name in ("live2d", "vrm", "mmd")
        )
    assert service.recover()[attempt] == "interrupted"


def test_different_packages_merge_completed_records_without_lost_updates(
    service, tmp_path
):
    first, _ = bundle_path(tmp_path)
    with first.open("rb") as source:
        attempt_a = service.receive(source)
    second, _ = bundle_path(tmp_path, "vrm")
    with second.open("rb") as source:
        attempt_b = service.receive(source)
    assert service.wait(attempt_a, 10)["phase"] == "completed"
    assert service.wait(attempt_b, 10)["phase"] == "completed"
    assert len(service.installer.registry.installed()) == 2


def test_lifecycle_mutex_prevents_a_concurrent_adapter_from_crossing_commit(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    digest = installer_module.validate_model_bundle(path).sha256
    extracted = threading.Event()
    original = service.installer._extract

    def extract(*args):
        result = original(*args)
        extracted.set()
        return result

    monkeypatch.setattr(service.installer, "_extract", extract)
    with service.installer.lifecycle(digest):
        with path.open("rb") as source:
            attempt = service.receive(source)
        assert extracted.wait(5)
        assert not (service.paths.root / "live2d").exists()
        assert service.status(attempt)["phase"] == "staging"
    assert service.wait(attempt, 10)["phase"] == "completed"


def test_real_process_death_after_publish_recovers_on_the_next_owner(config, tmp_path):
    """Kill the writer after actual publication, then recover with no process-local task state."""
    path, _ = bundle_path(tmp_path, "vrm")
    code = """
import sys, threading
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from utils import cloudsave_runtime
from utils.storage import location_bootstrap
from services.model_workshop.tasks import WorkshopTasks
root=Path(sys.argv[1])
config=SimpleNamespace(app_docs_dir=root,live2d_dir=root/'live2d',vrm_dir=root/'vrm',mmd_dir=root/'mmd')
cloudsave_runtime.cloudsave_writable_transaction=lambda *_a,**_kw:nullcontext()
location_bootstrap.get_storage_startup_blocking_reason_readonly=lambda _cfg:''
service=WorkshopTasks(config); service.start()
publish=service.installer._publish
hold=threading.Event()
def blocked(*args):
    publish(*args)
    print('published',flush=True)
    hold.wait()
service.installer._publish=blocked
with open(sys.argv[2],'rb') as source:
    attempt=service.receive(source)
service.wait(attempt)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(config.app_docs_dir), str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(child.stdout.readline)
            try:
                assert ready.result(15).strip() == "published"
            except BaseException:
                child.kill()
                child.wait(5)
                raise
        other = WorkshopTasks(config)
        with pytest.raises(WorkshopError, match="busy"):
            other.start()
    finally:
        child.kill()
        child.wait(5)
        child.stdout.close()
        child.stderr.close()
    replacement = WorkshopTasks(config)
    try:
        replacement.start()
        assert list(replacement.recovery.values()) == ["completed"]
        assert replacement.installer.registry.installed()[0]["status"] == "installed"
    finally:
        replacement.close()


def test_registry_disappearing_after_open_is_not_reinterpreted_as_empty(
    service, monkeypatch
):
    from services.model_workshop import paths as path_module

    path = service.paths.private / "installed.json"
    path.write_text('{"schema":"invalid"}')
    original = path_module.identity
    calls = 0

    def disappear(candidate):
        nonlocal calls
        if candidate == path:
            calls += 1
            if calls == 3:
                path.unlink()
        return original(candidate)

    monkeypatch.setattr(path_module, "identity", disappear)
    with pytest.raises(WorkshopError, match="state_unreadable"):
        read_json(path, STATE_BYTES)


def test_read_only_availability_rechecks_registry_changed_during_journal_scan(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    first = install(service, path)["result"]
    registry = service.installer.registry
    old = registry.read()
    (service.paths.root / first["target"]).unlink()
    original_save = fail_registration(service, monkeypatch)
    second = install(service, path)
    assert second["phase"] == "failed"
    monkeypatch.setattr(registry, "save", original_save)
    original_read = registry.read
    calls = 0

    def stale_once():
        nonlocal calls
        calls += 1
        return old if calls == 1 else original_read()

    monkeypatch.setattr(registry, "read", stale_once)
    assert registry.unavailable() == {first["target"]}


def test_extraction_disk_error_is_not_reported_as_a_bad_input_package(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)

    def fail(*_args):
        raise OSError("injected disk full")

    monkeypatch.setattr(installer_module, "write_all", fail)
    result = install(service, path)
    assert result["error"] == "model_install_io_error"
    assert service.installer.registry.load() == {}


def test_cancellation_during_extraction_keeps_its_actual_error_code(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    original = installer_module.write_all

    def cancel_after_write(*args):
        original(*args)
        next(
            task for task in service._tasks.values() if not task.done.is_set()
        ).cancel.set()

    monkeypatch.setattr(installer_module, "write_all", cancel_after_write)
    result = install(service, path)
    assert (
        result["phase"] == "cancelled" and result["error"] == "model_install_cancelled"
    )
    assert not (service.paths.root / "live2d").exists()


def test_short_writes_are_completed_before_using_their_source_hash():
    from services.model_workshop.paths import write_all

    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    target = ShortWriter()
    write_all(target, b"complete resource bytes")
    assert target.getvalue() == b"complete resource bytes"


def test_receive_budget_rejects_without_a_task_or_final_resource(service, monkeypatch):
    from services.model_workshop import tasks as task_module

    monkeypatch.setattr(
        task_module, "BundleLimits", lambda: SimpleNamespace(archive_bytes=4)
    )
    with pytest.raises(WorkshopError, match="model_bundle_too_large"):
        service.receive(io.BytesIO(b"12345"))
    assert not list((service.paths.private / "staging").iterdir())
    assert not list((service.paths.private / "transactions").iterdir())


def test_failed_publication_intent_persistence_never_publishes_resources(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    original = service.installer.save_journal

    def fail(data):
        if data["phase"] == "committing":
            raise OSError("cannot persist publication evidence")
        return original(data)

    monkeypatch.setattr(service.installer, "save_journal", fail)
    monkeypatch.setattr(
        service.installer,
        "_publish",
        lambda *_args: pytest.fail("unlogged publication"),
    )
    result = install(service, path)
    assert result["phase"] == "failed"
    assert not list((service.paths.root / "live2d").iterdir())
    monkeypatch.setattr(service.installer, "save_journal", original)
    assert service.recover()[result["task_id"]] == "interrupted"


def test_model_directory_link_is_rejected_without_writing_outside_runtime(
    service, tmp_path
):
    path, _ = bundle_path(tmp_path)
    external = tmp_path / "foreign-models"
    external.mkdir()
    try:
        (service.paths.root / "live2d").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("symlink privilege unavailable")
        raise
    assert install(service, path)["phase"] == "failed"
    assert not list(external.iterdir())


def test_expired_task_status_does_not_remove_completed_models(
    service, tmp_path, monkeypatch
):
    from services.model_workshop import tasks as task_module

    monkeypatch.setattr(task_module, "MAX_RECENT", 2)
    path, _ = bundle_path(tmp_path)
    first = install(service, path)
    assert install(service, path)["phase"] == "completed"
    assert install(service, path)["phase"] == "completed"
    with pytest.raises(WorkshopError, match="task_not_found"):
        service.status(first["task_id"])
    assert service.installer.registry.installed()[0]["status"] == "installed"


@pytest.mark.parametrize("phase", ["receiving", "queued", "validating", "staging"])
def test_restarted_unpublished_tasks_are_interrupted_without_resuming(
    service, tmp_path, phase
):
    path, _ = bundle_path(tmp_path)
    attempt = uuid.uuid4().hex
    data = service.installer.begin(attempt)
    if phase == "staging":
        data["record"] = make_record(
            installer_module.validate_model_bundle(path), attempt
        )
    service.installer.phase(data, phase)
    assert service.recover()[attempt] == "interrupted"
    assert not service.installer.stage(attempt).exists()
    assert service.installer.registry.load() == {}
    assert not (service.paths.root / "live2d").exists()


def test_unreliable_object_identity_stops_before_publication(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    original = installer_module.identity

    def unsupported(candidate):
        if candidate.name == "payload":
            raise WorkshopError("model_workshop_identity_unavailable")
        return original(candidate)

    monkeypatch.setattr(installer_module, "identity", unsupported)
    result = install(service, path)
    assert result["error"] == "model_workshop_identity_unavailable"
    assert service.installer.registry.load() == {}
    assert not list((service.paths.root / "live2d").iterdir())


def test_same_mmd_basename_in_two_packages_uses_independent_stems_and_roots(
    service, tmp_path
):
    first_path, files = bundle_path(tmp_path, "pmx")
    first = install(service, first_path)["result"]
    files["Textures/face.png"] += b"other model texture"
    second_path = make_native_bundle(
        tmp_path, kind="mmd", entry="Models/Avatar.pmx", files=files
    )
    second = install(service, second_path)["result"]
    assert first["target"] != second["target"]
    assert Path(first["local_entrypoint"]).stem != Path(second["local_entrypoint"]).stem
    assert (service.paths.root / first["local_entrypoint"]).exists()
    assert (service.paths.root / second["local_entrypoint"]).exists()


def test_partial_completed_model_is_reported_missing_and_never_overwritten(
    service, tmp_path
):
    path, _ = bundle_path(tmp_path)
    first = install(service, path)["result"]
    entry = service.paths.root / first["local_entrypoint"]
    entry.unlink()
    assert service.installer.registry.installed()[0]["status"] == "missing"
    result = install(service, path)
    assert (
        result["phase"] == "failed"
        and result["error"] == "model_workshop_install_conflict"
    )
    assert not entry.exists()
    assert (
        service.installer.registry.load()[first["package_sha256"]]["install_id"]
        == first["install_id"]
    )


def test_invalid_physical_identity_in_journal_cannot_claim_published_model(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    original = fail_registration(service, monkeypatch)
    result = install(service, path)
    data = journal(service, result["task_id"])
    data["object_identity"][1] = 0
    service.installer.save_journal(data)
    monkeypatch.setattr(service.installer.registry, "save", original)
    assert service.recover()[result["task_id"]] == "model_workshop_state_unreadable"
    assert (service.paths.root / data["record"]["target"]).exists()


def test_destination_created_after_preflight_still_wins_the_publish_race(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path)
    original = service.installer._publish

    def race(source, target, record):
        target.mkdir()
        return original(source, target, record)

    monkeypatch.setattr(service.installer, "_publish", race)
    result = install(service, path)
    assert result["phase"] == "failed"
    data = journal(service, result["task_id"])
    target = service.paths.root / data["record"]["target"]
    assert list(target.iterdir()) == []
    assert service.recover()[result["task_id"]] == "model_workshop_recovery_conflict"
    assert target.exists()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX hard-link publication has a second temporary name"
)
def test_vrm_publication_survives_failure_to_unlink_its_staged_hardlink(
    service, tmp_path, monkeypatch
):
    from utils import file_utils

    path, _ = bundle_path(tmp_path, "vrm")
    original_publish, original_unlink = service.installer._publish, file_utils.os.unlink

    def publish(source, target, record):
        def fail_staged_unlink(candidate, *args, **kwargs):
            if Path(candidate) == source:
                raise PermissionError("staged name remains")
            return original_unlink(candidate, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(file_utils.os, "unlink", fail_staged_unlink)
            original_publish(source, target, record)
            assert source.exists() and identity(source) == identity(target)

    monkeypatch.setattr(service.installer, "_publish", publish)
    result = install(service, path)
    assert result["phase"] == "completed"
    assert (service.paths.root / result["result"]["target"]).stat().st_nlink == 1


@pytest.mark.parametrize("kind", ["live2d", "vrm"])
@pytest.mark.parametrize("during", ["install", "recovery"])
def test_object_replacement_during_final_verification_cannot_be_claimed(
    service, tmp_path, monkeypatch, kind, during
):
    path, _ = bundle_path(tmp_path, kind)
    original_verify = service.installer.verify_resources

    def replace_after_check(record, target):
        original_verify(record, target)
        retained = target.with_name(target.name + "-original")
        target.rename(retained)
        if retained.is_dir():
            shutil.copytree(retained, target)
        else:
            shutil.copyfile(retained, target)
        assert identity(retained) != identity(target)

    if during == "install":
        monkeypatch.setattr(service.installer, "verify_resources", replace_after_check)
        result = install(service, path)
        assert (
            result["phase"] == "failed"
            and result["error"] == "model_workshop_recovery_conflict"
        )
    else:
        original_save = fail_registration(service, monkeypatch)
        result = install(service, path)
        monkeypatch.setattr(service.installer.registry, "save", original_save)
        monkeypatch.setattr(service.installer, "verify_resources", replace_after_check)
        assert (
            service.recover()[result["task_id"]] == "model_workshop_recovery_conflict"
        )
    assert service.installer.registry.load() == {}
    assert service.installer.journal_path(result["task_id"]).exists()


@pytest.mark.parametrize("failure", ["publication", "intent"])
def test_recovery_reclaims_only_proven_unpublished_attempts(service, tmp_path, monkeypatch, failure):
    path, _ = bundle_path(tmp_path)
    original_publish = service.installer._publish
    original_journal = service.installer.save_journal

    def fail(*args):
        raise OSError("injected unpublished failure")

    if failure == "publication":
        monkeypatch.setattr(service.installer, "_publish", fail)
    else:
        def fail_intent(value):
            if value["phase"] == "committing":
                fail()
            return original_journal(value)
        monkeypatch.setattr(service.installer, "save_journal", fail_intent)
    result = install(service, path)
    assert result["phase"] == "failed"
    assert bool(service.installer.registry.read()["attempts"]) == (failure == "intent")
    monkeypatch.setattr(service.installer, "_publish", original_publish)
    monkeypatch.setattr(service.installer, "save_journal", original_journal)
    if failure == "intent":
        assert service.recover()[result["task_id"]] == "interrupted"
    else:
        assert not service.installer.journal_path(result["task_id"]).exists()
    assert service.installer.registry.read() == {"installed": {}, "attempts": {}}
    assert install(service, path)["phase"] == "completed"


def test_failed_reinstall_recovery_keeps_the_previous_record_readable(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    completed = install(service, path)
    record = completed["result"]
    shutil.rmtree(service.paths.root / record["target"])
    def fail(*args):
        raise OSError("injected failed reinstall")
    monkeypatch.setattr(service.installer, "_publish", fail)
    failed = install(service, path)
    assert failed["phase"] == "failed"
    assert not service.installer.journal_path(failed["task_id"]).exists()
    state = service.installer.registry.read()
    assert state["installed"][record["package_sha256"]]["install_id"] == completed["task_id"]
    assert state["attempts"][record["package_sha256"]] == completed["task_id"]


def test_publication_failure_can_retry_same_package_without_restart(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    original = service.installer._publish
    def fail(*args): raise PermissionError('injected publication failure')
    monkeypatch.setattr(service.installer, '_publish', fail)
    failed = install(service, path)
    assert failed['phase'] == 'failed'
    monkeypatch.setattr(service.installer, '_publish', original)
    assert install(service, path)['phase'] == 'completed'

def test_first_journal_failure_removes_only_its_empty_stage(service, monkeypatch):
    attempt = uuid.uuid4().hex
    monkeypatch.setattr(service.installer, 'save_journal', lambda data: (_ for _ in ()).throw(OSError('journal write failed')))
    with pytest.raises(OSError, match='journal write failed'): service.installer.begin(attempt)
    assert not service.installer.stage(attempt).exists()


def test_directory_change_during_walk_cannot_be_recorded(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    result = install(service, path)['result']; target = service.paths.root / result['target']
    original = installer_module.os.walk
    def changed(*args, **kwargs):
        for row in original(*args, **kwargs):
            if Path(row[0]) == target: (target / 'unexpected.txt').write_text('late file')
            yield row
    monkeypatch.setattr(installer_module.os, 'walk', changed)
    with pytest.raises(WorkshopError, match='recovery_conflict'): service.installer.verify_resources(result, target)


def test_committed_install_survives_private_cleanup_conflict(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    monkeypatch.setattr(service.installer, 'cleanup_private_stage', lambda data: (_ for _ in ()).throw(WorkshopError('model_workshop_recovery_conflict')))
    result = install(service, path)
    assert result['phase'] == 'completed'
    assert service.installer.registry.installed()[0]['status'] == 'installed'
    assert service.installer.journal_path(result['task_id']).exists()


def test_opened_registry_disappearing_is_not_an_empty_registry(tmp_path, monkeypatch):
    from services.model_workshop import paths
    path = tmp_path / 'installed.json'; path.write_text('{"installed":{}}')
    original = paths.identity; calls = 0
    def removed(candidate):
        nonlocal calls
        calls += 1
        if candidate == path and calls == 2: path.unlink()
        return original(candidate)
    monkeypatch.setattr(paths, 'identity', removed)
    with pytest.raises(WorkshopError, match='state_unreadable'): paths.read_json(path, STATE_BYTES)


@pytest.mark.parametrize('kind', ['wait', 'cancel', 'request_cancel'])
def test_acquired_task_survives_eviction_before_return(service, monkeypatch, kind):
    from services.model_workshop.tasks import _Task
    task = _Task(uuid.uuid4().hex, phase='completed', result={'kept': True}); task.done.set()
    service._tasks[task.task_id] = task
    if kind == 'request_cancel':
        original = service._task
        def acquired(attempt):
            result = original(attempt); service._tasks.pop(attempt); return result
        monkeypatch.setattr(service, '_task', acquired)
    else:
        def waited(timeout=None):
            service._tasks.pop(task.task_id); return True
        monkeypatch.setattr(task.done, 'wait', waited)
    result = getattr(service, kind)(task.task_id)
    assert result['phase'] == 'completed' and result['result'] == {'kept': True}


def test_one_modified_local_installation_does_not_hide_other_models(service, tmp_path):
    path, _ = bundle_path(tmp_path)
    first = install(service, path)['result']
    files = live2d_files(); files['LICENSE.txt'] = b'Another fixture'
    second = install(service, make_native_bundle(tmp_path, files=files))['result']
    resource = service.paths.root / first['files'][0]['local_path']
    outside = tmp_path / 'outside'; outside.write_bytes(resource.read_bytes()); resource.unlink()
    try: resource.symlink_to(outside)
    except (OSError, NotImplementedError): pytest.skip('symlink creation is unavailable')
    rows = {row['package_sha256']: row for row in service.installer.registry.installed()}
    assert rows[first['package_sha256']]['status'] == 'missing'
    assert rows[second['package_sha256']]['status'] == 'installed'
    assert outside.exists()


def test_task_reports_compatibility_warnings_without_extending_registry(service, tmp_path):
    files = live2d_files(); model = json.loads(files['Avatar.model3.json'])
    model['FileReferences']['Physics'] = 'missing.physics3.json'; files['Avatar.model3.json'] = json.dumps(model).encode()
    result = install(service, make_native_bundle(tmp_path, files=files))['result']
    assert result['compatibility_warning_count'] == 1
    assert result['compatibility_warnings'] == [{'code': 'live2d_optional_resource_unavailable', 'path': 'missing.physics3.json'}]
    stored = service.installer.registry.load()[result['package_sha256']]
    assert 'compatibility_warnings' not in stored


def test_unregistered_publication_keeps_capacity_for_recovery(service, tmp_path, monkeypatch):
    from services.model_workshop import registry as registry_module
    path, _ = bundle_path(tmp_path)
    original = fail_registration(service, monkeypatch)
    first = install(service, path); pending = journal(service, first['task_id'])['record']
    monkeypatch.setattr(service.installer.registry, 'save', original)
    files = live2d_files(); files['LICENSE.txt'] = b'B package'
    other_path = make_native_bundle(tmp_path, files=files)
    other = make_record(installer_module.validate_model_bundle(other_path), uuid.uuid4().hex)
    attempts = {row['package_sha256']: row['install_id'] for row in (pending, other)}
    def size(rows):
        return len(json.dumps(dict(schema=registry_module.REGISTRY_SCHEMA, installed=rows, attempts=attempts), ensure_ascii=False, separators=(',', ':')).encode())
    capacity = max(size({row['package_sha256']: row}) for row in (pending, other)) + 32
    monkeypatch.setattr(registry_module, 'STATE_BYTES', capacity)
    rejected = install(service, other_path)
    assert rejected['phase'] == 'failed' and rejected['error'] == 'model_workshop_state_too_large'
    assert not (service.paths.root / other['target']).exists()
    assert service.recover()[first['task_id']] == 'completed'


def test_root_redirection_is_not_a_missing_model_status(service, tmp_path):
    path, _ = bundle_path(tmp_path)
    record = install(service, path)['result']
    root = service.paths.root; saved = root.with_name('original-runtime')
    # Windows prevents renaming a tree while its writer lock file is open.
    service.close()
    root.rename(saved)
    try:
        try: root.symlink_to(saved, target_is_directory=True)
        except (OSError, NotImplementedError): pytest.skip('directory symlink creation is unavailable')
        with pytest.raises(WorkshopError): service.installer.registry.status(record)
    finally:
        if root.is_symlink(): root.unlink()
        saved.rename(root)
