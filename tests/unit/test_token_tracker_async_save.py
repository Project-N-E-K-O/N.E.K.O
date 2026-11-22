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

    queue_at_request = []

    def failing_urlopen(request, *, timeout):
        queue_at_request.append(tracker._unsent_queue_path.exists())
        raise TimeoutError("offline")

    monkeypatch.setattr(reporting.urllib.request, "urlopen", failing_urlopen)
    tracker.save()
    assert queue_at_request == [False]
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
    assert disk_after["daily"][today]["total_tokens"] == 10
    assert disk_after["next_daily"][today]["total_tokens"] == 20
    assert disk_after["batch_seq"] == inflight_seq


    restarted = tracker_factory()
    restarted._load_unsent_queue()
    assert restarted._unsent_queue_path.exists()
    batches = []

    def capture(daily, records, seq, is_retry, timeout):
        batches.append((daily[today]["total_tokens"], [r["model"] for r in records], seq))

    monkeypatch.setattr(restarted, "_send_report", capture)
    restarted.save()
    assert batches[0] == (10, ["model-1"], inflight_seq)
    assert batches[1][:2] == (20, ["model-2"])
    assert batches[1][2] != inflight_seq
    assert len(batches) == 2
    assert not restarted._unsent_queue_path.exists()


def test_numbered_queue_write_failure_defers_post(tracker_factory, monkeypatch):
    from utils.token_tracker import reporting

    tracker = tracker_factory()
    tracker._report_interval = float("inf")
    tracker.record("model", 6, 4, 10)
    tracker.save()
    tracker._report_interval = 0
    real_write = reporting.atomic_write_json
    sent = []

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(reporting, "atomic_write_json", disk_full)
    monkeypatch.setattr(tracker, "_send_report", lambda *args: sent.append(args))
    tracker.save()
    assert not sent
    assert json.loads(tracker._unsent_queue_path.read_text())["batch_seq"] is None
    seq = tracker._pending_batch_seq
    assert seq
    monkeypatch.setattr(reporting, "atomic_write_json", real_write)
    tracker.save()
    assert len(sent) == 1
    assert sent[0][2] == seq
    assert sent[0][1][0]["tt"] == 10


@pytest.mark.parametrize("bad", [
    "{", '{"version": 2}', '{"version": 1, "daily_stats":{"day":{"total_tokens":10,"by_model":[]}}}',
    '{"version":1,"daily_stats":{"day":{"total_tokens":10}},"recent_records":[null]}',
    '{"version": 1, "daily_stats": {"day": {"total_tokens": NaN}}}',
    '{"version": 1, "daily_stats": {"day": {"total_tokens": Infinity}}}',
    '{"version": 1, "daily_stats": {"day": {"total_tokens": 1}}, "recent_records": [{"ts": 1, "tt": NaN}]}',
])
def test_invalid_legacy_file_cannot_contribute_or_be_removed(tracker_factory, bad):
    tracker = tracker_factory()
    folder = tracker._storage_dir
    invalid = folder / "token_usage_b.json"
    invalid.write_text(bad)
    for suffix in ("a", "c"):
        (folder / f"token_usage_{suffix}.json").write_text(json.dumps({
            "version": 1, "daily_stats": {"day": {"total_tokens": 1}},
        }))
    tracker._migrate_legacy_files()
    assert invalid.read_text() == bad
    merged = json.loads(tracker._storage_path.read_text())
    assert merged["daily_stats"]["day"]["total_tokens"] == 2
    assert invalid.name not in merged.get("migrated_legacy_files", {})


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


def test_legacy_migration_rejects_numeric_overflow_without_blocking_valid_files(tracker_factory):
    """Overflowing float addition during migration rejects only the offending legacy file."""
    tracker = tracker_factory()
    folder = tracker._storage_dir
    (folder / "token_usage_a.json").write_text(
        json.dumps({"version": 1, "daily_stats": {"day": {"total_tokens": 1e308}}}),
        encoding="utf-8",
    )
    overflowing = folder / "token_usage_b.json"
    overflowing.write_text(
        json.dumps({"version": 1, "daily_stats": {"day": {"total_tokens": 1e308}}}),
        encoding="utf-8",
    )
    (folder / "token_usage_c.json").write_text(
        json.dumps({"version": 1, "daily_stats": {"other_day": {"total_tokens": 7}}}),
        encoding="utf-8",
    )

    tracker._migrate_legacy_files()
    assert not (folder / "token_usage_a.json").exists()
    assert overflowing.exists()
    assert not (folder / "token_usage_c.json").exists()
    merged = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert merged["daily_stats"]["day"]["total_tokens"] == 1e308
    assert merged["daily_stats"]["other_day"]["total_tokens"] == 7
    assert overflowing.name not in merged.get("migrated_legacy_files", {})


def test_unsent_queue_retries_recovery_before_overwriting_disk_snapshot(tracker_factory, monkeypatch):
    """Startup read OSError keeps unsent file intact across saves and replays A before B on recovery."""
    from datetime import date
    from pathlib import Path
    from utils.token_tracker import reporting

    seed = tracker_factory()
    today = date.today().isoformat()
    initial_queue = {
        "batch_seq": "seq-a",
        "daily": {today: {"total_tokens": 10, "call_count": 1}},
        "records": [{"ts": 1.0, "model": "model-a", "tt": 10}],
    }
    seed._unsent_queue_path.write_text(json.dumps(initial_queue), encoding="utf-8")

    real_read_text = Path.read_text
    fail_queue_read = True

    def flaky_read_text(self, *args, **kwargs):
        if fail_queue_read and self == seed._unsent_queue_path:
            raise OSError("temporary share lock")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    tracker = tracker_factory()
    assert tracker._unsent_queue_loaded is False

    queue_writes = []
    real_write = reporting.atomic_write_json

    def spy_write(path, data, *args, **kwargs):
        if path == tracker._unsent_queue_path:
            queue_writes.append(data)
        return real_write(path, data, *args, **kwargs)

    sent = []
    monkeypatch.setattr(reporting, "atomic_write_json", spy_write)
    monkeypatch.setattr(tracker, "_send_report", lambda *args: sent.append(args))

    tracker._report_interval = float("inf")
    tracker.record("model-b", 12, 8, 20)
    tracker.save()

    tracker._report_interval = 0
    tracker.save()

    assert queue_writes == []
    assert sent == []
    assert json.loads(real_read_text(tracker._unsent_queue_path, encoding="utf-8")) == initial_queue

    fail_queue_read = False
    tracker.save()

    assert len(sent) == 2
    assert sent[0][0][today]["total_tokens"] == 10
    assert [r["model"] for r in sent[0][1]] == ["model-a"]
    assert sent[0][2] == "seq-a"
    assert sent[0][3] is True

    assert sent[1][0][today]["total_tokens"] == 20
    assert [r["model"] for r in sent[1][1]] == ["model-b"]
    assert sent[1][2] != "seq-a"
    assert sent[1][3] is False
    assert not tracker._unsent_queue_path.exists()


def test_storage_dir_sync_failure_retries_only_fsync_without_replaying_deltas(
    tracker_factory, monkeypatch
):
    """PublishedFileSyncError marks parent dir sync pending and retries on idle save without doubling totals."""
    import errno
    from utils import file_utils
    from utils.token_tracker import reporting

    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "")
    tracker = tracker_factory()

    sync_calls = []

    def flaky_fsync_dir(path):
        sync_calls.append(path)
        if len(sync_calls) <= 2:
            raise OSError(errno.EIO, "directory sync failed")

    monkeypatch.setattr(file_utils, "_fsync_directory", flaky_fsync_dir)

    tracker.record("sync-model", 6, 4, 10)
    tracker.save()

    assert len(sync_calls) == 1
    assert tracker._dirty is False
    assert tracker._storage_dir_sync_pending is True
    persisted = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert persisted["daily_stats"]
    only_day = next(iter(persisted["daily_stats"].values()))
    assert only_day["total_tokens"] == 10

    # Idle save with remote reporting disabled still retries directory fsync (fails once more).
    tracker.save()
    assert len(sync_calls) == 2
    assert sync_calls[1] == tracker._storage_path.parent
    assert tracker._storage_dir_sync_pending is True
    persisted_after_fail = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert next(iter(persisted_after_fail["daily_stats"].values()))["total_tokens"] == 10

    # Next idle save succeeds and clears pending flag without replaying delta.
    tracker.save()
    assert len(sync_calls) == 3
    assert tracker._storage_dir_sync_pending is False
    persisted_after_ok = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert next(iter(persisted_after_ok["daily_stats"].values()))["total_tokens"] == 10

    # Further idle save performs no extra directory fsync.
    tracker.save()
    assert len(sync_calls) == 3


@pytest.mark.asyncio
async def test_periodic_save_loop_retries_pending_dir_sync_and_unread_queue_while_idle(
    tracker_factory, monkeypatch
):
    """Periodic save loop retries pending parent dir fsync and unread queue recovery while idle."""
    import errno
    from utils import file_utils, instrument
    from utils.token_tracker import reporting

    monkeypatch.setattr(instrument, "has_data", lambda: False)
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "")
    tracker = tracker_factory()

    sync_calls = []

    def flaky_fsync_dir(path):
        sync_calls.append(path)
        if len(sync_calls) == 1:
            raise OSError(errno.EIO, "directory sync failed")

    monkeypatch.setattr(file_utils, "_fsync_directory", flaky_fsync_dir)

    tracker.record("idle-model", 3, 2, 5)
    tracker.save()
    assert tracker._dirty is False
    assert tracker._storage_dir_sync_pending is True
    assert len(sync_calls) == 1

    tracker._save_interval = 0.01
    tracker.start_periodic_save()
    try:
        async with asyncio.timeout(2):
            while tracker._storage_dir_sync_pending:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
    finally:
        if tracker._save_task is not None:
            tracker._save_task.cancel()
            await asyncio.gather(tracker._save_task, return_exceptions=True)

    assert len(sync_calls) == 2
    persisted = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert next(iter(persisted["daily_stats"].values()))["total_tokens"] == 5

    # Part B: unread unsent queue also triggers periodic save once telemetry is enabled.
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", False)
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "http://127.0.0.1:1")
    tracker._unsent_queue_loaded = False
    assert tracker._needs_periodic_save() is True
    tracker._unsent_queue_loaded = True
    assert tracker._needs_periodic_save() is False


def test_unsent_queue_isolates_non_finite_window_without_blocking_valid_window(
    tracker_factory, monkeypatch
):
    """Non-finite numbers in one unsent queue window must not discard or block the other."""
    from datetime import date

    today = date.today().isoformat()

    # Part A: valid pending numbered batch + corrupt next window -> preserves seq-a without renumbering.
    seed_a = tracker_factory()
    seed_a._unsent_queue_path.write_text(
        json.dumps(
            {
                "batch_seq": "seq-a",
                "daily": {today: {"total_tokens": 10, "call_count": 1}},
                "records": [{"ts": 1.0, "model": "model-a", "tt": 10}],
                "next_daily": {today: {"total_tokens": float("inf")}},
                "next_records": [{"ts": 2.0, "model": "bad-next", "tt": 99}],
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    tracker_a = tracker_factory()
    sent_a = []
    monkeypatch.setattr(tracker_a, "_send_report", lambda *args: sent_a.append(args))
    tracker_a.record("model-b", 6, 4, 10)
    tracker_a.save()

    assert len(sent_a) == 2
    assert sent_a[0][0][today]["total_tokens"] == 10
    assert [r["model"] for r in sent_a[0][1]] == ["model-a"]
    assert sent_a[0][2] == "seq-a"
    assert sent_a[0][3] is True
    assert sent_a[1][0][today]["total_tokens"] == 10
    assert [r["model"] for r in sent_a[1][1]] == ["model-b"]
    assert not tracker_a._unsent_queue_path.exists()

    # Part B: corrupt primary numbered batch + valid next window -> quarantines seq-bad, recovers next window.
    seed_a._unsent_queue_path.write_text(
        json.dumps(
            {
                "batch_seq": "seq-bad",
                "daily": {today: {"total_tokens": float("inf")}},
                "records": [{"ts": 1.0, "model": "bad-primary", "tt": 10}],
                "next_daily": {today: {"total_tokens": 25, "call_count": 1}},
                "next_records": [{"ts": 2.0, "model": "model-next", "tt": 25}],
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    tracker_b = tracker_factory()
    sent_b = []
    monkeypatch.setattr(tracker_b, "_send_report", lambda *args: sent_b.append(args))
    tracker_b.record("model-c", 3, 2, 5)
    tracker_b.save()

    assert len(sent_b) == 1
    assert sent_b[0][0][today]["total_tokens"] == 30
    assert [r["model"] for r in sent_b[0][1]] == ["model-next", "model-c"]
    assert sent_b[0][2] != "seq-bad"
    assert sent_b[0][3] is False
    assert not tracker_b._unsent_queue_path.exists()


def test_legacy_migration_syncs_parent_dir_before_pruning_digests_and_retains_on_sync_error(
    tracker_factory, monkeypatch
):
    """Legacy migration fsyncs parent directory after unlink before removing migrated_legacy_files digests."""
    import errno
    from pathlib import Path
    from utils import file_utils
    from utils.token_tracker import reporting, storage

    # Part A: unlink -> _fsync_directory -> pruning atomic_write_json ordering.
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "")
    tracker = tracker_factory()
    folder = tracker._storage_dir
    legacy = folder / "token_usage_legacy.json"
    legacy.write_text(
        json.dumps({"version": 1, "daily_stats": {"2026-11-01": {"total_tokens": 42}}}),
        encoding="utf-8",
    )

    events = []
    real_unlink = Path.unlink
    real_fsync_dir = file_utils._fsync_directory
    real_atomic_write = storage.atomic_write_json

    def spy_unlink(self, *args, **kwargs):
        if self == legacy:
            events.append("unlink")
        return real_unlink(self, *args, **kwargs)

    def spy_fsync(path):
        if Path(path) == folder:
            events.append("fsync_dir")
        return real_fsync_dir(path)

    def spy_atomic_write(path, data, *args, **kwargs):
        if Path(path) == tracker._storage_path:
            events.append(("write", "migrated_legacy_files" in data))
        return real_atomic_write(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", spy_unlink)
    monkeypatch.setattr(file_utils, "_fsync_directory", spy_fsync)
    monkeypatch.setattr(storage, "atomic_write_json", spy_atomic_write)

    tracker._migrate_legacy_files()
    assert events == [
        ("write", True),
        "fsync_dir",
        "unlink",
        "fsync_dir",
        ("write", False),
        "fsync_dir",
    ]

    # Part B: fsync error on the deleted legacy dir preserves migrated_legacy_files digest.
    legacy_b = folder / "token_usage_b.json"
    legacy_b.write_text(
        json.dumps({"version": 1, "daily_stats": {"2026-11-02": {"total_tokens": 11}}}),
        encoding="utf-8",
    )
    events.clear()
    seen_unlink = False

    def fail_post_unlink_fsync(path):
        nonlocal seen_unlink
        if Path(path) == folder and seen_unlink:
            raise OSError(errno.EIO, "dir sync failed after unlink")
        return real_fsync_dir(path)

    def track_unlink_b(self, *args, **kwargs):
        nonlocal seen_unlink
        if self == legacy_b:
            seen_unlink = True
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", track_unlink_b)
    monkeypatch.setattr(file_utils, "_fsync_directory", fail_post_unlink_fsync)

    tracker._migrate_legacy_files()
    assert not legacy_b.exists()
    persisted = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert persisted["daily_stats"]["2026-11-02"]["total_tokens"] == 11
    assert legacy_b.name in persisted.get("migrated_legacy_files", {})


def test_unsent_queue_rejects_unmergeable_stats_surrogates_and_corrupt_batch_seq(
    tracker_factory, monkeypatch
):
    """Recovery isolates unmergeable types, lone UTF-8 surrogates, and orphaned corrupt-seq primary batches."""
    from datetime import date

    today = date.today().isoformat()

    # Case 1: unmergeable string stat in primary window must not prevent valid next window + live delta merge.
    seed = tracker_factory()
    seed._unsent_queue_path.write_text(
        json.dumps(
            {
                "batch_seq": "seq-bad-stats",
                "daily": {today: {"total_tokens": "10", "call_count": 1}},
                "records": [{"ts": 1.0, "model": "bad-stats"}],
                "next_daily": {today: {"total_tokens": 15, "call_count": 1}},
                "next_records": [{"ts": 2.0, "model": "valid-next"}],
            }
        ),
        encoding="utf-8",
    )
    tracker_1 = tracker_factory()
    sent_1 = []
    monkeypatch.setattr(tracker_1, "_send_report", lambda *args: sent_1.append(args))
    tracker_1.record("live-model", 3, 2, 5)
    tracker_1.save()

    assert len(sent_1) == 1
    assert sent_1[0][0][today]["total_tokens"] == 20
    assert [r["model"] for r in sent_1[0][1]] == ["valid-next", "live-model"]
    assert not tracker_1._unsent_queue_path.exists()

    # Case 2: lone surrogate in next_records isolates next window while preserving valid Unicode seq-a.
    seed._unsent_queue_path.write_text(
        json.dumps(
            {
                "batch_seq": "seq-a",
                "daily": {today: {"total_tokens": 10, "call_count": 1}},
                "records": [{"ts": 1.0, "model": "模型-正常", "tt": 10}],
                "next_daily": {today: {"total_tokens": 99, "call_count": 1}},
                "next_records": [{"ts": 2.0, "model": "\ud800", "tt": 99}],
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    tracker_2 = tracker_factory()
    sent_2 = []
    monkeypatch.setattr(tracker_2, "_send_report", lambda *args: sent_2.append(args))
    tracker_2.record("model-b", 6, 4, 10)
    tracker_2.save()

    assert len(sent_2) == 2
    assert sent_2[0][2] == "seq-a" and sent_2[0][3] is True
    assert [r["model"] for r in sent_2[0][1]] == ["模型-正常"]
    assert sent_2[1][0][today]["total_tokens"] == 10
    assert [r["model"] for r in sent_2[1][1]] == ["model-b"]
    assert not tracker_2._unsent_queue_path.exists()

    # Case 3: numbered snapshot with corrupt empty batch_seq quarantines primary and recovers next window.
    seed._unsent_queue_path.write_text(
        json.dumps(
            {
                "batch_seq": "",
                "daily": {today: {"total_tokens": 10, "call_count": 1}},
                "records": [{"ts": 1.0, "model": "orphaned-primary"}],
                "next_daily": {today: {"total_tokens": 25, "call_count": 1}},
                "next_records": [{"ts": 2.0, "model": "recovered-next"}],
            }
        ),
        encoding="utf-8",
    )
    tracker_3 = tracker_factory()
    sent_3 = []
    monkeypatch.setattr(tracker_3, "_send_report", lambda *args: sent_3.append(args))
    tracker_3.record("model-c", 3, 2, 5)
    tracker_3.save()

    assert len(sent_3) == 1
    assert sent_3[0][0][today]["total_tokens"] == 30
    assert [r["model"] for r in sent_3[0][1]] == ["recovered-next", "model-c"]
    assert sent_3[0][3] is False
    assert not tracker_3._unsent_queue_path.exists()


def test_legacy_migration_rejects_lone_surrogate_and_syncs_dir_before_pruning_stale_digests(
    tracker_factory, monkeypatch
):
    """Migration isolates lone-surrogate legacy files and fsyncs storage_dir before pruning stale digests."""
    import errno
    import hashlib
    from pathlib import Path
    from utils import file_utils
    from utils.token_tracker import reporting, storage

    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", True)
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "")
    tracker = tracker_factory()
    folder = tracker._storage_dir

    # Part A: legacy file with lone surrogate stays untouched while valid sibling migrates.
    surrogate_legacy = folder / "token_usage_surrogate.json"
    surrogate_legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "daily_stats": {"2026-11-01": {"total_tokens": 10}},
                "recent_records": [{"ts": "2026-11-01T00:00:00", "m": "\ud800", "tt": 10}],
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    valid_legacy = folder / "token_usage_valid.json"
    valid_legacy.write_text(
        json.dumps({"version": 1, "daily_stats": {"2026-11-01": {"total_tokens": 12}}}),
        encoding="utf-8",
    )

    tracker._migrate_legacy_files()
    assert surrogate_legacy.exists()
    assert not valid_legacy.exists()
    merged = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert merged["daily_stats"]["2026-11-01"]["total_tokens"] == 12
    assert surrogate_legacy.name not in merged.get("migrated_legacy_files", {})

    # Remove surrogate fixture cleanly before Part B.
    surrogate_legacy.unlink()

    # Part B: stale digest in token_usage.json for absent file stays if pre-prune dir sync fails,
    # and is pruned once pre-prune dir sync succeeds.
    stale_digest = hashlib.sha256(b"prior-legacy").hexdigest()
    merged["migrated_legacy_files"] = {"token_usage_old.json": stale_digest}
    storage.atomic_write_json(tracker._storage_path, merged)

    present_legacy = folder / "token_usage_new.json"
    present_legacy.write_text(
        json.dumps({"version": 1, "daily_stats": {"2026-11-03": {"total_tokens": 8}}}),
        encoding="utf-8",
    )

    real_fsync_dir = file_utils._fsync_directory
    fsync_calls = 0

    def flaky_fsync_dir(path):
        nonlocal fsync_calls
        if Path(path) == folder:
            fsync_calls += 1
            if fsync_calls == 1:
                raise OSError(errno.EIO, "dir sync failed before prune")
        return real_fsync_dir(path)

    monkeypatch.setattr(file_utils, "_fsync_directory", flaky_fsync_dir)
    tracker._migrate_legacy_files()
    after_failed_sync = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert not present_legacy.exists()
    assert after_failed_sync["daily_stats"]["2026-11-03"]["total_tokens"] == 8
    assert after_failed_sync["migrated_legacy_files"]["token_usage_old.json"] == stale_digest

    # Next run with healthy dir sync sees another legacy file and prunes stale digest after fsync.
    another_legacy = folder / "token_usage_another.json"
    another_legacy.write_text(
        json.dumps({"version": 1, "daily_stats": {"2026-11-04": {"total_tokens": 4}}}),
        encoding="utf-8",
    )
    tracker._migrate_legacy_files()
    after_ok_sync = json.loads(tracker._storage_path.read_text(encoding="utf-8"))
    assert after_ok_sync["daily_stats"]["2026-11-04"]["total_tokens"] == 4
    assert "migrated_legacy_files" not in after_ok_sync





