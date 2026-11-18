from __future__ import annotations

import json
import threading
import time

import pytest

from plugin.server.application.plugins.call_audit import CallAudit

pytestmark = pytest.mark.plugin_unit


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_entries_reach_the_file_and_the_ring(tmp_path):
    path = tmp_path / "a" / "call_audit.jsonl"
    audit = CallAudit(path_resolver=lambda: path)
    audit.record({"kind": "forward", "target": "p"})
    assert audit.close(2.0) is True
    (line,) = _lines(path)
    assert line["kind"] == "forward" and "ts" in line
    assert audit.entries(limit=5)[0]["target"] == "p"


def test_ring_is_newest_first_and_filtered(tmp_path):
    audit = CallAudit(path_resolver=lambda: tmp_path / "x.jsonl", ring_size=3)
    for index in range(5):
        audit.record({"kind": "decision" if index % 2 else "forward", "n": index})
    assert [e["n"] for e in audit.entries(limit=10)] == [4, 3, 2]
    assert [e["n"] for e in audit.entries(limit=10, filters={"kind": "decision"})] == [3]
    audit.close(2.0)


def test_rotation_keeps_three_backups(tmp_path):
    path = tmp_path / "call_audit.jsonl"
    audit = CallAudit(path_resolver=lambda: path, max_bytes=200, backups=3)
    for index in range(40):
        audit.record({"kind": "forward", "n": index, "pad": "x" * 60})
        # One entry per write so every batch can trigger a rotation.
        while not audit._queue.empty():
            time.sleep(0.001)
    assert audit.close(2.0) is True
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["call_audit.jsonl", "call_audit.jsonl.1", "call_audit.jsonl.2", "call_audit.jsonl.3"]
    assert all(p.stat().st_size <= 400 for p in tmp_path.iterdir())


def test_full_queue_drops_and_counts_without_blocking(tmp_path):
    gate = threading.Event()
    audit = CallAudit(path_resolver=lambda: tmp_path / "x.jsonl", queue_size=2)
    audit._write = lambda batch: gate.wait(5)  # type: ignore[method-assign]
    started = time.monotonic()
    for index in range(50):
        audit.record({"n": index})
    assert time.monotonic() - started < 1.0
    assert audit.stats()["dropped"] > 0
    assert len(audit.entries(limit=100)) == 50  # the ring still has them
    gate.set()
    audit.close(2.0)


def test_close_is_bounded_when_the_writer_is_stuck(tmp_path):
    release = threading.Event()
    audit = CallAudit(path_resolver=lambda: tmp_path / "x.jsonl", queue_size=1)
    audit._write = lambda batch: release.wait(10)  # type: ignore[method-assign]
    audit.record({"n": 1})
    audit.record({"n": 2})
    started = time.monotonic()
    assert audit.close(0.3) is False
    assert time.monotonic() - started < 1.5
    assert audit._thread.daemon is True  # never holds interpreter exit
    release.set()


def test_records_after_close_stay_in_the_ring_only(tmp_path):
    path = tmp_path / "x.jsonl"
    audit = CallAudit(path_resolver=lambda: path)
    audit.close(1.0)
    audit.record({"n": 1})
    assert audit.stats()["dropped"] == 1
    assert not path.exists()
    audit.reopen()
    audit.record({"n": 2})
    assert audit.close(2.0) is True
    assert [line["n"] for line in _lines(path)] == [2]


def test_write_failure_is_counted_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    audit = CallAudit(path_resolver=lambda: blocker / "sub" / "x.jsonl")
    audit.record({"n": 1})
    assert audit.close(2.0) is True
    assert audit.stats()["write_errors"] == 1


def test_denials_are_deduplicated_within_the_window(tmp_path):
    now = [0.0]
    audit = CallAudit(path_resolver=lambda: tmp_path / "x.jsonl", dedup_window=60, clock=lambda: now[0])
    key = ("a", "memory.read", "caller_chain", ("b",))
    for _ in range(3):
        audit.record_denial({"kind": "decision"}, key)
    now[0] = 61
    audit.record_denial({"kind": "decision"}, key)
    entries = audit.entries(limit=10)
    assert [e["suppressed"] for e in entries] == [2, 0]
    audit.close(1.0)


def test_unattributed_counts_flush_on_interval(tmp_path):
    now = [0.0]
    audit = CallAudit(path_resolver=lambda: tmp_path / "x.jsonl", summary_interval=60, clock=lambda: now[0])
    audit.count_unattributed("a", "memory.read", mode="observe")
    audit.count_unattributed("a", "memory.read", mode="observe")
    assert audit.entries(limit=10) == []
    now[0] = 60
    audit.count_unattributed("a", "messages.push", mode="observe")
    (summary,) = audit.entries(limit=10)
    assert summary["counts"] == {"memory.read": 2, "messages.push": 1}
    audit.close(1.0)
