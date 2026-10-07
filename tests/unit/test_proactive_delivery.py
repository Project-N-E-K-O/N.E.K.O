"""Unit tests for the generic ProactiveDeliveryManager front stage.

Covers the behaviours the manager adds in front of the existing enqueue/
trigger delivery core: priority ordering (HIGHER = more important, unspecified
0 = least), OPT-IN coalescing, the playback gate (don't release while audio
plays), BATCHED release (cues piled up while speaking go out together in one
turn), min-gap pacing, and drain-on-teardown (cues are handed back, never
silently dropped).
"""
import asyncio
import time

import pytest

import main_logic.core as core_module
from main_logic.proactive_delivery import (
    DELIVERY_ACK_FUTURE_KEY,
    DELIVERY_RETRACTED_KEY,
    CALLBACK_EXPIRES_AT_KEY,
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


async def test_delivery_manager_defers_callback_expiry_to_session_filter():
    delivered = []
    mgr = _make(delivered, ttl_s=0)
    mgr.on_playback_start()
    future = asyncio.get_running_loop().create_future()
    mgr.submit(
        {
            "id": "expired",
            CALLBACK_EXPIRES_AT_KEY: time.monotonic() - 1,
            DELIVERY_ACK_FUTURE_KEY: future,
        },
        priority=1,
    )
    mgr.on_playback_end()
    await _settle()

    assert [callback["id"] for callback in delivered] == ["expired"]
    assert not future.done()


def test_drain_pending_preserves_expired_callback_for_session_filter():
    delivered = []
    mgr = _make(delivered, ttl_s=0)
    mgr.on_playback_start()
    future = _FakeAckFuture()
    mgr.submit(
        {
            "id": "expired",
            CALLBACK_EXPIRES_AT_KEY: time.monotonic() - 1,
            DELIVERY_ACK_FUTURE_KEY: future,
        },
        priority=1,
    )

    assert [callback["id"] for callback in mgr.drain_pending()] == ["expired"]
    assert not future.done()


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


@pytest.mark.parametrize(
    "ownership_key",
    [VOICE_DELIVERY_COMMITTED_KEY, SWAP_PRIME_DELIVERY_CLAIM_KEY],
)
def test_expired_mirror_does_not_retract_provider_owned_callback(ownership_key):
    mgr = _make_session_mgr()
    future = _FakeAckFuture()
    callback = _proactive_cb("provider-owned callback")
    callback[DELIVERY_ACK_FUTURE_KEY] = future
    mgr.enqueue_agent_callback(callback)
    callback[ownership_key] = True
    mirror = mgr.pending_extra_replies[0]
    mirror[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1

    mgr._purge_undeliverable_callbacks()

    assert mgr.pending_agent_callbacks == [callback]
    assert mgr.pending_extra_replies == []
    assert callback.get(ownership_key) is True
    assert not future.done()


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


async def test_deliver_batch_drops_receipt_shadowed_by_terminal_task_result():
    mgr = _make_session_mgr()
    triggered = []

    class _MgrStub:
        def release_inflight_noop(self):
            raise AssertionError("terminal task result should still be delivered")

    mgr.proactive_manager = _MgrStub()
    mgr._topic_hook_release_allowed = lambda cb: True

    async def _fake_trigger():
        triggered.append(True)
        return True

    mgr.trigger_agent_callbacks = _fake_trigger
    receipt_ack = _FakeAckFuture()
    receipt = _proactive_cb(
        "started",
        task_id="task-1",
        channel="user_plugin",
        **{DELIVERY_ACK_FUTURE_KEY: receipt_ack},
    )
    terminal = _proactive_cb(
        "finished",
        task_id="task-1",
        channel="user_plugin",
        origin="task_result",
    )

    await core_module.LLMSessionManager._deliver_proactive_batch(
        mgr, [terminal, receipt]
    )

    assert receipt_ack.done() and receipt_ack.result is False
    assert [cb["summary"] for cb in mgr.pending_agent_callbacks] == ["finished"]
    assert triggered == [True]


async def test_deliver_batch_drops_pending_receipt_shadowed_by_later_terminal():
    mgr = _make_session_mgr()
    triggered = []

    class _MgrStub:
        def release_inflight_noop(self):
            raise AssertionError("terminal task result should still be delivered")

    mgr.proactive_manager = _MgrStub()
    mgr._topic_hook_release_allowed = lambda cb: True

    async def _fake_trigger():
        triggered.append(True)
        return True

    mgr.trigger_agent_callbacks = _fake_trigger
    receipt_ack = _FakeAckFuture()
    receipt = _proactive_cb(
        "started",
        task_id="task-1",
        channel="user_plugin",
        **{DELIVERY_ACK_FUTURE_KEY: receipt_ack},
    )
    mgr.enqueue_agent_callback(receipt)
    terminal = _proactive_cb(
        "finished",
        task_id="task-1",
        channel="user_plugin",
        origin="task_result",
    )

    await core_module.LLMSessionManager._deliver_proactive_batch(
        mgr, [terminal]
    )

    assert receipt_ack.done() and receipt_ack.result is False
    assert [cb["summary"] for cb in mgr.pending_agent_callbacks] == ["finished"]
    assert [extra["summary"] for extra in mgr.pending_extra_replies] == ["finished"]
    assert triggered == [True]


def test_terminal_result_preserves_provider_owned_pending_receipt():
    mgr = _make_session_mgr()
    receipt_ack = _FakeAckFuture()
    receipt = _proactive_cb(
        "provider already owns receipt",
        task_id="task-1",
        channel="user_plugin",
        **{DELIVERY_ACK_FUTURE_KEY: receipt_ack},
    )
    mgr.enqueue_agent_callback(receipt)
    receipt[VOICE_DELIVERY_COMMITTED_KEY] = True
    terminal = _proactive_cb(
        "finished",
        task_id="task-1",
        channel="user_plugin",
        origin="task_result",
    )

    deliverable = core_module.LLMSessionManager._drop_receipts_shadowed_by_terminal_result(
        mgr, [terminal]
    )

    assert deliverable == [terminal]
    assert mgr.pending_agent_callbacks == [receipt]
    assert not receipt_ack.done()
    assert receipt.get(VOICE_DELIVERY_COMMITTED_KEY) is True


def test_passive_drain_drops_expired_callback_and_voice_mirror():
    mgr = _make_session_mgr()
    callback = _proactive_cb(
        "old status",
        **{CALLBACK_EXPIRES_AT_KEY: time.monotonic() - 1},
    )
    future = _FakeAckFuture()
    callback[DELIVERY_ACK_FUTURE_KEY] = future
    mgr.enqueue_agent_callback(callback)

    assert core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr) == ""
    assert future.done() and future.result is False
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []


def test_filter_deliverable_callbacks_drops_expired_and_paired_mirror():
    mgr = _make_session_mgr()
    future = _FakeAckFuture()
    expired = _proactive_cb(
        "expired",
        **{
            CALLBACK_EXPIRES_AT_KEY: time.monotonic() - 1,
            DELIVERY_ACK_FUTURE_KEY: future,
        },
    )
    active = _proactive_cb(
        "active",
        **{CALLBACK_EXPIRES_AT_KEY: time.monotonic() + 60},
    )
    mgr.enqueue_agent_callback(expired)
    mgr.enqueue_agent_callback(active)

    deliverable = core_module.LLMSessionManager.filter_deliverable_callbacks(
        mgr,
        list(mgr.pending_agent_callbacks),
    )

    assert deliverable == [active]
    assert mgr.pending_agent_callbacks == [active]
    assert [extra["summary"] for extra in mgr.pending_extra_replies] == ["active"]
    assert future.done() and future.result is False


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


# ---------------------------------------------------------------------------
# Per-turn image budget
#
# A trigger drains EVERY pending proactive callback into one model turn, so a
# per-push cap does not bound the request. Cues pile up whenever the proactive
# claim is denied (the user is mid-conversation) and then release together.
# ---------------------------------------------------------------------------


def _image_cb(name: str, images: list[str]) -> dict:
    return {"_callback_delivery_id": name, "status": "completed",
            "summary": name, "media_images": list(images)}


def test_image_budget_constants_are_pinned() -> None:
    """Anchor the literals the split tests below compute against."""
    from main_logic.proactive_delivery import (
        CALLBACK_IMAGE_MAX_COUNT,
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
    )

    assert CALLBACK_IMAGE_MAX_COUNT == 8
    assert CALLBACK_IMAGE_MAX_TOTAL_BYTES == 8 * 1024 * 1024


def test_split_takes_a_callback_atomic_fifo_prefix() -> None:
    """Whole callbacks only — a taken cb keeps its complete media set.

    Splitting mid-callback would break the downstream preserve-until-success
    retry, which re-streams ``media_images`` as one unit.
    """
    from main_logic.proactive_delivery import split_callbacks_by_image_budget

    cbs = [_image_cb("a", ["a1", "a2", "a3", "a4"]),
           _image_cb("b", ["b1", "b2", "b3", "b4"]),
           _image_cb("c", ["c1", "c2", "c3", "c4"])]

    taken, overflow = split_callbacks_by_image_budget(cbs)

    assert [cb["summary"] for cb in taken] == ["a", "b"]
    assert [cb["summary"] for cb in overflow] == ["c"]
    assert taken[1]["media_images"] == ["b1", "b2", "b3", "b4"]


def test_split_always_takes_the_head_even_when_it_alone_overflows() -> None:
    """Guarantees forward progress.

    Deferring an over-budget head would park a cue that can never fit and the
    queue would spin on it forever — the exact wedge this bound exists to stop.
    """
    from main_logic.proactive_delivery import split_callbacks_by_image_budget

    huge = _image_cb("huge", ["i%d" % i for i in range(40)])

    taken, overflow = split_callbacks_by_image_budget([huge, _image_cb("next", ["n"])])

    assert [cb["summary"] for cb in taken] == ["huge"]
    assert [cb["summary"] for cb in overflow] == ["next"]


def test_split_enforces_the_byte_budget_not_just_the_count() -> None:
    from main_logic.proactive_delivery import (
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
        split_callbacks_by_image_budget,
    )

    five_mib = "A" * (5 * 1024 * 1024 * 4 // 3)
    cbs = [_image_cb("first", [five_mib]), _image_cb("second", [five_mib])]

    taken, overflow = split_callbacks_by_image_budget(cbs)

    assert 2 * (len(five_mib) * 3 // 4) > CALLBACK_IMAGE_MAX_TOTAL_BYTES
    assert [cb["summary"] for cb in taken] == ["first"]
    assert [cb["summary"] for cb in overflow] == ["second"]


def test_split_defers_text_only_callbacks_behind_the_budget() -> None:
    """Strict FIFO: later text must not jump ahead of deferred image cues.

    The instruction renders callbacks in order, so letting text overtake would
    reorder the narrative against what the user already saw queued.
    """
    from main_logic.proactive_delivery import split_callbacks_by_image_budget

    text_only = {"_callback_delivery_id": "t", "status": "completed", "summary": "t"}
    cbs = [_image_cb("a", ["i%d" % i for i in range(8)]),
           _image_cb("b", ["b1"]),
           text_only]

    taken, overflow = split_callbacks_by_image_budget(cbs)

    assert [cb["summary"] for cb in taken] == ["a"]
    assert [cb["summary"] for cb in overflow] == ["b", "t"]


def test_split_passes_text_only_callbacks_through_untouched() -> None:
    """A batch with no images must never be deferred by an image budget."""
    from main_logic.proactive_delivery import split_callbacks_by_image_budget

    cbs = [{"summary": "x"}, {"summary": "y"}, {"summary": "z"}]

    taken, overflow = split_callbacks_by_image_budget(cbs)

    assert taken == cbs
    assert overflow == []


# ---------------------------------------------------------------------------
# Queue budget: what the manager may HOLD, vs what one release may send.
#
# CALLBACK_IMAGE_MAX_* bound a single model turn. The queue itself had only a
# TTL, so cues piling up while the user talks (the claim keeps being denied)
# could hold hundreds of MB of base64 with nothing to stop them.
# ---------------------------------------------------------------------------


def _img_of_decoded_size(decoded_bytes: int) -> str:
    """A base64 string whose approx decoded size is decoded_bytes."""
    return "A" * ((decoded_bytes + 2) // 3 * 4)


def test_queue_budget_numbers_are_the_agreed_ones():
    """Pin the figures; every other test below derives from them."""
    from main_logic import proactive_delivery as pd

    assert pd.QUEUED_CUE_MAX_COUNT == 50
    assert pd.QUEUED_IMAGE_MAX_TOTAL_BYTES == 4 * pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES


@pytest.mark.asyncio
async def test_queue_depth_is_bounded_and_drops_are_acked():
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    loop = asyncio.get_running_loop()
    overflow = 15
    acks = []
    for i in range(pd.QUEUED_CUE_MAX_COUNT + overflow):
        fut = loop.create_future()
        acks.append(fut)
        mgr.submit(
            {"text": f"cue-{i}", pd.DELIVERY_ACK_FUTURE_KEY: fut},
            coalesce_key=f"k{i}",
        )

    assert len(mgr._queue) == pd.QUEUED_CUE_MAX_COUNT
    # Dropped producers were TOLD, not left waiting on a future forever --
    # the same contract a TTL drop honours.
    resolved = [f for f in acks if f.done()]
    assert len(resolved) == overflow
    assert all(f.result() is False for f in resolved)


def test_an_important_waiting_cue_survives_a_flood_of_trivial_ones():
    """The property that makes the drop policy defensible.

    Dropping the oldest would let a burst of unimportant cues evict the
    important one that has been waiting longest — the same failure shape that
    made a shared image cap unworkable.
    """
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    important = {"text": "important"}
    mgr.submit(important, priority=9, coalesce_key="important")

    for i in range(pd.QUEUED_CUE_MAX_COUNT * 2):
        mgr.submit({"text": f"noise-{i}"}, priority=0, coalesce_key=f"n{i}")

    queued = [c.callback for c in mgr._queue]
    assert important in queued
    assert len(mgr._queue) == pd.QUEUED_CUE_MAX_COUNT


def test_queued_image_bytes_are_bounded_independently_of_count():
    """Count and bytes are independent axes: few cues can still be huge."""
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    one_turn = pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES
    # Ten turns' worth in ten cues — far under the count cap, far over bytes.
    for i in range(10):
        mgr.submit(
            {"text": f"img-{i}", "media_images": [_img_of_decoded_size(one_turn)]},
            coalesce_key=f"i{i}",
        )

    assert len(mgr._queue) < 10, "byte ceiling never fired"
    total = sum(mgr._cue_image_bytes(c) for c in mgr._queue)
    assert total <= pd.QUEUED_IMAGE_MAX_TOTAL_BYTES


def test_text_only_cues_are_not_charged_image_bytes():
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    for i in range(pd.QUEUED_CUE_MAX_COUNT):
        mgr.submit({"text": f"plain-{i}"}, coalesce_key=f"p{i}")

    assert len(mgr._queue) == pd.QUEUED_CUE_MAX_COUNT
    assert sum(mgr._cue_image_bytes(c) for c in mgr._queue) == 0


def test_budget_eviction_reports_the_keys_it_dropped():
    """The manager coalesces on submit, so an eviction can strand bookkeeping.

    If the newly submitted cue displaced an older same-key one and is then
    itself evicted for budget, the key's recorded sequence still points at the
    evicted cue. The owner uses that sequence to retract older same-key cues as
    stale — so without this report BOTH are lost.
    """
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    for i in range(pd.QUEUED_CUE_MAX_COUNT):
        mgr.submit({"text": f"important-{i}"}, priority=9, coalesce_key=f"hi{i}")

    # Least important and newest: the budget's own victim by construction.
    evicted = mgr.submit({"text": "loser"}, priority=0, coalesce_key="loser-key")

    assert evicted == ["loser-key"]
    assert len(mgr._queue) == pd.QUEUED_CUE_MAX_COUNT
    assert all(c.callback.get("text") != "loser" for c in mgr._queue)


def test_submit_reports_nothing_when_nothing_was_evicted():
    delivered = []
    mgr = _make(delivered)
    assert mgr.submit({"text": "fits"}, coalesce_key="k") == []


def _tiny_jpeg(px: int = 720) -> str:
    """A JPEG that ALREADY sits at the model profile, encoded at high quality.

    720 (not 1080 or 900) because the ladder test below has to measure the
    LADDER, and rung 0 now normalizes every image to
    MODEL_IMAGE_MAX_WIDTH x COMPRESS_TARGET_HEIGHT before the ladder is even
    reached. A fixture over either bound would be rewritten by rung 0 first,
    and the sample/compress/drop assertions would then be reading numbers that
    rung 0 produced. 720x720 is inside both bounds, so rung 0 hands it straight
    back -- which the "budget is loose" case asserts explicitly by requiring
    ``notice is None``.

    quality=95 while the profile is q80, so the compress rung still has real
    work to do (measured ~0.38x). An image already at q80 would come back the
    same size, the rung would keep the original, and the test would be
    asserting nothing.
    """
    import base64
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (px, px), (120, 30, 200)).save(buf, "JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.asyncio
async def test_over_budget_turn_samples_then_compresses_before_dropping():
    """Going over the request budget must cost redundancy first, content last.

    A rejected request loses the whole turn, so something has to give. The
    order is deliberate: drop redundant frames (a burst's ends and midpoint
    carry nearly all of it), then quality, and only then content -- and every
    step is reported so the user is never silently short an image.
    """
    from main_logic.proactive_delivery import (
        approx_base64_decoded_bytes,
        fit_images_to_turn_budget,
    )

    images = [_tiny_jpeg() for _ in range(10)]
    total = sum(approx_base64_decoded_bytes(i) for i in images)

    # 预算宽松：一张都不动，也不打扰用户。
    kept, notice = await fit_images_to_turn_budget(images, total * 2)
    assert kept == images
    assert notice is None

    # 只需抽样：降到开头/中间/结尾三张，不压缩、不丢弃。
    kept, notice = await fit_images_to_turn_budget(images, total // 2)
    assert kept == [images[0], images[len(images) // 2], images[-1]]
    assert notice["sampled"] is True
    assert notice["compressed"] is False
    assert notice["dropped"] == 0

    # 抽样还不够：压缩，仍然一张不丢。
    kept, notice = await fit_images_to_turn_budget(images, total // 8)
    assert len(kept) == 3
    assert notice["compressed"] is True
    assert notice["dropped"] == 0
    assert sum(approx_base64_decoded_bytes(i) for i in kept) < total // 8 * 2

    # 压完仍超限才丢内容，且无条件保住至少一张。
    kept, notice = await fit_images_to_turn_budget(images, 2000)
    assert len(kept) >= 1
    assert notice["dropped"] > 0
    assert notice["final_count"] == len(kept)

# ---------------------------------------------------------------------------
# Byte-axis eviction must not eat text-only cues.
#
# Both axes drop "the cue that would go out last". For DEPTH that always makes
# progress -- every cue holds a slot. For BYTES only image-bearing cues hold
# budget, so the global maximum sort_key is almost always a text cue whose
# eviction frees nothing, and the loop keeps going until it happens to reach
# the image cues. The lowest priority in the system is first-party text
# (topic hooks submit at -20), so the wrong victim is picked by default.
# ---------------------------------------------------------------------------


def _img_cue_payload(decoded_bytes: int, filler: str) -> dict:
    """A callback whose media_images decode to roughly ``decoded_bytes``."""
    return {"text": "img", "media_images": [filler * ((decoded_bytes * 4 // 3) // len(filler))]}


def test_byte_axis_evicts_image_cues_not_text_cues():
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)

    # ORDER IS LOAD-BEARING. The text cues must already be queued when the byte
    # budget blows, because _enforce_queue_budget runs on every submit: if the
    # image cues are submitted first they are trimmed into budget before any
    # text cue exists, and a shared victim pool would look identical to a split
    # one. Submitting text first is what makes the two versions diverge --
    # verified by mutation (reverting to a shared pool must turn this red).
    #
    # priority=-20 is the value first-party topic hooks really submit at
    # (main_logic/topic/delivery.py), which is BELOW the 0 an unspecified
    # plugin priority normalises to -- so these are the first cues a shared
    # pool would reach for.
    for i in range(10):
        mgr.submit({"text": f"hook-{i}"}, priority=-20, coalesce_key=f"hook{i}")

    # Now overflow the 32 MiB queue ceiling: 8 MiB each, the per-push model
    # budget a single cue can carry.
    per_cue = pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES
    for i in range(5):
        mgr.submit(_img_cue_payload(per_cue, "I"), priority=9, coalesce_key=f"img{i}")

    survivors = [c.callback.get("text") for c in mgr._queue]
    # Every text cue survives: they never held a single byte of the budget.
    for i in range(10):
        assert f"hook-{i}" in survivors, f"text cue hook-{i} was evicted by the byte axis"
    # And the budget is actually enforced -- this is not "the loop did nothing".
    assert sum(mgr._cue_image_bytes(c) for c in mgr._queue) <= pd.QUEUED_IMAGE_MAX_TOTAL_BYTES
    # Which means image cues DID get dropped.
    assert sum(1 for c in mgr._queue if mgr._cue_image_bytes(c) > 0) < 5


def test_byte_axis_keeps_the_queue_within_the_depth_ceiling_too():
    """The shared-pool version cut a 56-cue burst down to 4 against a 50 ceiling.

    Enforcing the byte budget must not collapse the queue far below the depth
    limit it also advertises.
    """
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    per_cue = pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES
    # Text first -- see the ordering note in the test above.
    for i in range(40):
        mgr.submit({"text": f"plain-{i}"}, priority=0, coalesce_key=f"p{i}")
    for i in range(8):
        mgr.submit(_img_cue_payload(per_cue, "I"), priority=9, coalesce_key=f"img{i}")

    assert len(mgr._queue) > 40, f"queue collapsed to {len(mgr._queue)}"
    assert len(mgr._queue) <= pd.QUEUED_CUE_MAX_COUNT
    assert sum(mgr._cue_image_bytes(c) for c in mgr._queue) <= pd.QUEUED_IMAGE_MAX_TOTAL_BYTES


def test_byte_axis_victim_is_the_last_image_cue_to_release():
    """Ordering rule is unchanged -- only the candidate pool narrowed."""
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    per_cue = pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES
    # Same priority, so FIFO decides: the newest image cue goes first.
    for i in range(5):
        cb = _img_cue_payload(per_cue, "I")
        cb["tag"] = f"img{i}"
        mgr.submit(cb, priority=5, coalesce_key=f"img{i}")

    remaining = [c.callback.get("tag") for c in mgr._queue]
    assert "img0" in remaining, "the longest-waiting image cue must survive"
    assert "img4" not in remaining, "the newest image cue must be the first evicted"


def test_depth_axis_victim_rule_is_unchanged():
    """Guards the half of the loop that was NOT supposed to change."""
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    mgr.submit({"text": "important"}, priority=9, coalesce_key="imp")
    for i in range(pd.QUEUED_CUE_MAX_COUNT):
        mgr.submit({"text": f"noise-{i}"}, priority=0, coalesce_key=f"n{i}")

    survivors = [c.callback.get("text") for c in mgr._queue]
    assert len(survivors) == pd.QUEUED_CUE_MAX_COUNT
    # High-priority waiter survives; the newest low-priority cue is the victim.
    assert "important" in survivors
    assert f"noise-{pd.QUEUED_CUE_MAX_COUNT - 1}" not in survivors
    assert "noise-0" in survivors


def test_byte_axis_still_acks_the_cues_it_drops():
    """Dropped cues must be told, same as a TTL drop."""
    from main_logic import proactive_delivery as pd

    delivered = []
    mgr = _make(delivered)
    per_cue = pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES
    keys = []
    for i in range(6):
        keys.append(mgr.submit(_img_cue_payload(per_cue, "I"), priority=5, coalesce_key=f"img{i}"))

    assert any(k for k in keys), "byte-axis eviction must report evicted keys"


# ---------------------------------------------------------------------------
# Rung 0: the model resolution profile.
#
# fit_images_to_turn_budget used to be a pure CEILING -- it totalled the
# payloads and returned them unchanged when they already fit, BEFORE sampling,
# compressing or dropping. So an image was only ever downscaled when it was too
# BIG, and nothing anywhere guaranteed a bounded resolution: a plugin frame the
# SDK had normalized to 2048x1536 / ~49 KiB sailed under the 8 MiB budget and
# reached the model at 1536px high. compress_screenshot could not have caught
# it either -- it bounds HEIGHT ONLY, so 16000x400 passes through completely
# untouched and 5120x1440 still comes out 2560 wide.
# ---------------------------------------------------------------------------


def _jpeg_of(width: int, height: int, *, quality: int = 95) -> str:
    """A JPEG of exact pixel dimensions, base64 without the ``data:`` prefix."""
    import base64
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (width, height), (120, 30, 200)).save(
        buf, "JPEG", quality=quality
    )
    return base64.b64encode(buf.getvalue()).decode()


def _size_of_b64(b64: str) -> tuple:
    import base64
    import io as _io

    from PIL import Image

    with Image.open(_io.BytesIO(base64.b64decode(b64))) as img:
        return img.size


def _size_of_jpeg_bytes(raw: bytes) -> tuple:
    import io as _io

    from PIL import Image

    with Image.open(_io.BytesIO(raw)) as img:
        return img.size


def test_model_image_profile_constants_are_pinned() -> None:
    """Anchor the literals every derived assertion below computes against.

    Without these pins a change to either constant would move the profile and
    the size assertions would quietly follow it, still green, while the images
    actually sent to the model changed shape.

    1280 is not a fresh number: static/app/app-state.js captures at
    MAX_SCREENSHOT_WIDTH: 1280, so backend and frontend agree instead of
    diverging silently.
    """
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    assert COMPRESS_TARGET_HEIGHT == 720
    assert MODEL_IMAGE_MAX_WIDTH == 1280


def test_compress_screenshot_bounds_width_only_when_asked() -> None:
    """``max_w`` is opt-in, so existing callers keep their exact old output.

    brain/computer_use.py asks for target_h=1080 deliberately; making the width
    bound a new floor everyone inherits would have changed what it produces.
    """
    from PIL import Image

    from utils.screenshot_utils import compress_screenshot

    ultrawide = Image.new("RGB", (5120, 1440), (10, 200, 90))

    height_only = compress_screenshot(ultrawide, target_h=720)
    assert _size_of_jpeg_bytes(height_only) == (2560, 720), (
        "the default must stay HEIGHT ONLY -- width unclamped, exactly as before"
    )

    both_bounds = compress_screenshot(ultrawide, target_h=720, max_w=1280)
    assert _size_of_jpeg_bytes(both_bounds) == (1280, 360)


@pytest.mark.asyncio
async def test_under_budget_image_is_normalized_and_stays_quiet() -> None:
    """Rung 0 runs even when the byte budget was never in danger.

    This is the case the old ceiling missed entirely, and the reason rung 0
    sits ABOVE the early return rather than inside the ladder.
    """
    from main_logic.proactive_delivery import (
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
        approx_base64_decoded_bytes,
        fit_images_to_turn_budget,
    )
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    plugin_image = _jpeg_of(2048, 1536)
    assert approx_base64_decoded_bytes(plugin_image) < CALLBACK_IMAGE_MAX_TOTAL_BYTES, (
        "fixture must be UNDER budget, or this tests the ladder and not rung 0"
    )

    kept, notice = await fit_images_to_turn_budget(
        [plugin_image], CALLBACK_IMAGE_MAX_TOTAL_BYTES
    )

    width, height = _size_of_b64(kept[0])
    assert height <= COMPRESS_TARGET_HEIGHT
    assert width <= MODEL_IMAGE_MAX_WIDTH
    assert notice is not None, "rung 0 did something, so it has to be reported"
    assert notice["normalized"] is True
    assert notice["dropped"] == 0
    # 例行归一化不弹窗。rung 0 几乎每个带图的回合都会跑，照旧「有 notice 就弹」
    # 的话用户会被刷屏，而他其实什么都没损失。
    assert notice["user_visible"] is False


@pytest.mark.asyncio
async def test_model_normalization_is_a_fixed_point(monkeypatch) -> None:
    """Feeding the normalizer its own output must NOT re-encode.

    Load-bearing rather than a nicety: images ride ``_conversation_history``
    for several more turns, so this code runs over the same payload again and
    again. A normalizer that re-encoded every time would degrade the picture
    generationally, one JPEG round-trip per turn, for no benefit at all.
    """
    from main_logic.proactive_delivery import (
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
        fit_images_to_turn_budget,
    )
    from utils import screenshot_utils as su

    once = su.normalize_image_for_model(_jpeg_of(2048, 1536))
    assert _size_of_b64(once)[1] <= su.COMPRESS_TARGET_HEIGHT, "fixture never normalized"

    class _ReEncodeAttempted(BaseException):
        # 刻意**不**继承 Exception：normalize_image_for_model 的失败兜底和 fit
        # 里的归一化循环都是 `except Exception`，用普通异常会被它们吞掉，这个
        # 测试就永远绿——连「删掉头部探测的跳过分支」这种变异都照样绿。
        pass

    def _boom(*args, **kwargs):
        raise _ReEncodeAttempted("re-encoded a payload that already fits the profile")

    monkeypatch.setattr(su, "compress_screenshot", _boom)

    assert su.normalize_image_for_model(once) is once

    # 再走一遍真实调用路径：rung 0 对已经合规的图必须是彻底的 no-op。
    kept, notice = await fit_images_to_turn_budget(
        [once], CALLBACK_IMAGE_MAX_TOTAL_BYTES
    )
    assert kept == [once]
    assert notice is None


@pytest.mark.asyncio
async def test_ultrawide_frame_is_bounded_on_width_too() -> None:
    """5120x1440 came out 2560x720 -- inside the height bound, still 2560 wide."""
    from main_logic.proactive_delivery import (
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
        fit_images_to_turn_budget,
    )
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    kept, _ = await fit_images_to_turn_budget(
        [_jpeg_of(5120, 1440)], CALLBACK_IMAGE_MAX_TOTAL_BYTES
    )

    width, height = _size_of_b64(kept[0])
    assert width <= MODEL_IMAGE_MAX_WIDTH, f"still {width}px wide"
    assert height <= COMPRESS_TARGET_HEIGHT


@pytest.mark.asyncio
async def test_letterbox_frame_that_used_to_pass_through_is_bounded() -> None:
    """16000x400 was the worst case: the height bound never even fired.

    Its height already fits, so the resize branch never ran and the frame
    reached the model at its full 16000px width.
    """
    from main_logic.proactive_delivery import (
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
        fit_images_to_turn_budget,
    )
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    kept, _ = await fit_images_to_turn_budget(
        [_jpeg_of(16000, 400)], CALLBACK_IMAGE_MAX_TOTAL_BYTES
    )

    width, height = _size_of_b64(kept[0])
    assert (width, height) != (16000, 400), "passed through completely untouched"
    assert width <= MODEL_IMAGE_MAX_WIDTH
    assert height <= COMPRESS_TARGET_HEIGHT


@pytest.mark.asyncio
async def test_losing_whole_images_is_user_visible_however_it_happened() -> None:
    """The toast gate is "a picture is gone", not "the drop rung ran".

    Sampling and dropping are different rungs in here -- one is framed as
    shedding redundancy, the other as shedding content -- but they are the
    same event from the reader's side: ``_sample_head_middle_tail`` keeps
    three frames and discards every other one WHOLE, exactly like the trim
    does. Gating the toast on ``dropped`` alone meant a ten-frame burst could
    silently arrive as three, and what she says next would not line up with
    what he sent.

    Both directions are asserted, because the one-sided version of this test
    is what let the sampling case slip: normalizing and re-compressing must
    stay quiet, and this test would pass just as well if the gate were wired
    to ``True``.

    Mutation A: put the gate back to ``notice["dropped"] > 0`` -- the sampling
    case fails.
    Mutation B: make the gate ``or notice["normalized"]`` (or just ``True``)
    -- the normalization case fails.
    """
    from main_logic.proactive_delivery import (
        TURN_IMAGE_SAMPLE_KEEP,
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
        approx_base64_decoded_bytes,
        fit_images_to_turn_budget,
    )

    images = [_tiny_jpeg() for _ in range(10)]
    total = sum(approx_base64_decoded_bytes(i) for i in images)

    # ── Sampling alone: no drop rung, no compress rung, and seven of the ten
    #    frames are nevertheless not in the turn any more.
    kept, sampled = await fit_images_to_turn_budget(images, total // 2)
    assert sampled["sampled"] is True
    assert sampled["compressed"] is False
    assert sampled["dropped"] == 0
    assert len(kept) == TURN_IMAGE_SAMPLE_KEEP < len(images), (
        "if sampling did not actually remove frames this asserts nothing"
    )
    assert sampled["user_visible"] is True

    # ── The trim rung: the case that was always user-visible.
    kept, dropped = await fit_images_to_turn_budget(images, 2000)
    assert dropped["dropped"] > 0
    assert dropped["user_visible"] is True
    assert len(kept) >= 1, "the turn must never lose every image"

    # ── Pure rung 0: one oversized frame, budget never in danger. The picture
    #    is rewritten smaller but nothing left the turn, so this stays a log
    #    line. Rung 0 fires on nearly every turn that carries an image, and a
    #    toast here would be a permanent stream of "images adjusted".
    only = _jpeg_of(2048, 1536)
    assert (
        approx_base64_decoded_bytes(only) < CALLBACK_IMAGE_MAX_TOTAL_BYTES
    ), "fixture must be UNDER budget, or the ladder runs and this proves nothing"
    kept, quiet = await fit_images_to_turn_budget([only], CALLBACK_IMAGE_MAX_TOTAL_BYTES)
    assert quiet is not None and quiet["normalized"] is True
    assert quiet["sampled"] is False and quiet["dropped"] == 0
    assert len(kept) == 1, "nothing may leave the turn on the normalize-only path"
    assert quiet["user_visible"] is False


def _jpeg_with_orientation(width: int, height: int, orientation: int | None) -> str:
    """A base64 JPEG whose STORED matrix is width x height, tagged for display."""
    import base64
    import io

    from PIL import Image

    image = Image.new("RGB", (width, height), "white")
    buffer = io.BytesIO()
    if orientation is None:
        image.save(buffer, format="JPEG")
    else:
        exif = Image.Exif()
        exif[0x0112] = orientation
        image.save(buffer, format="JPEG", exif=exif)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _displayed_size(b64: str) -> tuple[int, int]:
    import base64
    import io

    from PIL import Image, ImageOps

    image = Image.open(io.BytesIO(base64.b64decode(b64)))
    return ImageOps.exif_transpose(image).size


@pytest.mark.parametrize("orientation", [5, 6, 7, 8])
def test_model_normalization_honours_exif_orientation(orientation) -> None:
    """A rotated photo must not reach the model lying on its side.

    ``compress_screenshot`` writes a JPEG without an EXIF block, so any
    orientation tag on the input is dropped by the re-encode. Resizing the
    stored matrix and then discarding the tag hands the model a picture rotated
    90 degrees with no way to tell -- and the caller cannot tell either, because
    the bytes look perfectly valid.

    Measured before the fix: a 3000x1000 JPEG tagged orientation=6 (so it
    DISPLAYS as 1000x3000) came out 1280x426 with EXIF None -- landscape, from a
    portrait source.

    Orientations 5-8 are exactly the ones that transpose width and height, which
    is why they are the parametrised set: 1-4 keep the aspect the same and could
    not expose this.
    """
    from utils.screenshot_utils import (
        COMPRESS_TARGET_HEIGHT,
        MODEL_IMAGE_MAX_WIDTH,
        normalize_image_for_model,
    )

    source = _jpeg_with_orientation(3000, 1000, orientation)
    assert _displayed_size(source) == (1000, 3000), "fixture must display as portrait"

    out = normalize_image_for_model(source)
    width, height = _displayed_size(out)

    assert height > width, (
        f"portrait source came back as {width}x{height}: the model is being "
        "shown a sideways picture"
    )
    assert width <= MODEL_IMAGE_MAX_WIDTH
    assert height <= COMPRESS_TARGET_HEIGHT


def test_profile_probe_measures_the_displayed_shape_not_the_stored_one() -> None:
    """The skip test must compare the profile against what will be SEEN.

    A JPEG stored 720x1280 and tagged orientation=6 displays as 1280x720 --
    already exactly the model profile. Judging it by the stored matrix reads
    1280 as the height, decides it is over the 720 ceiling, and re-encodes a
    payload that needed nothing done to it. That is not just wasted work: it is
    a fixed-point break, and these payloads live on _conversation_history for
    several turns, so it would re-encode once per turn forever.
    """
    from utils.screenshot_utils import normalize_image_for_model

    already_fine = _jpeg_with_orientation(720, 1280, 6)
    assert _displayed_size(already_fine) == (1280, 720)

    assert normalize_image_for_model(already_fine) is already_fine
