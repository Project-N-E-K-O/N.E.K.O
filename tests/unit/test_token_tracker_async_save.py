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


def test_batch_seq_persisted_before_http_request_survives_mid_request_crash(tracker_factory, monkeypatch):
    """F0546: batch_seq must already be on disk when urlopen runs so crashes keep the same batch_id."""
    import hashlib
    from contextlib import contextmanager
    from utils.token_tracker import reporting

    tracker = tracker_factory()
    tracker.record("test-model", 10, 5, 15)

    observed_on_disk_at_urlopen = []
    sent_bodies = []

    class SimulatedCrashError(BaseException):
        pass

    def crash_during_urlopen(request, *, timeout):
        body = request.data
        if request.get_header("Content-encoding") == "gzip":
            body = gzip.decompress(body)
        sent_bodies.append(json.loads(body))
        # Read .telemetry_unsent.json at the exact moment urlopen starts (before returning/raising).
        assert tracker._unsent_queue_path.exists()
        observed_on_disk_at_urlopen.append(
            json.loads(tracker._unsent_queue_path.read_text(encoding="utf-8"))
        )
        raise SimulatedCrashError("SIGKILL simulation")

    monkeypatch.setattr(reporting.urllib.request, "urlopen", crash_during_urlopen)
    with pytest.raises(SimulatedCrashError):
        tracker.save()

    assert len(observed_on_disk_at_urlopen) == 1
    persisted = observed_on_disk_at_urlopen[0]
    persisted_seq = persisted["batch_seq"]
    assert isinstance(persisted_seq, str) and persisted_seq
    expected_batch_id = hashlib.sha256(
        json.dumps(
            {"device_id": "test-device", "batch_seq": persisted_seq},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:32]
    assert sent_bodies[0]["batch_id"] == expected_batch_id
    assert persisted["records"][0]["tt"] == 15

    # Simulate process restart from disk and retry.
    restored = tracker_factory()
    assert restored._pending_batch_seq == persisted_seq

    @contextmanager
    def successful(request, *, timeout):
        body = request.data
        if request.get_header("Content-encoding") == "gzip":
            body = gzip.decompress(body)
        sent_bodies.append(json.loads(body))
        yield SimpleNamespace(status=200)

    monkeypatch.setattr(reporting.urllib.request, "urlopen", successful)
    restored.save()
    assert sent_bodies[1]["batch_id"] == sent_bodies[0]["batch_id"]
    assert not restored._unsent_queue_path.exists()


def test_instrument_only_report_does_not_persist_stale_batch_seq_and_below_interval_keeps_null(
    tracker_factory, monkeypatch
):
    """F0546: instrument-only windows keep unsent file absent and below-interval saves batch_seq=None."""
    from utils import instrument
    from utils.token_tracker import reporting

    tracker = tracker_factory()
    monkeypatch.setattr(instrument, "has_data", lambda: True)
    monkeypatch.setattr(instrument, "snapshot", lambda: {"counters": {"ui_click": 2}})

    def failing_urlopen(request, *, timeout):
        assert not tracker._unsent_queue_path.exists()
        raise TimeoutError("offline")

    monkeypatch.setattr(reporting.urllib.request, "urlopen", failing_urlopen)
    tracker.save()
    assert tracker._pending_batch_seq is None
    assert not tracker._unsent_queue_path.exists()

    # Below-interval write persists daily with batch_seq=None so next window stays fresh.
    tracker._report_interval = float("inf")
    tracker.record("test-model", 1, 1, 2)
    tracker.save()
    queued = json.loads(tracker._unsent_queue_path.read_text(encoding="utf-8"))
    assert queued["batch_seq"] is None


def test_legacy_migration_preserves_source_files_on_atomic_write_failure_and_recovers(
    tracker_factory, monkeypatch
):
    """F0549: legacy token_usage_*.json files must survive failed atomic_write_json and migrate on retry."""
    from utils.token_tracker import storage

    tracker = tracker_factory()
    atexit.unregister(tracker._atexit_save)
    storage_dir = tracker._storage_dir

    legacy_a = storage_dir / "token_usage_proc1.json"
    legacy_b = storage_dir / "token_usage_proc2.json"
    legacy_a.write_text(
        json.dumps({
            "version": 1,
            "daily_stats": {"2026-11-01": {"total_tokens": 10, "call_count": 1}},
            "recent_records": [{"ts": "2026-11-01T00:00:00", "m": "m1", "tt": 10}],
        }),
        encoding="utf-8",
    )
    legacy_b.write_text(
        json.dumps({
            "version": 1,
            "daily_stats": {"2026-11-01": {"total_tokens": 20, "call_count": 2}},
            "recent_records": [{"ts": "2026-11-01T00:01:00", "m": "m1", "tt": 20}],
        }),
        encoding="utf-8",
    )

    real_atomic_write_json = storage.atomic_write_json

    def failing_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "atomic_write_json", failing_write)
    failed_tracker = tracker_factory()
    assert legacy_a.exists()
    assert legacy_b.exists()
    assert not failed_tracker._storage_path.exists()

    monkeypatch.setattr(storage, "atomic_write_json", real_atomic_write_json)
    recovered_tracker = tracker_factory()
    assert not legacy_a.exists()
    assert not legacy_b.exists()
    merged = json.loads(recovered_tracker._storage_path.read_text(encoding="utf-8"))
    assert merged["daily_stats"]["2026-11-01"]["total_tokens"] == 30
    assert merged["daily_stats"]["2026-11-01"]["call_count"] == 3
    assert len(merged["recent_records"]) == 2


def test_concurrent_report_to_server_serializes_batches_and_keeps_record_nonblocking(
    tracker_factory, monkeypatch
):
    """F0546: concurrent reports serialize behind _report_lock without blocking record()."""
    from contextlib import contextmanager
    from datetime import date
    from utils.token_tracker import reporting

    tracker = tracker_factory()
    tracker.record("model-1", 6, 4, 10)

    first_entered = threading.Event()
    release_first = threading.Event()
    sent_payloads = []
    disk_while_first_inflight = []
    call_count = {"n": 0}

    @contextmanager
    def slow_first_then_fast(request, *, timeout):
        body = request.data
        if request.get_header("Content-encoding") == "gzip":
            body = gzip.decompress(body)
        idx = call_count["n"]
        call_count["n"] += 1
        sent_payloads.append(json.loads(body))
        if idx == 0:
            first_entered.set()
            assert release_first.wait(5)
            disk_while_first_inflight.append(
                json.loads(tracker._unsent_queue_path.read_text(encoding="utf-8"))
            )
        yield SimpleNamespace(status=200)

    monkeypatch.setattr(reporting.urllib.request, "urlopen", slow_first_then_fast)

    t1 = threading.Thread(target=tracker.save)
    t1.start()
    assert first_entered.wait(5)

    # record() only uses self._lock and must not block while Thread 1 is mid-urlopen.
    record_done = threading.Event()

    def record_in_flight():
        tracker.record("model-inflight", 1, 1, 2)
        record_done.set()

    t_rec = threading.Thread(target=record_in_flight)
    t_rec.start()
    assert record_done.wait(1.0), "record() blocked on in-flight urlopen"
    t_rec.join(1.0)

    # Start concurrent _report_to_server (Thread 2) while Thread 1 is still inside urlopen.
    today = date.today().isoformat()
    t2_entered = threading.Event()
    t2_done = threading.Event()

    def run_t2():
        t2_entered.set()
        tracker._report_to_server(
            {today: {"total_tokens": 20, "call_count": 1}},
            [{"ts": 2.0, "model": "model-2", "tt": 20}],
        )
        t2_done.set()

    t2 = threading.Thread(target=run_t2)
    t2.start()
    assert t2_entered.wait(5)
    # Give Thread 2 a brief window to attempt entering _report_to_server; it must wait on _report_lock.
    assert not t2_done.wait(0.05)
    assert len(sent_payloads) == 1
    assert json.loads(tracker._unsent_queue_path.read_text(encoding="utf-8"))["records"][0]["model"] == "model-1"

    release_first.set()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive()

    assert len(disk_while_first_inflight) == 1
    assert disk_while_first_inflight[0]["records"][0]["model"] == "model-1"
    assert len(sent_payloads) == 2
    assert sent_payloads[0]["batch_id"] != sent_payloads[1]["batch_id"]
    assert sent_payloads[0]["payload"]["recent_records"][0]["model"] == "model-1"
    assert sent_payloads[1]["payload"]["recent_records"][0]["model"] == "model-2"
    assert not tracker._unsent_queue_path.exists()
    assert tracker._pending_batch_seq is None


def test_concurrent_below_interval_report_does_not_clobber_inflight_unsent_queue(
    tracker_factory, monkeypatch
):
    """F0546: a concurrent below-interval call cannot overwrite an in-flight batch on disk."""
    from datetime import date
    from utils.token_tracker import reporting

    tracker = tracker_factory()
    tracker.record("model-1", 6, 4, 10)

    first_entered = threading.Event()
    release_first = threading.Event()

    def failing_slow_urlopen(request, *, timeout):
        first_entered.set()
        assert release_first.wait(5)
        raise TimeoutError("offline")

    monkeypatch.setattr(reporting.urllib.request, "urlopen", failing_slow_urlopen)

    t1 = threading.Thread(target=tracker.save)
    t1.start()
    assert first_entered.wait(5)

    # Thread 2 arrives while Thread 1 is mid-urlopen, with interval now larger than elapsed time.
    tracker._report_interval = float("inf")
    today = date.today().isoformat()
    t2_done = threading.Event()

    def run_t2():
        tracker._report_to_server(
            {today: {"total_tokens": 20, "call_count": 1}},
            [{"ts": 2.0, "model": "model-2", "tt": 20}],
        )
        t2_done.set()

    t2 = threading.Thread(target=run_t2)
    t2.start()
    assert not t2_done.wait(0.05)

    # While Thread 1 is still in-flight, disk still holds model-1 with its batch_seq.
    disk_mid = json.loads(tracker._unsent_queue_path.read_text(encoding="utf-8"))
    assert disk_mid["daily"][today]["total_tokens"] == 10
    inflight_seq = disk_mid["batch_seq"]
    assert isinstance(inflight_seq, str) and inflight_seq

    release_first.set()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive()

    # After Thread 1 fails and Thread 2 runs its below-interval persistence, both batches are preserved.
    disk_after = json.loads(tracker._unsent_queue_path.read_text(encoding="utf-8"))
    assert disk_after["daily"][today]["total_tokens"] == 30
    assert disk_after["batch_seq"] == inflight_seq


def test_legacy_migration_is_idempotent_across_partial_unlink_permission_errors(
    tracker_factory, monkeypatch
):
    """F0549: partial unlink PermissionError after atomic_write_json must not double-count on restart."""
    from pathlib import Path

    seed = tracker_factory()
    storage_dir = seed._storage_dir

    legacy_1 = storage_dir / "token_usage_1.json"
    legacy_2 = storage_dir / "token_usage_2.json"
    legacy_1.write_text(
        json.dumps({
            "version": 1,
            "daily_stats": {"2026-11-01": {"total_tokens": 100, "call_count": 1}},
            "recent_records": [{"ts": "2026-11-01T00:00:00", "m": "m1", "tt": 100}],
        }),
        encoding="utf-8",
    )
    legacy_2.write_text(
        json.dumps({
            "version": 1,
            "daily_stats": {"2026-11-01": {"total_tokens": 200, "call_count": 2}},
            "recent_records": [{"ts": "2026-11-01T00:01:00", "m": "m1", "tt": 200}],
        }),
        encoding="utf-8",
    )

    real_unlink = Path.unlink

    def partial_locked_unlink(self, missing_ok=False):
        if self.name == "token_usage_2.json":
            raise PermissionError("file locked")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", partial_locked_unlink)

    first = tracker_factory()
    assert not legacy_1.exists()
    assert legacy_2.exists()
    merged_1 = json.loads(first._storage_path.read_text(encoding="utf-8"))
    assert merged_1["daily_stats"]["2026-11-01"]["total_tokens"] == 300
    assert merged_1["daily_stats"]["2026-11-01"]["call_count"] == 3

    # Second startup while token_usage_2.json is still locked must NOT double-count (300, not 500).
    second = tracker_factory()
    assert legacy_2.exists()
    merged_2 = json.loads(second._storage_path.read_text(encoding="utf-8"))
    assert merged_2["daily_stats"]["2026-11-01"]["total_tokens"] == 300
    assert merged_2["daily_stats"]["2026-11-01"]["call_count"] == 3

    # Once lock clears, startup cleans up token_usage_2.json and prunes migrated index without double-counting.
    monkeypatch.setattr(Path, "unlink", real_unlink)
    third = tracker_factory()
    assert not legacy_2.exists()
    merged_3 = json.loads(third._storage_path.read_text(encoding="utf-8"))
    assert merged_3["daily_stats"]["2026-11-01"]["total_tokens"] == 300
    assert merged_3["daily_stats"]["2026-11-01"]["call_count"] == 3
    assert "migrated_legacy_files" not in merged_3

    # A truly new legacy file with different content under the same name still migrates cleanly.
    legacy_2.write_text(
        json.dumps({
            "version": 1,
            "daily_stats": {"2026-11-01": {"total_tokens": 50, "call_count": 1}},
            "recent_records": [{"ts": "2026-11-01T00:02:00", "m": "m1", "tt": 50}],
        }),
        encoding="utf-8",
    )
    fourth = tracker_factory()
    assert not legacy_2.exists()
    merged_4 = json.loads(fourth._storage_path.read_text(encoding="utf-8"))
    assert merged_4["daily_stats"]["2026-11-01"]["total_tokens"] == 350
    assert merged_4["daily_stats"]["2026-11-01"]["call_count"] == 4


