"""Regression tests for final-swap cancellation and concurrent takeover.

Covers the latent zombie-swap defect confirmed during PR #2272 review:
step 1 of ``_perform_final_swap_sequence`` swallows ``CancelledError``
intending to absorb the just-cancelled old listener's echo, but an external
cancellation of ``final_swap_task`` itself surfaces the same exception type.
If it survives to the unlocked promote, the swap poisons or overwrites the
concurrent ``start_session`` winner. The fix combines three guards:

- step 1's ``except CancelledError`` re-raises when ``cancelling() > 0``
  (distinguishes the external cancel that *raises* from the listener echo);
- a pre-promote checkpoint re-raises when ``cancelling() > 0`` even if
  ``wait_for``/``close`` *swallowed* the cancel (Python 3.11 returns
  ``fut.result()`` and clears ``_must_cancel`` without re-raising);
- the promote itself is a locked CAS mirroring the start-side guard.

Also covers the follow-up (greptile P1 on PR #2283): the budget-selected
``pending_extra_replies`` entries used to be removed from the queue right
after being primed, so every abort exit that discarded the primed session
lost them. The fix keeps the queue UNTOUCHED through the prime window and
removes the selected entries (by object identity) only at promote success:
pre-promote aborts keep the queue intact with zero restore code, concurrent
removers (voice proactive delivery's success prune, retraction purge, the
topic voice-block sweep, the flood cap) hit queue-resident entries normally
— no checked-out-entry TOCTOU — and the one exit where removal has already
happened but the promoted session dies unspoken (post-promote ws-invalid
fail-close) restores the removed entries to the queue head. Aborts where
the promoted session survives must NOT re-queue anything (the primed
context delivers on the next turn; re-queueing would double-deliver).

The restore tests run the swap in PRODUCTION topology (registered as
``mgr.final_swap_task``, exactly as turn.py creates it): the in-handler
``_reset_preparation_state`` calls used to snapshot-and-cancel that very
task (self-cancel), killing every handler line after the reset — restores
AND the pre-existing fail-closes — while direct-await tests stayed green.
``_reset_preparation_state`` now excludes ``asyncio.current_task()`` from
its cancel set; ``assert not swap_task.cancelled()`` pins that guard.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.core as core_module
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.omni_realtime_client import (
    ImageStageResult,
    MultimodalTurnDelivery,
    OmniRealtimeClient,
)
from main_logic.proactive_delivery import (
    CALLBACK_EXPIRES_AT_KEY,
    DELIVERY_ACK_FUTURE_KEY,
    DELIVERY_RETRACTED_KEY,
    PASSIVE_MEDIA_BUDGET_DEFERRED_KEY,
    PASSIVE_MEDIA_RETRY_KEY,
    PASSIVE_MEDIA_TRANSIENT_KEY,
    SWAP_PRIME_DELIVERY_CLAIM_KEY,
)


class _FakeSession:
    def __init__(self, name):
        self.name = name
        self.closed = False
        self.prime_calls = []

    async def prime_context(self, text, *, skipped=False):
        self.prime_calls.append((text, skipped))

    async def close(self):
        self.closed = True

    async def handle_messages(self):
        await asyncio.Event().wait()


async def _noop_async(*args, **kwargs):
    return None


def _make_swap_manager():
    mgr = object.__new__(core_module.LLMSessionManager)
    mgr.lanlan_name = "Lan"
    mgr.master_name = "Master"
    mgr.user_language = "zh"
    # Production always has this (LLMSessionManager.__init__ defaults it to
    # 'audio' and start_session overwrites it); object.__new__ skips __init__,
    # so the swap sequence's context-summary line would hit an AttributeError
    # here and nowhere else.
    mgr.input_mode = "audio"
    mgr.lock = asyncio.Lock()
    mgr.session = None
    mgr.message_handler_task = None
    mgr.pending_session = None
    # Production sets this in __init__; the pending-session close is a task the
    # manager owns so a cancelled caller cannot strand an open socket.
    mgr._pending_session_close_tasks = set()
    mgr.background_preparation_task = None
    mgr.final_swap_task = None
    mgr.pending_session_warmed_up_event = None
    mgr.pending_session_final_prime_complete_event = None
    mgr.pending_use_tts = None
    mgr.is_hot_swap_imminent = False
    mgr.is_active = False
    mgr.is_preparing_new_session = False
    mgr._require_context_append_current_delivery = False
    mgr.summary_triggered_time = None
    mgr.initial_cache_snapshot_len = 0
    mgr.initial_next_session_context_snapshot_len = 0
    mgr.message_cache_for_new_session = []
    mgr.next_session_context_messages = []
    mgr.pending_extra_replies = []
    mgr.current_speech_id = None
    mgr.send_status = _noop_async
    # Peripheral post-step-3 actions are irrelevant here; collapse to no-ops.
    mgr._apply_pending_tts_route_after_swap = _noop_async
    mgr._sync_tools_to_active_session = _noop_async

    async def _prime_late(*args, **kwargs):
        return 0

    mgr._prime_late_next_session_context_after_swap = _prime_late
    mgr._flush_hot_swap_audio_cache = _noop_async
    return mgr


async def _drain_task(task):
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        # Best-effort teardown: the task is being cancelled on purpose, so its
        # cancellation (or any error surfacing as it unwinds) is expected and moot.
        pass


@pytest.mark.asyncio
async def test_final_swap_cancelled_at_step1_does_not_promote_zombie():
    """A swap parked at step 1's wait_for(old listener) then cancelled by
    _reset_preparation_state must not survive as a zombie: no promote, the
    new_session gets closed, and no task reference leaks."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    listener_cancelled = asyncio.Event()

    async def _stubborn_listener():
        # Absorb the first cancel (models the old listener stuck in recv())
        # so the swap parks on wait_for's await point waiting for it.
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            listener_cancelled.set()
            await asyncio.Event().wait()
            raise

    mgr.message_handler_task = asyncio.create_task(_stubborn_listener())
    await asyncio.sleep(0)

    mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
    swap_task = mgr.final_swap_task
    # The old listener receiving cancel proves the swap reached step 1 and is
    # parked on wait_for.
    await asyncio.wait_for(listener_cancelled.wait(), timeout=5)
    await asyncio.sleep(0)

    try:
        # Model a new start_session prelude cancelling the in-flight swap.
        await asyncio.wait_for(
            mgr._reset_preparation_state(clear_main_cache=True), timeout=5
        )

        # Outcome-focused: the swap terminated without promoting. We assert the
        # observable results rather than swap_task.cancelled(), which couples to
        # the exact way the outer handler winds the task down.
        assert swap_task.done()
        assert mgr.session is old_session, "zombie swap must not promote self.session"
        assert new_session.closed, "the cancelled swap must close new_session (no ws leak)"
        assert not old_session.closed, "cancel landed before step 2; old session is the takeover's to close"
        assert mgr.final_swap_task is None, "final_swap_task reference must not leak"
        assert mgr.is_hot_swap_imminent is False
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
async def test_final_swap_swallowed_cancel_still_aborts_before_promote():
    """The pre-promote checkpoint: even when the external cancel is *swallowed*
    by an await (Python 3.11 wait_for/close returns normally, clearing
    _must_cancel while cancelling() stays > 0), the swap must still abort before
    overwriting self.session. Without the checkpoint the CAS would wave the
    zombie through, since self.session is still old_main_session at promote."""
    mgr = _make_swap_manager()
    new_session = _FakeSession("pending")

    class _SwallowExternalCancelOnClose(_FakeSession):
        async def close(self):
            await super().close()
            # Reproduce the 3.11 quirk deterministically: an external cancel
            # arrives and is consumed by an inner await that eats it, leaving
            # _must_cancel cleared but cancelling() == 1.
            t = asyncio.current_task()
            t.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                # Swallow it on purpose — this IS the swallow being reproduced:
                # the cancel is consumed here so _must_cancel clears while
                # cancelling() stays 1, mimicking wait_for/close eating the cancel.
                pass

    old_session = _SwallowExternalCancelOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None  # old listener already gone; step 1 is skipped

    # The checkpoint raises CancelledError, which the swap's own
    # ``except CancelledError`` handler catches and cleans up after — so the
    # coroutine returns normally rather than propagating.
    await mgr._perform_final_swap_sequence()

    assert mgr.session is old_session, "swallowed external cancel must still abort before promote"
    assert new_session.closed, "aborted swap must close new_session"
    assert mgr.is_hot_swap_imminent is False


@pytest.mark.asyncio
async def test_final_swap_promote_aborts_when_session_taken_over():
    """Promote-side CAS: when self.session is taken over mid-swap (cleared or
    replaced by a concurrent winner), the swap must abort and close its
    new_session instead of overwriting the winner."""
    mgr = _make_swap_manager()
    winner_session = _FakeSession("winner")
    winner_listener = object()
    new_session = _FakeSession("pending")

    class _TakeoverOnClose(_FakeSession):
        async def close(self):
            await super().close()
            # Inside step 2's close() await window, a new start_session finishes
            # its takeover.
            mgr.session = winner_session
            mgr.message_handler_task = winner_listener

    old_session = _TakeoverOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None  # old listener already gone; step 1 is skipped

    await mgr._perform_final_swap_sequence()

    assert mgr.session is winner_session, "swap must not overwrite the concurrently promoted winner"
    assert mgr.message_handler_task is winner_listener, "winner's listener must not be replaced"
    assert new_session.closed, "aborting the promote must close new_session"
    assert mgr.is_hot_swap_imminent is False


@pytest.mark.asyncio
async def test_final_swap_happy_path_still_promotes():
    """An uninterfered hot swap still completes end to end: the old listener's
    cancellation echo is swallowed (not re-raised), the old session closes, the
    new session promotes, and step 4 starts a *fresh* listener."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    echo_raised = asyncio.Event()

    async def _old_listener():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Pin the echo path: step 1's wait_for must actually see the old
            # listener's cancellation surface, otherwise (a)'s "swallow the
            # echo" branch has zero coverage yet the test still passes.
            echo_raised.set()
            raise

    old_listener_task = asyncio.create_task(_old_listener())
    mgr.message_handler_task = old_listener_task
    await asyncio.sleep(0)

    try:
        await mgr._perform_final_swap_sequence()

        assert echo_raised.is_set(), "old listener's cancel echo must reach wait_for (echo-swallow branch coverage)"
        assert mgr.session is new_session
        assert old_session.closed
        assert not new_session.closed
        assert mgr.pending_session is None
        assert mgr.is_hot_swap_imminent is False
        assert mgr.message_handler_task is not None
        assert mgr.message_handler_task is not old_listener_task, "step 4 must start a fresh listener, not keep the old one"
        assert not mgr.message_handler_task.done(), "the fresh listener should be running"
    finally:
        await _drain_task(mgr.message_handler_task)
        await _drain_task(old_listener_task)


@pytest.mark.asyncio
async def test_final_swap_does_not_emit_prime_context(monkeypatch, capsys):
    sentinel = "PRIVATE_FINAL_PRIME_SENTINEL_2635"
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None
    mgr.message_cache_for_new_session = [object()]
    mgr._convert_cache_to_str = lambda _cache: sentinel

    logged = []

    def _capture_log(message, *args, **kwargs):
        del kwargs
        logged.append(str(message) % args if args else str(message))

    for level in ("debug", "info", "warning", "error", "exception"):
        monkeypatch.setattr(core_module.logger, level, _capture_log)

    try:
        await mgr._perform_final_swap_sequence()
        captured = capsys.readouterr()
        emitted = captured.out + captured.err + "\n".join(logged)
        assert sentinel not in emitted
    finally:
        await _drain_task(mgr.message_handler_task)


def _extra_entry(delivery_id, summary):
    """A pending_extra_replies entry in the exact shape enqueue_agent_callback produces."""
    return {
        "_callback_delivery_id": delivery_id,
        "origin": "event",
        "summary": summary,
        "detail": "",
        "status": "completed",
        "context_source": "proactive.callback",
        "source_kind": "unknown",
        "source_name": "",
        "error_message": "",
    }


def _make_fake_realtime_session(name):
    """A _FakeSession-alike that passes ``isinstance(x, OmniRealtimeClient)``.

    Needed to reach the post-promote ws-invalid exit, which is gated on the
    real client type. Bypasses ``__init__`` (no network) and shadows the
    methods the swap sequence calls with instance-level fakes.
    """
    s = object.__new__(OmniRealtimeClient)
    s.name = name
    s.ws = object()  # truthy: passes the entry ws check
    s._fatal_error_occurred = False
    s.closed = False
    s.prime_calls = []

    async def _prime(text, *, skipped=False):
        s.prime_calls.append((text, skipped))

    async def _close():
        # Mirror the real client: close() clears the ws reference.
        s.closed = True
        s.ws = None

    async def _handle():
        await asyncio.Event().wait()

    s.prime_context = _prime
    s.close = _close
    s.handle_messages = _handle
    return s


async def _run_swap_as_final_swap_task(mgr):
    """Run the swap exactly as production does (turn.py): registered as
    ``mgr.final_swap_task``. This is the topology where the in-handler
    ``_reset_preparation_state`` used to self-cancel the running swap task,
    killing every handler line after it (restores, fail-closes) — awaiting
    the coroutine directly would silently skip that whole failure mode."""
    mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
    task = mgr.final_swap_task
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.CancelledError:
        # A regressed self-cancel would end the task CANCELLED and re-raise
        # here; swallow it so the caller's `assert not task.cancelled()` can
        # report the regression instead of the test erroring out.
        pass
    return task


@pytest.mark.asyncio
async def test_final_swap_drops_expired_orphan_extra_before_render():
    """An orphan voice mirror expiring during warm-up is never primed."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None
    extra = _extra_entry("id-expired", "stale event")
    extra[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1
    mgr.pending_extra_replies = [extra]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.pending_extra_replies == []
    assert new_session.prime_calls
    assert all("stale event" not in text for text, _skipped in new_session.prime_calls)


def _passive_callback(summary, *, coalesce_key=""):
    return {
        "event": "agent_task_callback",
        "origin": "event",
        "summary": summary,
        "detail": summary,
        "status": "completed",
        "delivery_mode": "passive",
        "coalesce_key": coalesce_key,
    }


def test_swap_prime_render_excludes_retracted_claimed_callback():
    mgr = _make_swap_manager()
    callback = _passive_callback("expired during media staging")
    callback[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
    callback[DELIVERY_RETRACTED_KEY] = True

    ready, rendered = mgr._render_claimed_passive_callbacks_for_swap_prime(
        [callback]
    )

    assert ready == []
    assert rendered == ""
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in callback


def test_swap_prime_render_stops_at_a_budget_deferred_callback():
    """Nothing after a budget-deferred callback may jump ahead in this swap.

    ``split_callbacks_by_image_budget`` is strictly FIFO, so once the budget is
    spent the ENTIRE suffix is marked with PASSIVE_MEDIA_BUDGET_DEFERRED_KEY --
    including the text-only callbacks in it. ``_callback_media_ready_for_session``
    returns True for any callback without images, so filtering on it alone lets
    the text-only entry queued BEHIND a deferred media cue be injected and
    dequeued by this swap, and the model hears them out of order (the media cue
    is still queued for the next turn).

    The assertion is that the trailing text-only callback was not rendered, not
    that the media one was not: the latter passes under a media_ready-only
    filter too and would not catch this.
    """
    mgr = _make_swap_manager()
    deferred_media = _passive_callback("带图的，预算没轮到它")
    deferred_media["media_images"] = ["img-b64"]
    deferred_media[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
    deferred_media[PASSIVE_MEDIA_BUDGET_DEFERRED_KEY] = True
    deferred_text = _passive_callback("排在它后面的纯文本")
    deferred_text[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
    deferred_text[PASSIVE_MEDIA_BUDGET_DEFERRED_KEY] = True

    ready, rendered = mgr._render_claimed_passive_callbacks_for_swap_prime(
        [deferred_media, deferred_text]
    )

    assert ready == []
    assert "排在它后面的纯文本" not in rendered
    # 两条都留在队列里等下一轮，claim 也都要还回去。
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in deferred_media
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in deferred_text


def test_swap_prime_render_stops_at_a_transient_media_failure():
    """A retryable staging failure must hold back everything behind it too.

    Same FIFO argument as the budget-deferred case, different branch: skipping
    a media callback whose staging hit a TRANSIENT rejection lets a later
    text-only callback render and be dequeued after promotion while the earlier
    one stays queued, so the model hears them in the wrong order.

    Matches drain_agent_callbacks_for_llm()'s transient branch. The retry count
    is deliberately NOT incremented here -- the drain owns that accounting, and
    charging it at both consumers burns the budget twice as fast.
    """
    mgr = _make_swap_manager()
    stuck_media = _passive_callback("带图的，挂图瞬时失败")
    stuck_media["media_images"] = ["img-b64"]
    stuck_media[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
    stuck_media[PASSIVE_MEDIA_TRANSIENT_KEY] = True
    later_text = _passive_callback("排在它后面的纯文本")
    later_text[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True

    ready, rendered = mgr._render_claimed_passive_callbacks_for_swap_prime(
        [stuck_media, later_text]
    )

    assert ready == []
    assert "排在它后面的纯文本" not in rendered
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in stuck_media
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in later_text
    # 重试记账归 drain，这里不能碰（两处都加会让额度按两倍速烧完）。
    assert PASSIVE_MEDIA_RETRY_KEY not in stuck_media
    # 判据不是这个标记：终局失败同样 STOP（见下面那条对偶）。


def test_swap_prime_render_stops_at_a_terminal_media_failure_too():
    """A terminal failure stops the swap as well -- FIFO does not care why.

    Skipping it lets the later callback be primed and dequeued after promotion
    while this one stays queued, reversing their order. Stopping does not
    strand it either: drain_agent_callbacks_for_llm() delivers a terminally
    unstaged media callback text-only on its best-effort path, so the next user
    turn releases it together with everything behind it.

    Deliberately NOT classified by PASSIVE_MEDIA_TRANSIENT_KEY: the staging
    exception path leaves that marker unset on purpose (drain's contract for an
    exception is text-only delivery, pinned by
    test_first_native_passive_media_exception_requires_session_retirement), so
    keying on it here would misread an exception as terminal. Both cases stop,
    so no classification is needed.
    """
    mgr = _make_swap_manager()
    dead_media = _passive_callback("带图的，终局失败")
    dead_media["media_images"] = ["img-b64"]
    dead_media[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
    later_text = _passive_callback("排在它后面的纯文本")
    later_text[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True

    ready, rendered = mgr._render_claimed_passive_callbacks_for_swap_prime(
        [dead_media, later_text]
    )

    assert ready == []
    assert "排在它后面的纯文本" not in rendered
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in dead_media
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in later_text


def test_swap_prime_render_still_takes_an_undeferred_text_callback():
    """Dual: an undeferred text-only callback still renders.

    Guards against widening the gate into "hold back every text-only callback".
    """
    mgr = _make_swap_manager()
    plain = _passive_callback("正常的纯文本通知")
    plain[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True

    ready, rendered = mgr._render_claimed_passive_callbacks_for_swap_prime([plain])

    assert ready == [plain]
    assert "正常的纯文本通知" in rendered


def _install_passive_prime_barrier(session, *, expected_text):
    prime_entered = asyncio.Event()
    allow_prime = asyncio.Event()

    async def _prime(text, *, skipped=False):
        session.prime_calls.append((text, skipped))
        if expected_text in text:
            prime_entered.set()
            await allow_prime.wait()

    session.prime_context = _prime
    return prime_entered, allow_prime


@pytest.mark.asyncio
async def test_final_swap_drops_passive_callback_expired_during_initial_prime():
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")
    initial_prime_entered = asyncio.Event()
    allow_initial_prime = asyncio.Event()

    async def _prime(text, *, skipped=False):
        new_session.prime_calls.append((text, skipped))
        if len(new_session.prime_calls) == 1:
            initial_prime_entered.set()
            await allow_initial_prime.wait()

    new_session.prime_context = _prime
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    delivery_ack = asyncio.get_running_loop().create_future()
    callback = _passive_callback("expires during initial prime")
    callback[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() + 60
    callback[DELIVERY_ACK_FUTURE_KEY] = delivery_ack
    mgr.enqueue_agent_callback(callback)

    mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
    swap_task = mgr.final_swap_task
    await asyncio.wait_for(initial_prime_entered.wait(), timeout=5)
    callback[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1
    allow_initial_prime.set()
    await asyncio.wait_for(swap_task, timeout=10)
    try:
        assert mgr.session is new_session
        assert len(new_session.prime_calls) == 1
        assert "expires during initial prime" not in new_session.prime_calls[0][0]
        assert delivery_ack.done() and delivery_ack.result() is False
        assert callback not in mgr.pending_agent_callbacks
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
async def test_final_swap_prime_claim_survives_flood_until_promote(monkeypatch):
    """Exercise claim/flood/late-ACK through the complete swap sequence."""
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_QUEUE_MAX_ITEMS", 2)
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")
    prime_entered, allow_prime = _install_passive_prime_barrier(
        new_session,
        expected_text="selected before provider await",
    )
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    loop = asyncio.get_running_loop()
    delivered_ack = loop.create_future()
    dropped_ack = loop.create_future()
    delivered = _passive_callback("selected before provider await")
    delivered[DELIVERY_ACK_FUTURE_KEY] = delivered_ack
    mgr.enqueue_agent_callback(delivered)

    mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
    swap_task = mgr.final_swap_task
    await asyncio.wait_for(prime_entered.wait(), timeout=5)

    dropped = _passive_callback("oldest retractable filler")
    dropped[DELIVERY_ACK_FUTURE_KEY] = dropped_ack
    newest = _passive_callback("newest filler")
    mgr.enqueue_agent_callback(dropped)
    mgr.enqueue_agent_callback(newest)

    assert mgr.pending_agent_callbacks == [delivered, newest]
    assert not delivered_ack.done()
    assert dropped_ack.done() and dropped_ack.result() is False
    assert delivered.get(SWAP_PRIME_DELIVERY_CLAIM_KEY) is True

    allow_prime.set()
    await asyncio.wait_for(swap_task, timeout=10)
    try:
        assert mgr.session is new_session
        assert delivered_ack.done() and delivered_ack.result() is True
        assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in delivered
        assert mgr.pending_agent_callbacks == [newest]
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
async def test_final_swap_cancel_releases_claim_after_same_key_update():
    """A full-sequence abort releases claim without a premature ACK."""
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")
    prime_entered, _allow_prime = _install_passive_prime_barrier(
        new_session,
        expected_text="old snapshot",
    )
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    old_ack = asyncio.get_running_loop().create_future()
    old = _passive_callback("old snapshot", coalesce_key="state")
    old[DELIVERY_ACK_FUTURE_KEY] = old_ack
    mgr.enqueue_agent_callback(old)

    mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
    swap_task = mgr.final_swap_task
    await asyncio.wait_for(prime_entered.wait(), timeout=5)

    newer = _passive_callback("new snapshot", coalesce_key="state")
    mgr.enqueue_agent_callback(newer)
    assert mgr.pending_agent_callbacks == [old, newer]
    assert old.get(SWAP_PRIME_DELIVERY_CLAIM_KEY) is True
    assert not old_ack.done()

    swap_task.cancel()
    await asyncio.wait_for(swap_task, timeout=10)
    assert mgr.session is old_session
    assert new_session.closed
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in old
    assert not old_ack.done()

    selected, text = mgr._select_passive_callbacks_for_swap_prime()
    assert old_ack.done() and old_ack.result() is False
    assert selected == [newer]
    assert "new snapshot" in text and "old snapshot" not in text
    assert mgr.pending_agent_callbacks == [newer]
    mgr._release_swap_prime_passive_claims(selected)


@pytest.mark.asyncio
async def test_final_swap_passive_prime_failure_aborts_pending_session():
    """An uncertain provider write cannot be promoted and retried later."""
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")

    async def _prime(text, *, skipped=False):
        new_session.prime_calls.append((text, skipped))
        if "uncertain provider write" in text:
            raise RuntimeError("provider outcome unknown")

    new_session.prime_context = _prime
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    delivery_ack = asyncio.get_running_loop().create_future()
    callback = _passive_callback("uncertain provider write")
    callback[DELIVERY_ACK_FUTURE_KEY] = delivery_ack
    mgr.enqueue_agent_callback(callback)

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.session is old_session
    assert new_session.closed
    assert mgr.pending_agent_callbacks == [callback]
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in callback
    assert not delivery_ack.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_gemini", [False, True])
async def test_final_swap_native_media_retraction_before_text_aborts_pending(
    is_gemini,
):
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")
    new_session._is_gemini = is_gemini
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    callback = _passive_callback("retracted after native staging")
    callback[DELIVERY_RETRACTED_KEY] = True
    mgr.pending_agent_callbacks = [callback]
    mgr._select_passive_callbacks_for_swap_prime = (
        lambda **_kwargs: ([callback], "")
    )
    mgr._stage_passive_callback_media = AsyncMock(return_value={
        "safe_to_continue": True,
        "native_prefix_committed": True,
        "native_rejection_pending": not is_gemini,
        "rejected": False,
    })
    mgr._render_claimed_passive_callbacks_for_swap_prime = MagicMock(
        return_value=([], "")
    )

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.session is old_session
    assert new_session.closed is True
    assert mgr.pending_agent_callbacks == []


@pytest.mark.asyncio
async def test_final_swap_retracted_claim_aborts_pending_session():
    """A business retraction during prime wins without provider/local drift."""
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")
    prime_entered, allow_prime = _install_passive_prime_barrier(
        new_session,
        expected_text="cancelled music request",
    )
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    delivery_ack = asyncio.get_running_loop().create_future()
    callback = _passive_callback("cancelled music request")
    callback["channel"] = "music_playback"
    callback[DELIVERY_ACK_FUTURE_KEY] = delivery_ack
    mgr.enqueue_agent_callback(callback)

    mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
    swap_task = mgr.final_swap_task
    await asyncio.wait_for(prime_entered.wait(), timeout=5)

    mirror = {
        "_callback_delivery_id": callback["_callback_delivery_id"],
        "summary": "cancelled music request",
    }
    legacy_extra = "legacy plain-string extra"
    mgr.pending_extra_replies.extend([legacy_extra, mirror])
    callback[DELIVERY_RETRACTED_KEY] = True
    mgr._purge_undeliverable_callbacks()
    assert mgr.pending_agent_callbacks == [callback]
    assert mgr.pending_extra_replies == [legacy_extra, mirror]
    assert callback.get(SWAP_PRIME_DELIVERY_CLAIM_KEY) is True

    allow_prime.set()
    await asyncio.wait_for(swap_task, timeout=10)

    assert mgr.session is old_session
    assert new_session.closed
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == [legacy_extra]
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in callback
    assert delivery_ack.done() and delivery_ack.result() is False


@pytest.mark.asyncio
async def test_final_swap_rechecks_retraction_after_core_voice_lock_wait():
    """Retraction while the shared voice lock is held must not close old."""
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    old_session = _make_fake_realtime_session("old")
    new_session = _make_fake_realtime_session("pending")
    prime_entered, allow_prime = _install_passive_prime_barrier(
        new_session,
        expected_text="retracted during voice lock wait",
    )

    class _CoreVoiceLockBarrier:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def __aenter__(self):
            self.entered.set()
            await self.release.wait()

        async def __aexit__(self, *_exc):
            return False

    voice_lock = _CoreVoiceLockBarrier()
    mgr._core_voice_session_swap_lock = voice_lock
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True

    delivery_ack = asyncio.get_running_loop().create_future()
    callback = _passive_callback("retracted during voice lock wait")
    callback[DELIVERY_ACK_FUTURE_KEY] = delivery_ack
    mgr.enqueue_agent_callback(callback)

    mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
    swap_task = mgr.final_swap_task
    try:
        await asyncio.wait_for(prime_entered.wait(), timeout=5)
        allow_prime.set()
        await asyncio.wait_for(voice_lock.entered.wait(), timeout=5)
        callback[DELIVERY_RETRACTED_KEY] = True
        mgr._purge_undeliverable_callbacks()
        assert callback.get(SWAP_PRIME_DELIVERY_CLAIM_KEY) is True
        voice_lock.release.set()
        await asyncio.wait_for(swap_task, timeout=10)

        assert mgr.session is old_session
        assert not old_session.closed and old_session.ws is not None
        assert new_session.closed
        assert mgr.pending_agent_callbacks == []
        assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in callback
        assert delivery_ack.done() and delivery_ack.result() is False
        assert mgr.is_hot_swap_imminent is False
    finally:
        allow_prime.set()
        voice_lock.release.set()
        if not swap_task.done():
            swap_task.cancel()
        await _drain_task(swap_task)
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
async def test_final_swap_rechecks_retraction_after_waiting_for_promote_lock():
    """A claim retracted while promote waits for the CAS lock must abort."""
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    old_close_complete = asyncio.Event()

    class _CloseSignals(_FakeSession):
        async def close(self):
            await super().close()
            old_close_complete.set()

    old_session = _CloseSignals("old")
    new_session = _make_fake_realtime_session("pending")
    prime_entered, allow_prime = _install_passive_prime_barrier(
        new_session,
        expected_text="retracted during CAS wait",
    )
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    delivery_ack = asyncio.get_running_loop().create_future()
    callback = _passive_callback("retracted during CAS wait")
    callback[DELIVERY_ACK_FUTURE_KEY] = delivery_ack
    mgr.enqueue_agent_callback(callback)

    await mgr.lock.acquire()
    try:
        mgr.final_swap_task = asyncio.create_task(mgr._perform_final_swap_sequence())
        swap_task = mgr.final_swap_task
        await asyncio.wait_for(prime_entered.wait(), timeout=5)
        allow_prime.set()
        await asyncio.wait_for(old_close_complete.wait(), timeout=5)
        callback[DELIVERY_RETRACTED_KEY] = True
        mgr._purge_undeliverable_callbacks()
        assert callback.get(SWAP_PRIME_DELIVERY_CLAIM_KEY) is True
    finally:
        mgr.lock.release()

    await asyncio.wait_for(swap_task, timeout=10)

    assert mgr.session is old_session
    assert new_session.closed
    assert mgr.pending_agent_callbacks == []
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in callback
    assert delivery_ack.done() and delivery_ack.result() is False
    assert mgr.is_hot_swap_imminent is False


@pytest.mark.asyncio
async def test_final_swap_promote_cas_loss_releases_passive_claim():
    """A takeover at promote keeps the callback queued and unclaimed."""
    mgr = _make_swap_manager()
    mgr.pending_agent_callbacks = []
    mgr._normalize_context_text_for_source = lambda _source, text: text
    winner_session = _FakeSession("winner")
    new_session = _make_fake_realtime_session("pending")

    class _TakeoverOnClose(_FakeSession):
        async def close(self):
            await super().close()
            mgr.session = winner_session
            mgr.message_handler_task = object()

    old_session = _TakeoverOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None

    delivery_ack = asyncio.get_running_loop().create_future()
    callback = _passive_callback("queued across CAS loss")
    callback[DELIVERY_ACK_FUTURE_KEY] = delivery_ack
    mgr.enqueue_agent_callback(callback)

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.session is winner_session
    assert new_session.closed
    assert mgr.pending_agent_callbacks == [callback]
    assert SWAP_PRIME_DELIVERY_CLAIM_KEY not in callback
    assert not delivery_ack.done()


@pytest.mark.asyncio
async def test_final_swap_promote_cas_loss_restores_injected_extras():
    """Promote CAS loss discards the primed session — the selected extras
    must remain queued for the takeover epoch's next hot-swap (the queue is
    kept untouched through the prime window), mirroring _deferred."""
    mgr = _make_swap_manager()
    winner_session = _FakeSession("winner")
    new_session = _FakeSession("pending")

    class _TakeoverOnClose(_FakeSession):
        async def close(self):
            await super().close()
            mgr.session = winner_session
            mgr.message_handler_task = object()

    old_session = _TakeoverOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None

    extra_a = _extra_entry("id-a", "task A finished")
    extra_b = _extra_entry("id-b", "task B finished")
    mgr.pending_extra_replies = [extra_a, extra_b]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled(), "abort cleanup must not self-cancel the swap task"
    assert mgr.session is winner_session
    assert new_session.closed
    assert new_session.prime_calls and new_session.prime_calls[0][1] is False, \
        "extras must have actually been primed (skipped=False) before the abort"
    assert mgr.pending_extra_replies == [extra_a, extra_b], \
        "CAS-loss abort must restore the injected extras to the queue"


@pytest.mark.asyncio
async def test_final_swap_swallowed_cancel_restores_injected_extras():
    """The pre-promote checkpoint exit (external cancel swallowed by an inner
    await) also discards the primed session — injected extras must be restored."""
    mgr = _make_swap_manager()
    new_session = _FakeSession("pending")

    class _SwallowExternalCancelOnClose(_FakeSession):
        async def close(self):
            await super().close()
            t = asyncio.current_task()
            t.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                # Swallow on purpose — this reproduces the 3.11 quirk where an
                # inner await consumes the cancel (_must_cancel clears while
                # cancelling() stays 1), so only the pre-promote checkpoint
                # can catch it.
                pass

    old_session = _SwallowExternalCancelOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None

    extra = _extra_entry("id-cancel", "task C finished")
    mgr.pending_extra_replies = [extra]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled(), \
        "the handler must survive its own cleanup (no reset self-cancel)"
    assert new_session.prime_calls and new_session.prime_calls[0][1] is False
    assert mgr.session is old_session
    assert new_session.closed
    assert mgr.pending_extra_replies == [extra], \
        "cancelled swap must restore the injected extras to the queue"


@pytest.mark.asyncio
async def test_final_swap_listener_cancel_timeout_restores_injected_extras():
    """Step 1's old-listener cancel timeout aborts via RuntimeError into the
    fail-close path (session cleared). The primed session never speaks — the
    injected extras must be restored before the early return."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True

    async def _stubborn_listener():
        # Absorb the swap's cancel and keep hanging so its 2s wait_for times
        # out; the second cancel (test teardown) is allowed through.
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()
            raise

    listener_task = asyncio.create_task(_stubborn_listener())
    mgr.message_handler_task = listener_task
    await asyncio.sleep(0)

    extra = _extra_entry("id-timeout", "task T finished")
    mgr.pending_extra_replies = [extra]

    try:
        swap_task = await _run_swap_as_final_swap_task(mgr)

        assert not swap_task.cancelled(), \
            "the handler must survive its own cleanup (no reset self-cancel)"
        assert mgr.session is None, "listener-timeout abort fail-closes the session"
        assert mgr.is_active is False
        assert new_session.closed
        assert mgr.pending_extra_replies == [extra], \
            "listener-timeout abort must restore the injected extras to the queue"
    finally:
        await _drain_task(listener_task)


@pytest.mark.asyncio
async def test_final_swap_post_promote_ws_invalid_restores_injected_extras():
    """The 4th exit: promote succeeds (removal happens) but the new session's
    ws is already dead, so the swap raises and fail-closes (session cleared).
    The primed session never speaks — the promote-removed extras must be
    restored; topic-hook extras are excluded from the restore (their
    lifecycle belongs to the voice-block sweep / TopicHookPool retry)."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True  # fail-close 必须真的把它翻回 False，默认 False 会假绿
    mgr.message_handler_task = None

    async def _kill_new_session_ws(*args, **kwargs):
        # Models the server dropping the new connection inside the swap window.
        mgr.session.ws = None

    mgr._apply_pending_tts_route_after_swap = _kill_new_session_ws

    extra = _extra_entry("id-ws", "task W finished")
    extra_topic = _extra_entry("id-ws-topic", "deep topic hook")
    extra_topic["source_kind"] = "topic"
    mgr.pending_extra_replies = [extra, extra_topic]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled(), \
        "the handler must survive its own cleanup (no reset self-cancel)"
    assert mgr.session is None, "post-promote ws-invalid must fail-close the session"
    assert mgr.is_active is False
    assert mgr.pending_extra_replies == [extra], \
        "post-promote ws-invalid abort must restore the removed extras (topic excluded)"


@pytest.mark.asyncio
async def test_final_swap_post_promote_failure_with_live_session_does_not_restore():
    """Double-delivery guard: when a post-promote step fails but the promoted
    session survives as self.session, the primed extras are already in its
    context and will be spoken next turn — they must NOT be restored."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None

    async def _boom(*args, **kwargs):
        raise RuntimeError("post-promote step failed")

    mgr._prime_late_next_session_context_after_swap = _boom

    extra = _extra_entry("id-live", "task L finished")
    mgr.pending_extra_replies = [extra]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.session is new_session, "session survives the post-promote failure"
    assert not new_session.closed
    assert mgr.pending_extra_replies == [], \
        "extras already primed into the live session must not be re-queued"


@pytest.mark.asyncio
async def test_final_swap_restore_preserves_order_with_deferred_and_new_entries(monkeypatch):
    """After an abort the queue preserves original order: selected entries
    stay in place at the head (never checked out), ahead of the deferred ones
    and of entries enqueued during the swap window."""
    mgr = _make_swap_manager()
    winner_session = _FakeSession("winner")
    new_session = _FakeSession("pending")

    extra_a = _extra_entry("id-1", "first")
    extra_b = _extra_entry("id-2", "second")
    extra_c = _extra_entry("id-3", "arrived mid-swap")

    monkeypatch.setattr(
        "main_logic.core.lifecycle._select_callbacks_within_token_budget",
        lambda callbacks, budget: (callbacks[:1], list(callbacks[1:])),
    )

    class _TakeoverOnClose(_FakeSession):
        async def close(self):
            await super().close()
            mgr.pending_extra_replies.append(extra_c)  # new arrival inside the swap window
            mgr.session = winner_session
            mgr.message_handler_task = object()

    old_session = _TakeoverOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None
    mgr.pending_extra_replies = [extra_a, extra_b]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.pending_extra_replies == [extra_a, extra_b, extra_c], \
        "restore must preserve original relative order: selected, deferred, new arrivals"


@pytest.mark.asyncio
async def test_final_swap_abort_leaves_retracted_extras_purgeable():
    """A retraction landing inside the swap window must stay effective: the
    selected entries remain queue-resident through the window (no checkout),
    so the standard retraction purge still sees and removes them after the
    abort — nothing escapes and nothing resurrects."""
    mgr = _make_swap_manager()
    winner_session = _FakeSession("winner")
    new_session = _FakeSession("pending")

    extra_kept = _extra_entry("id-kept", "still wanted")
    extra_retracted = _extra_entry("id-retracted", "withdrawn mid-swap")

    class _TakeoverOnClose(_FakeSession):
        async def close(self):
            await super().close()
            mgr.pending_agent_callbacks = [
                {"_callback_delivery_id": "id-retracted", DELIVERY_RETRACTED_KEY: True},
            ]
            mgr.session = winner_session
            mgr.message_handler_task = object()

    old_session = _TakeoverOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None
    mgr.pending_extra_replies = [extra_kept, extra_retracted]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    # 中止后条目仍在队列（原地保留），常规 purge 照常清掉窗口期内 retract 的那条
    mgr._purge_undeliverable_callbacks()
    assert mgr.pending_extra_replies == [extra_kept], \
        "a retraction inside the swap window must remain purgeable after the abort"


@pytest.mark.asyncio
async def test_final_swap_abort_does_not_resurrect_concurrently_delivered_extra():
    """greptile P1 on this PR: a voice proactive delivery completing inside
    the swap window prunes the delivered extra from the queue by delivery_id.
    Because selected entries stay queue-resident (no checkout), that prune
    hits them normally and an aborted swap must NOT bring them back."""
    mgr = _make_swap_manager()
    winner_session = _FakeSession("winner")
    new_session = _FakeSession("pending")

    extra_spoken = _extra_entry("id-spoken", "delivered by voice mid-swap")
    extra_other = _extra_entry("id-other", "still pending")

    class _VoiceDeliverThenTakeoverOnClose(_FakeSession):
        async def close(self):
            await super().close()
            # 模拟 trigger_agent_callbacks 语音投递成功清除（按 delivery_id）
            mgr.pending_extra_replies = [
                e for e in mgr.pending_extra_replies
                if e.get("_callback_delivery_id") != "id-spoken"
            ]
            mgr.session = winner_session
            mgr.message_handler_task = object()

    old_session = _VoiceDeliverThenTakeoverOnClose("old")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None
    mgr.pending_extra_replies = [extra_spoken, extra_other]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.pending_extra_replies == [extra_other], \
        "an extra delivered by voice during the swap window must not be resurrected"


@pytest.mark.asyncio
async def test_final_swap_ws_invalid_restore_excludes_concurrently_delivered_extra():
    """The ws-invalid exit restores only what the promote actually removed:
    an extra pruned by a concurrent voice delivery BEFORE promote is not in
    the removed set and must stay gone even on the restore-carrying exit."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")

    extra_spoken = _extra_entry("id-spoken", "delivered by voice mid-swap")
    extra_other = _extra_entry("id-other", "still pending")

    async def _voice_deliver_on_old_close():
        mgr.pending_extra_replies = [
            e for e in mgr.pending_extra_replies
            if e.get("_callback_delivery_id") != "id-spoken"
        ]
        old_session.closed = True

    old_session.close = _voice_deliver_on_old_close
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None

    async def _kill_new_session_ws(*args, **kwargs):
        mgr.session.ws = None

    mgr._apply_pending_tts_route_after_swap = _kill_new_session_ws
    mgr.pending_extra_replies = [extra_spoken, extra_other]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert mgr.session is None
    assert mgr.pending_extra_replies == [extra_other], \
        "ws-invalid restore must re-queue only the promote-removed entries"


@pytest.mark.asyncio
async def test_final_swap_happy_path_consumes_selected_keeps_deferred(monkeypatch):
    """A successful swap must not restore anything: selected extras were
    delivered into the promoted session, deferred ones stay for the next swap."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True
    mgr.message_handler_task = None

    extra_sel = _extra_entry("id-sel", "goes into this swap")
    extra_def = _extra_entry("id-def", "waits for the next swap")
    mgr.pending_extra_replies = [extra_sel, extra_def]

    monkeypatch.setattr(
        "main_logic.core.lifecycle._select_callbacks_within_token_budget",
        lambda callbacks, budget: (callbacks[:1], list(callbacks[1:])),
    )

    try:
        swap_task = await _run_swap_as_final_swap_task(mgr)

        assert not swap_task.cancelled()
        assert mgr.session is new_session
        assert new_session.prime_calls and new_session.prime_calls[0][1] is False
        assert mgr.pending_extra_replies == [extra_def], \
            "successful swap consumes selected extras and keeps only deferred ones"
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
async def test_final_swap_cancel_after_old_close_fail_closes_instead_of_restarting():
    """Cancellation landing after step 2 closed the old session but before
    promote leaves self.session pointing at a closed client (ws=None). The
    CancelledError handler must fail-close instead of restarting a listener
    on the dead session (coderabbit Major on this PR)."""
    mgr = _make_swap_manager()
    new_session = _FakeSession("pending")
    old_session = _make_fake_realtime_session("old")

    _orig_close = old_session.close

    async def _close_then_swallowed_cancel():
        # Step 2 closes the old session (ws cleared), then the external cancel
        # arrives and is swallowed — the pre-promote checkpoint re-raises it.
        await _orig_close()
        t = asyncio.current_task()
        t.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            # Swallow on purpose: reproduces the checkpoint-only cancel path.
            pass

    old_session.close = _close_then_swallowed_cancel
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True
    mgr.message_handler_task = None

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert new_session.closed
    assert mgr.session is None, \
        "cancel after old close must fail-close, not keep the dead session"
    assert mgr.is_active is False
    assert mgr.message_handler_task is None, \
        "no listener may be restarted on a closed session"


@pytest.mark.asyncio
async def test_final_swap_cancel_after_old_close_fail_closes_text_session_too():
    """Same scenario for a TEXT session: OmniOfflineClient clears ``llm`` on
    close() (it has no ws), so the dead-session fail-close must recognize it
    instead of restarting its keep-alive listener on a closed client
    (coderabbit follow-up on this PR)."""
    mgr = _make_swap_manager()
    new_session = _FakeSession("pending")

    old_session = object.__new__(OmniOfflineClient)
    old_session.llm = object()  # 活跃文本会话的标志；close 后被清空

    async def _close_then_swallowed_cancel():
        old_session.llm = None  # mirror OmniOfflineClient.close()
        t = asyncio.current_task()
        t.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            # Swallow on purpose: reproduces the checkpoint-only cancel path.
            pass

    old_session.close = _close_then_swallowed_cancel
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True
    mgr.message_handler_task = None

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled()
    assert new_session.closed
    assert mgr.session is None, \
        "a closed text session must be fail-closed, not kept as self.session"
    assert mgr.is_active is False
    assert mgr.message_handler_task is None, \
        "no keep-alive listener may be restarted on a closed text session"


@pytest.mark.asyncio
async def test_final_swap_post_promote_cancel_restores_removed_extras():
    """Cancellation after promote comes only from external reset/end_session/
    start_session preludes, all of which close the promoted session next — the
    primed content never delivers, so the promote-removed extras must go back
    to the queue (coderabbit Major on this PR)."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True
    mgr.message_handler_task = None

    async def _cancelled_mid_post_promote(*args, **kwargs):
        # External cancel lands on the swap task during a post-promote await.
        t = asyncio.current_task()
        t.cancel()
        await asyncio.sleep(0)
        return 0

    mgr._prime_late_next_session_context_after_swap = _cancelled_mid_post_promote

    extra = _extra_entry("id-post-promote", "task P finished")
    mgr.pending_extra_replies = [extra]

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled(), \
        "the handler absorbs the cancel and finishes its cleanup normally"
    assert mgr.session is new_session, \
        "the handler leaves the promoted session for the canceller to close"
    assert mgr.pending_extra_replies == [extra], \
        "post-promote cancel must restore the promote-removed extras"
    # 双投守卫：restore 之后不得给将死的 promoted 会话重启 listener——
    # 没有 listener，服务器响应不会被消费播出，塞回的条目才是唯一投递路径。
    assert mgr.message_handler_task is None, \
        "no listener may be restarted on the about-to-close promoted session"


@pytest.mark.asyncio
async def test_final_swap_restore_excludes_extra_delivered_after_promote():
    """greptile follow-up P1: a voice delivery can consume a callback AFTER
    promote removed its extra (the delivery's extras prune no-ops on the
    checked-out entry). The restore detects this via the paired callback
    vanishing from pending_agent_callbacks inside the window and must not
    re-queue the already-announced entry; an entry whose callback is still
    pending restores normally."""
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None

    extra_spoken = _extra_entry("id-spoken", "delivered by voice after promote")
    extra_kept = _extra_entry("id-kept", "still undelivered")
    mgr.pending_extra_replies = [extra_spoken, extra_kept]
    mgr.pending_agent_callbacks = [
        {"_callback_delivery_id": "id-spoken"},
        {"_callback_delivery_id": "id-kept"},
    ]

    async def _voice_delivery_then_external_cancel(*args, **kwargs):
        # 窗口期内语音投递成功消费 id-spoken：cb 侧被 prune；extras 侧因条目
        # 已在 promote 时摘走而 no-op（正是被复活的那半边）。随后外部取消命中。
        mgr.pending_agent_callbacks = [
            cb for cb in mgr.pending_agent_callbacks
            if cb.get("_callback_delivery_id") != "id-spoken"
        ]
        mgr.pending_extra_replies = [
            e for e in mgr.pending_extra_replies
            if e.get("_callback_delivery_id") != "id-spoken"
        ]
        t = asyncio.current_task()
        t.cancel()
        await asyncio.sleep(0)
        return 0

    mgr._prime_late_next_session_context_after_swap = _voice_delivery_then_external_cancel

    swap_task = await _run_swap_as_final_swap_task(mgr)

    assert not swap_task.cancelled(), \
        "the handler absorbs the cancel and finishes its cleanup normally"
    assert mgr.session is new_session
    assert mgr.pending_extra_replies == [extra_kept], \
        "an extra whose callback was consumed inside the window must not be re-queued"


# ─────────────────────────────────────────────────────────────────────────────
# prime_context dispatch failures (#2515 T2): since #2345 the skipped=False
# prime dispatches through the response arbiter, whose ticket surfaces
# failures as RuntimeError / ConnectionError / TimeoutError — none of them in
# the targeted handler's legacy (ConnectionClosed, AttributeError) tuple.
# Nothing leaked before this fix: the swap's OUTER except Exception caught
# them and ran the same cleanup. What was wrong is the ENVELOPE — the outer
# handler reports INTERNAL_UPDATE_FAILED to the frontend, while a failed
# prime is the benign "abandon this swap, keep the old session" case the
# targeted handler exists for. These tests pin the envelope, one per prime
# branch (each guards its own except site).
# ─────────────────────────────────────────────────────────────────────────────


class _PrimeDispatchFails(_FakeSession):
    """prime_context raises the way an arbiter ticket failure surfaces."""

    async def prime_context(self, text, *, skipped=False):
        await super().prime_context(text, skipped=skipped)
        raise RuntimeError(
            "response dispatch interrupted by pending server VAD response"
        )


@pytest.mark.asyncio
async def test_prime_dispatch_failure_takes_the_targeted_abort_not_the_generic_handler():
    """skipped=False branch (announce prime, the arbiter-queued one)."""
    mgr = _make_swap_manager()
    statuses = []

    async def _send_status(payload):
        statuses.append(payload)

    mgr.send_status = _send_status
    old_session = _FakeSession("old")
    new_session = _PrimeDispatchFails("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None
    # A queued extra forces the skipped=False announce-prime branch.
    mgr.pending_extra_replies = [_extra_entry("id-a", "task A finished")]

    await mgr._perform_final_swap_sequence()

    assert new_session.prime_calls and new_session.prime_calls[0][1] is False, \
        "the failing prime must be the skipped=False announce prime"
    assert mgr.session is old_session, "an abandoned swap keeps the old session"
    assert new_session.closed, "the targeted abort must close the pending session"
    assert mgr.pending_session is None
    assert mgr.is_hot_swap_imminent is False
    assert not any("INTERNAL_UPDATE_FAILED" in s for s in statuses), (
        "a failed prime dispatch is a benign swap abort — it must take the "
        "targeted handler, not fall through to the generic one that alarms "
        "the frontend with INTERNAL_UPDATE_FAILED"
    )


@pytest.mark.asyncio
async def test_prime_dispatch_failure_on_the_skipped_branch_takes_the_targeted_abort():
    """skipped=True branch (instruction prime) — guards the second except site."""
    mgr = _make_swap_manager()
    statuses = []

    async def _send_status(payload):
        statuses.append(payload)

    mgr.send_status = _send_status
    old_session = _FakeSession("old")
    new_session = _PrimeDispatchFails("pending")
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True
    mgr.message_handler_task = None
    mgr.pending_extra_replies = []  # no extras → the skipped=True branch

    await mgr._perform_final_swap_sequence()

    assert new_session.prime_calls and new_session.prime_calls[0][1] is True, \
        "the failing prime must be the skipped=True instruction prime"
    assert mgr.session is old_session
    assert new_session.closed
    assert mgr.pending_session is None
    assert mgr.is_hot_swap_imminent is False
    assert not any("INTERNAL_UPDATE_FAILED" in s for s in statuses)


@pytest.mark.asyncio
async def test_passive_native_rejection_retires_replacement_before_callback_ack():
    mgr = _make_swap_manager()
    statuses = []
    cleanup_steps = []

    def _record_status(status):
        statuses.append(status)
        cleanup_steps.append("status")

    mgr._close_independent_asr = AsyncMock(
        side_effect=lambda **_kwargs: cleanup_steps.append("asr")
    )
    mgr.send_status = AsyncMock(side_effect=_record_status)
    mgr.send_session_ended_by_server = AsyncMock(
        side_effect=lambda: cleanup_steps.append("ended")
    )
    old_session = _FakeSession("old")
    new_session = OmniRealtimeClient.__new__(OmniRealtimeClient)
    new_session.ws = object()
    new_session._fatal_error_occurred = False
    new_session._is_gemini = False
    new_session._session_update_ack_waiters = []
    new_session.get_multimodal_turn_delivery = MagicMock(
        return_value=MultimodalTurnDelivery.DIRECT_ATOMIC
    )
    new_session.instructions = "initial instructions"
    new_session.closed = False
    new_session.prime_calls = []
    rejection_handler = None

    async def prime_context(text, *, skipped=False):
        new_session.prime_calls.append((text, skipped))
        new_session.instructions += "\n" + text

    async def stream_image(_image_b64, *, on_rejected=None, **_kwargs):
        nonlocal rejection_handler
        rejection_handler = on_rejected
        return ImageStageResult(accepted=True, mode="native")

    async def handle_messages():
        assert rejection_handler is not None
        # Longer than the removed 50ms grace period: the transaction must stay
        # uncommitted until a real Provider acknowledgement or rejection.
        await asyncio.sleep(0.08)
        rejection_handler("provider rejected passive image")
        await asyncio.Event().wait()

    async def close():
        new_session.closed = True
        new_session.ws = None

    new_session.prime_context = prime_context
    new_session.stream_image = stream_image
    new_session.handle_messages = handle_messages
    new_session.close = close

    callback_ack = asyncio.get_running_loop().create_future()
    callback = {
        "_callback_delivery_id": "id-passive-native-reject",
        "status": "completed",
        "summary": "inspect this native image",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image"],
        DELIVERY_ACK_FUTURE_KEY: callback_ack,
    }
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.pending_agent_callbacks = [callback]
    mgr.pending_extra_replies = []
    mgr.is_active = True
    mgr.is_hot_swap_imminent = True
    mgr._select_passive_callbacks_for_swap_prime = (
        lambda **_kwargs: ([callback], "")
    )
    mgr._render_claimed_passive_callbacks_for_swap_prime = (
        lambda selected: (selected, "passive callback text")
    )
    mgr._purge_undeliverable_callbacks = lambda: None

    await mgr._perform_final_swap_sequence()

    assert old_session.closed is True
    assert new_session.closed is True
    assert mgr.session is None
    mgr._close_independent_asr.assert_awaited_once_with(
        next_route_mode="blocked",
    )
    assert any("INTERNAL_UPDATE_FAILED" in status for status in statuses)
    mgr.send_session_ended_by_server.assert_awaited_once_with()
    assert cleanup_steps == ["asr", "status", "ended"]
    assert mgr.pending_agent_callbacks == [callback]
    assert callback_ack.done() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover_stage", ["barrier_wait", "listener_cancel"])
async def test_passive_native_rejection_preserves_concurrent_session_takeover(
    takeover_stage,
):
    """A stale media-rejection cleanup may retire only its own session pair."""
    mgr = _make_swap_manager()
    mgr._close_independent_asr = AsyncMock()
    mgr.send_status = AsyncMock()
    mgr.send_session_ended_by_server = AsyncMock()
    mgr._reset_preparation_state = AsyncMock()

    old_session = _FakeSession("old")
    winner_session = _FakeSession("winner")
    winner_listener = asyncio.create_task(asyncio.Event().wait())
    new_session = OmniRealtimeClient.__new__(OmniRealtimeClient)
    new_session.ws = object()
    new_session._fatal_error_occurred = False
    new_session._is_gemini = False
    new_session._session_update_ack_waiters = []
    new_session.get_multimodal_turn_delivery = MagicMock(
        return_value=MultimodalTurnDelivery.DIRECT_ATOMIC
    )
    new_session.instructions = "initial instructions"
    new_session.closed = False
    rejection_handler = None
    winner_installed = False

    def install_winner():
        nonlocal winner_installed
        if winner_installed:
            return
        winner_installed = True
        mgr.session = winner_session
        mgr.message_handler_task = winner_listener
        mgr.is_active = True

    async def prime_context(text, *, skipped=False):
        del skipped
        new_session.instructions += "\n" + text

    async def stream_image(_image_b64, *, on_rejected=None, **_kwargs):
        nonlocal rejection_handler
        rejection_handler = on_rejected
        return ImageStageResult(accepted=True, mode="native")

    async def handle_messages():
        assert rejection_handler is not None
        if takeover_stage == "barrier_wait":
            install_winner()
        rejection_handler("provider rejected passive image")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if takeover_stage == "listener_cancel":
                install_winner()
            raise

    async def close():
        new_session.closed = True
        new_session.ws = None

    new_session.prime_context = prime_context
    new_session.stream_image = stream_image
    new_session.handle_messages = handle_messages
    new_session.close = close

    callback_ack = asyncio.get_running_loop().create_future()
    callback = {
        "_callback_delivery_id": f"id-takeover-{takeover_stage}",
        "status": "completed",
        "summary": "inspect this native image",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image"],
        DELIVERY_ACK_FUTURE_KEY: callback_ack,
    }
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.pending_agent_callbacks = [callback]
    mgr.pending_extra_replies = []
    mgr.is_active = True
    mgr.is_hot_swap_imminent = True
    mgr._select_passive_callbacks_for_swap_prime = (
        lambda **_kwargs: ([callback], "")
    )
    mgr._render_claimed_passive_callbacks_for_swap_prime = (
        lambda selected: (selected, "passive callback text")
    )
    mgr._purge_undeliverable_callbacks = lambda: None

    try:
        await mgr._perform_final_swap_sequence()

        assert old_session.closed is True
        assert new_session.closed is True
        assert winner_installed is True
        assert mgr.session is winner_session
        assert mgr.message_handler_task is winner_listener
        assert winner_listener.done() is False
        assert mgr.is_active is True
        mgr._close_independent_asr.assert_not_awaited()
        mgr.send_status.assert_not_awaited()
        mgr.send_session_ended_by_server.assert_not_awaited()
        mgr._reset_preparation_state.assert_not_awaited()
        assert mgr.pending_agent_callbacks == [callback]
        assert callback_ack.done() is False
    finally:
        winner_listener.cancel()
        await asyncio.gather(winner_listener, return_exceptions=True)


@pytest.mark.asyncio
async def test_passive_native_session_update_ack_commits_callback():
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = OmniRealtimeClient.__new__(OmniRealtimeClient)
    new_session.ws = object()
    new_session._fatal_error_occurred = False
    new_session._is_gemini = False
    new_session._session_update_ack_waiters = []
    new_session.instructions = "initial instructions"
    new_session.closed = False
    new_session.prime_calls = []

    async def prime_context(text, *, skipped=False):
        new_session.prime_calls.append((text, skipped))
        new_session.instructions += "\n" + text

    async def stream_image(_image_b64, **_kwargs):
        return ImageStageResult(accepted=True, mode="native")

    async def handle_messages():
        # A stale setup acknowledgement must not settle the media handoff.
        new_session._notify_session_updated(
            {"session": {"instructions": "initial instructions"}}
        )
        await asyncio.sleep(0)
        new_session._notify_session_updated(
            {"session": {"instructions": new_session.instructions}}
        )
        await asyncio.Event().wait()

    async def close():
        new_session.closed = True
        new_session.ws = None

    new_session.prime_context = prime_context
    new_session.stream_image = stream_image
    new_session.handle_messages = handle_messages
    new_session.close = close

    callback_ack = asyncio.get_running_loop().create_future()
    callback = {
        "_callback_delivery_id": "id-passive-native-ack",
        "status": "completed",
        "summary": "inspect this native image",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image"],
        DELIVERY_ACK_FUTURE_KEY: callback_ack,
    }
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.pending_agent_callbacks = [callback]
    mgr.pending_extra_replies = []
    mgr.is_active = True
    mgr.is_hot_swap_imminent = True
    mgr._select_passive_callbacks_for_swap_prime = (
        lambda **_kwargs: ([callback], "")
    )
    mgr._render_claimed_passive_callbacks_for_swap_prime = (
        lambda selected: (selected, "passive callback text")
    )
    mgr._purge_undeliverable_callbacks = lambda: None

    await mgr._perform_final_swap_sequence()

    assert old_session.closed is True
    assert new_session.closed is False
    assert mgr.session is new_session
    assert mgr.pending_agent_callbacks == []
    assert callback_ack.result() is True


@pytest.mark.asyncio
async def test_settled_media_barrier_releases_image_rejection_handlers():
    """A settled barrier makes late image rejections moot; free their closures.

    ``stream_image`` deliberately leaves each image's rejection handler
    registered after a successful send, because a provider rejection can arrive
    later than the send returns. Once the ``session.updated`` barrier proves the
    provider processed the whole prefix, those handlers are dead weight -- and
    each closure pins the entire callback, which can hold several multi-megabyte
    base64 images, until the 60-second expiry.
    """
    mgr = _make_swap_manager()
    old_session = _FakeSession("old")
    new_session = _make_fake_realtime_session("pending")
    new_session._is_gemini = False
    new_session._inject_rejection_handlers = {}
    new_session.instructions = "prompt"
    _ack_waiters = []

    def _expect_session_update_ack(_instructions):
        fut = asyncio.get_running_loop().create_future()
        _ack_waiters.append(fut)
        # provider 立刻确认：屏障落地，晚到的图片拒绝就无关了。
        fut.set_result(True)
        return fut

    new_session.expect_session_update_ack = _expect_session_update_ack
    new_session.discard_session_update_ack = lambda _fut: None
    mgr.session = old_session
    mgr.pending_session = new_session
    mgr.is_hot_swap_imminent = True

    big_payload = "x" * 4096
    staged_ids = ["evt-callback-image-1", "evt-callback-image-2"]
    for staged in staged_ids:
        new_session._inject_rejection_handlers[staged] = (
            lambda _msg, _held=big_payload: None
        )
    # 一个不属于本次媒体投递的 handler，必须原样留着。
    new_session._inject_rejection_handlers["evt-unrelated"] = lambda _msg: None

    callback = _passive_callback("camera frame delivered")
    mgr.pending_agent_callbacks = [callback]
    mgr._select_passive_callbacks_for_swap_prime = (
        lambda **_kwargs: ([callback], "")
    )
    mgr._stage_passive_callback_media = AsyncMock(return_value={
        "safe_to_continue": True,
        "native_prefix_committed": True,
        "native_rejection_pending": True,
        "rejected": False,
        "rejection_observed": asyncio.Event(),
        "settled": False,
        "rejection_event_ids": list(staged_ids),
    })
    mgr._render_claimed_passive_callbacks_for_swap_prime = MagicMock(
        return_value=([callback], "camera frame delivered")
    )

    await _run_swap_as_final_swap_task(mgr)

    remaining = set(new_session._inject_rejection_handlers)
    assert remaining == {"evt-unrelated"}, remaining
