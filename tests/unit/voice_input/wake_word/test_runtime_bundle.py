"""Prebuilt delivery validates bytes and ownership before atomic publication."""

import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest

from main_logic.voice_identity_service import runtime_installation as bundle
from main_logic.voice_input.wake_word import runtime_bundle


@pytest.fixture
def artifact(monkeypatch, tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("sherpa_onnx/__init__.py", "# trusted fixture")
        archive.writestr("sherpa_onnx/lib/_sherpa_onnx.cp311-win_amd64.pyd", b"native fixture")
        archive.writestr("sherpa_onnx/lib/onnxruntime.dll", b"dll fixture")
        archive.writestr("sherpa_onnx-1.13.8.dist-info/METADATA", "Version: 1.13.8+neko.kws2")
    raw = stream.getvalue()
    monkeypatch.setattr(bundle, "WHEEL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(bundle, "WHEEL_BYTES", len(raw))
    monkeypatch.setattr(runtime_bundle, "WHEEL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(runtime_bundle, "WHEEL_BYTES", len(raw))
    monkeypatch.setattr(bundle, "runtime_repair_supported", lambda: True)
    monkeypatch.setattr(bundle.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(raw))
    monkeypatch.setattr(bundle, "_load_directory", lambda path: None)
    return tmp_path / "runtime", raw


def test_download_stage_has_real_progress_but_no_publication(artifact):
    root, raw = artifact
    progress = []
    staged = bundle.stage_runtime(root=root, on_progress=progress.append)
    assert staged["installed"] is False
    assert not bundle.runtime_present(root)
    assert not (root / "current.json").exists()
    transfer = [item for item in progress if item["phase"] == "downloading"]
    assert transfer[0]["downloaded_bytes"] == 0
    assert transfer[-1]["downloaded_bytes"] == transfer[-1]["total_bytes"] == len(raw)
    assert progress[-1] == {"phase": "loading", "resource": "wake_runtime"}
    bundle.publish_runtime(root, staged["ticket"])
    assert bundle.runtime_present(root)
    assert bundle.runtime_version_published(root, staged["ticket"])
    current = bundle._current(root)
    bundle._verify_directory(root, current)
    assert not (current / "sherpa_onnx-1.13.8.dist-info").exists()


def test_verified_install_reuses_wheel_without_downloading(artifact, monkeypatch):
    root, _ = artifact
    staged = bundle.stage_runtime(root=root)
    bundle.publish_runtime(root, staged["ticket"])
    def forbidden(*args, **kwargs):
        pytest.fail("Verified installation must not redownload")
    monkeypatch.setattr(bundle.urllib.request, "urlopen", forbidden)
    assert bundle.stage_runtime(root=root) == {"installed": True}


@pytest.mark.parametrize("bad", [b"wrong checksum", b"oversized" * 10000, b""], ids=["checksum", "oversize", "empty"])
def test_download_failure_never_publishes(artifact, monkeypatch, bad):
    root, _ = artifact
    monkeypatch.setattr(bundle.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(bad))
    with pytest.raises(bundle.RuntimeBundleError, match="RUNTIME_FIX_REQUIRED"):
        bundle.stage_runtime(root=root)
    assert not bundle.runtime_present(root)
    assert not (root / "current.json").exists()


def test_native_probe_failure_never_publishes_and_can_retry(artifact, monkeypatch):
    root, _ = artifact
    def broken(path):
        raise ImportError("native DLL cannot load")
    monkeypatch.setattr(bundle, "_load_directory", broken)
    with pytest.raises(ImportError):
        bundle.stage_runtime(root=root)
    assert not bundle.runtime_present(root)
    monkeypatch.setattr(bundle, "_load_directory", lambda path: None)
    staged = bundle.stage_runtime(root=root)
    bundle.publish_runtime(root, staged["ticket"])
    assert bundle.runtime_present(root)


def test_damaged_generation_is_preserved_and_repaired_in_new_generation(artifact):
    root, _ = artifact
    first = bundle.stage_runtime(root=root)
    bundle.publish_runtime(root, first["ticket"])
    old = bundle._current(root)
    damaged = old / "sherpa_onnx/__init__.py"
    damaged.write_text("damaged")
    second = bundle.stage_runtime(root=root)
    assert bundle._current(root) == old
    bundle.publish_runtime(root, second["ticket"])
    assert bundle._current(root) != old
    assert damaged.read_text() == "damaged"
    assert not bundle.runtime_version_published(root, first["ticket"])
    bundle._verify_directory(root, bundle._current(root))


def test_stale_staging_ticket_cannot_publish_successor(artifact):
    root, _ = artifact
    first = bundle.stage_runtime(root=root)
    second = bundle.stage_runtime(root=root)
    with pytest.raises(bundle.RuntimeBundleError, match="resource_operation_busy"):
        bundle.publish_runtime(root, first["ticket"])
    assert not bundle.runtime_present(root)
    bundle.publish_runtime(root, second["ticket"])


@pytest.mark.parametrize("value", ["../outside", "wrong", "x" * 600, None, 2, {}, []])
def test_invalid_pointer_is_closed(artifact, value):
    root, _ = artifact
    root.mkdir()
    (root / "current.json").write_text(json.dumps({"version": value}))
    assert not bundle.runtime_present(root)
    assert not bundle.runtime_version_published(root, "0" * 32)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "sherpa_onnx/../outside", "sherpa_onnx/C:escape", "sherpa_onnx\\bad"])
def test_archive_path_traversal_is_rejected(name):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, "bad")
    if "\\" in name:
        stream = io.BytesIO(stream.getvalue().replace(b"sherpa_onnx/bad", b"sherpa_onnx\\bad"))
    with zipfile.ZipFile(stream) as archive, pytest.raises(bundle.RuntimeBundleError, match="resource_cache_unsafe"):
        bundle._inventory(archive)


def test_unknown_staging_file_is_never_deleted(artifact):
    root, _ = artifact
    bundle.stage_runtime(root=root)
    foreign = root / "pending/keep-me.txt"
    foreign.write_text("user data")
    with pytest.raises(bundle.RuntimeBundleError, match="resource_cache_unsafe"):
        bundle.stage_runtime(root=root)
    assert foreign.read_text() == "user data"


def test_capacity_refuses_to_delete_published_versions(artifact):
    root, _ = artifact
    for _ in range(3):
        staged = bundle.stage_runtime(root=root)
        bundle.publish_runtime(root, staged["ticket"])
        (bundle._current(root) / "sherpa_onnx/__init__.py").write_text("broken")
    pointer = (root / "current.json").read_bytes()
    with pytest.raises(bundle.RuntimeBundleError, match="^resource_cache_full$"):
        bundle.stage_runtime(root=root)
    assert len(list(root.glob(bundle.WHEEL_SHA256 + "-*"))) == 3
    assert (root / "current.json").read_bytes() == pointer


def test_capacity_reached_after_staging_preserves_pending_and_current(artifact):
    root, _ = artifact
    first = bundle.stage_runtime(root=root)
    bundle.publish_runtime(root, first["ticket"])
    (bundle._current(root) / "sherpa_onnx/__init__.py").write_text("broken")
    staged = bundle.stage_runtime(root=root)
    # Another owner can consume the remaining capacity before publication.
    for ticket in ("1" * 32, "2" * 32):
        (root / (bundle.WHEEL_SHA256 + "-" + ticket)).mkdir()
    pointer = (root / "current.json").read_bytes()
    with pytest.raises(bundle.RuntimeBundleError, match="^resource_cache_full$"):
        bundle.publish_runtime(root, staged["ticket"])
    assert (root / "current.json").read_bytes() == pointer
    assert (root / "pending").is_dir()
    assert (root / "pending.id").read_text() == staged["ticket"]
    assert len(list(root.glob(bundle.WHEEL_SHA256 + "-*"))) == 3


def test_symlink_staging_is_rejected_before_cleanup(artifact, tmp_path):
    root, _ = artifact
    root.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    keep = foreign / "keep.txt"
    keep.write_text("keep")
    try:
        (root / "pending").symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not grant symlink creation")
    with pytest.raises(bundle.RuntimeBundleError, match="resource_cache_unsafe"):
        bundle.stage_runtime(root=root)
    assert keep.read_text() == "keep"


def test_platform_support_is_explicit(monkeypatch):
    monkeypatch.setattr(runtime_bundle.platform, "system", lambda: "Linux")
    assert bundle.runtime_repair_supported() is False
    with pytest.raises(bundle.RuntimeBundleError, match="PLATFORM_UNSUPPORTED"):
        bundle.stage_runtime()
