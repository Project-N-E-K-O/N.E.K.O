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
"""
import asyncio

import pytest

import main_logic.core as core_module


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
