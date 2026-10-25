"""SessionStateMachine unit tests.

Coverage:
1. Initial state and normal proactive lifecycle transitions.
2. USER_INPUT sticky preemption during phase1/phase2/committing.
3. Zero-cost is_proactive_preempted reads using sticky flags and claim-token fallback.
4. can_start_proactive results across states.
5. PROACTIVE_DONE ownership reset without overwriting USER after preemption.
6. Subscriber ordering/state consistency.
7. Concurrent-fire consistency independent of scheduling order.
"""

from __future__ import annotations

import asyncio

import pytest

from main_logic.session_state import (
    ProactivePhase,
    SessionEvent,
    SessionStateMachine,
    TurnOwner,
)


def _sm() -> SessionStateMachine:
    return SessionStateMachine(lanlan_name="Test")


# ─────────────────────────────────────────────────────────────────────────────
# Initial state and normal proactive lifecycle.
# ─────────────────────────────────────────────────────────────────────────────

async def test_initial_state_is_idle():
    sm = _sm()
    assert sm.owner is TurnOwner.NONE
    assert sm.phase is ProactivePhase.IDLE
    assert sm.proactive_sid is None
    assert sm._preempted is False
    assert sm.is_proactive_preempted() is False
    assert sm.can_start_proactive() is True


async def test_normal_proactive_lifecycle():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.owner is TurnOwner.PROACTIVE

    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_1")
    assert sm.proactive_sid == "sid_1"

    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    assert sm.phase is ProactivePhase.PHASE2

    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    assert sm.phase is ProactivePhase.COMMITTING

    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.NONE
    assert sm.proactive_sid is None


# ─────────────────────────────────────────────────────────────────────────────
# USER_INPUT sticky preempt
# ─────────────────────────────────────────────────────────────────────────────

async def test_user_input_during_phase1_marks_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="user_sid")
    assert sm._preempted is True
    assert sm.owner is TurnOwner.USER
    assert sm.is_proactive_preempted() is True


async def test_user_input_during_phase2_marks_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_2")
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    await sm.fire(SessionEvent.USER_INPUT, sid="user_sid")
    assert sm._preempted is True
    assert sm.is_proactive_preempted(claim_token="sid_2") is True


async def test_user_input_during_committing_marks_preempted():
    """Mark preemption even in tiny commit windows. Later effects such as _record_proactive_chat
    still skip via committed=False, but the state machine must reflect reality."""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_3")
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    await sm.fire(SessionEvent.USER_INPUT, sid="user_sid")
    assert sm._preempted is True


async def test_user_input_when_idle_does_not_flip_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    assert sm._preempted is False
    assert sm.owner is TurnOwner.USER
    assert sm.is_proactive_preempted() is False


# ─────────────────────────────────────────────────────────────────────────────
# is_proactive_preempted claim_token fallback.
# ─────────────────────────────────────────────────────────────────────────────

async def test_is_proactive_preempted_sticky_after_user_input():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_x")
    # Sticky flag is already True even before sid changes.
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    # Matching claim_token/proactive_sid still returns True through sticky state.
    assert sm.is_proactive_preempted(claim_token="sid_x") is True


async def test_is_proactive_preempted_claim_token_mismatch_fallback():
    """Different proactive_sid/claim_token also returns True as defensive fallback;
    sticky normally triggers first, providing two layers."""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_current")
    assert sm.is_proactive_preempted(claim_token="sid_stale") is True


async def test_is_proactive_preempted_claim_token_matches_not_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_m")
    assert sm.is_proactive_preempted(claim_token="sid_m") is False


async def test_is_proactive_preempted_phase1_none_token_ok():
    """Before phase1 claims, claim_token=None uses sticky state only."""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.is_proactive_preempted(claim_token=None) is False


async def test_mark_user_input_preempt_sync_flips_flag_during_active_phase():
    """sid rotation synchronously calls mark_user_input_preempt under self.lock,
    atomically exposing sid writes/preemption. Active phases set it; idle does not.
    """
    sm = _sm()
    # No-op while idle.
    sm.mark_user_input_preempt()
    assert sm._preempted is False

    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm._preempted is False
    sm.mark_user_input_preempt()
    assert sm._preempted is True

    # Cover PHASE2 / COMMITTING too.
    sm._preempted = False
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    sm.mark_user_input_preempt()
    assert sm._preempted is True

    sm._preempted = False
    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    sm.mark_user_input_preempt()
    assert sm._preempted is True


async def test_mark_user_input_preempt_atomically_visible_before_full_fire():
    """Race regression: prepare_proactive_delivery must observe preemption after
    mark_user_input_preempt but before fire(USER_INPUT) through is_proactive_preempted().
    Model core.py's sid-rotation critical-section semantics.
    """
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="claim_sid")

    # Simulate handle_new_message writing sid and marking preemption under self.lock.
    sm.mark_user_input_preempt()
    # fire(USER_INPUT) has not run, but prepare_proactive_delivery's concurrent
    # locked recheck already sees preemption and cannot overwrite current_speech_id.
    assert sm.is_proactive_preempted(claim_token="claim_sid") is True


async def test_proactive_claim_dropped_if_preempted():
    """After phase1 user preemption, a later prepare_proactive_delivery CLAIM must not
    populate proactive_sid, or phase2 falsely believes it still owns the turn."""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="should_not_stick")
    assert sm.proactive_sid is None
    assert sm.is_proactive_preempted() is True


# ─────────────────────────────────────────────────────────────────────────────
# can_start_proactive
# ─────────────────────────────────────────────────────────────────────────────

async def test_can_start_proactive_false_when_phase1():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.can_start_proactive() is False


async def test_can_start_proactive_true_after_user_turn_completes():
    """Regression: owner==USER cannot permanently block proactive chat.

    USER_INPUT sets USER without AI_RESPONSE_END reset, not migrated in Stage 2.
    Gating can_start_proactive on USER permanently rejects all proactive calls after
    the first user message, a Codex-review P1. Check only phase
    and session._is_responding.
    """
    sm = _sm()
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    # User finished and AI is not responding, session=None; proactive work may start.
    assert sm.can_start_proactive(session=None) is True
    # A present session with _is_responding=False also permits startup.
    assert sm.can_start_proactive(session=_FakeSession(is_responding=False)) is True


async def test_can_start_proactive_true_after_done():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.can_start_proactive() is True


class _FakeSession:
    """Minimal session stub exposing only _is_responding."""

    def __init__(self, is_responding: bool) -> None:
        self._is_responding = is_responding


async def test_can_start_proactive_false_when_session_is_responding():
    """While AI replies to users, proactive cannot start even if the state machine is IDLE.

    Centralize the router's previous direct session._is_responding check into the state machine.
    """
    sm = _sm()
    assert sm.phase is ProactivePhase.IDLE
    session = _FakeSession(is_responding=True)
    assert sm.can_start_proactive(session=session) is False


async def test_can_start_proactive_true_with_idle_session():
    sm = _sm()
    session = _FakeSession(is_responding=False)
    assert sm.can_start_proactive(session=session) is True


async def test_can_start_proactive_none_session_falls_back_to_sm_only():
    """session=None checks only state-machine fields, retaining unit-test compatibility."""
    sm = _sm()
    assert sm.can_start_proactive(session=None) is True
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.can_start_proactive(session=None) is False


async def test_can_start_proactive_session_without_is_responding_attr():
    """Legacy sessions missing _is_responding must not raise; treat as False."""
    sm = _sm()

    class _BareSession:
        pass

    assert sm.can_start_proactive(session=_BareSession()) is True


# ─────────────────────────────────────────────────────────────────────────────
# PROACTIVE_DONE ownership handling.
# ─────────────────────────────────────────────────────────────────────────────

async def test_proactive_done_preserves_user_ownership_after_preempt():
    """After preemption owner is already USER when proactive fires DONE;
    preserve USER rather than NONE because users still own the turn."""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    assert sm.owner is TurnOwner.USER
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.owner is TurnOwner.USER
    # Phase and sticky flag really reset.
    assert sm.phase is ProactivePhase.IDLE
    assert sm._preempted is False


async def test_proactive_done_flips_owner_to_none_on_clean_exit():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="c")
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.owner is TurnOwner.NONE


async def test_second_proactive_after_done_starts_fresh():
    """After preemption, new proactive rounds transition IDLE -> PHASE1 with sticky cleared."""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u1")
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    # USER_INPUT makes owner USER; next START must reclaim ownership.
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm._preempted is False
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.owner is TurnOwner.PROACTIVE
    assert sm.is_proactive_preempted() is False


# ─────────────────────────────────────────────────────────────────────────────
# Subscribe.
# ─────────────────────────────────────────────────────────────────────────────

async def test_subscriber_sees_post_apply_state():
    """Subscriber callbacks always observe post-apply event state."""
    sm = _sm()
    observed: list[tuple[ProactivePhase, TurnOwner]] = []

    def cb(event, payload):
        observed.append((sm.phase, sm.owner))

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    # Subscriber callbacks schedule asynchronously; yield one loop turn.
    await asyncio.sleep(0)
    assert observed == [(ProactivePhase.PHASE1, TurnOwner.PROACTIVE)]


async def test_subscriber_exception_does_not_break_event_flow():
    sm = _sm()

    def bad(event, payload):
        raise RuntimeError("subscriber should not break caller")

    sm.subscribe(SessionEvent.PROACTIVE_START, bad)
    # fire must not raise.
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.phase is ProactivePhase.PHASE1


async def test_wildcard_subscriber_gets_all_events():
    sm = _sm()
    received: list[SessionEvent] = []

    def cb(event, payload):
        received.append(event)

    sm.subscribe(None, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    await asyncio.sleep(0)
    assert SessionEvent.PROACTIVE_START in received
    assert SessionEvent.USER_INPUT in received


async def test_async_subscriber_coroutine_is_scheduled():
    sm = _sm()
    fired = asyncio.Event()

    async def cb(event, payload):
        fired.set()

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await asyncio.wait_for(fired.wait(), timeout=1.0)


async def test_async_subscriber_returning_task_is_also_awaited():
    """Dispatch must own precreated Task returns as well as coroutines,
    preventing exceptions/cancellation bypassing _swallow_subscriber_exc into the loop.
    """
    sm = _sm()
    inner_fired = asyncio.Event()

    async def inner():
        inner_fired.set()

    def cb(event, payload):
        # Return Task rather than coroutine; earlier iscoroutine handling missed it.
        return asyncio.create_task(inner())

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await asyncio.wait_for(inner_fired.wait(), timeout=1.0)


async def test_async_subscriber_returning_task_with_exception_is_swallowed():
    """Task-returning subscribers must not emit Task-exception-was-never-retrieved
    warnings; done callbacks consume failures silently.
    """
    sm = _sm()

    async def inner_raises():
        raise RuntimeError("boom")

    def cb(event, payload):
        return asyncio.create_task(inner_raises())

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    # Let inner_raises run and be consumed by its done callback.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ─────────────────────────────────────────────────────────────────────────────
# Concurrency.
# ─────────────────────────────────────────────────────────────────────────────

async def test_interleaved_user_input_and_proactive_start_converges():
    """Both USER_INPUT/PROACTIVE_START interleavings must end consistently.

    - USER_INPUT first: owner=USER; PROACTIVE_START sets PROACTIVE,
      phase=PHASE1, and is_proactive_preempted=False because START
      deliberately clears sticky for a new round, preventing prior
      preemption leaking into it.
    - PROACTIVE_START first: phase=PHASE1; USER_INPUT sets _preempted=True,
      owner=USER, and sticky preemption.
    Different final states are expected; verify each is internally consistent.
    """
    # Case A: USER_INPUT first.
    sm_a = _sm()
    await sm_a.fire(SessionEvent.USER_INPUT, sid="u")
    await sm_a.fire(SessionEvent.PROACTIVE_START)
    assert sm_a.phase is ProactivePhase.PHASE1
    assert sm_a.owner is TurnOwner.PROACTIVE
    assert sm_a._preempted is False

    # Case B: PROACTIVE_START first.
    sm_b = _sm()
    await sm_b.fire(SessionEvent.PROACTIVE_START)
    await sm_b.fire(SessionEvent.USER_INPUT, sid="u")
    assert sm_b.phase is ProactivePhase.PHASE1  # Phase remains unchanged until DONE clears it.
    assert sm_b.owner is TurnOwner.USER
    assert sm_b._preempted is True


async def test_try_start_proactive_atomic_only_one_winner():
    """Concurrent try_start_proactive grants ownership to one caller; all others return False.

    Atomic checking/reservation is essential: splitting can_start_proactive and
    fire(PROACTIVE_START) lets both requests pass IDLE checks and enter
    PHASE1, overwriting each other's claim/commit ownership.
    """
    sm = _sm()

    results = await asyncio.gather(
        sm.try_start_proactive(),
        sm.try_start_proactive(),
        sm.try_start_proactive(),
    )
    assert results.count(True) == 1
    assert results.count(False) == 2
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.owner is TurnOwner.PROACTIVE


async def test_try_start_proactive_refuses_when_session_is_responding():
    """While session._is_responding=True, try_start_proactive returns False despite IDLE,
    matching can_start_proactive semantics.
    """
    sm = _sm()

    class _Resp:
        _is_responding = True

    ok = await sm.try_start_proactive(session=_Resp())
    assert ok is False
    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.NONE


async def test_try_start_proactive_dispatches_subscribers():
    """Winning try_start_proactive dispatches PROACTIVE_START subscribers,
    matching direct fire(PROACTIVE_START).
    """
    sm = _sm()
    hits: list[SessionEvent] = []

    def cb(event, payload):
        hits.append(event)

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    ok = await sm.try_start_proactive()
    assert ok is True
    await asyncio.sleep(0)
    assert hits == [SessionEvent.PROACTIVE_START]


async def test_try_start_proactive_false_does_not_dispatch():
    """Losing second try_start_proactive must not dispatch PROACTIVE_START subscribers,
    falsely implying two active proactive rounds.
    """
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)  # Reserve first.
    hits: list[SessionEvent] = []

    def cb(event, payload):
        hits.append(event)

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    ok = await sm.try_start_proactive()
    assert ok is False
    await asyncio.sleep(0)
    assert hits == []


async def test_swallow_subscriber_cancelled_error():
    """Canceled async subscribers' done callbacks consume CancelledError,
    preventing repeated Task-exception-was-never-retrieved warnings.
    """
    from main_logic.session_state import _swallow_subscriber_exc

    async def never_returns():
        await asyncio.sleep(10)

    task = asyncio.create_task(never_returns())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # Deliberately swallow: drive the task to done for _swallow_subscriber_exc below;
        # CancelledError itself is the expected test outcome.
        pass
    _swallow_subscriber_exc(task)


async def test_fire_is_serialized_by_write_lock():
    """write_lock serializes concurrent fire calls, preserving consistent internal state."""
    sm = _sm()

    async def burst():
        await sm.fire(SessionEvent.PROACTIVE_START)
        await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="s")
        await sm.fire(SessionEvent.PROACTIVE_PHASE2)
        await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
        await sm.fire(SessionEvent.PROACTIVE_DONE)

    await asyncio.gather(burst(), burst(), burst())
    # Three complete lifecycles necessarily end IDLE.
    assert sm.phase is ProactivePhase.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# USER_ACTIVITY is silent, without sid/owner rotation.
# ─────────────────────────────────────────────────────────────────────────────

async def test_user_activity_updates_timestamp_without_owner_flip():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    before_activity = sm.last_user_activity
    await sm.fire(SessionEvent.USER_ACTIVITY)
    assert sm.last_user_activity > before_activity
    # Owner unchanged.
    assert sm.owner is TurnOwner.PROACTIVE
    # Sticky unchanged; USER_ACTIVITY does not mean preemption.
    assert sm._preempted is False


# ─────────────────────────────────────────────────────────────────────────────
# snapshot
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# reset() teardown prevents cross-session state leakage.
# ─────────────────────────────────────────────────────────────────────────────

async def test_reset_clears_state_after_proactive_done():
    """After PROACTIVE_DONE, reset clears all per-session fields."""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_x")
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    await sm.fire(SessionEvent.PROACTIVE_DONE)

    # owner=USER after normal preemption, phase=IDLE.
    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.USER

    await sm.reset()

    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.NONE
    assert sm.proactive_sid is None
    assert sm.user_sid is None
    assert sm._preempted is False
    assert sm.can_start_proactive() is True


async def test_reset_is_noop_during_active_proactive_phase():
    """reset must no-op during PROACTIVE_ACTIVE: prepare_proactive_delivery's auto-start
    may indirectly invoke it through start_session -> end_session -> _init_renew_status.
    Preserve active phase/preemption state or later phase1/phase2
    checks lose preemption visibility.
    """
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="live_sid")
    await sm.fire(SessionEvent.USER_INPUT, sid="preempt_sid")
    assert sm._preempted is True

    await sm.reset()

    # Active-phase reset has no effect on phase/_preempted/sids.
    assert sm.phase is ProactivePhase.PHASE1
    assert sm._preempted is True
    assert sm.proactive_sid == "live_sid"
    assert sm.user_sid == "preempt_sid"


async def test_reset_force_clears_state_even_during_active_proactive():
    """reset(force=True) is real WS-disconnect/end_session teardown, forcing cleanup
    even during stuck PHASE1/PHASE2, or phase/_preempted leaks into later
    sessions and permanently blocks IDLE admission.
    """
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="live_sid")
    await sm.fire(SessionEvent.USER_INPUT, sid="preempt_sid")
    assert sm.phase is ProactivePhase.PHASE1
    assert sm._preempted is True

    await sm.reset(force=True)

    # force=True bypasses active-phase protection and clears all fields.
    assert sm.phase is ProactivePhase.IDLE
    assert sm._preempted is False
    assert sm.proactive_sid is None
    assert sm.user_sid is None
    assert sm.owner is TurnOwner.NONE
    assert sm.can_start_proactive() is True


async def test_reset_preserves_subscribers():
    """reset teardown must preserve application-registered subscribers."""
    sm = _sm()
    hits: list[SessionEvent] = []

    def cb(event, payload):
        hits.append(event)

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.reset()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await asyncio.sleep(0)
    # Both START calls dispatched subscribers.
    assert hits == [SessionEvent.PROACTIVE_START, SessionEvent.PROACTIVE_START]


async def test_reset_preserves_last_user_activity():
    """reset must retain last_user_activity for cross-session tracking."""
    sm = _sm()
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    activity = sm.last_user_activity
    assert activity > 0
    await sm.reset()
    assert sm.last_user_activity == activity


async def test_snapshot_fields():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_snap")
    snap = sm.snapshot()
    assert snap["lanlan_name"] == "Test"
    assert snap["owner"] == TurnOwner.PROACTIVE.value
    assert snap["phase"] == ProactivePhase.PHASE1.value
    assert snap["proactive_sid"] == "sid_snap"
    assert snap["preempted"] is False
@pytest.mark.asyncio
@pytest.mark.parametrize('event', [
    SessionEvent.PROACTIVE_CLAIM, SessionEvent.PROACTIVE_PHASE2,
    SessionEvent.PROACTIVE_COMMITTING, SessionEvent.PROACTIVE_DONE,
])
async def test_retired_round_cannot_transition_replacement(event):
    sm = SessionStateMachine('test')
    assert await sm.try_start_proactive(round_id='old')
    await sm.reset(force=True)
    assert sm.proactive_round_id is None
    assert await sm.try_start_proactive(round_id='new')
    await sm.fire(event, round_id='old', sid='old-sid')
    assert sm.proactive_round_id == 'new'
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.proactive_sid is None


@pytest.mark.asyncio
async def test_done_ownership_is_rechecked_after_waiting_for_state_lock():
    from types import SimpleNamespace
    from main_logic.proactive_chat.delivery import ProactiveLifecycle

    sm = SessionStateMachine('test')
    assert await sm.try_start_proactive(round_id='old')
    manager = SimpleNamespace(state=sm, _proactive_screenshot_request_id='old')
    lifecycle = ProactiveLifecycle(manager, SessionEvent.PROACTIVE_DONE, 'test', screenshot_request_id='old')

    async def replacement():
        assert await sm.try_start_proactive(round_id='new')
        manager._proactive_screenshot_request_id = 'new'

    async with sm._write_lock:
        reset = asyncio.create_task(sm.reset(force=True))
        await asyncio.sleep(0)
        start = asyncio.create_task(replacement())
        await asyncio.sleep(0)
        finish = asyncio.create_task(lifecycle.safe_done())
        await asyncio.sleep(0)
    await asyncio.gather(reset, start, finish)
    lifecycle.clear_screenshot_request()
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.proactive_round_id == 'new'
    assert manager._proactive_screenshot_request_id == 'new'
