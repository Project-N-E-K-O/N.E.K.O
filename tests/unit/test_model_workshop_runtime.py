"""N3 read isolation and existing-delete coordination, with real N2 transactions."""

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


@pytest.fixture
def owned(service, monkeypatch):
    monkeypatch.setattr(runtime, "_services", {str(service.paths.root): service})
    monkeypatch.setattr(runtime, "_stopped", False)
    return service


def test_read_snapshot_does_not_initialize_workshop(config):
    state = runtime.snapshot(config)
    assert state.records == () and not state.blocked
    assert list(config.app_docs_dir.iterdir()) == []


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
