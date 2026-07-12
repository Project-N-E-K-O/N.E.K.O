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

import pytest

import main_logic.core as core_module
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.proactive_delivery import DELIVERY_RETRACTED_KEY


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
    mgr.lock = asyncio.Lock()
    mgr.session = None
    mgr.message_handler_task = None
    mgr.pending_session = None
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
    mgr._purge_retracted_agent_callbacks()
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
