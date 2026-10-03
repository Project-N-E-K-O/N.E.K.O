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
    # 字节桶拒了，条数桶不应被扣。
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
    # 读不出来不能当空表：那会把被拉黑的人放进来；原文件留在原处待修复
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
    # 修好后重新加载即恢复
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
    # 丢掉坏行恰好会放进被拉黑的那个人：任一行坏就整体 fail closed
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
    # 读不了（被杀毒 / 备份锁住）≠ 不存在：只有 FileNotFoundError 才是空表
    from main_logic.visit import limits

    (tmp_path / VISIT_BLOCKLIST_FILENAME).write_text('{"blocked": []}', encoding="utf-8")

    def locked(*_a, **_k):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(limits, "read_json", locked)
    assert Blocklist.load(tmp_path).available is False
    assert (await Blocklist.aload(tmp_path)).available is False


async def test_cancelled_ablock_still_lands_in_memory_and_on_disk(tmp_path, monkeypatch):
    # 写盘途中调用方被取消：事务照常做完，内存与磁盘一致
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
    # outbox 的字节桶容量可能小于单条大消息：按容量封顶扣，否则它永远攒不够
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
    # 同步 block / unblock 不拿锁，和 ablock 交错会互相冲掉：只留串行化的异步版
    assert not hasattr(Blocklist, "block") and not hasattr(Blocklist, "unblock")


async def test_two_instances_on_one_file_keep_each_others_rows(tmp_path):
    # 两个实例各自一把锁、各自旧视图时，后写的整表会冲掉先写的拉黑记录
    a = Blocklist.load(tmp_path)
    b = Blocklist.load(tmp_path)
    other = "f" * 24
    assert await a.ablock(UID, display_name_at_block="A", now=1.0)
    assert await b.ablock(other, display_name_at_block="B", now=2.0)
    on_disk = Blocklist.load(tmp_path)
    assert on_disk.is_blocked(UID) and on_disk.is_blocked(other)
    assert b.is_blocked(UID)                      # 写入时顺带刷新到最新
    assert await a.aunblock(other)                # a 手里原本没有这条，也能解除
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
    # 两个线程、各自的事件循环、各自的实例：A 在事务里读完盘后停住，此时启动 B。
    # 有按路径登记的线程锁时 B 进不了读盘这一步；没有锁时 B 会读到同一份旧表、
    # 两边互相冲掉。用握手而不是计时屏障，结论不依赖 CI 调度快慢
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
        # 先读（文件还不存在时是 FileNotFoundError），记下结果再打信号 / 停住，最后原样交回
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
        except Exception as exc:          # noqa: BLE001 - 收集后在主线程断言
            errors.append(exc)

    # 事务跑在 asyncio.to_thread 的工作线程里，靠 contextvars（to_thread 会复制过去）
    # 区分是谁在读盘
    def run_a():
        who.set("a")
        worker(bl_a, uid_a)

    def run_b():
        who.set("b")
        worker(bl_b, uid_b)

    ta = threading.Thread(target=run_a)
    ta.start()
    assert a_read.wait(5)                  # A 已在事务内读完盘、持锁停住
    tb = threading.Thread(target=run_b)
    tb.start()
    entered = b_read.wait(1.0)             # 有锁：B 进不来
    release_a.set()
    ta.join(10)
    tb.join(10)
    monkeypatch.undo()
    assert errors == []
    assert not entered, "a second transaction read the file while the first held the lock"
    on_disk = Blocklist.load(tmp_path)
    assert on_disk.is_blocked(uid_a) and on_disk.is_blocked(uid_b)


async def test_a_block_through_one_instance_is_seen_by_every_live_instance(tmp_path):
    # 身份核验手里的实例（启动时加载）要立刻看到别处刚拉黑的人
    verifier = Blocklist.load(tmp_path)
    panel = Blocklist.load(tmp_path)
    assert not verifier.is_blocked(UID)
    assert await panel.ablock(UID, display_name_at_block="Mimi")
    assert verifier.is_blocked(UID)
    assert await panel.aunblock(UID)
    assert not verifier.is_blocked(UID)
    # 不带 entries 直接构造的实例不能清空别人看到的表
    await panel.ablock(UID, display_name_at_block="Mimi")
    Blocklist(tmp_path)
    assert verifier.is_blocked(UID)


def test_a_stale_load_cannot_overwrite_a_concurrent_block(tmp_path, monkeypatch):
    # load 读到旧文件后停住，此时另一个实例拉黑：有锁时拉黑要等 load 刷完快照，
    # 不会被旧内容覆盖；没锁时 load 随后用旧表冲掉共享快照，核验放行刚拉黑的人
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
        if first:                                 # 第一次是 load：读完停住
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

    blocker = threading.Thread(target=block)
    blocker.start()
    finished_early = blocked.wait(0.5)        # 有锁：拉黑等 load 完成
    release_load.set()
    loader.join(10)
    blocker.join(10)
    monkeypatch.undo()
    assert not finished_early
    assert verifier.is_blocked(UID)
