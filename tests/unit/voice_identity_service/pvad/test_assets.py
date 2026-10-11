import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import threading

import httpx
import pytest

pytestmark = pytest.mark.runtime

from main_logic.voice_identity_service.pvad import assets


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
async def test_close_is_bounded_while_download_native_io_is_blocked(
    monkeypatch, tmp_path, small_bundle,
):
    entered, release = threading.Event(), threading.Event()
    original_mkdir = Path.mkdir

    def blocked_mkdir(path, *args, **kwargs):
        if path.name.startswith(".ecapa-"):
            entered.set()
            assert release.wait(2)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", blocked_mkdir)
    download = downloader(tmp_path, lambda request: httpx.Response(
        200, content=small_bundle[request.url.path[1:]]))
    download.start()
    assert await asyncio.to_thread(entered.wait, 1)

    await asyncio.wait_for(download.close(timeout=0.01), 0.5)
    assert not download._task.done()
    assert download.snapshot() is None

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await download._task
    assert not list(tmp_path.glob(".ecapa-*"))


@pytest.mark.asyncio
async def test_initialize_cancellation_keeps_verifier_owned_until_it_returns(
    monkeypatch, tmp_path,
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked_verify(_directory):
        entered.set()
        assert release.wait(2)
        finished.set()
        raise ValueError("missing")

    monkeypatch.setattr(assets, "_verify_bundle", blocked_verify)
    download = downloader(tmp_path, lambda request: pytest.fail("unexpected download"))
    startup = asyncio.create_task(download.initialize())
    assert await asyncio.to_thread(entered.wait, 1)

    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup
    assert download._task is not None and not download._task.done()
    assert not finished.is_set()

    await asyncio.wait_for(download.close(timeout=0.01), 0.5)
    assert not finished.is_set()

    release.set()
    await download._task
    assert finished.is_set()


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


@pytest.mark.asyncio
async def test_public_retirement_status_for_idle_and_ready_resources(tmp_path, small_bundle):
    download = downloader(tmp_path, lambda request: httpx.Response(
        200, content=small_bundle[request.url.path[1:]]))
    assert not download.busy and not download.confirmed_stopped
    await finish(download)
    assert download.ready and not download.busy
    assert not download.status()["confirmed_stopped"]
    assert await download.close() is True
    assert download.confirmed_stopped and not download.status()["busy"]
    assert download.snapshot() is None
    assert await download.close(timeout=0) is True
    with pytest.raises(RuntimeError, match="download_closed"):
        download.start()

    idle = assets.EcapaDownload(tmp_path)
    assert await idle.close(timeout=0) is True
    assert idle.status()["confirmed_stopped"]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["initialize", "mkdir", "cleanup"])
async def test_public_retirement_status_tracks_native_completion(
    monkeypatch, tmp_path, small_bundle, boundary,
):
    entered, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    close_waiting = asyncio.Event()
    original_wait = asyncio.wait

    async def observed_wait(*args, **kwargs):
        close_waiting.set()
        return await original_wait(*args, **kwargs)

    def block():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)

    if boundary == "initialize":
        def blocked_verify(directory):
            block()
            return assets.EcapaModelSnapshot(directory)
        monkeypatch.setattr(assets, "_verify_bundle", blocked_verify)
    elif boundary == "mkdir":
        original_mkdir = Path.mkdir
        def blocked_mkdir(path, *args, **kwargs):
            if path.name.startswith(".ecapa-"):
                block()
            return original_mkdir(path, *args, **kwargs)
        monkeypatch.setattr(Path, "mkdir", blocked_mkdir)
    else:
        original_cleanup = assets._discard_staging
        def blocked_cleanup(*args):
            block()
            return original_cleanup(*args)
        monkeypatch.setattr(assets, "_discard_staging", blocked_cleanup)

    download = downloader(tmp_path, lambda request: httpx.Response(
        200, content=small_bundle[request.url.path[1:]]))
    startup = asyncio.create_task(download.initialize()) if boundary == "initialize" else None
    if startup is None:
        download.start()
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert download.status()["busy"] and not download.confirmed_stopped
        assert await asyncio.wait_for(download.close(timeout=0), 1) is False
        assert download.busy and not download.status()["confirmed_stopped"]
        assert download.snapshot() is None

        # Cancel the close waiter at its actual wait boundary, then repeat close.
        monkeypatch.setattr(asyncio, "wait", observed_wait)
        closing = asyncio.create_task(download.close(timeout=5))
        await asyncio.wait_for(close_waiting.wait(), 1)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert download.busy and not download.confirmed_stopped
        assert await download.close(timeout=0) is False
    finally:
        release.set()
        assert await asyncio.wait_for(download.close(timeout=5), 6) is True
        if startup is not None:
            await asyncio.gather(startup, return_exceptions=True)
    assert download.confirmed_stopped and not download.busy
    assert download.status()["confirmed_stopped"]
    assert download.snapshot() is None
    assert not list(tmp_path.glob(".ecapa-*"))
