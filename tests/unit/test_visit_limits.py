# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Visit receive limiter (drop and count) and blocklist sync/async twins."""

from __future__ import annotations

import json

import pytest

from config.visit_settings import (
    VISIT_BLOCKLIST_FILENAME,
    VISIT_INBOUND_TEXT_BURST,
    VISIT_PEER_CTL_PER_S,
    VISIT_PEER_LOSSY_PER_S,
    VISIT_PEER_RECV_MSGS_PER_S,
)
from main_logic.visit.limits import (
    Blocklist,
    PeerRateLimiter,
    RateChannel,
    channel_for,
)

UID = "0123456789abcdef01234567"


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ── PeerRateLimiter ──


def test_text_bucket_drops_and_counts_toward_streak():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    for _ in range(VISIT_INBOUND_TEXT_BURST):
        assert lim.admit("g_a", RateChannel.TEXT).allowed
    d = lim.admit("g_a", RateChannel.TEXT)
    assert not d.allowed and d.reason == "text_rate" and d.counts_toward_streak
    assert lim.dropped("g_a") == {"text_rate": 1}
    assert lim.text_accepted("g_a") == VISIT_INBOUND_TEXT_BURST


def test_text_steady_rate_is_twenty_per_ten_seconds():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    for _ in range(VISIT_INBOUND_TEXT_BURST):
        lim.admit("g_a", "text")
    clock.t += 10.0
    allowed = sum(lim.admit("g_a", "text").allowed for _ in range(30))
    assert allowed == 20
    assert lim.total_dropped("g_a") == 10


def test_text_visit_cap():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock, text_visit_max=5, text_burst=100)
    assert all(lim.admit("g_a", "text").allowed for _ in range(5))
    d = lim.admit("g_a", "text")
    assert d.reason == "text_visit_cap" and d.counts_toward_streak


@pytest.mark.parametrize("channel,rate,reason", [
    (RateChannel.CTL, VISIT_PEER_CTL_PER_S, "ctl_rate"),
    (RateChannel.LOSSY, VISIT_PEER_LOSSY_PER_S, "lossy_rate"),
])
def test_ctl_and_lossy_per_second(channel, rate, reason):
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    for _ in range(rate):
        assert lim.admit("g_a", channel).allowed
    d = lim.admit("g_a", channel)
    assert not d.allowed and d.reason == reason
    assert not d.counts_toward_streak and not d.sustained_overflow
    clock.t += 1.0
    assert lim.admit("g_a", channel).allowed
    assert lim.dropped("g_a") == {reason: 1}


def test_senders_are_independent():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    for _ in range(VISIT_PEER_CTL_PER_S):
        lim.admit("g_a", "ctl")
    assert not lim.admit("g_a", "ctl").allowed
    assert lim.admit("h_b", "ctl").allowed


def test_explicit_now_overrides_clock():
    lim = PeerRateLimiter(clock=lambda: 0.0)
    for _ in range(VISIT_PEER_LOSSY_PER_S):
        lim.admit("g_a", "lossy", now=5.0)
    assert not lim.admit("g_a", "lossy", now=5.0).allowed
    assert lim.admit("g_a", "lossy", now=6.0).allowed


def test_frame_buckets_drop_before_reassembly():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    for _ in range(VISIT_PEER_RECV_MSGS_PER_S):
        assert lim.admit_frame("g_a", 10).allowed
    d = lim.admit_frame("g_a", 10)
    assert d.reason == "recv_msgs" and not d.counts_toward_streak
    clock.t += 10
    assert lim.admit_frame("g_a", 16 * 1024).allowed
    d = lim.admit_frame("g_a", 1)
    assert d.reason == "recv_bytes"


def test_rejected_frame_does_not_charge_the_other_bucket():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    assert not lim.admit_frame("g_a", 10 ** 6).allowed
    # Rejection by the byte bucket must not debit the item-count bucket.
    assert sum(lim.admit_frame("g_a", 1).allowed for _ in range(VISIT_PEER_RECV_MSGS_PER_S)) \
        == VISIT_PEER_RECV_MSGS_PER_S


def test_sustained_overflow_after_thirty_seconds():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    sustained_at = None
    for step in range(0, 400):
        clock.t = 1000.0 + step * 0.1
        for _ in range(3):
            d = lim.admit("g_a", "lossy")
            if d.sustained_overflow and sustained_at is None:
                sustained_at = clock.t - 1000.0
    assert sustained_at is not None and 29.9 <= sustained_at <= 30.2


def test_overflow_run_resets_after_a_quiet_gap():
    clock = FakeClock()
    lim = PeerRateLimiter(clock=clock)
    for burst_start in (0.0, 20.0):
        clock.t = 1000.0 + burst_start
        for k in range(150):
            clock.t = 1000.0 + burst_start + k * 0.1
            for _ in range(3):
                assert not lim.admit("g_a", "lossy").sustained_overflow


def test_channel_mapping():
    assert channel_for("text") is RateChannel.TEXT
    assert channel_for("hello") is RateChannel.CTL
    assert channel_for("ack") is RateChannel.CTL
    assert channel_for("typing") is RateChannel.LOSSY
    assert channel_for("line_delta") is None
    assert channel_for("future", cmd=1) is RateChannel.CTL
    assert channel_for("future", cmd=3) is RateChannel.LOSSY
    assert channel_for("future", cmd=2) is None
    assert PeerRateLimiter().admit("g_a", None).allowed


# ── Blocklist ──


def test_missing_file_is_empty(tmp_path):
    bl = Blocklist.load(tmp_path)
    assert len(bl) == 0 and not bl.is_blocked(UID)


async def test_block_roundtrip_and_schema(tmp_path):
    bl = Blocklist.load(tmp_path)
    assert await bl.ablock(UID, display_name_at_block="Mimi", reason="rude", now=123.0)
    assert not await bl.ablock(UID, display_name_at_block="Mimi", now=124.0)
    data = json.loads((tmp_path / VISIT_BLOCKLIST_FILENAME).read_text(encoding="utf-8"))
    assert data == {"blocked": [{
        "visit_uid": UID, "display_name_at_block": "Mimi", "blocked_at": 123.0, "reason": "rude",
    }]}
    again = Blocklist.load(tmp_path)
    assert again.is_blocked(UID) and again.is_blocked(UID.upper())
    assert UID in again
    assert await again.aunblock(UID) and not await again.aunblock(UID)
    assert not Blocklist.load(tmp_path).is_blocked(UID)


async def test_async_twin_matches_sync(tmp_path):
    bl = await Blocklist.aload(tmp_path)
    assert await bl.ablock(UID, display_name_at_block="Mimi", now=5.0)
    assert not await bl.ablock(UID, display_name_at_block="Mimi")
    sync_view = Blocklist.load(tmp_path)
    assert sync_view.is_blocked(UID)
    assert sync_view.get(UID).display_name_at_block == "Mimi"
    assert "reason" not in json.loads((tmp_path / VISIT_BLOCKLIST_FILENAME).read_text(encoding="utf-8"))["blocked"][0]
    async_view = await Blocklist.aload(tmp_path)
    assert [e.visit_uid for e in async_view.entries()] == [UID]
    assert await async_view.aunblock(UID)
    assert not (await Blocklist.aload(tmp_path)).is_blocked(UID)


async def test_async_writes_are_serialised(tmp_path):
    import asyncio

    bl = await Blocklist.aload(tmp_path)
    uids = [f"{i:024x}" for i in range(10)]
    await asyncio.gather(*(bl.ablock(u, display_name_at_block="x", now=float(i)) for i, u in enumerate(uids)))
    reloaded = Blocklist.load(tmp_path)
    assert [e.visit_uid for e in reloaded.entries()] == uids


async def test_failed_write_keeps_memory_consistent(tmp_path, monkeypatch):
    from main_logic.visit import limits

    bl = Blocklist.load(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(limits, "atomic_write_json", boom)
    with pytest.raises(OSError):
        await bl.ablock(UID, display_name_at_block="Mimi")
    assert not bl.is_blocked(UID)


async def test_corrupt_file_fails_closed_and_is_left_in_place(tmp_path):
    # Unreadable data must not become an empty table, allowing blocked users through; leave the original file in place for repair
    from main_logic.visit.limits import BlocklistUnavailable

    path = tmp_path / VISIT_BLOCKLIST_FILENAME
    path.write_text("{not json", encoding="utf-8")
    bl = Blocklist.load(tmp_path)
    assert bl.available is False
    with pytest.raises(BlocklistUnavailable):
        bl.is_blocked(UID)
    with pytest.raises(BlocklistUnavailable):
        await bl.ablock(UID, display_name_at_block="Mimi")
    assert path.read_text(encoding="utf-8") == "{not json"
    assert not (tmp_path / (VISIT_BLOCKLIST_FILENAME + ".corrupt")).exists()


async def test_async_unreadable_file_fails_closed_then_recovers(tmp_path):
    from main_logic.visit.limits import BlocklistUnavailable

    path = tmp_path / VISIT_BLOCKLIST_FILENAME
    path.write_text('{"blocked": 3}', encoding="utf-8")
    bl = await Blocklist.aload(tmp_path)
    assert bl.available is False
    with pytest.raises(BlocklistUnavailable):
        await bl.ablock(UID, display_name_at_block="Mimi")
    # Reload after repair restores operation
    path.write_text('{"blocked": []}', encoding="utf-8")
    assert (await Blocklist.aload(tmp_path)).available is True


def test_duplicate_rows_are_merged(tmp_path):
    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text(json.dumps({"blocked": [
        {"visit_uid": UID, "display_name_at_block": "a", "blocked_at": 1},
        {"visit_uid": UID.upper(), "display_name_at_block": "b", "blocked_at": 2},
    ]}), encoding="utf-8")
    bl = Blocklist.load(tmp_path)
    assert bl.available and len(bl) == 1 and bl.get(UID).display_name_at_block == "b"


@pytest.mark.parametrize("bad_row", [{"visit_uid": ""}, "junk", {"display_name_at_block": "x"},
                                     {"visit_uid": 123}])
def test_any_malformed_row_makes_the_list_unavailable(tmp_path, bad_row):
    # Dropping an invalid row would admit that blocked person: any invalid row makes the entire list fail closed
    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text(json.dumps({"blocked": [
        {"visit_uid": UID, "display_name_at_block": "a", "blocked_at": 1}, bad_row,
    ]}), encoding="utf-8")
    assert Blocklist.load(tmp_path).available is False


async def test_blocklist_is_not_partitioned_by_account(tmp_path):
    await Blocklist.load(tmp_path).ablock(UID, display_name_at_block="Mimi")
    data = json.loads((tmp_path / VISIT_BLOCKLIST_FILENAME).read_text(encoding="utf-8"))
    assert set(data) == {"blocked"}


async def test_empty_uid_rejected(tmp_path):
    with pytest.raises(ValueError):
        await Blocklist.load(tmp_path).ablock("  ", display_name_at_block="x")


def test_missing_file_is_an_empty_available_list(tmp_path):
    bl = Blocklist.load(tmp_path)
    assert bl.available is True and len(bl) == 0
    assert not bl.is_blocked(UID)


async def test_permission_error_fails_closed_instead_of_empty(tmp_path, monkeypatch):
    # Unreadable (antivirus/backup locking) is not absent; only FileNotFoundError means an empty table
    from main_logic.visit import limits

    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text('{"blocked": []}', encoding="utf-8")

    def locked(*_a, **_k):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(limits, "read_json", locked)
    assert Blocklist.load(tmp_path).available is False
    assert (await Blocklist.aload(tmp_path)).available is False


async def test_cancelled_ablock_still_lands_in_memory_and_on_disk(tmp_path, monkeypatch):
    # Caller cancelled during disk writing: complete the transaction normally, keeping memory and disk consistent
    import asyncio
    import threading

    from main_logic.visit import limits

    bl = Blocklist.load(tmp_path)
    gate = threading.Event()
    real = limits.atomic_write_json

    def slow_write(path, payload):
        gate.wait(5)
        real(path, payload)

    monkeypatch.setattr(limits, "atomic_write_json", slow_write)
    task = asyncio.create_task(bl.ablock(UID, display_name_at_block="Mimi"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    for _ in range(200):
        if bl.is_blocked(UID):
            break
        await asyncio.sleep(0.01)
    assert bl.is_blocked(UID)
    assert Blocklist.load(tmp_path).is_blocked(UID)


def test_token_bucket_caps_an_oversized_cost_only_when_asked():
    # Outbox byte-bucket capacity may be smaller than a large message: cap debit at capacity, or it can never accumulate enough
    from main_logic.visit.limits import TokenBucket

    capped = TokenBucket.full(10.0, 10.0, 0.0, cap_cost=True)
    assert capped.fits(50)
    capped.charge(50)
    assert capped.tokens == 0.0
    assert not capped.fits(1)
    capped.refill(1.0)
    assert capped.fits(50)
    plain = TokenBucket.full(10.0, 10.0, 0.0)
    assert not plain.fits(50) and not plain.take(50, 100.0)


def test_there_is_no_unlocked_sync_mutation_path():
    # Unlocked synchronous block/unblock can overwrite interleaved ablock changes; retain only serialized asynchronous versions
    assert not hasattr(Blocklist, "block") and not hasattr(Blocklist, "unblock")


async def test_two_instances_on_one_file_keep_each_others_rows(tmp_path):
    # Two instances with separate locks and old views would overwrite earlier block records when the later full table is saved
    a = Blocklist.load(tmp_path)
    b = Blocklist.load(tmp_path)
    other = "f" * 24
    assert await a.ablock(UID, display_name_at_block="A", now=1.0)
    assert await b.ablock(other, display_name_at_block="B", now=2.0)
    on_disk = Blocklist.load(tmp_path)
    assert on_disk.is_blocked(UID) and on_disk.is_blocked(other)
    assert b.is_blocked(UID)                      # Refresh to the latest state while writing
    assert await a.aunblock(other)                # a can unblock an entry absent from its previous view
    assert not Blocklist.load(tmp_path).is_blocked(other)


async def test_concurrent_writes_from_two_instances_are_serialised(tmp_path, monkeypatch):
    import asyncio

    from main_logic.visit import limits

    a = Blocklist.load(tmp_path)
    b = Blocklist.load(tmp_path)
    import time as _time

    real = limits.atomic_write_json

    def slow(path, payload):
        _time.sleep(0.05)
        real(path, payload)

    monkeypatch.setattr(limits, "atomic_write_json", slow)
    other = "e" * 24
    await asyncio.gather(a.ablock(UID, display_name_at_block="A", now=1.0),
                         b.ablock(other, display_name_at_block="B", now=2.0))
    on_disk = Blocklist.load(tmp_path)
    assert on_disk.is_blocked(UID) and on_disk.is_blocked(other)


def test_instances_on_different_event_loops_and_threads_do_not_lose_rows(tmp_path, monkeypatch):
    # Two threads, separate event loops and instances: pause A after its transactional disk read, then start B.
    # A path-registered thread lock prevents B reaching its disk read; without it, B reads the same old table and
    # their writes overwrite each other. Use handshakes, not timing barriers, so conclusions do not depend on CI scheduling
    import asyncio
    import contextvars
    import threading

    from main_logic.visit import limits

    who: contextvars.ContextVar[str] = contextvars.ContextVar("who", default="")
    uid_a, uid_b = "a" * 24, "b" * 24
    bl_a, bl_b = Blocklist.load(tmp_path), Blocklist.load(tmp_path)
    a_read, release_a, b_read = threading.Event(), threading.Event(), threading.Event()
    real_read = limits.read_json

    def read_hook(path):
        # Read first (FileNotFoundError if absent), retain the result, signal/pause, then return it unchanged
        try:
            result: object = real_read(path)
        except FileNotFoundError as exc:
            result = exc
        if who.get() == "a":
            a_read.set()
            release_a.wait(5)
        else:
            b_read.set()
        if isinstance(result, FileNotFoundError):
            raise result
        return result

    monkeypatch.setattr(limits, "read_json", read_hook)
    errors: list[Exception] = []

    def worker(bl, uid):
        try:
            asyncio.run(bl.ablock(uid, display_name_at_block="x"))
        except Exception as exc:          # noqa: BLE001 - collect exceptions and assert them on the main thread
            errors.append(exc)

    # Transactions run in asyncio.to_thread workers; distinguish disk readers using contextvars,
    # which to_thread copies into the worker
    def run_a():
        who.set("a")
        worker(bl_a, uid_a)

    def run_b():
        who.set("b")
        worker(bl_b, uid_b)

    ta = threading.Thread(target=run_a)
    ta.start()
    assert a_read.wait(5)                  # A completed its transactional read and pauses while holding the lock
    tb = threading.Thread(target=run_b)
    tb.start()
    entered = b_read.wait(1.0)             # With the lock, B cannot enter
    release_a.set()
    ta.join(10)
    tb.join(10)
    monkeypatch.undo()
    assert errors == []
    assert not entered, "a second transaction read the file while the first held the lock"
    on_disk = Blocklist.load(tmp_path)
    assert on_disk.is_blocked(uid_a) and on_disk.is_blocked(uid_b)


async def test_a_block_through_one_instance_is_seen_by_every_live_instance(tmp_path):
    # The identity verifier's startup-loaded instance must immediately see users newly blocked elsewhere
    verifier = Blocklist.load(tmp_path)
    panel = Blocklist.load(tmp_path)
    assert not verifier.is_blocked(UID)
    assert await panel.ablock(UID, display_name_at_block="Mimi")
    assert verifier.is_blocked(UID)
    assert await panel.aunblock(UID)
    assert not verifier.is_blocked(UID)
    # Constructing an instance without entries must not clear the table seen by others
    await panel.ablock(UID, display_name_at_block="Mimi")
    Blocklist(tmp_path)
    assert verifier.is_blocked(UID)


def test_a_stale_load_cannot_overwrite_a_concurrent_block(tmp_path, monkeypatch):
    # Pause load after reading the old file, then block through another instance: with locking, block waits until load refreshes the snapshot,
    # avoiding overwrite; without locking, load later replaces the shared snapshot with the old table and verification admits the newly blocked user
    import asyncio
    import threading

    from main_logic.visit import limits

    verifier = Blocklist.load(tmp_path)
    writer = Blocklist.load(tmp_path)
    load_read, release_load = threading.Event(), threading.Event()
    real_read = limits.read_json
    calls = {"n": 0}

    def read_hook(path):
        calls["n"] += 1
        first = calls["n"] == 1
        try:
            result: object = real_read(path)
        except FileNotFoundError as exc:
            result = exc
        if first:                                 # The first call is load: pause after reading
            load_read.set()
            release_load.wait(5)
        if isinstance(result, FileNotFoundError):
            raise result
        return result

    monkeypatch.setattr(limits, "read_json", read_hook)
    loader = threading.Thread(target=lambda: Blocklist.load(tmp_path))
    loader.start()
    assert load_read.wait(5)
    blocked = threading.Event()

    def block():
        asyncio.run(writer.ablock(UID, display_name_at_block="Mimi"))
        blocked.set()

    # Whether load holds this per-path lock after reading is precisely the tested property, independent of timing
    from main_logic.visit.subjects import path_lock

    held = path_lock(verifier.path).locked()
    blocker = threading.Thread(target=block)
    blocker.start()
    if not held:
        # Deterministically reproduce 'block finishes before old-table overwrite' when locking fails: release load only after block completes
        assert blocked.wait(5)
    release_load.set()
    loader.join(10)
    blocker.join(10)
    monkeypatch.undo()
    assert held, "load must hold the per-path lock while it refreshes the shared snapshot"
    assert blocked.is_set() and verifier.is_blocked(UID)


async def test_a_failed_read_fails_every_live_instance_closed(tmp_path):
    # If any instance detects corruption, the verifier's long-lived instance must also fail closed
    from main_logic.visit.limits import BlocklistUnavailable

    verifier = Blocklist.load(tmp_path)
    writer = Blocklist.load(tmp_path)
    await writer.ablock(UID, display_name_at_block="Mimi")
    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(BlocklistUnavailable):
        await writer.ablock("f" * 24, display_name_at_block="x")
    assert verifier.available is False
    with pytest.raises(BlocklistUnavailable):
        verifier.is_blocked("e" * 24)
    # After repair and successful reload, every instance recovers together
    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text('{"blocked": []}', encoding="utf-8")
    Blocklist.load(tmp_path)
    assert verifier.available is True and not verifier.is_blocked(UID)
    # Constructing without entries must not reset shared unavailability to available
    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text("{broken", encoding="utf-8")
    Blocklist.load(tmp_path)
    Blocklist(tmp_path)
    assert verifier.available is False


async def test_a_deeply_nested_blocklist_fails_closed(tmp_path):
    # Deep JSON nesting raises RecursionError: fail closed like other unreadable cases instead of escaping load/block
    from main_logic.visit.limits import BlocklistUnavailable

    writer = Blocklist.load(tmp_path)
    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text("[" * 5000, encoding="utf-8")
    with pytest.raises(BlocklistUnavailable):
        await writer.ablock(UID, display_name_at_block="Mimi")
    assert writer.available is False
    assert Blocklist.load(tmp_path).available is False
    assert (await Blocklist.aload(tmp_path)).available is False


async def test_a_mutation_recovers_from_a_transient_read_failure(tmp_path, monkeypatch):
    # After a transient read failure marks shared state unavailable, a successful reread on the next block restores all instances once disk recovers,
    # without requiring callers to load separately
    from main_logic.visit import limits
    from main_logic.visit.limits import BlocklistUnavailable

    verifier = Blocklist.load(tmp_path)
    writer = Blocklist.load(tmp_path)
    real_read = limits.read_json

    def locked(*_a, **_k):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(limits, "read_json", locked)
    with pytest.raises(BlocklistUnavailable):
        await writer.ablock(UID, display_name_at_block="Mimi")
    assert verifier.available is False
    monkeypatch.setattr(limits, "read_json", real_read)
    assert await writer.ablock(UID, display_name_at_block="Mimi") is True
    assert verifier.available is True and verifier.is_blocked(UID)
    # Recover with an existing file too, through the successful-parse branch
    monkeypatch.setattr(limits, "read_json", locked)
    with pytest.raises(BlocklistUnavailable):
        await writer.aunblock(UID)
    monkeypatch.setattr(limits, "read_json", real_read)
    assert await writer.aunblock(UID) is True
    assert verifier.available is True and not verifier.is_blocked(UID)
