"""Shutdown reporting keeps local data and retry identity without blocking the loop."""

import asyncio
import atexit
import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest


@pytest.fixture
def tracker_factory(tmp_path, monkeypatch):
    from utils import token_tracker
    from utils.token_tracker import reporting

    monkeypatch.setattr(token_tracker, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", False)
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(reporting, "_get_legacy_device_id", lambda: "test-device")
    monkeypatch.setattr(reporting, "_get_app_version_from_changelog", lambda: "test")
    monkeypatch.setattr(reporting, "_get_telemetry_metadata", lambda: ("source", ""))
    monkeypatch.setattr(reporting, "_get_device_hw", lambda: "win|x86_64|unknown|unknown")
    def create():
        tracker = token_tracker.TokenTracker()
        tracker._device_id = "test-device"
        tracker._branch = "test"
        tracker._report_interval = 0
        atexit.unregister(tracker._atexit_save)
        return tracker

    return create


@pytest.mark.parametrize("report_only", [False, True])
@pytest.mark.asyncio
async def test_short_shutdown_report_preserves_disk_and_retry_batch(tracker_factory, monkeypatch, report_only):
    from utils.token_tracker import reporting

    tracker = tracker_factory()
    tracker.record("test-model", 2, 1, 3)
    if report_only:
        tracker._report_interval = float("inf")
        tracker.save()
        tracker._report_interval = 0
        assert not tracker._dirty

    attempts = []

    def collect_attempt(request, timeout):
        body = request.data
        if request.get_header("Content-encoding") == "gzip":
            body = gzip.decompress(body)
        attempts.append((timeout, json.loads(body)))

    def offline(request, *, timeout):
        collect_attempt(request, timeout)
        raise TimeoutError("controlled offline endpoint")

    monkeypatch.setattr(reporting.urllib.request, "urlopen", offline)
    await tracker.asave(report_timeout=1.0)
    assert attempts[0][0] == 1.0
    persisted = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    queued = json.loads(tracker._unsent_queue_path.read_text(encoding="utf-8"))
    assert persisted["recent_records"][0]["tt"] == 3
    assert queued["records"][0]["tt"] == 3
    restored = tracker_factory()
    assert restored._pending_batch_seq == tracker._pending_batch_seq

    from contextlib import contextmanager

    @contextmanager
    def successful(request, *, timeout):
        collect_attempt(request, timeout)
        yield SimpleNamespace(status=200)

    monkeypatch.setattr(reporting.urllib.request, "urlopen", successful)
    restored.save()
    assert attempts[-1][0] == reporting._TELEMETRY_TIMEOUT
    assert attempts[-1][1]["batch_id"] == attempts[0][1]["batch_id"]
    assert not restored._unsent_queue_path.exists()
    assert json.loads(restored._storage_path.read_text(encoding="utf-8")) == persisted


@pytest.mark.asyncio
async def test_async_save_does_not_block_on_a_real_slow_http_endpoint(tracker_factory, monkeypatch):
    from utils.token_tracker import reporting

    entered, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            release.wait(3)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", f"http://127.0.0.1:{server.server_port}")
    tracker = tracker_factory()
    tracker.record("test-model", 2, 1, 3)
    task = asyncio.create_task(tracker.asave(report_timeout=.2))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
        assert not task.done(), "save blocked the event loop until the HTTP timeout"
        await asyncio.wait_for(task, 2)
        assert tracker._storage_path.exists()
        assert tracker._unsent_queue_path.exists()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(thread.join, 2)


@pytest.mark.asyncio
async def test_final_save_runs_when_default_executor_is_full(tracker_factory, monkeypatch):
    from utils.token_tracker import reporting

    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    tracker = tracker_factory()
    tracker.record("test-model", 2, 1, 3)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: None)
    count = loop._default_executor._max_workers
    entered = threading.Barrier(count + 1)
    release = threading.Event()

    def work():
        entered.wait(5)
        release.wait(5)

    workers = [loop.run_in_executor(None, work) for _ in range(count)]
    try:
        entered.wait(5)
        await asyncio.wait_for(tracker.asave(report_timeout=1), 2)
        assert not release.is_set(), "the default pool must remain occupied"
        assert json.loads(tracker._storage_path.read_text())["recent_records"][0]["tt"] == 3
    finally:
        release.set()
        await asyncio.gather(*workers)


@pytest.mark.asyncio
async def test_cancelled_wait_does_not_cancel_a_queued_save(tracker_factory, monkeypatch):
    from utils.token_tracker import reporting

    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    tracker = tracker_factory()
    tracker.record("test-model", 2, 1, 3)
    release = threading.Event()
    blocker = reporting._SAVE_EXECUTOR.submit(release.wait, 5)
    submitted = []
    original_submit = tracker.submit_save

    def submit(**kwargs):
        future = original_submit(**kwargs)
        submitted.append(future)
        return future

    monkeypatch.setattr(tracker, "submit_save", submit)
    task = asyncio.create_task(tracker.asave(report_timeout=1))
    try:
        async with asyncio.timeout(2):
            while not submitted:
                await asyncio.sleep(0)
        task.cancel("stop waiting")
        with pytest.raises(asyncio.CancelledError, match="stop waiting"):
            await task
        assert not submitted[0].cancelled()
        assert not tracker._storage_path.exists()
    finally:
        release.set()
        await asyncio.wrap_future(blocker)
        await asyncio.gather(task, return_exceptions=True)
        if submitted:
            await asyncio.gather(asyncio.wrap_future(submitted[0]), return_exceptions=True)
    assert json.loads(tracker._storage_path.read_text())["recent_records"][0]["tt"] == 3
