import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import threading

import httpx
import pytest

from main_logic.voice_identity.pvad import assets


@pytest.fixture
def small_bundle(monkeypatch):
    payloads = {"network.bin": b"model", "bank.bin": b"filterbank"}
    records = tuple(assets.Asset(name, len(data), hashlib.sha256(data).hexdigest(),
                                "https://models.test/" + name) for name, data in payloads.items())
    monkeypatch.setattr(assets, "ECAPA_ASSETS", records)
    return payloads


def downloader(root, handler):
    return assets.EcapaDownload(root, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True))


async def finish(download):
    download.start()
    await download._task


@pytest.mark.asyncio
async def test_download_and_restart_verify_bytes_without_loading_enrollment_model(
    monkeypatch, tmp_path, small_bundle,
):
    import onnxruntime as ort

    monkeypatch.setattr(ort, "InferenceSession", lambda *args, **kwargs: pytest.fail(
        "ECAPA may only load during enrollment extraction"))
    download = downloader(tmp_path, lambda request: httpx.Response(
        200, content=small_bundle[request.url.path[1:]]))
    await finish(download)
    assert download.ready
    await download.close()
    restarted = downloader(tmp_path, lambda request: pytest.fail("unexpected download"))
    await restarted.initialize()
    assert restarted.ready and restarted.snapshot() is not None
    await restarted.close()


@pytest.mark.asyncio
async def test_bundle_publishes_atomically_snapshot_is_frozen_and_status_never_reads_disk(
    monkeypatch, tmp_path, small_bundle,
):
    calls = []
    def response(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=small_bundle[request.url.path[1:]])
    download = downloader(tmp_path, response)
    await download.initialize()
    assert download.snapshot() is None
    download.start()
    task = download._task
    download.start()
    assert download._task is task
    await task
    snapshot = download.snapshot()
    assert snapshot is not None and snapshot.directory.name == assets.ECAPA_RESOURCE_REVISION
    assert len(calls) == 2
    assert not list(tmp_path.glob(".ecapa-*"))
    assert assets._verify_bundle(snapshot.directory) == snapshot
    with pytest.raises(FrozenInstanceError):
        snapshot.directory = tmp_path
    monkeypatch.setattr(assets, "_verify_bundle", lambda directory: pytest.fail("status did filesystem IO"))
    for _ in range(3):
        assert download.status()["state"] == "ready"
        assert download.snapshot() is snapshot
    await download.close()
    assert download.snapshot() is None
    assert snapshot.directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["short", "long", "checksum", "http", "disconnect"])
async def test_download_failures_never_publish_partial_bundle_and_retry_starts_fresh(
    tmp_path, small_bundle, mode,
):
    failing = True
    def response(request):
        data = small_bundle[request.url.path[1:]]
        if failing:
            if mode == "http":
                return httpx.Response(503)
            if mode == "disconnect":
                raise httpx.ReadError("disconnected")
            data = {"short": data[:-1], "long": data + b"x", "checksum": b"x" * len(data)}[mode]
        return httpx.Response(200, content=data)
    download = downloader(tmp_path, response)
    await finish(download)
    assert download.status()["state"] == "failed"
    assert download.snapshot() is None
    assert not list(tmp_path.iterdir())
    failing = False
    await finish(download)
    assert download.ready
    await download.close()


@pytest.mark.asyncio
async def test_https_cdn_redirect_works_but_http_downgrade_is_never_requested(tmp_path, small_bundle):
    requested = []
    insecure = True
    def response(request):
        requested.append(str(request.url))
        if request.url.host == "models.test":
            scheme = "http" if insecure else "https"
            return httpx.Response(302, headers={"location": scheme + "://cdn.test" + request.url.path})
        return httpx.Response(200, content=small_bundle[request.url.path[1:]])
    download = downloader(tmp_path, response)
    await finish(download)
    assert download.status()["state"] == "failed"
    assert len(requested) == 1 and requested[0].startswith("https://")
    insecure = False
    await finish(download)
    assert download.ready
    await download.close()


@pytest.mark.asyncio
async def test_cancel_stream_cleans_staging_without_publishing(tmp_path, small_bundle):
    entered = asyncio.Event()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"m"
            entered.set()
            await asyncio.Event().wait()
    download = downloader(tmp_path, lambda request: httpx.Response(200, stream=Stream()))
    download.start()
    await asyncio.wait_for(entered.wait(), 1)
    await download.close()
    assert download.snapshot() is None
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["open", "write", "verify", "replace"])
async def test_cancel_joins_owned_io_before_cleaning_its_path(
    monkeypatch, tmp_path, small_bundle, boundary,
):
    entered, release = threading.Event(), threading.Event()
    handles = []
    if boundary in {"open", "write"}:
        original = Path.open
        class DelayedWriter:
            def __init__(self, handle):
                self.handle = handle

            def write(self, data):
                entered.set()
                assert release.wait(2)
                return self.handle.write(data)

            def close(self):
                self.handle.close()
        def blocked_open(path, *args, **kwargs):
            handle = original(path, *args, **kwargs)
            if args == ("xb",):
                handles.append(handle)
                if boundary == "open":
                    entered.set()
                    assert release.wait(2)
                else:
                    return DelayedWriter(handle)
            return handle
        monkeypatch.setattr(Path, "open", blocked_open)
    elif boundary == "verify":
        original = assets.verify_asset
        def blocked_verify(directory, asset):
            entered.set()
            assert release.wait(2)
            return original(directory, asset)
        monkeypatch.setattr(assets, "verify_asset", blocked_verify)
    else:
        original = assets.os.replace
        def blocked_replace(source, target):
            entered.set()
            assert release.wait(2)
            return original(source, target)
        monkeypatch.setattr(assets.os, "replace", blocked_replace)
    download = downloader(tmp_path, lambda request: httpx.Response(
        200, content=small_bundle[request.url.path[1:]]))
    download.start()
    assert await asyncio.to_thread(entered.wait, 1)
    close = asyncio.create_task(download.close())
    await asyncio.sleep(0.01)
    assert not close.done()
    download._task.cancel()  # A second cancellation must still join native IO.
    await asyncio.sleep(0.01)
    assert not close.done()
    release.set()
    await asyncio.wait_for(close, 2)
    assert download.snapshot() is None
    assert all(handle.closed for handle in handles)
    assert not list(tmp_path.glob(".ecapa-*"))
    if boundary == "replace":
        restarted = downloader(tmp_path, lambda request: pytest.fail("restart downloaded"))
        await restarted.initialize()
        assert restarted.ready  # Complete directory publication survived cancellation.
        await restarted.close()
    else:
        assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_invalid_existing_bundle_is_not_ready(tmp_path, small_bundle):
    destination = tmp_path / assets.ECAPA_RESOURCE_REVISION
    destination.mkdir()
    (destination / "bundle.json").write_text(json.dumps(assets._bundle_manifest()))
    for name, data in small_bundle.items():
        (destination / name).write_bytes(data)
    (destination / "bank.bin").write_bytes(b"bad")
    download = downloader(tmp_path, lambda request: httpx.Response(
        200, content=small_bundle[request.url.path[1:]]))
    await download.initialize()
    assert not download.ready and download.snapshot() is None
    await finish(download)
    assert download.ready
    assert assets._verify_bundle(destination) == download.snapshot()
    assert not list(tmp_path.glob(".ecapa-*"))
    await download.close()


def test_staging_cleanup_rejects_other_directories(tmp_path):
    outside = tmp_path / "unrelated"
    outside.mkdir()
    with pytest.raises(ValueError, match="staging"):
        assets._discard_staging(tmp_path, outside)
    assert outside.exists()
