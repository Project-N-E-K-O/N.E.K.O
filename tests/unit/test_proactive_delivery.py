"""Unit tests for the generic ProactiveDeliveryManager front stage.

Covers the behaviours the manager adds in front of the existing enqueue/
trigger delivery core: priority ordering (HIGHER = more important, unspecified
0 = least), OPT-IN coalescing, the playback gate (don't release while audio
plays), BATCHED release (cues piled up while speaking go out together in one
turn), min-gap pacing, and drain-on-teardown (cues are handed back, never
silently dropped).
"""
import asyncio

import pytest

import main_logic.core as core_module
from main_logic.proactive_delivery import (
    DELIVERY_ACK_FUTURE_KEY,
    DELIVERY_RETRACTED_KEY,
    ProactiveDeliveryManager,
    SWAP_PRIME_DELIVERY_CLAIM_KEY,
    VOICE_DELIVERY_COMMITTED_KEY,
    effective_priority,
)

pytestmark = pytest.mark.unit


def _make(delivered, **kw):
    async def deliver(batch):
        # deliver receives the WHOLE batch (list of callbacks) per release.
        delivered.extend(batch)
    kw.setdefault("min_gap_s", 0.0)
    kw.setdefault("inflight_timeout_s", 0.05)
    return ProactiveDeliveryManager(deliver=deliver, **kw)


async def _settle():
    # Let scheduled call_later(0)/create_task work run.
    for _ in range(5):
        await asyncio.sleep(0.01)


async def _wait_until(predicate, *, timeout=5.0, what="condition"):
    # 轮询到条件成立，超时才报错。不能用固定 sleep 等后台跑完：Windows 事件
    # 循环时钟精度 15.625ms，短于一格的 sleep 会在下一轮循环立刻返回（等于零
    # 等待），长于一格的又成倍超发，等多久完全取决于与被测逻辑无关的其它活动。
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError(f"{what} not satisfied within {timeout}s")
        await asyncio.sleep(0.005)


async def _stays_true(predicate, *, until, what="condition"):
    # 按真实时钟走到 until（绝对时刻），期间每圈复查。"窗口内还没发生"这类负向断言
    # 只在一瞬间取样是没有牙齿的：被测窗口被误缩短十倍时单点取样照样通过（实测过），
    # 只有按真实时钟走一段才测得出窗口本身还在。
    # until 必须是绝对时刻而不是时长：相对时长会在前面的等待被拖长时把观察窗口推到
    # 被测窗口之外，那时发生的放行是合法的，断言就变成假红。
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(0.005)
        # 醒来后先复查挂钟再断言。这一觉可能被别的任务拖长而睡过了窗口。
        if loop.time() >= until:
            return
        assert predicate(), f"{what} broke before {until}"


def test_effective_priority_normalisation():
    # HIGHER = more important; unspecified / invalid → 0 (least important).
    assert effective_priority(1) == 1
    assert effective_priority(9) == 9
    assert effective_priority(0) == 0
    assert effective_priority(None) == 0
    assert effective_priority("x") == 0
    # A cue that set any positive priority outranks an unspecified one.
    assert effective_priority(2) > effective_priority(0)


async def test_batch_released_together_in_priority_order():
    # Cues that pile up while she's speaking are released as ONE batch when
    # the gate opens, sorted by importance DESC (higher first), unspecified last.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "keep_going"}, priority=3, coalesce_key="a")
    mgr.submit({"id": "alert"}, priority=9, coalesce_key="b")
    mgr.submit({"id": "unspecified"}, priority=0, coalesce_key="c")
    await _settle()
    assert delivered == []  # nothing released while playing
    mgr.on_playback_end()   # gate opens → whole batch released at once
    await _settle()
    assert [c["id"] for c in delivered] == ["alert", "keep_going", "unspecified"]


async def test_coalescing_is_opt_in():
    # Same explicit key → newest replaces older.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "old"}, priority=2, coalesce_key="dup")
    mgr.submit({"id": "new"}, priority=2, coalesce_key="dup")
    await _settle()
    mgr.on_playback_end()
    await _settle()
    assert [c["id"] for c in delivered] == ["new"]


async def test_coalescing_resolves_dropped_delivery_ack_false():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    old_future = asyncio.get_running_loop().create_future()
    mgr.submit({"id": "old", DELIVERY_ACK_FUTURE_KEY: old_future}, priority=2, coalesce_key="dup")
    mgr.submit({"id": "new"}, priority=2, coalesce_key="dup")

    assert old_future.done()
    assert old_future.result() is False


async def test_no_coalesce_key_never_collapses():
    # Unset key → unique → both delivered (no silent drop). This is the
    # non-regression guarantee for plugins that didn't opt in.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "a"}, priority=2)
    mgr.submit({"id": "b"}, priority=2)
    await _settle()
    mgr.on_playback_end()
    await _settle()
    assert sorted(c["id"] for c in delivered) == ["a", "b"]


async def test_playback_gate_holds_until_end():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "x"}, priority=1)
    await _settle()
    assert delivered == []          # gate closed while playing
    mgr.on_playback_end()
    await _settle()
    assert [c["id"] for c in delivered] == ["x"]


async def test_second_batch_waits_for_next_play_end():
    # After one batch is released (in-flight), cues that arrive during its
    # playback must wait for the NEXT voice_play_end, not pile on immediately.
    delivered = []
    mgr = _make(delivered, inflight_timeout_s=5.0)
    mgr.submit({"id": "a"}, priority=1)
    await _settle()
    assert [c["id"] for c in delivered] == ["a"]   # first batch out (gate open)
    mgr.on_playback_start()                         # a is now playing
    mgr.submit({"id": "b"}, priority=1)             # arrives mid-playback
    await _settle()
    assert [c["id"] for c in delivered] == ["a"]   # b held, not delivered
    mgr.on_playback_end()
    await _settle()
    assert [c["id"] for c in delivered] == ["a", "b"]


async def test_noop_release_frees_inflight_slot_immediately():
    # A release that delivers nothing (e.g. every cue dropped at a release-time
    # gate) emits no playback signal, so without an explicit release the slot
    # would stay armed for the whole inflight timeout and hold back the next
    # cue. release_inflight_noop frees it so the follow-up cue goes out promptly.
    delivered = []
    mgr = None

    async def deliver(batch):
        delivered.append([c for c in batch])
        if len(delivered) == 1:
            mgr.release_inflight_noop()  # simulate the gate-drop no-op release

    mgr = ProactiveDeliveryManager(deliver=deliver, min_gap_s=0.0, inflight_timeout_s=5.0)
    mgr.submit({"id": "dropped"}, priority=1)
    await _settle()
    assert len(delivered) == 1  # first batch released, delivered nothing

    mgr.submit({"id": "second"}, priority=1)
    await _settle()
    # slot was freed immediately, so 'second' is delivered within _settle
    # rather than after the 5s inflight timeout.
    assert [batch[0]["id"] for batch in delivered] == ["dropped", "second"]


async def test_min_gap_delays_release():
    delivered = []
    # min-gap 抬到 1.0s：负向断言必须落在窗口内，而 Windows 上 sleep 和 pump 各
    # 自会超发一格时钟（15.625ms），0.2s 的窗口给"还没到点"留的余量不足十倍。
    min_gap = 1.0
    mgr = _make(delivered, min_gap_s=min_gap)
    loop = asyncio.get_running_loop()
    mgr.on_playback_start()
    mgr.submit({"id": "x"}, priority=1)
    mgr.on_playback_end()           # records last_play_end; gap not elapsed
    window_end = loop.time() + min_gap
    # 不能先 _settle() 再裸断言：那是 5 次不受检的真实 sleep，被拖长到超过 min-gap
    # 之后放行是合法的，裸断言就会假红。交给 _stays_true 并给它**绝对**的窗口终点，
    # 第一次醒来（约 5ms）时 call_later(0) 那轮 pump 早已跑过，所以「不受 min-gap
    # 约束就会立刻放行」这个回归照样会被第一次断言抓住。走到窗口的 80% 处收手。
    await _stays_true(
        lambda: delivered == [], until=window_end - 0.2, what="min-gap window"
    )
    await _wait_until(lambda: bool(delivered), what="min-gap release")
    assert [c["id"] for c in delivered] == ["x"]


async def test_playing_watchdog_recovers_missing_play_end():
    # voice_play_start with no matching voice_play_end (frontend disconnect)
    # must not wedge the queue forever — the max_play watchdog re-opens it.
    delivered = []
    max_play = 0.5
    mgr = _make(delivered, max_play_s=max_play)
    loop = asyncio.get_running_loop()
    mgr.on_playback_start()          # ...and voice_play_end never arrives
    window_end = loop.time() + max_play
    mgr.submit({"id": "x"}, priority=1)
    armed = mgr._pump_handle         # the call_later(0) submit just scheduled
    # 负向那半不睡固定时长：0.05s 的 sleep 在 Windows 上实际耗 62.5ms，离窗口边界
    # 只剩几格时钟，随时滑过看门狗变假红。改成等这一轮 pump 真的跑完——它没有放
    # 行，而是把自己重排到看门狗到点（handle 被换掉），这才是"窗口内"的确定证据。
    await _wait_until(lambda: mgr._pump_handle is not armed, what="first pump run")
    # 但要先确认这一轮 pump 仍落在看门狗窗口内才有资格断言「还没放行」：事件循环被
    # 拖到窗口之后才跑第一轮 pump 时，那一轮直接走看门狗分支放行是**正确行为**。
    if loop.time() < window_end:
        assert delivered == []           # still within max_play window
        assert mgr._playing              # 闸门确实还关着，正向断言才不会空过
        assert mgr._pump_handle is not None  # 看门狗自己会到点，不靠外部再踢一脚
        # 上面几条只是窗口内某一瞬的取样，看门狗窗口被误缩短时它们照样通过；
        # 只有按真实时钟走到窗口的 80% 处才能证明窗口本身还在。
        await _stays_true(
            lambda: delivered == [], until=window_end - 0.1, what="max_play window"
        )
    await _wait_until(lambda: bool(delivered), what="watchdog release")
    assert [c["id"] for c in delivered] == ["x"]


async def test_drain_pending_returns_queue_without_delivering():
    # Teardown path: drain_pending hands queued cues back (for the caller to
    # move into pending_agent_callbacks) instead of dropping them.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()          # gate closed → cues queue up
    # Submit out of priority order; drain must return importance-DESC (FIFO
    # ties) so redelivery preserves ordering.
    mgr.submit({"id": "a"}, priority=1)
    mgr.submit({"id": "b"}, priority=2)
    drained = mgr.drain_pending()
    assert [c["id"] for c in drained] == ["b", "a"]
    await _settle()
    assert delivered == []           # drained, not delivered by the manager
    # And the queue really is empty now: opening the gate releases nothing.
    mgr.on_playback_end()
    await _settle()
    assert delivered == []


async def test_drain_pending_keeps_delivery_ack_pending_for_redelivery():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    future = asyncio.get_running_loop().create_future()
    mgr.submit({"id": "queued", DELIVERY_ACK_FUTURE_KEY: future}, priority=2)

    drained = mgr.drain_pending()

    assert [c["id"] for c in drained] == ["queued"]
    assert not future.done()


async def test_reset_gate_clears_gate_but_keeps_queue():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()          # gate closed → cue queues up
    mgr.submit({"id": "queued"}, priority=2)
    mgr.reset_gate()                 # clears playing/inflight; queue PRESERVED
    await _settle()
    # reset_gate alone does NOT release (it cancels the pump, no auto-pump).
    assert delivered == []
    # Next submit re-opens the pump; the preserved cue rides out in the SAME
    # batch (importance order), proving reset_gate didn't drop the queue.
    mgr.submit({"id": "c"}, priority=1)
    await _settle()
    assert [c["id"] for c in delivered] == ["queued", "c"]


async def test_stale_cue_dropped_by_ttl():
    delivered = []
    mgr = _make(delivered, ttl_s=0.05)
    mgr.on_playback_start()         # gate closed so the cue waits and ages
    mgr.submit({"id": "stale"}, priority=1)
    await asyncio.sleep(0.1)        # exceed ttl
    mgr.on_playback_end()
    await _settle()
    assert delivered == []          # dropped as stale, never spoken


# ── enqueue_agent_callback path (passive / ai_behavior="read") ────────────────
# The ProactiveDeliveryManager above only governs proactive ("respond") cues.
# Passive/read cues bypass it and land directly in pending_agent_callbacks; the
# same OPT-IN coalesce_key semantics apply there so a rapid read-stream can
# dedup queued snapshots by key instead of piling up until the flood guard.


class _FakeAckFuture:
    """Minimal delivery-ack future stand-in (no event loop needed)."""

    def __init__(self):
        self._done = False
        self.result = None

    def done(self):
        return self._done

    def set_result(self, value):
        self._done = True
        self.result = value


def _make_session_mgr():
    mgr = core_module.LLMSessionManager.__new__(core_module.LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.pending_agent_callbacks = []
    mgr.pending_extra_replies = []
    # Identity normalizer: isolate the per-source token-budget path, which is
    # irrelevant to coalescing and pulls in config/budget dependencies.
    mgr._normalize_context_text_for_source = lambda _src, text: text
    return mgr


def _passive_cb(summary, *, coalesce_key="", **extra):
    cb = {
        "event": "agent_task_callback",
        "origin": "event",
        "summary": summary,
        "detail": summary,
        "status": "completed",
        "delivery_mode": "passive",
        "coalesce_key": coalesce_key,
    }
    cb.update(extra)
    return cb


def _proactive_cb(summary, *, coalesce_key="", **extra):
    cb = _passive_cb(summary, coalesce_key=coalesce_key, **extra)
    cb["delivery_mode"] = "proactive"
    return cb


def test_enqueue_coalesce_same_key_newest_replaces():
    # Same explicit key → newest collapses the older passive cue in the
    # LLM-inject queue. Passive cues never create a voice hot-swap mirror.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("old snapshot", coalesce_key="gamestate"))
    mgr.enqueue_agent_callback(_passive_cb("new snapshot", coalesce_key="gamestate"))
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["new snapshot"]
    assert mgr.pending_extra_replies == []


def test_enqueue_coalesce_empty_key_never_collapses():
    # Unset / explicit-empty key never coalesces — the non-regression guarantee
    # for read-cues that didn't opt in.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("a"))                    # no key
    mgr.enqueue_agent_callback(_passive_cb("b"))                    # no key
    mgr.enqueue_agent_callback(_passive_cb("c", coalesce_key=""))   # explicit empty
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["a", "b", "c"]
    assert mgr.pending_extra_replies == []


def test_enqueue_coalesce_distinct_keys_independent():
    # Only the matching key collapses; a different key is untouched.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("x1", coalesce_key="x"))
    mgr.enqueue_agent_callback(_passive_cb("y1", coalesce_key="y"))
    mgr.enqueue_agent_callback(_passive_cb("x2", coalesce_key="x"))
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["y1", "x2"]
    assert mgr.pending_extra_replies == []


def test_enqueue_proactive_still_creates_voice_mirror():
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_proactive_cb("respond now", coalesce_key="notice"))
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["respond now"]
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["respond now"]


def test_enqueue_newer_proactive_replaces_passive_and_creates_mirror():
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("context", coalesce_key="shared"))
    mgr.enqueue_agent_callback(_proactive_cb("respond", coalesce_key="shared"))
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["respond"]
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["respond"]


def test_enqueue_newer_passive_replaces_proactive_and_removes_mirror():
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_proactive_cb("respond", coalesce_key="shared"))
    mgr.enqueue_agent_callback(_passive_cb("context", coalesce_key="shared"))
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["context"]
    assert mgr.pending_extra_replies == []


def test_enqueue_unknown_delivery_mode_keeps_legacy_proactive_behavior():
    mgr = _make_session_mgr()
    callback = _passive_cb("legacy")
    callback["delivery_mode"] = "PASSIVE"
    mgr.enqueue_agent_callback(callback)
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["legacy"]
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["legacy"]


def test_passive_flood_guard_bounds_callback_queue_without_extras(monkeypatch):
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_QUEUE_MAX_ITEMS", 2)
    mgr = _make_session_mgr()
    dropped_ack = _FakeAckFuture()
    oldest = _passive_cb("oldest")
    oldest[DELIVERY_ACK_FUTURE_KEY] = dropped_ack
    mgr.enqueue_agent_callback(oldest)
    mgr.enqueue_agent_callback(_passive_cb("middle"))
    mgr.enqueue_agent_callback(_passive_cb("newest"))

    assert [c["summary"] for c in mgr.pending_agent_callbacks] == [
        "middle",
        "newest",
    ]
    assert mgr.pending_extra_replies == []
    assert dropped_ack.done() and dropped_ack.result is False


def test_flood_guard_rejects_incoming_when_older_send_started(monkeypatch):
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_QUEUE_MAX_ITEMS", 1)
    mgr = _make_session_mgr()
    committed_ack = _FakeAckFuture()
    incoming_ack = _FakeAckFuture()
    committed = _passive_cb("provider send started")
    committed[VOICE_DELIVERY_COMMITTED_KEY] = True
    committed[DELIVERY_ACK_FUTURE_KEY] = committed_ack
    incoming = _passive_cb("cannot displace committed")
    incoming[DELIVERY_ACK_FUTURE_KEY] = incoming_ack
    mgr.pending_agent_callbacks = [committed]

    mgr.enqueue_agent_callback(incoming)

    assert mgr.pending_agent_callbacks == [committed]
    assert not committed_ack.done()
    assert incoming_ack.done() and incoming_ack.result is False
    assert incoming.get(DELIVERY_RETRACTED_KEY) is True


@pytest.mark.parametrize(
    "ownership_key",
    [VOICE_DELIVERY_COMMITTED_KEY, SWAP_PRIME_DELIVERY_CLAIM_KEY],
)
@pytest.mark.parametrize("pre_submitted", [False, True])
def test_flood_rejected_newer_does_not_stale_provider_owned_old(
    monkeypatch,
    ownership_key,
    pre_submitted,
):
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_QUEUE_MAX_ITEMS", 1)
    mgr = _make_session_mgr()
    old = _passive_cb("provider-owned old", coalesce_key="state")
    mgr.enqueue_agent_callback(old)
    old[ownership_key] = True
    old_seq = old["_coalesce_submit_seq"]

    rejected_ack = _FakeAckFuture()
    newer = _proactive_cb("flood rejected newer", coalesce_key="state")
    newer[DELIVERY_ACK_FUTURE_KEY] = rejected_ack
    if pre_submitted:
        class _ManagerStub:
            def submit(self, callback, **_kwargs):
                self.submitted = callback

        manager = _ManagerStub()
        mgr.proactive_manager = manager
        mgr.is_goodbye_silent = lambda: False
        mgr.submit_proactive_callback(newer, coalesce_key="state")
        assert manager.submitted is newer
        assert mgr._coalesce_latest["state"] == newer["_coalesce_submit_seq"]
    mgr.enqueue_agent_callback(newer)

    assert mgr.pending_agent_callbacks == [old]
    assert rejected_ack.done() and rejected_ack.result is False
    assert mgr._coalesce_latest["state"] == old_seq
    old.pop(ownership_key)
    assert mgr._retract_stale_coalesced([old]) is False


def test_flood_rollback_preserves_newer_manager_held_sequence(monkeypatch):
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_QUEUE_MAX_ITEMS", 1)
    mgr = _make_session_mgr()
    old = _passive_cb("claimed old", coalesce_key="state")
    mgr.enqueue_agent_callback(old)
    old[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True

    async def _deliver(_batch):
        return None

    mgr.proactive_manager = ProactiveDeliveryManager(deliver=_deliver)
    mgr.is_goodbye_silent = lambda: False
    held = _proactive_cb("manager-held newer", coalesce_key="state")
    mgr.submit_proactive_callback(held, coalesce_key="state")

    rejected = _passive_cb("flood-rejected newest", coalesce_key="state")
    mgr.enqueue_agent_callback(rejected)

    assert mgr.pending_agent_callbacks == [old]
    assert rejected.get(DELIVERY_RETRACTED_KEY) is True
    assert mgr._coalesce_latest["state"] == held["_coalesce_submit_seq"]
    old.pop(SWAP_PRIME_DELIVERY_CLAIM_KEY)
    assert mgr._retract_stale_coalesced([old]) is True


def test_newer_same_key_keeps_committed_callback_voice_mirror():
    mgr = _make_session_mgr()
    committed = _proactive_cb("provider send started", coalesce_key="state")
    mgr.enqueue_agent_callback(committed)
    committed[VOICE_DELIVERY_COMMITTED_KEY] = True
    committed_id = committed["_callback_delivery_id"]

    newer = _proactive_cb("next snapshot", coalesce_key="state")
    mgr.enqueue_agent_callback(newer)

    assert mgr.pending_agent_callbacks == [committed, newer]
    assert [extra["summary"] for extra in mgr.pending_extra_replies] == [
        "provider send started",
        "next snapshot",
    ]
    assert mgr.pending_extra_replies[0]["_callback_delivery_id"] == committed_id


def test_extra_flood_guard_keeps_provider_owned_voice_mirror(monkeypatch):
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_QUEUE_MAX_ITEMS", 1)
    mgr = _make_session_mgr()
    committed = _proactive_cb("provider send started")
    mgr.enqueue_agent_callback(committed)
    committed[VOICE_DELIVERY_COMMITTED_KEY] = True
    committed_mirror = mgr.pending_extra_replies[0]
    mgr.pending_extra_replies.append({
        "_callback_delivery_id": "orphan-id",
        "summary": "safe orphan",
    })

    # A passive incoming callback triggers both flood guards. It is rejected
    # from the callback queue; the unrelated orphan, not the committed mirror,
    # is the only safe extra victim.
    mgr.enqueue_agent_callback(_passive_cb("incoming"))

    assert mgr.pending_agent_callbacks == [committed]
    assert mgr.pending_extra_replies == [committed_mirror]


def test_enqueue_coalesce_resolves_superseded_ack_false():
    # A superseded cue's delivery-ack future resolves False immediately so a
    # waiter unblocks instead of stalling until timeout (parity with the
    # manager path).
    mgr = _make_session_mgr()
    fut = _FakeAckFuture()
    old = _passive_cb("old", coalesce_key="k")
    old[DELIVERY_ACK_FUTURE_KEY] = fut
    mgr.enqueue_agent_callback(old)
    mgr.enqueue_agent_callback(_passive_cb("new", coalesce_key="k"))
    assert fut.done() and fut.result is False
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["new"]


def test_enqueue_coalesce_marks_superseded_retracted():
    # A superseded cue must be FLAGGED retracted, not merely dropped: a voice
    # delivery already in flight snapshots pending_agent_callbacks before its
    # await and re-filters that snapshot only by DELIVERY_RETRACTED_KEY. Without
    # the flag the captured stale cue is still spoken even though its ack was
    # resolved False.
    mgr = _make_session_mgr()
    old = _passive_cb("old", coalesce_key="k")
    mgr.enqueue_agent_callback(old)
    mgr.enqueue_agent_callback(_passive_cb("new", coalesce_key="k"))
    assert old.get(DELIVERY_RETRACTED_KEY) is True
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["new"]


def test_enqueue_coalesce_older_manager_release_loses_to_newer_read():
    # Cross-path newest-wins: a respond cue held in ProactiveDeliveryManager
    # (submission seq stamped at submit_proactive_callback) that is RELEASED into
    # enqueue AFTER a newer same-key read cue was direct-queued must NOT overwrite
    # the newer read cue. The submission seq lets enqueue tell the late manager
    # release from a genuinely newer cue.
    mgr = _make_session_mgr()
    # A respond cue stamped early, then held by the manager during playback.
    respond = _proactive_cb("respond held", coalesce_key="k")
    respond["_coalesce_submit_seq"] = 1
    # A newer read cue enqueued directly gets a later seq.
    mgr._coalesce_seq_counter = 5  # next direct-enqueue seq = 6 > 1
    mgr.enqueue_agent_callback(_passive_cb("newer read", coalesce_key="k"))
    # The manager now releases the OLDER respond cue into the same queue.
    mgr.enqueue_agent_callback(respond)
    # Newer read cue survives; the stale respond is dropped AND retracted (so any
    # in-flight snapshot that captured it discards it too).
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["newer read"]
    assert respond.get(DELIVERY_RETRACTED_KEY) is True


def test_pull_model_retracts_checked_out_snapshot():
    # PULL model: a cue checked OUT of pending_agent_callbacks into a local
    # delivery snapshot is invisible to the enqueue-time push scan. When a
    # newer same-key cue arrives (bumping _coalesce_latest), the delivery point
    # calls _retract_stale_coalesced on its snapshot and the stale cue must be
    # retracted there — acked False and flagged — instead of being delivered.
    mgr = _make_session_mgr()
    old = _passive_cb("checked out", coalesce_key="k")
    mgr.enqueue_agent_callback(old)
    snapshot = list(mgr.pending_agent_callbacks)
    mgr.pending_agent_callbacks.clear()  # simulate checkout (text delivery path)
    # Newer same-key cue arrives while the old one is in-flight.
    mgr.enqueue_agent_callback(_passive_cb("newer", coalesce_key="k"))
    fut = _FakeAckFuture()
    snapshot[0][DELIVERY_ACK_FUTURE_KEY] = fut
    assert mgr._retract_stale_coalesced(snapshot) is True
    assert snapshot[0].get(DELIVERY_RETRACTED_KEY) is True
    assert fut.done() and fut.result is False
    # The newer cue is NOT stale (it holds the latest seq itself).
    assert mgr._retract_stale_coalesced(list(mgr.pending_agent_callbacks)) is False


def test_pull_model_manager_held_window():
    # The staleness map is bumped at SUBMIT time (submit_proactive_callback),
    # not only at enqueue — so an older direct-queued cue is already stale
    # while the newer respond cue is still held by the delivery manager.
    mgr = _make_session_mgr()
    old = _passive_cb("old read", coalesce_key="k")
    mgr.enqueue_agent_callback(old)
    # Newer respond cue submitted; manager holds it (we stub the manager).
    class _MgrStub:
        def submit(self, cb, **kw):
            pass
    mgr.proactive_manager = _MgrStub()
    mgr.is_goodbye_silent = lambda: False
    newer = {"status": "completed", "summary": "respond held"}
    core_module.LLMSessionManager.submit_proactive_callback(
        mgr, newer, priority=1, coalesce_key="k",
    )
    # The old cue must now test stale even though the newer one never enqueued.
    assert mgr._coalesce_entry_is_stale(old) is True
    assert mgr._coalesce_entry_is_stale(newer) is False


def test_pull_model_stale_proactive_extra_is_detected():
    # _coalesce_entry_is_stale works on pending_extra_replies mirrors too (the
    # hot-swap prime guard filters its selection with it). Legacy plain-string
    # extras are never stale.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_proactive_cb("old", coalesce_key="k"))
    old_extra = mgr.pending_extra_replies[0]
    mgr.enqueue_agent_callback(_proactive_cb("new", coalesce_key="k"))
    assert mgr._coalesce_entry_is_stale(old_extra) is True
    assert mgr._coalesce_entry_is_stale(mgr.pending_extra_replies[-1]) is False
    assert mgr._coalesce_entry_is_stale("legacy plain string") is False


async def test_deliver_batch_releases_inflight_when_all_superseded():
    # An older manager-released respond cue that loses to a newer same-key
    # direct cue during enqueue produces an all-retracted batch; the release
    # hook must free the manager's inflight slot instead of stalling until the
    # inflight timeout (and must not fire a trigger for nothing).
    mgr = _make_session_mgr()
    released = []
    class _MgrStub:
        def release_inflight_noop(self):
            released.append(True)
    mgr.proactive_manager = _MgrStub()
    triggered = []
    async def _fake_trigger():
        triggered.append(True)
        return True
    mgr.trigger_agent_callbacks = _fake_trigger
    mgr._topic_hook_release_allowed = lambda cb: True
    # Newer same-key cue already direct-queued with a higher seq.
    mgr._coalesce_seq_counter = 5
    mgr.enqueue_agent_callback(_passive_cb("newer read", coalesce_key="k"))
    # Manager releases the OLDER respond cue (stamped seq=1 at submit time).
    stale = _proactive_cb("older respond", coalesce_key="k")
    stale["_coalesce_submit_seq"] = 1
    await core_module.LLMSessionManager._deliver_proactive_batch(mgr, [stale])
    assert released == [True]   # inflight slot freed immediately
    assert triggered == []      # no pointless trigger for an empty batch
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["newer read"]


def test_drain_skips_read_superseded_by_manager_held_respond():
    # Codex scenario: a newer respond cue is submitted and PARKED in the
    # delivery manager (playback / min-gap keeps it from releasing). If the
    # user's next text turn arrives first, the passive drain must already see
    # the older same-key read cue as stale — the submit-time bump of
    # _coalesce_latest plus the drain-point pull check cover the window where
    # release-time coalescing has not run yet.
    mgr = _make_session_mgr()
    old = _passive_cb("stale read", coalesce_key="k")
    fut = _FakeAckFuture()
    old[DELIVERY_ACK_FUTURE_KEY] = fut
    mgr.enqueue_agent_callback(old)

    class _MgrStub:  # manager holds the newer cue; never releases in this test
        def submit(self, cb, **kw):
            pass

    mgr.proactive_manager = _MgrStub()
    mgr.is_goodbye_silent = lambda: False
    core_module.LLMSessionManager.submit_proactive_callback(
        mgr, {"status": "completed", "summary": "newer respond"},
        priority=1, coalesce_key="k",
    )
    out = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)
    assert out == ""                      # stale read NOT injected
    assert fut.done() and fut.result is False
    assert mgr.pending_agent_callbacks == []  # purged, not redelivered later


def test_enqueue_coalesce_evicts_legacy_mirror_by_delivery_id():
    # A mirror created before the coalesce_key stamp existed (legacy shape,
    # paired by _callback_delivery_id only) must still be evicted when its
    # callback half is retracted by a same-key enqueue — key-only matching
    # would orphan it for the hot-swap path.
    mgr = _make_session_mgr()
    old = _proactive_cb("old", coalesce_key="k")
    mgr.enqueue_agent_callback(old)
    # Strip the key/seq stamps from the mirror to simulate the legacy shape.
    legacy_mirror = mgr.pending_extra_replies[0]
    legacy_mirror.pop("coalesce_key", None)
    legacy_mirror.pop("_coalesce_submit_seq", None)
    mgr.enqueue_agent_callback(_proactive_cb("new", coalesce_key="k"))
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["new"]


def test_enqueue_coalesce_guards_legacy_string_extra():
    # pending_extra_replies may hold a legacy plain-string entry that the render
    # path tolerates. A keyed enqueue must not raise on it (the broad except in
    # enqueue_agent_callback would otherwise swallow the error and silently drop
    # the new callback).
    mgr = _make_session_mgr()
    mgr.pending_extra_replies.append("legacy plain string")  # non-dict entry
    mgr.enqueue_agent_callback(_passive_cb("fresh", coalesce_key="k"))
    assert "fresh" in [
        c["summary"] for c in mgr.pending_agent_callbacks
    ]  # new callback survived, not swallowed
    assert "legacy plain string" in mgr.pending_extra_replies  # legacy left intact


def test_enqueue_coalesce_evicts_drained_extras_orphan():
    # A proactive callback can be drained by a text turn after its proactive
    # claim was deferred, leaving a paired voice mirror. A later same-key cue
    # must evict that orphan by its stamped key.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_proactive_cb("old snapshot", coalesce_key="gs"))
    mgr.pending_agent_callbacks.clear()  # simulate drain (callback side only)
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["old snapshot"]
    mgr.enqueue_agent_callback(_proactive_cb("new snapshot", coalesce_key="gs"))
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["new snapshot"]
