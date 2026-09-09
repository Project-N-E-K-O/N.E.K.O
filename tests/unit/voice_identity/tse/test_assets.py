"""Transaction behavior using real ZIP/hash/I/O and isolated tiny model fixtures."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import threading
import zipfile

import httpx
import pytest

from main_logic.voice_identity.tse import assets


def hashed(data: bytes) -> dict:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def zip_bytes(files: dict[str, bytes], *, mutate=None) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            if mutate:
                mutate(info)
            archive.writestr(info, data)
    return target.getvalue()


@pytest.fixture
def bundle():
    manifest = copy.deepcopy(assets.RELEASE_MANIFEST)
    metadata = {key: manifest[key] for key in (
        "resource_revision", "model_revision", "preprocessing_revision", "reference_method",
    )}
    files = {"bundle.json": json.dumps(metadata).encode(), "tiny.onnx": b"a reviewed tiny fixture"}
    data = zip_bytes(files)
    manifest["files"] = {name: hashed(value) for name, value in files.items()}
    manifest["archive"] = hashed(data)
    manifest["onnx"] = {}
    return manifest, files, data


def service(tmp_path, bundle, **kwargs):
    manifest, _, _ = bundle
    return assets.TseAssets(tmp_path / "models", _manifest=manifest,
                            _validate_models=kwargs.pop("validator", lambda *_: None), **kwargs)


async def chunks(data: bytes, size: int = 17):
    for offset in range(0, len(data), size):
        yield data[offset:offset + size]


async def wait_finished(manager):
    if manager._task:
        await manager._task
    return manager.status()


@pytest.mark.asyncio
async def test_import_publishes_whole_bundle_and_restart_verifies_disk(tmp_path, bundle):
    manifest, files, data = bundle
    manager = service(tmp_path, bundle)
    await manager.initialize()
    assert manager.snapshot() is None
    result = await manager.import_stream(chunks(data))
    assert result["state"] == "ready"
    assert result["downloaded_bytes"] == len(data)
    snapshot = manager.snapshot()
    assert snapshot.resource_revision == manifest["resource_revision"]
    assert snapshot.model_identity.model_revision == manifest["model_revision"]
    assert {path.name: path.read_bytes() for path in snapshot.directory.iterdir()} == files
    assert not list(manager.directory.glob(".tse-*"))
    await manager.close()
    restarted = service(tmp_path, bundle)
    await restarted.initialize()
    assert restarted.ready
    await restarted.close()


@pytest.mark.asyncio
async def test_unknown_source_is_explicit_but_local_import_works(tmp_path, bundle):
    manager = service(tmp_path, bundle)
    assert manager.status()["source_configured"] is False
    with pytest.raises(assets.TseAssetError, match="tse_source_unconfigured"):
        manager.start()
    assert (await manager.import_stream(chunks(bundle[2])))["installed"] is True
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["short", "long", "changed", "html"])
async def test_bad_archive_cannot_become_installed(tmp_path, bundle, corruption):
    data = bundle[2]
    bad = {"short": data[:-1], "long": data + b"x", "changed": b"X" + data[1:],
           "html": b"<html>login</html>"}[corruption]
    manager = service(tmp_path, bundle)
    result = await manager.import_stream(chunks(bad))
    assert result["state"] == "failed"
    assert result["error_code"] == "tse_integrity_error"
    assert manager.snapshot() is None
    assert not (manager.directory / bundle[0]["resource_revision"]).exists()
    assert not list(manager.directory.glob(".tse-*"))
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["path", "link", "extra", "duplicate", "expanded_size", "inner_hash", "version"])
async def test_even_trusted_archive_digest_does_not_bypass_member_contract(tmp_path, bundle, kind):
    manifest, files, data = bundle
    if kind == "path":
        files["../escape"] = b"escape"
        manifest["files"]["../escape"] = hashed(b"escape")
        data = zip_bytes(files)
    elif kind == "link":
        data = zip_bytes(files, mutate=lambda info: setattr(info, "external_attr", (stat.S_IFLNK | 0o777) << 16))
    elif kind == "extra":
        data = zip_bytes({**files, "extra": b"unexpected"})
    elif kind == "duplicate":
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for name, value in files.items():
                archive.writestr(name, value)
            with pytest.warns(UserWarning):
                archive.writestr("tiny.onnx", b"duplicate")
        data = out.getvalue()
    elif kind == "expanded_size":
        data = zip_bytes({**files, "tiny.onnx": b"x" * 10000})
    elif kind == "inner_hash":
        data = zip_bytes({**files, "tiny.onnx": b"z" * len(files["tiny.onnx"])})
    elif kind == "version":
        metadata = json.loads(files["bundle.json"])
        metadata["resource_revision"] = "old-version"
        files["bundle.json"] = json.dumps(metadata).encode()
        manifest["files"]["bundle.json"] = hashed(files["bundle.json"])
        data = zip_bytes(files)
    manifest["archive"] = hashed(data)
    manager = service(tmp_path, bundle)
    result = await manager.import_stream(chunks(data))
    assert result["state"] == "failed"
    assert result["error_code"] in {"tse_invalid_archive", "tse_integrity_error", "tse_model_contract_error"}
    assert not manager.ready
    assert not (tmp_path / "escape").exists()
    await manager.close()


@pytest.mark.asyncio
async def test_failed_reimport_preserves_existing_snapshot_and_files(tmp_path, bundle):
    manager = service(tmp_path, bundle)
    await manager.import_stream(chunks(bundle[2]))
    old = manager.snapshot()
    before = {path.name: path.read_bytes() for path in old.directory.iterdir()}
    result = await manager.import_stream(chunks(b"bad"))
    assert result["state"] == "failed"
    assert result["installed"] is True
    assert manager.snapshot() == old
    assert {path.name: path.read_bytes() for path in old.directory.iterdir()} == before
    await manager.close()


@pytest.mark.asyncio
async def test_damaged_installed_version_is_repairable(tmp_path, bundle):
    manager = service(tmp_path, bundle)
    await manager.import_stream(chunks(bundle[2]))
    directory = manager.snapshot().directory
    await manager.close()
    (directory / "tiny.onnx").write_bytes(b"bad")
    restarted = service(tmp_path, bundle)
    await restarted.initialize()
    assert not restarted.ready
    await restarted.import_stream(chunks(bundle[2]))
    assert restarted.ready
    assert (directory / "tiny.onnx").read_bytes() == bundle[1]["tiny.onnx"]
    await restarted.close()


@pytest.mark.asyncio
async def test_concurrent_operations_share_download_and_reject_second_upload(tmp_path, bundle):
    manager = service(tmp_path, bundle)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        started.set()
        await release.wait()
        yield bundle[2]

    request = asyncio.create_task(manager.import_stream(delayed()))
    await started.wait()
    task = manager._task
    assert manager.start()["busy"]
    assert manager._task is task
    with pytest.raises(assets.TseAssetError, match="tse_assets_busy"):
        await manager.import_stream(chunks(bundle[2]))
    release.set()
    await request
    assert manager.ready
    await manager.close()


@pytest.mark.asyncio
async def test_disconnect_retires_import_and_retry_owns_fresh_staging(tmp_path, bundle):
    manager = service(tmp_path, bundle)

    async def disconnected():
        yield bundle[2][:10]
        raise ConnectionError("client disconnected")

    assert (await manager.import_stream(disconnected()))["state"] == "failed"
    assert not manager.busy
    assert not list(manager.directory.glob(".tse-*"))
    assert (await manager.import_stream(chunks(bundle[2])))["state"] == "ready"
    await manager.close()


@pytest.mark.asyncio
async def test_disk_full_cleans_own_stage_and_can_retry(tmp_path, bundle, monkeypatch):
    manager = service(tmp_path, bundle)
    original = assets._extract_archive

    def disk_full(*_):
        raise OSError(28, "No space left")

    monkeypatch.setattr(assets, "_extract_archive", disk_full)
    result = await manager.import_stream(chunks(bundle[2]))
    assert result["state"] == "failed"
    assert not manager.ready
    assert not list(manager.directory.glob(".tse-*"))
    monkeypatch.setattr(assets, "_extract_archive", original)
    assert (await manager.import_stream(chunks(bundle[2])))["state"] == "ready"
    await manager.close()


@pytest.mark.asyncio
async def test_publication_failure_rolls_back_retired_directory(tmp_path, bundle, monkeypatch):
    manager = service(tmp_path, bundle)
    await manager.import_stream(chunks(bundle[2]))
    directory = manager.snapshot().directory
    await manager.close()
    (directory / "tiny.onnx").write_bytes(b"damaged old version")
    original = assets.os.replace

    def failing_replace(source, target):
        if Path(source).name == "bundle":
            raise OSError("publication failure")
        return original(source, target)

    monkeypatch.setattr(assets.os, "replace", failing_replace)
    restarted = service(tmp_path, bundle)
    assert (await restarted.import_stream(chunks(bundle[2])))["state"] == "failed"
    assert (directory / "tiny.onnx").read_bytes() == b"damaged old version"
    await restarted.close()


@pytest.mark.asyncio
async def test_close_is_bounded_and_late_native_result_never_publishes(tmp_path, bundle):
    entered, release = threading.Event(), threading.Event()

    def blocked_validation(*_):
        entered.set()
        assert release.wait(10)

    manager = service(tmp_path, bundle, validator=blocked_validation)
    importing = asyncio.create_task(manager.import_stream(chunks(bundle[2])))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        await asyncio.wait_for(manager.close(timeout=0.02), 0.5)
        assert not manager.ready
        with pytest.raises(assets.TseAssetError, match="tse_assets_closed"):
            manager.start()
        assert not (manager.directory / bundle[0]["resource_revision"]).exists()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await importing
    assert not (manager.directory / bundle[0]["resource_revision"]).exists()
    assert not list(manager.directory.glob(".tse-*"))


@pytest.mark.asyncio
async def test_canceling_upload_does_not_cancel_other_operation_cleanup(tmp_path, bundle):
    manager = service(tmp_path, bundle)
    entered = asyncio.Event()

    async def never_complete():
        yield bundle[2][:10]
        entered.set()
        await asyncio.Event().wait()

    request = asyncio.create_task(manager.import_stream(never_complete()))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    with pytest.raises(asyncio.CancelledError):
        await manager._task
    assert not manager.ready
    assert not list(manager.directory.glob(".tse-*"))
    assert (await manager.import_stream(chunks(bundle[2])))["state"] == "ready"
    await manager.close()


def configured(manifest):
    manifest["source"] = {"provider": "modelscope", "revision": "v1.0.0",
                          "url": "https://modelscope.cn/models/neko/test/resolve/v1.0.0/model.zip"}


@pytest.mark.asyncio
async def test_download_follows_fresh_https_redirect_and_verifies_file(tmp_path, bundle):
    configured(bundle[0])
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "modelscope.cn":
            return httpx.Response(302, headers={"location": "https://cdn.example.org/ephemeral?sig=abc"})
        return httpx.Response(200, content=bundle[2], headers={"content-type": "application/zip"})

    manager = service(tmp_path, bundle, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    manager.start()
    task = manager._task
    manager.start()
    assert manager._task is task
    assert (await wait_finished(manager))["state"] == "ready"
    assert len(calls) == 2
    assert "ephemeral" not in manager._url
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["http_redirect", "html", "disconnect", "length", "timeout"])
async def test_download_failures_are_visible_and_never_publish(tmp_path, bundle, fault):
    configured(bundle[0])

    def handler(request):
        if fault == "http_redirect":
            return httpx.Response(302, headers={"location": "http://cdn.example.org/model.zip"})
        if fault == "html":
            return httpx.Response(200, content=bundle[2], headers={"content-type": "text/html"})
        if fault == "disconnect":
            raise httpx.ConnectError("offline", request=request)
        if fault == "timeout":
            raise httpx.ReadTimeout("idle", request=request)
        return httpx.Response(200, content=bundle[2], headers={"content-length": "2"})

    manager = service(tmp_path, bundle, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    manager.start()
    result = await wait_finished(manager)
    assert result["state"] == "failed"
    assert result["error_code"]
    assert not manager.ready
    await manager.close()


@pytest.mark.parametrize("revision", ["main", "master", "latest", ""])
def test_source_cannot_use_a_mutable_default_revision(tmp_path, bundle, revision):
    configured(bundle[0])
    bundle[0]["source"]["revision"] = revision
    with pytest.raises(assets.TseAssetError, match="tse_source_invalid"):
        service(tmp_path, bundle)


def test_model_shape_validation_rejects_wrong_io_types_and_dimensions():
    class Node:
        type = "tensor(float)"
        shape = [1, 192]

    assets._assert_shape(Node(), [1, 192])
    with pytest.raises(assets.TseAssetError, match="tse_model_contract_error"):
        assets._assert_shape(Node(), [1, 256])
    with pytest.raises(assets.TseAssetError, match="tse_model_contract_error"):
        assets._assert_shape(Node(), [1, "frames"])
    Node.type = "tensor(double)"
    with pytest.raises(assets.TseAssetError, match="tse_model_contract_error"):
        assets._assert_shape(Node(), [1, 192])


@pytest.mark.asyncio
async def test_initialization_serializes_native_validation_and_shutdown(tmp_path, bundle):
    manager = service(tmp_path, bundle)
    await manager.import_stream(chunks(bundle[2]))
    await manager.close()
    entered, release = threading.Event(), threading.Event()

    def waiting(*_):
        entered.set()
        assert release.wait(10)

    manager = service(tmp_path, bundle, validator=waiting)
    startup = asyncio.create_task(manager.initialize())
    assert await asyncio.to_thread(entered.wait, 5)
    assert manager.busy
    task = manager._task
    assert manager.start()["state"] == "verifying"
    assert manager._task is task
    try:
        await asyncio.wait_for(manager.close(timeout=0.01), 0.5)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await startup
    assert not manager.ready


@pytest.mark.asyncio
async def test_total_timeout_retires_native_publication_before_native_call_returns(tmp_path, bundle, monkeypatch):
    entered, expired, release = threading.Event(), threading.Event(), threading.Event()
    original = assets._publish

    def delayed(directory, root, manifest, stop, validate):
        entered.set()
        if stop.wait(5):
            expired.set()
        assert release.wait(5)
        return original(directory, root, manifest, stop, validate)

    monkeypatch.setattr(assets, "TOTAL_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(assets, "_publish", delayed)
    manager = service(tmp_path, bundle)
    request = asyncio.create_task(manager.import_stream(chunks(bundle[2], size=len(bundle[2]))))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        # The event must fire while _owned_io is still shielding the native call.
        assert await asyncio.to_thread(expired.wait, 5)
        assert not request.done()
        assert not (manager.directory / bundle[0]["resource_revision"]).exists()
    finally:
        release.set()
    result = await request
    assert result["state"] == "failed"
    assert result["error_code"] == "tse_timeout"
    assert not manager.ready
    assert not (manager.directory / bundle[0]["resource_revision"]).exists()
    assert not list(manager.directory.glob(".tse-*"))
    await manager.close()
