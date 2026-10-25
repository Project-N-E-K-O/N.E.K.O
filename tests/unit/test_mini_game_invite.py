"""Tests for mini-game invitation shortcut channels.

Three contract layers:
1. _mini_game_invite_in_cooldown always locks pending invitations;
   after response, both 24 hours and ten chats must pass.
2. _mini_game_invite_advance_response marks pending invitations responded when last-user-message
   timestamps exceed delivered_at, retaining pending without activity_snapshot.
3. _maybe_deliver_mini_game_invite checks eligibility in order: DISABLED,
   absent activity_snapshot, restricted_screen_only, away, cooldown, then dice;
   success runs prepare -> feed_tts -> finish and writes _proactive_chat_history.
"""
from __future__ import annotations

import os
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import main_logic.proactive_chat.mini_game_invite as sr  # noqa: E402
import main_logic.proactive_chat.state as sr_history  # noqa: E402
import main_logic.proactive_chat.contracts as sr_parsing  # noqa: E402
from tests.fake_clock import patch_module_clock  # noqa: E402

LANLAN = "test_lanlan"
MASTER = "小明"


def _make_snapshot(
    state="casual_browsing",
    propensity="open",
    seconds_since_user_msg=None,
    unfinished_thread=None,
):
    """Create a duck-typed ActivitySnapshot substitute using only state,
    propensity, seconds_since_user_msg, and unfinished_thread."""
    return types.SimpleNamespace(
        state=state,
        propensity=propensity,
        seconds_since_user_msg=seconds_since_user_msg,
        unfinished_thread=unfinished_thread,
    )


def _make_mgr(
    *,
    prepare_ok=True,
    feed_ok=True,
    finish_ok=True,
    sid="sid-test",
):
    mgr = MagicMock()
    mgr.prepare_proactive_delivery = AsyncMock(return_value=prepare_ok)
    mgr.finish_proactive_delivery = AsyncMock(return_value=finish_ok)
    mgr.feed_tts_chunk = AsyncMock(return_value=feed_ok)
    mgr.handle_new_message = AsyncMock()
    mgr.current_speech_id = sid
    mgr.last_user_engagement_time = 100.0
    mgr.state = MagicMock()
    mgr.state.fire = AsyncMock()
    mgr.state.is_proactive_preempted.return_value = False
    # The real PROACTIVE_CLAIM records the claimed SID on the state machine;
    # cleanup ownership checks compare against it.
    mgr.state.proactive_sid = sid
    return mgr


@pytest.fixture(autouse=True)
def _clear_mini_game_state():
    """Clear module-level state before and after every test."""
    sr._mini_game_invite_state.clear()
    sr_history._proactive_chat_history.clear()
    sr_history._proactive_chat_totals.clear()
    sr_history._invite_ever_delivered.clear()
    yield
    sr._mini_game_invite_state.clear()
    sr_history._proactive_chat_history.clear()
    sr_history._proactive_chat_totals.clear()
    sr_history._invite_ever_delivered.clear()


@pytest.fixture(autouse=True)
def _force_invite_enabled_default(monkeypatch):
    """Force MINI_GAME_INVITE_ENABLED=True and reset debug game-type overrides by default.
    flag=None。

    - ENABLED=True: most tests assume invitations enabled before checking specific
      gates. A future default False, e.g. rollout, must not silently make
      every delivery/gate test pass via the ENABLED shortcut,
      concealing actual gate regressions.
    - FORCE_GAME_TYPE=None: local manual testing often sets MINI_GAME_INVITE_FORCE_
      GAME_TYPE='soccer', intentionally a dev override. Leaving it set shortcuts
      gates and falsely fails cooldown/dice-miss/propensity tests.
      Autouse reset to None keeps gate paths deterministic.

    Establish the enabled-channel-and-working-gate precondition; disabled-path tests
    such as test_maybe_deliver_returns_none_when_disabled or force-game tests
    explicitly reset False/'soccer' themselves."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_ENABLED', True)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_FORCE_GAME_TYPE', None)


@pytest.fixture(autouse=True)
def _stub_persistent_counter(monkeypatch):
    """Unit tests do not initialize shared_state.config_manager, so real _proactive_chat_totals_path
    raises RuntimeError. Replace load/increment/mark-ever-delivered with in-memory
    versions; assertions can directly set/read sr_history._proactive_chat_totals[lanlan]
    and sr_history._invite_ever_delivered[lanlan]."""  # noqa: DOCSTRING_CJK
    async def _noop_load():
        sr_history._proactive_chat_totals_loaded = True

    async def _bump_only(lanlan_name: str) -> int:
        n = sr_history._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr_history._proactive_chat_totals[lanlan_name] = n
        return n

    async def _mark_only(lanlan_name: str) -> None:
        sr_history._invite_ever_delivered[lanlan_name] = True

    async def _record_delivery_only(lanlan_name: str) -> int:
        # Simulate atomic persistence: counter increment plus ever_delivered in one state update.
        n = sr_history._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr_history._proactive_chat_totals[lanlan_name] = n
        sr_history._invite_ever_delivered[lanlan_name] = True
        return n

    # After package splitting, proactive_history owns persistence; mini_game_invite retains
    # from-import snapshots. Patch both consumer snapshots and owner-internal calls.
    monkeypatch.setattr(sr, '_ensure_proactive_chat_totals_loaded', _noop_load)
    monkeypatch.setattr(sr_history, '_ensure_proactive_chat_totals_loaded', _noop_load)
    monkeypatch.setattr(sr_history, '_increment_proactive_chat_total', _bump_only)
    monkeypatch.setattr(sr_history, '_mark_invite_ever_delivered', _mark_only)
    monkeypatch.setattr(sr, '_record_invite_delivery_persistent', _record_delivery_only)
    monkeypatch.setattr(sr_history, '_record_invite_delivery_persistent', _record_delivery_only)
    sr_history._proactive_chat_totals_loaded = False  # Force each test through _noop_load once.


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_in_cooldown
# ─────────────────────────────────────────────────────────────────────────────

def test_in_cooldown_false_when_never_delivered():
    """Never invited: no cooldown."""
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_in_cooldown_true_when_pending():
    """Delivered but unanswered pending invitations lock cooldown, preventing duplicates."""
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_true_when_responded_within_24h_and_under_10_chats():
    """Responded but neither time nor ten chats reached: cooldown."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 60
    state['responded_at'] = time.time() - 60
    state['chats_since_response'] = 5
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_game_specific_cooldown_does_not_cross_block_other_game():
    """Declining soccer should not block a badminton invite cooldown."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 60
    state['responded_at'] = time.time() - 60
    state['chats_since_response'] = 5
    state['last_game_type'] = 'soccer'

    assert sr._mini_game_invite_in_cooldown(LANLAN, 'soccer') is True
    assert sr._mini_game_invite_in_cooldown(LANLAN, 'badminton') is False


def test_later_suppression_cross_blocks_other_games():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = None
    state['responded_at'] = None
    state['last_game_type'] = 'soccer'
    state['suppressed_until'] = time.time() + 60

    assert sr._mini_game_invite_in_cooldown(LANLAN, 'badminton') is True


def test_in_cooldown_true_when_only_time_elapsed_chats_short():
    """Time passed but fewer than ten chats: still cooldown, AND semantics.

    Use DECLINE offsets exceeding both accept(2h) and decline(5h) fallback thresholds.
    """
    state = sr._mini_game_invite_get_state(LANLAN)
    long_ago = time.time() - sr.MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS - 100
    state['delivered_at'] = long_ago
    state['responded_at'] = long_ago
    state['chats_since_response'] = 3
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_true_when_only_chats_done_time_short():
    """Ten chats reached but insufficient time: still cooldown, AND semantics."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 600
    state['responded_at'] = time.time() - 600
    state['chats_since_response'] = 99
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_false_when_both_thresholds_passed():
    """Both time and ten-chat thresholds passed: unlock and roll next time.

    Use DECLINE thresholds to exceed either accept/decline duration."""
    state = sr._mini_game_invite_get_state(LANLAN)
    long_ago = time.time() - sr.MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS - 100
    state['delivered_at'] = long_ago
    state['responded_at'] = long_ago
    state['chats_since_response'] = sr.MINI_GAME_INVITE_COOLDOWN_CHATS
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_in_cooldown_accept_uses_2h_threshold():
    """Accept with three hours elapsed and enough chats unlocks, using two-hour threshold."""
    state = sr._mini_game_invite_get_state(LANLAN)
    long_ago = time.time() - 3 * 3600
    state['delivered_at'] = long_ago
    state['responded_at'] = long_ago
    state['chats_since_response'] = sr.MINI_GAME_INVITE_COOLDOWN_CHATS
    state['last_response_choice'] = 'accept'
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_in_cooldown_decline_holds_past_accept_threshold():
    """Decline with three hours, above accept but below decline, and enough chats remains locked.

    Guard longer refusal cooldowns against repeated short-term interruption."""
    state = sr._mini_game_invite_get_state(LANLAN)
    long_ago = time.time() - 3 * 3600
    state['delivered_at'] = long_ago
    state['responded_at'] = long_ago
    state['chats_since_response'] = sr.MINI_GAME_INVITE_COOLDOWN_CHATS
    state['last_response_choice'] = 'decline'
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_decline_releases_past_decline_threshold():
    """Decline with six hours, above five-hour threshold, and enough chats unlocks."""
    state = sr._mini_game_invite_get_state(LANLAN)
    long_ago = time.time() - 6 * 3600
    state['delivered_at'] = long_ago
    state['responded_at'] = long_ago
    state['chats_since_response'] = sr.MINI_GAME_INVITE_COOLDOWN_CHATS
    state['last_response_choice'] = 'decline'
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_in_cooldown_legacy_state_falls_back_to_accept_threshold():
    """Missing/None last_response_choice uses accept's two-hour threshold, never arbitrarily five hours.

    Preserve legacy states having responded_at but no last_response_choice:
    rollout must not add three hours after users already waited two through decline fallback.
    Changing fallback to decline must fail this test."""
    state = sr._mini_game_invite_get_state(LANLAN)
    long_ago = time.time() - 3 * 3600
    state['delivered_at'] = long_ago
    state['responded_at'] = long_ago
    state['chats_since_response'] = sr.MINI_GAME_INVITE_COOLDOWN_CHATS
    state['last_response_choice'] = None
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_advance_response
# ─────────────────────────────────────────────────────────────────────────────

def test_advance_response_noop_when_never_delivered():
    """Never delivered: advance is noop."""
    sr._mini_game_invite_advance_response(LANLAN, time.time() - 1.0)
    assert LANLAN not in sr._mini_game_invite_state


def test_advance_response_noop_when_already_responded():
    """Already responded: further advance does not rewrite timestamps."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 1000
    original_responded = time.time() - 500
    state['responded_at'] = original_responded
    state['chats_since_response'] = 3

    sr._mini_game_invite_advance_response(LANLAN, time.time() - 10.0)
    assert state['responded_at'] == original_responded
    assert state['chats_since_response'] == 3


def test_advance_response_noop_when_last_user_msg_at_none():
    """Unavailable last_user_msg_at, privacy mode or empty tracker, preserves pending."""
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
    sr._mini_game_invite_advance_response(LANLAN, None)
    assert sr._mini_game_invite_state[LANLAN]['responded_at'] is None


def test_advance_response_dismisses_pending_invite_with_short_suppression(monkeypatch):
    """Any ordinary post-delivery user message without explicit choice/keywords
    silently dismisses through advance with five-minute suppression, never long cooldown.

    Old PR #1141 treated speech as implicit response, setting responded_at plus
    one-hour locks. CodeRabbit Major found ordinary text before button clicks made endpoints
    return expired while silently entering long cooldown, violating D2. Match later's
    reset/short-suppression semantics, retaining ever_delivered but no long lock."""
    fixed_now = 1_700_000_000.0
    # Patch mini_game_invite's own time.time: advance -> _apply_mini_game_invite_choice ->
    # suppressed_until reads its module clock.
    patch_module_clock(monkeypatch, sr, time=lambda: fixed_now)

    delivered_at = fixed_now - 30
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = delivered_at
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = 'pre-advance-sess'

    # User spoke five seconds ago, after delivered_at.
    sr._mini_game_invite_advance_response(LANLAN, fixed_now - 5.0)
    # Do not set responded_at again, avoiding long cooldown.
    assert state['responded_at'] is None, (
        "advance_response 不应再 set responded_at——长冷却只该由显式 accept/decline 触发"
    )
    # Enter dismissed/reset: clear delivered_at and pending session.
    assert state['delivered_at'] is None
    assert state['pending_session_id'] is None
    # Five-minute suppression: suppressed_until=fixed_now+5min.
    assert state['suppressed_until'] == fixed_now + sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS


def test_advance_response_does_not_trigger_long_cooldown(monkeypatch):
    """Key CodeRabbit Major regression:
    1. Deliver invitation.
    2. Ordinary nonkeyword user message runs advance.
    3. User clicks accept afterward.
    Old behavior: advance already marks responded; endpoint sees responded_at
    and returns expired after wrongly starting one-hour cooldown.
    New behavior: advance dismisses with five-minute suppression only; endpoint
    still returns expired without pending because the button is late, but proactive dice
    may invite again after five minutes rather than a long lock."""
    fixed_now = 1_700_005_000.0
    # advance / _apply_mini_game_invite_choice / _mini_game_invite_in_cooldown
    # All three assertions read mini_game_invite's own time.time.
    patch_module_clock(monkeypatch, sr, time=lambda: fixed_now)

    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = fixed_now - 60
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = 'sess-x'
    sr_history._invite_ever_delivered[LANLAN] = True

    # User sent ordinary text 30 seconds ago, observed by advance.
    sr._mini_game_invite_advance_response(LANLAN, fixed_now - 30.0)

    # Cooldown reflects five-minute suppression only, not a one-hour long lock.
    assert state['responded_at'] is None
    assert state['chats_since_response'] == 0  # Not the responded state counting ten chats.
    assert state['suppressed_until'] is not None
    suppress_window = state['suppressed_until'] - fixed_now
    assert (
        sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS - 1
        <= suppress_window
        <= sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS + 1
    ), f"suppressed_until 应是 5min 短抑制，实际 {suppress_window}s"
    # Cooldown naturally ends after five minutes.
    patch_module_clock(
        monkeypatch, sr,
        time=lambda: fixed_now + sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS + 1,
    )
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_advance_response_does_not_flip_when_last_user_msg_predates_invite():
    """Last user message predates delivery: keep pending."""
    now = time.time()
    delivered_at = now - 5
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = delivered_at
    state['responded_at'] = None

    # User spoke 100 seconds ago, well before delivery five seconds ago.
    sr._mini_game_invite_advance_response(LANLAN, now - 100.0)
    assert state['responded_at'] is None


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_count_post_response_chat
# ─────────────────────────────────────────────────────────────────────────────

def test_count_noop_when_never_delivered():
    sr._mini_game_invite_count_post_response_chat(LANLAN)
    assert LANLAN not in sr._mini_game_invite_state


def test_count_noop_during_pending():
    """Pending invitations never advance counters; invitations cannot themselves consume ten chats."""
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
    sr._mini_game_invite_count_post_response_chat(LANLAN)
    sr._mini_game_invite_count_post_response_chat(LANLAN)
    assert sr._mini_game_invite_state[LANLAN]['chats_since_response'] == 0


def test_count_increments_after_responded():
    """After response, every successful proactive delivery increments regardless of channel."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 1000
    state['responded_at'] = time.time() - 500
    state['chats_since_response'] = 0

    for _ in range(7):
        sr._mini_game_invite_count_post_response_chat(LANLAN)
    assert state['chats_since_response'] == 7


# ─────────────────────────────────────────────────────────────────────────────
# _maybe_deliver_mini_game_invite: eligibility and delivery.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_ENABLED', False)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_snapshot_none():
    """Privacy mode/unavailable trackers conservatively suppress invites, lacking work-state knowledge."""
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=None,
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_restricted_screen_only(monkeypatch):
    """Focused work/noncasual gaming suppresses invitations."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(
            state='focused_work', propensity='restricted_screen_only',
        ),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_away(monkeypatch):
    """User away: nobody can answer invitations."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(state='away', propensity='open'),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_unfinished_thread_pending(monkeypatch):
    """Unanswered AI questions give unfinished threads priority over game invitations,
    matching skip_probability/restricted_screen_only's unfinished_thread precedence;
    promised follow-ups never yield to external sources/invitations."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    fake_thread = types.SimpleNamespace(
        text='你今天准备几点出发?', age_seconds=60.0,
        follow_up_count=0, max_follow_ups=2,
    )
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(unfinished_thread=fake_thread),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_in_cooldown(monkeypatch):
    """Pending always suppresses dice rolls."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_dice_misses(monkeypatch):
    """Failed probability rolls produce no delivery."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_user_toggle_disabled(monkeypatch):
    """Frontend CHAT_MODE_CONFIG disabling mini-game invitations means
    user_toggle_enabled=False prevents delivery even with all gates passed
    and guaranteed dice, the core independent-source-toggle contract."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
        user_toggle_enabled=False,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_user_toggle_default_true_keeps_bc(monkeypatch):
    """Old clients omit mini_game_invite_enabled; caller default True
    retains original eligibility behavior. Preserve this default."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(sid='sid-bc'),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
        # Intentionally omit user_toggle_enabled to test its default.
    )
    assert out is not None
    assert out["action"] == "chat"
    assert out["reason_code"] == sr.PROACTIVE_REASON_CHAT_DELIVERED
    assert out["stage"] == sr_parsing.PROACTIVE_STAGE_DELIVERY


@pytest.mark.asyncio
async def test_force_game_type_overrides_snapshot_and_cooldown_gates(monkeypatch):
    """MINI_GAME_INVITE_FORCE_GAME_TYPE='soccer' bypasses snapshot/cooldown/dice
    but respects user toggles, as tested by
    test_force_game_type_respects_user_toggle. Only global MINI_GAME_INVITE_ENABLED=False
    or user_toggle_enabled=False blocks it."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_FORCE_GAME_TYPE', 'soccer')
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    sr._mini_game_invite_record_delivered(LANLAN, "stale-session")
    mgr = _make_mgr(sid='sid-forced')
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=None,
        invite_lang='zh', master_name=MASTER,
        user_toggle_enabled=True,
    )
    assert out is not None
    assert out["action"] == "chat"
    assert out["reason_code"] == sr.PROACTIVE_REASON_CHAT_DELIVERED
    assert out["stage"] == sr_parsing.PROACTIVE_STAGE_DELIVERY
    assert out.get("game_type") == 'soccer'


@pytest.mark.asyncio
async def test_force_game_type_respects_user_toggle(monkeypatch):
    """Developer flags must not override users explicitly disabling mini-game sources.
    user_toggle_enabled=False still returns None under force flags,
    equal in priority to the global kill switch."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_FORCE_GAME_TYPE', 'soccer')
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=None,
        invite_lang='zh', master_name=MASTER,
        user_toggle_enabled=False,
    )
    assert out is None


@pytest.mark.asyncio
async def test_force_game_type_invalid_value_warns_and_skips(monkeypatch):
    """Unknown forced keys absent from MINI_GAME_INVITE_LINES_BY_GAME warn and return None,
    never raising and breaking proactive pipelines on configuration issues."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_FORCE_GAME_TYPE', 'definitely_not_a_game')
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
        user_toggle_enabled=True,
    )
    assert out is None


@pytest.mark.asyncio
async def test_force_game_type_still_respects_global_kill_switch(monkeypatch):
    """Global MINI_GAME_INVITE_ENABLED=False beats every enabled debug flag,
    preserving the ultimate kill-switch contract."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_ENABLED', False)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_FORCE_GAME_TYPE', 'soccer')
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
        user_toggle_enabled=True,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_chat_when_eligible(monkeypatch):
    """All eligibility gates plus guaranteed dice run prepare/feed_tts/finish,
    enter pending, and write _proactive_chat_history."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(sid='sid-eligible')
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "chat"
    assert out["reason_code"] == sr.PROACTIVE_REASON_CHAT_DELIVERED
    assert out["stage"] == sr_parsing.PROACTIVE_STAGE_DELIVERY
    assert out["channel"] == "mini_game"
    assert out["turn_id"] == "sid-eligible"

    mgr.prepare_proactive_delivery.assert_awaited_once()
    mgr.finish_proactive_delivery.assert_awaited_once()
    # Both feed_tts and finish carry current speech_id.
    feed_call = mgr.feed_tts_chunk.await_args
    assert feed_call.kwargs.get('expected_speech_id') == 'sid-eligible'
    assert feed_call.kwargs.get('expected_user_engagement_time') == 100.0
    finish_call = mgr.finish_proactive_delivery.await_args
    assert finish_call.kwargs.get('expected_speech_id') == 'sid-eligible'
    assert finish_call.kwargs.get('expected_user_engagement_time') == 100.0

    # Enter pending.
    state = sr._mini_game_invite_state[LANLAN]
    assert state['delivered_at'] is not None
    assert state['responded_at'] is None
    assert state['chats_since_response'] == 0

    # _proactive_chat_history also gains channel='mini_game'.
    history = sr_history._proactive_chat_history[LANLAN]
    assert len(history) == 1
    _, message, channel = history[0]
    assert MASTER in message
    assert channel == 'mini_game'


@pytest.mark.asyncio
async def test_maybe_deliver_aborts_when_user_engages_during_phase2_transition(
    monkeypatch,
):
    """The Phase 2 state-lock wait cannot adopt a newer engagement baseline."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(sid='sid-phase2-race')

    async def _fire_after_engagement(_event):
        mgr.last_user_engagement_time = 101.0

    mgr.state.fire.side_effect = _fire_after_engagement

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN,
        mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh',
        master_name=MASTER,
    )

    assert out is not None
    assert out["action"] == "pass"
    assert out["reason_code"] == sr.PROACTIVE_REASON_DELIVERY_PREEMPTED
    mgr.feed_tts_chunk.assert_not_awaited()
    mgr.finish_proactive_delivery.assert_not_awaited()
    mgr.handle_new_message.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_maybe_deliver_pass_when_prepare_refuses(monkeypatch):
    """Rejected prepare, e.g. recent speech/no websocket, returns pass without state writes."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(prepare_ok=False)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "pass"
    assert out["reason_code"] == sr.PROACTIVE_REASON_PASS_DELIVERY_BUSY
    assert out["stage"] == sr_parsing.PROACTIVE_STAGE_DELIVERY
    mgr.finish_proactive_delivery.assert_not_awaited()
    assert LANLAN not in sr._mini_game_invite_state


@pytest.mark.asyncio
async def test_maybe_deliver_pass_when_user_takes_over_before_finish(monkeypatch):
    """False finish_proactive_delivery from user takeover
    writes neither history nor cooldown, preventing erroneous later suppression."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(finish_ok=False)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "pass"
    assert out["reason_code"] == sr.PROACTIVE_REASON_DELIVERY_PREEMPTED
    assert out["stage"] == sr_parsing.PROACTIVE_STAGE_DELIVERY
    assert LANLAN not in sr._mini_game_invite_state
    assert LANLAN not in sr_history._proactive_chat_history


@pytest.mark.asyncio
async def test_maybe_deliver_pass_when_engagement_rejects_tts(monkeypatch):
    """Direct invites stop before commit when the guarded TTS feed rejects."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(feed_ok=False, sid='sid-engagement')

    async def reject_after_user_engagement(*_args, **_kwargs):
        mgr.last_user_engagement_time = 101.0
        return False

    mgr.feed_tts_chunk.side_effect = reject_after_user_engagement
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN,
        mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh',
        master_name=MASTER,
    )

    assert out is not None
    assert out["reason_code"] == sr.PROACTIVE_REASON_DELIVERY_PREEMPTED
    mgr.finish_proactive_delivery.assert_not_awaited()
    mgr.handle_new_message.assert_awaited_once_with()
    assert LANLAN not in sr._mini_game_invite_state


@pytest.mark.asyncio
async def test_maybe_deliver_commits_text_when_local_tts_enqueue_fails(monkeypatch):
    """A local TTS failure must not discard a direct invite's UI delivery."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(feed_ok=False, sid='sid-tts-failure')

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN,
        mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh',
        master_name=MASTER,
    )

    assert out is not None
    assert out["reason_code"] == sr.PROACTIVE_REASON_CHAT_DELIVERED
    mgr.finish_proactive_delivery.assert_awaited_once()
    mgr.handle_new_message.assert_not_awaited()
    assert LANLAN in sr._mini_game_invite_state


@pytest.mark.asyncio
async def test_maybe_deliver_does_not_clear_new_avatar_turn(monkeypatch):
    """A SID replacement owns its TTS; rejected invite cleanup must not touch it."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(finish_ok=False, sid='sid-invite')

    async def finish_after_avatar_response(*_args, **_kwargs):
        mgr.current_speech_id = 'sid-avatar'
        return False

    mgr.finish_proactive_delivery.side_effect = finish_after_avatar_response
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN,
        mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh',
        master_name=MASTER,
    )

    assert out is not None
    assert out["reason_code"] == sr.PROACTIVE_REASON_DELIVERY_PREEMPTED
    mgr.handle_new_message.assert_not_awaited()
    assert mgr.current_speech_id == 'sid-avatar'


@pytest.mark.asyncio
async def test_maybe_deliver_uses_localized_template(monkeypatch):
    """English invite_lang selects English templates and actual master_name expansion."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr()
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='en', master_name='Alice',
    )
    assert out is not None
    history = sr_history._proactive_chat_history[LANLAN]
    _, message, _ = history[0]
    assert 'Alice' in message
    from config.prompts.prompts_proactive import MINI_GAME_INVITE_LINES_BY_GAME

    expected = MINI_GAME_INVITE_LINES_BY_GAME[out["game_type"]]["en"].format(master_name="Alice")
    assert message == expected


# ─────────────────────────────────────────────────────────────────────────────
# i18n coverage contract.
# ─────────────────────────────────────────────────────────────────────────────

def test_invite_lines_cover_all_native_locales_per_game():
    """Every MINI_GAME_INVITE_AVAILABLE_GAMES entry needs format-ready templates
    for five native locales in MINI_GAME_INVITE_LINES_BY_GAME.
    This extension gate prevents new games missing translations, where _loc
    falls back to Chinese for other-language users."""
    from config import MINI_GAME_INVITE_AVAILABLE_GAMES
    from config.prompts.prompts_proactive import MINI_GAME_INVITE_LINES_BY_GAME
    assert MINI_GAME_INVITE_AVAILABLE_GAMES, "AVAILABLE_GAMES 不能空"
    for game in MINI_GAME_INVITE_AVAILABLE_GAMES:
        assert game in MINI_GAME_INVITE_LINES_BY_GAME, \
            f"AVAILABLE_GAMES 列了 {game!r} 但 LINES 里没；多游戏接口契约破了"
        lines = MINI_GAME_INVITE_LINES_BY_GAME[game]
        for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
            line = lines.get(lang)
            assert line, f"{game!r} 缺 {lang!r} 模板"
            assert '{master_name}' in line, f"{game!r}/{lang} 缺 master_name 占位符"
            rendered = line.format(master_name='测试')
            assert '测试' in rendered
            assert 5 <= len(line) <= 200, f"{game!r}/{lang} 模板长度异常: {len(line)}"


def test_format_recent_proactive_chats_renders_mini_game_channel():
    """Runtime rendering: mini_game invitations recorded by _record_proactive_chat
    enter _proactive_chat_history; next proactive prompts render
    them through _format_recent_proactive_chats with readable
    time/channel labels without crashes in five native locales.

    Labels use RECENT_PROACTIVE_CHANNEL_LABELS, not PROACTIVE_SOURCE_LABELS,
    used only during Phase-1 web aggregation bypassed by earlier mini-game shortcuts.
    The dictionary currently translates vision/web only; music/meme/news/video/home/personal/
    window/mini_game use cl.get(ch,ch) raw-key fallback, so expect
    literal mini_game, matching existing music/meme behavior."""
    sample = "{master_name}, ".format(master_name=MASTER) + "要不要踢一会儿足球？"
    sr_history._proactive_chat_history[LANLAN] = __import__('collections').deque(
        [(time.time() - 30, sample, 'mini_game')], maxlen=10,
    )
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        rendered = sr_history._format_recent_proactive_chats(LANLAN, lang)
        assert rendered, f"{lang} 渲染输出空"
        assert sample in rendered, f"{lang} 渲染丢了消息正文"
        assert 'mini_game' in rendered, (
            f"{lang} 渲染丢了 channel 标记——"
            f"应至少用 raw-key fallback 暴露 mini_game"
        )


@pytest.mark.asyncio
async def test_invite_e2e_renders_in_recent_chats(monkeypatch):
    """End-to-end: immediately rerender _maybe_deliver_mini_game_invite output through
    _format_recent_proactive_chats, verifying the whole path/channel labels."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr()
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    rendered = sr_history._format_recent_proactive_chats(LANLAN, 'zh')
    assert MASTER in rendered
    assert 'mini_game' in rendered


# ─────────────────────────────────────────────────────────────────────────────
# Force-first path invites new users on their Nth proactive chat.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_first_triggers_when_new_user_at_threshold(monkeypatch):
    """New users with delivered_at=None and persisted total>=NEW_USER_FORCE_AT-1
    force invitations even at probability zero, the fixed-Nth-proactive
    invitation contract."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    # Default NEW_USER_FORCE_AT=4 triggers when total>=3.
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr_history._proactive_chat_totals[LANLAN] = 3  # Three successful ordinary proactive deliveries already occurred.

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "chat"
    assert out["force_first"] is True
    assert out["game_type"] in sr.MINI_GAME_INVITE_AVAILABLE_GAMES


@pytest.mark.asyncio
async def test_force_first_skipped_when_total_below_threshold(monkeypatch):
    """Below total threshold, force-first stays inactive and dice=0 returns None."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr_history._proactive_chat_totals[LANLAN] = 2  # One remaining.

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None  # Before fourth delivery plus dice=0: no invitation.


@pytest.mark.asyncio
async def test_force_first_skipped_when_ever_delivered_persistent_flag_set(monkeypatch):
    """Once persisted _invite_ever_delivered is True, force-first stops,
    returning to ordinary ten-percent dice. This defines new users independently
    of in-memory state.delivered_at."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr_history._invite_ever_delivered[LANLAN] = True  # Invitation previously delivered.
    sr_history._proactive_chat_totals[LANLAN] = 99

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None  # Existing user plus dice=0: no invitation.


@pytest.mark.asyncio
async def test_force_first_skipped_after_simulated_restart(monkeypatch):
    """Codex P1/CodeRabbit Major cross-restart regression:
    restart empties _mini_game_invite_state while loading persisted _proactive_chat_totals
    and _invite_ever_delivered. Previously invited users must not
    be forced again as new users."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    # Simulate restart: in-memory state empty, both persisted records retained.
    sr._mini_game_invite_state.clear()
    sr_history._proactive_chat_totals[LANLAN] = 99
    sr_history._invite_ever_delivered[LANLAN] = True  # Already delivered before restart.

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None, (
        "重启后已邀请过的用户被 force-first 重新邀请——"
        "force-first 检查必须基于持久化的 _invite_ever_delivered，"
        "不能基于 in-memory 的 state.delivered_at"
    )


@pytest.mark.asyncio
async def test_invite_marks_ever_delivered_persistent(monkeypatch):
    """Successful delivery persists _invite_ever_delivered=True,
    identifying previously invited users across restarts and preventing repeat force-first."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    assert not sr._was_invite_ever_delivered(LANLAN)

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert sr._was_invite_ever_delivered(LANLAN), (
        "投递成功但 ever_delivered 没置 True——下次 force-first 会重复触发"
    )


@pytest.mark.asyncio
async def test_invite_delivery_uses_atomic_persistence_helper(monkeypatch):
    """Invitation persistence must atomically use _record_invite_delivery_persistent under one lock,
    never separate _increment_proactive_chat_total and _mark_invite_ever_delivered
    awaits. Unlocking between them permits crashes with totals incremented but old
    ever_delivered, repeating force-first after restart; identified by CodeRabbit
    Major review."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)

    increment_calls: list[str] = []
    mark_calls: list[str] = []
    record_calls: list[str] = []

    async def _counting_increment(lanlan_name: str) -> int:
        increment_calls.append(lanlan_name)
        n = sr_history._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr_history._proactive_chat_totals[lanlan_name] = n
        return n

    async def _counting_mark(lanlan_name: str) -> None:
        mark_calls.append(lanlan_name)
        sr_history._invite_ever_delivered[lanlan_name] = True

    async def _counting_record(lanlan_name: str) -> int:
        record_calls.append(lanlan_name)
        n = sr_history._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr_history._proactive_chat_totals[lanlan_name] = n
        sr_history._invite_ever_delivered[lanlan_name] = True
        return n

    monkeypatch.setattr(sr_history, '_increment_proactive_chat_total', _counting_increment)
    monkeypatch.setattr(sr_history, '_mark_invite_ever_delivered', _counting_mark)
    monkeypatch.setattr(sr, '_record_invite_delivery_persistent', _counting_record)

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None

    assert record_calls == [LANLAN], (
        f"_record_invite_delivery_persistent should be called once, "
        f"got {record_calls}"
    )
    assert increment_calls == [], (
        f"_increment_proactive_chat_total should NOT be called from invite "
        f"delivery, got {increment_calls}——拆成两步的 racy pattern 被复活了"
    )
    assert mark_calls == [], (
        f"_mark_invite_ever_delivered should NOT be called from invite "
        f"delivery, got {mark_calls}——拆成两步的 racy pattern 被复活了"
    )


@pytest.mark.asyncio
async def test_force_first_still_respects_unfinished_thread(monkeypatch):
    """Force-first yields to unfinished_thread; after unanswered AI questions,
    even a new user's fixed fourth invitation cannot change topics."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr_history._proactive_chat_totals[LANLAN] = 3
    fake_thread = types.SimpleNamespace(
        text='你今天准备几点出发?', age_seconds=60.0,
        follow_up_count=0, max_follow_ups=2,
    )
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(unfinished_thread=fake_thread),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_force_first_still_respects_restricted_screen_only(monkeypatch):
    """Force-first also yields to restricted_screen_only during work/immersive
    gaming, where forced invitations would interrupt."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr_history._proactive_chat_totals[LANLAN] = 3
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(
            state='focused_work', propensity='restricted_screen_only',
        ),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


# ─────────────────────────────────────────────────────────────────────────────
# Multigame API contract C: share cooldown across game_type.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invite_outcome_includes_game_type(monkeypatch):
    """Successful outcomes include game_type so callers know which game frontend
    should open, including PR-B acceptance buttons. Also save state.last_game_type
    so next proactive requests in other processes identify previous invitations."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out['game_type'] in sr.MINI_GAME_INVITE_AVAILABLE_GAMES
    state = sr._mini_game_invite_state[LANLAN]
    assert state['last_game_type'] == out['game_type']


@pytest.mark.asyncio
async def test_invite_skipped_when_no_game_available(monkeypatch):
    """AVAILABLE_GAMES/LINES configuration mismatch silently suppresses delivery,
    never raising or sending empty text; defensive multigame-extension guard."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_AVAILABLE_GAMES', ('nonexistent_game',))
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None
    assert LANLAN not in sr._mini_game_invite_state


def test_cooldown_accept_is_2h_by_default():
    """Accept keeps the invite cooldown at 2 hours by default."""
    assert sr.MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS == 2 * 3600


def test_cooldown_decline_is_5h_by_default():
    """Decline keeps the invite cooldown at 5 hours by default."""
    assert sr.MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS == 5 * 3600


@pytest.mark.asyncio
async def test_declined_soccer_invite_still_allows_badminton_invite(monkeypatch):
    """A soccer cooldown should still allow a badminton invite for the character."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_AVAILABLE_GAMES', ('soccer', 'badminton'))
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 60
    state['responded_at'] = time.time() - 60
    state['chats_since_response'] = 0
    state['last_game_type'] = 'soccer'

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )

    assert out is not None
    assert out['game_type'] == 'badminton'


def test_new_user_force_at_is_4_by_default():
    """Spec: users never having played receive a fixed invitation on their fourth opening proactive chat.
    Pin defaults; future changes require explicit follow-up."""
    assert sr.MINI_GAME_INVITE_NEW_USER_FORCE_AT == 4


def test_later_suppress_seconds_is_5min_by_default():
    """Spec D2: later resets state and prevents rolls for five minutes."""
    assert sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS == 5 * 60


# ─────────────────────────────────────────────────────────────────────────────
# D2 short suppression for later.
# ─────────────────────────────────────────────────────────────────────────────

def test_in_cooldown_true_when_suppressed_until_in_future():
    """suppressed_until>now locks cooldown regardless of delivered_at,
    implementing D2's post-reset waiting window."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = None  # Reset occurred.
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['suppressed_until'] = time.time() + 60
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_false_when_suppressed_until_past():
    """suppressed_until<=now ends suppression; cooldown consults delivered_at.
    Reset delivered_at=None means unlocked and dice may roll next proactive turn."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = None
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['suppressed_until'] = time.time() - 1
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_record_delivered_clears_suppressed_until():
    """New invitations clear previous D2 suppression windows because delivering again
    implies the later-wait window already expired."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['suppressed_until'] = time.time() + 999
    sr._mini_game_invite_record_delivered(LANLAN, "new-session")
    assert state['suppressed_until'] is None


def test_record_delivered_sets_pending_session_id():
    """Refresh pending_session_id on new delivery, expiring old invitations;
    endpoints receiving old session IDs return expired."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['pending_session_id'] = 'old-session'
    sr._mini_game_invite_record_delivered(LANLAN, 'new-session')
    assert state['pending_session_id'] == 'new-session'


# ─────────────────────────────────────────────────────────────────────────────
# _apply_mini_game_invite_choice (state machine)
# ─────────────────────────────────────────────────────────────────────────────

def test_apply_choice_ignored_when_no_pending_invite():
    """Without pending invitations, choices return ignored without state mutation."""
    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='test')
    assert result['action'] == 'ignored'
    assert result.get('reason') == 'no_pending_invite'


def test_apply_choice_ignored_when_already_responded():
    """Repeated button clicks after response are ignored."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 60
    state['responded_at'] = time.time() - 30
    state['pending_session_id'] = 'sess'
    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='test')
    assert result['action'] == 'ignored'


def test_apply_choice_accept_returns_open_game_with_url():
    """Accept returns game_url with lanlan_name/session_id queries plus top-level
    session_id for frontend deduplication, marks responded, and starts
    one-hour-plus-ten-chat cooldown."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'sess-abc'
    state['last_game_type'] = 'soccer'
    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='button')
    assert result['action'] == 'open_game'
    assert result['game_type'] == 'soccer'
    assert result['game_url'].startswith('/soccer_demo?')
    assert 'lanlan_name=' + LANLAN in result['game_url']
    assert 'session_id=sess-abc' in result['game_url']
    # Require top-level session_id: core.py keyword-triggered mini_game_launch WebSocket payloads
    # use it as the same frontend dedup key as button endpoints,
    # preventing duplicate game windows (Codex P2).
    assert result['session_id'] == 'sess-abc'
    # Enter cooldown.
    assert state['responded_at'] is not None
    assert state['chats_since_response'] == 0


def test_apply_choice_accept_fallback_reports_launched_game_type(monkeypatch):
    """Report the fallback launch target while preserving cooldown for the invited game."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'sess-fallback'
    state['last_game_type'] = 'badminton'

    def _fake_launch_url(game_type: str, lanlan_name: str, session_id: str) -> str | None:
        if game_type == 'badminton':
            return None
        return f'/soccer_demo?lanlan_name={lanlan_name}&session_id={session_id}'

    monkeypatch.setattr(sr, '_mini_game_launch_url', _fake_launch_url)

    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='button')

    assert result['action'] == 'open_game'
    assert result['game_type'] == 'soccer'
    assert result['game_url'] == f'/soccer_demo?lanlan_name={LANLAN}&session_id=sess-fallback'
    assert state['last_game_type'] == 'badminton'
    assert sr._mini_game_invite_in_cooldown(LANLAN, 'badminton') is True
    assert sr._mini_game_invite_in_cooldown(LANLAN, 'soccer') is False


def test_apply_choice_decline_starts_cooldown_no_url():
    """Decline marks responded/start cooldown without opening games."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'sess'
    result = sr._apply_mini_game_invite_choice(LANLAN, 'decline', source='button')
    assert result['action'] == 'cooldown'
    assert 'game_url' not in result
    assert state['responded_at'] is not None


def test_apply_choice_later_resets_state_with_suppression():
    """Later D2 fully resets delivered_at etc. while setting suppressed_until=now+5min."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = 'sess'
    before = time.time()
    result = sr._apply_mini_game_invite_choice(LANLAN, 'later', source='button')
    assert result['action'] == 'suppress'
    assert state['delivered_at'] is None
    assert state['responded_at'] is None
    assert state['chats_since_response'] == 0
    assert state['pending_session_id'] is None
    assert state['suppressed_until'] is not None
    suppress_window = state['suppressed_until'] - before
    assert (
        sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS - 5
        <= suppress_window
        <= sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS + 5
    )


# ─────────────────────────────────────────────────────────────────────────────
# Keyword matching, E2 text fallback.
# ─────────────────────────────────────────────────────────────────────────────

def test_keyword_matcher_returns_none_for_empty_text():
    assert sr._match_mini_game_invite_keyword('') is None
    assert sr._match_mini_game_invite_keyword('   ') is None
    assert sr._match_mini_game_invite_keyword(None) is None


def test_keyword_matcher_accept_zh():
    assert sr._match_mini_game_invite_keyword('好啊') == 'accept'
    assert sr._match_mini_game_invite_keyword('来吧') == 'accept'
    assert sr._match_mini_game_invite_keyword('一起玩吧') == 'accept'


def test_keyword_matcher_no_false_positive_on_chong_substring():
    """Codex P2: single-character 冲 removed from Chinese accept;
    ordinary 我去冲个澡 / 冲咖啡 must not match substring-based
    CJK acceptance without word boundaries."""  # noqa: DOCSTRING_CJK
    assert sr._match_mini_game_invite_keyword('我去冲个澡') is None
    assert sr._match_mini_game_invite_keyword('在冲咖啡') is None
    # Complete 来吧 still matches accept.
    assert sr._match_mini_game_invite_keyword('来吧') == 'accept'


def test_keyword_matcher_no_false_positive_on_keyi_negation():
    """Codex P2: 可以 removed from accept because 不可以 contains it and
    priority cannot help unless decline includes 不可以. Add that decline
    phrase too against future accidental reintroduction of 可以."""  # noqa: DOCSTRING_CJK
    # 不可以 must decline, never accept.
    assert sr._match_mini_game_invite_keyword('不可以') == 'decline'
    assert sr._match_mini_game_invite_keyword('不可以的') == 'decline'
    # Standalone 可以 no longer matches; use 好啊/行啊/来吧 for acceptance.
    assert sr._match_mini_game_invite_keyword('可以') is None


def test_keyword_matcher_no_false_positive_on_korean_eung():
    """Codex P2: single Korean 응 removed from accept; ordinary 적응/반응/
    응답 contain it without acceptance intent."""  # noqa: DOCSTRING_CJK
    assert sr._match_mini_game_invite_keyword('적응 중이야') is None
    assert sr._match_mini_game_invite_keyword('반응이 좋네') is None
    # Two-character Korean 좋아 still matches.
    assert sr._match_mini_game_invite_keyword('좋아') == 'accept'


def test_keyword_matcher_accept_does_not_match_negation():
    """Keyword lists and priority jointly guard 不好/我不行 against acceptance.
    Current 好啊/好的/行啊 phrases exclude 不好; additionally
    decline beats accept, so either safeguard catches failures of the other."""  # noqa: DOCSTRING_CJK
    # Substring check: 不好 / 我不行 contain no acceptance phrase.
    assert sr._match_mini_game_invite_keyword('不好') == 'decline'
    assert sr._match_mini_game_invite_keyword('我不行') == 'decline'
    assert sr._match_mini_game_invite_keyword('不好玩') == 'decline'


def test_keyword_matcher_accept_en():
    assert sr._match_mini_game_invite_keyword('yes please') == 'accept'
    # let's go! no longer matches: let's became let's play, and go is absent;
    # phrases like let's play / i'll play match, narrowed after CodeRabbit Major review.
    assert sr._match_mini_game_invite_keyword("let's play this") == 'accept'
    assert sr._match_mini_game_invite_keyword('Yeah!') == 'accept'
    assert sr._match_mini_game_invite_keyword('i wanna play') == 'accept'


def test_keyword_matcher_decline_negated_let_and_wanna():
    """CodeRabbit Major: acceptance let's play / wanna play can still occur inside
    let's not play / I don't wanna play; add decline phrases
    let's not / don't wanna so priority protects negation."""
    assert sr._match_mini_game_invite_keyword("let's not play") == 'decline'
    assert sr._match_mini_game_invite_keyword("I don't wanna play") == 'decline'
    # Positive phrases remain accepted.
    assert sr._match_mini_game_invite_keyword("yeah let's play") == 'accept'


def test_keyword_matcher_no_false_positive_on_negated_accept_phrases():
    """Codex P2: word-bounded okay/sure still match within
    not okay/not sure; without corresponding decline phrases,
    priority cannot help. Remove okay and add not okay/not sure/not yet
    to decline as dual protection."""
    # okay removed; independently verify not okay / okay / okay let's go behavior.
    assert sr._match_mini_game_invite_keyword('not okay') == 'decline'
    assert sr._match_mini_game_invite_keyword('okay') is None  # Removed from accept.
    # sure remains accepted, but not sure now declines and takes priority.
    assert sr._match_mini_game_invite_keyword("i'm not sure") == 'decline'
    assert sr._match_mini_game_invite_keyword('sure thing') == 'accept'
    # not yet declines, covering i'm not yet ready.
    assert sr._match_mini_game_invite_keyword("not yet") == 'decline'


def test_keyword_matcher_decline_zh():
    assert sr._match_mini_game_invite_keyword('不要') == 'decline'
    assert sr._match_mini_game_invite_keyword('算了吧') == 'decline'
    assert sr._match_mini_game_invite_keyword('不想玩') == 'decline'


def test_keyword_matcher_later_zh():
    assert sr._match_mini_game_invite_keyword('回头说') == 'later'
    assert sr._match_mini_game_invite_keyword('等会') == 'later'
    assert sr._match_mini_game_invite_keyword('晚点吧') == 'later'


def test_keyword_matcher_priority_decline_over_later_over_accept():
    """Priority is decline>later>accept after CodeRabbit Major review.
    Negation must never launch games through incidental acceptance substring hits;
    later signals outrank pure acceptance too."""
    # decline > later: 不要 plus 回头 yields decline.
    assert sr._match_mini_game_invite_keyword('不要，回头说吧') == 'decline'
    # later > accept: 好的 plus 等下 yields later.
    # Accept-but-wait intent should defer rather than immediately open games.
    assert sr._match_mini_game_invite_keyword('好的等下') == 'later'
    # decline > accept: 不行 plus 可以.
    assert sr._match_mini_game_invite_keyword('不行吧，但可以下次') == 'decline'


def test_keyword_matcher_decline_via_phrase():
    """我现在没空 must decline through 没空, pinning keyword coverage;
    earlier None-or-decline assertions were too permissive and missed
    regressions after keyword removal (CodeRabbit Minor)."""  # noqa: DOCSTRING_CJK
    result = sr._match_mini_game_invite_keyword('我现在没空')
    assert result == 'decline'


def test_keyword_matcher_no_false_positive_on_common_english_phrases():
    """Codex P1/CodeRabbit Major: English no removed from decline;
    ordinary I have no idea / no worries / know must not decline,
    accidentally starting one-hour cooldown during pending invitations."""
    assert sr._match_mini_game_invite_keyword('i have no idea') is None
    assert sr._match_mini_game_invite_keyword('no worries') is None
    assert sr._match_mini_game_invite_keyword('i know what you mean') is None
    # Explicit no thanks still declines.
    assert sr._match_mini_game_invite_keyword('no thanks') == 'decline'


def test_maybe_apply_keyword_noop_when_no_pending():
    """Ordinary chat text must not launch games without a pending invite."""
    for text in (
        '好啊',
        '来玩一局羽毛球',
        '来一局足球',
        'please play badminton',
        'play badminton',
        'what about badminton, can we play?',
        '不想踢足球，想打羽毛球',
    ):
        assert sr._maybe_apply_mini_game_invite_keyword(LANLAN, text) is None


def test_response_cooldowns_are_kept_per_game_after_later_invites():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 60
    state['responded_at'] = None
    state['pending_session_id'] = 'soccer-sess'
    state['last_game_type'] = 'soccer'
    soccer_result = sr._apply_mini_game_invite_choice(LANLAN, 'decline', source='unit')
    assert soccer_result['action'] == 'cooldown'

    sr._mini_game_invite_record_delivered(LANLAN, 'badminton-sess')
    state['last_game_type'] = 'badminton'
    badminton_result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='unit')
    assert badminton_result['action'] == 'open_game'

    assert sr._mini_game_invite_in_cooldown(LANLAN, 'soccer') is True
    assert sr._mini_game_invite_in_cooldown(LANLAN, 'badminton') is True


def test_response_cooldowns_advance_after_top_level_later_reset():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = None
    state['responded_at'] = None
    state['response_cooldowns'] = {
        'soccer': {
            'responded_at': time.time() - 60,
            'chats_since_response': 0,
            'last_response_choice': 'decline',
        },
    }

    sr._mini_game_invite_count_post_response_chat(LANLAN)

    assert state['response_cooldowns']['soccer']['chats_since_response'] == 1


def test_response_cooldowns_do_not_advance_for_pending_invite_message():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time()
    state['responded_at'] = None
    state['response_cooldowns'] = {
        'soccer': {
            'responded_at': time.time() - 60,
            'chats_since_response': 0,
            'last_response_choice': 'decline',
        },
    }

    sr._mini_game_invite_count_post_response_chat(LANLAN)

    assert state['response_cooldowns']['soccer']['chats_since_response'] == 0


def test_maybe_apply_keyword_accept_returns_open_game():
    """Pending plus acceptance keywords changes state and returns open_game."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'kw-sess'
    state['last_game_type'] = 'soccer'
    result = sr._maybe_apply_mini_game_invite_keyword(LANLAN, '好啊')
    assert result is not None
    assert result['action'] == 'open_game'
    assert state['responded_at'] is not None  # mark responded


def test_maybe_apply_keyword_decline_starts_cooldown():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'kw-sess'
    result = sr._maybe_apply_mini_game_invite_keyword(LANLAN, '不要')
    assert result is not None
    assert result['action'] == 'cooldown'
    assert state['responded_at'] is not None


def test_maybe_apply_keyword_later_resets_state():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'kw-sess'
    result = sr._maybe_apply_mini_game_invite_keyword(LANLAN, '回头再说')
    assert result is not None
    assert result['action'] == 'suppress'
    assert state['delivered_at'] is None
    assert state['suppressed_until'] is not None


def test_keyword_matcher_no_false_positive_on_substring_words():
    """Match short English replies on word boundaries, not inside other words."""
    # yes must not match yesterday.
    assert sr._match_mini_game_invite_keyword('yesterday i talked to him') is None
    # no in no idea is a standalone token and should match decline, despite the initial contrary example.
    # Instead use know, containing no as a substring; word boundaries must prevent matches.
    assert sr._match_mini_game_invite_keyword('i know what you mean') is None
    # Standalone sure matches accept.
    assert sr._match_mini_game_invite_keyword('sure thing') == 'accept'
    # sure must not match substring-bearing pressure.
    assert sr._match_mini_game_invite_keyword('feeling pressure') is None


@pytest.mark.asyncio
async def test_button_endpoint_pushes_resolved_ws_for_all_actions(monkeypatch):
    """Codex P2: accept/decline/later endpoint handling must broadcast
    mini_game_invite_resolved so every page dismisses prompts consistently.
    Previously only accept sent mini_game_launch,
    leaving decline/later buttons visible in other windows."""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'btn-sess'
    state['last_game_type'] = 'soccer'

    # Mock manager and websocket.
    mgr = MagicMock()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state

    await sr._push_mini_game_invite_resolved(
        mgr, session_id='btn-sess', action='cooldown',
    )
    mgr.websocket.send_json.assert_awaited_once()
    payload = mgr.websocket.send_json.await_args.args[0]
    assert payload['type'] == 'mini_game_invite_resolved'
    assert payload['session_id'] == 'btn-sess'
    assert payload['action'] == 'cooldown'
    # Cooldown has no game_url.
    assert 'game_url' not in payload


@pytest.mark.asyncio
async def test_push_resolved_includes_game_url_for_open_game(monkeypatch):
    """Include the launch URL in the resolved WebSocket event for acceptance."""
    mgr = MagicMock()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state

    await sr._push_mini_game_invite_resolved(
        mgr,
        session_id='accept-sess',
        action='open_game',
        game_url='/soccer_demo?lanlan_name=foo&session_id=accept-sess',
        game_type='soccer',
    )
    payload = mgr.websocket.send_json.await_args.args[0]
    assert payload['action'] == 'open_game'
    assert payload['game_url'].startswith('/soccer_demo?')
    assert payload['game_type'] == 'soccer'


@pytest.mark.asyncio
async def test_push_resolved_noop_without_session_id():
    """Empty session_id noops, preventing accidental expired-invitation broadcasts."""
    mgr = MagicMock()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    await sr._push_mini_game_invite_resolved(mgr, session_id='', action='cooldown')
    mgr.websocket.send_json.assert_not_awaited()


def test_advance_response_returns_outcome_for_caller_ws_push():
    """Return an outcome so the caller can notify every window over WebSocket."""
    fixed_now = 1_700_020_000.0
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = fixed_now - 60
    state['responded_at'] = None
    state['pending_session_id'] = 'adv-sess'

    result = sr._mini_game_invite_advance_response(LANLAN, fixed_now - 30)
    assert result is not None
    assert result['action'] == 'suppress'
    assert result['session_id'] == 'adv-sess'


# ─────────────────────────────────────────────────────────────────────────────
# Invitation WebSocket delivery and session_id propagation.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invite_short_circuit_returns_options_for_router(monkeypatch):
    """Successful delivery returns options; the Router owns WebSocket sending."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_AVAILABLE_GAMES', ('drawing_guess',))
    mgr = _make_mgr()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    # Mock client_state so send_json passes connectivity checks.
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state

    short_circuit = await sr._run_mini_game_invite_short_circuit(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert short_circuit is not None
    out = short_circuit.result.body
    assert out is not None and out['action'] == 'chat'
    mgr.websocket.send_json.assert_not_awaited()
    payload = short_circuit.options_payload
    assert payload is not None
    assert payload['type'] == 'mini_game_invite_options'
    assert payload['session_id'] == out['invite_session_id']
    assert payload['game_type'] == 'drawing_guess'
    assert isinstance(payload['options'], list) and len(payload['options']) == 3
    choices = [opt['choice'] for opt in payload['options']]
    assert choices == ['accept', 'decline', 'later']


@pytest.mark.asyncio
async def test_direct_invite_delivery_preserves_options_websocket_compat(monkeypatch):
    """The pre-5.5 direct helper still pushes its options event exactly once."""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN,
        mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh',
        master_name=MASTER,
    )

    assert out is not None and out['action'] == 'chat'
    mgr.websocket.send_json.assert_awaited_once()
    payload = mgr.websocket.send_json.await_args.args[0]
    assert payload['type'] == 'mini_game_invite_options'
    assert payload['session_id'] == out['invite_session_id']
    assert payload['game_type'] == out['game_type']
    # State stores pending_session_id too.
    state = sr._mini_game_invite_state[LANLAN]
    assert state['pending_session_id'] == out['invite_session_id']


# ─────────────────────────────────────────────────────────────────────────────
# i18n completeness.
# ─────────────────────────────────────────────────────────────────────────────

def test_option_labels_cover_all_native_locales():
    """Every native locale must define a non-empty label for each choice."""
    from config.prompts.prompts_proactive import MINI_GAME_INVITE_OPTION_LABELS
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        assert lang in MINI_GAME_INVITE_OPTION_LABELS, f"缺 {lang} option labels"
        labels = MINI_GAME_INVITE_OPTION_LABELS[lang]
        for choice in ('accept', 'decline', 'later'):
            assert choice in labels, f"{lang} 缺 {choice} 标签"
            assert labels[choice].strip(), f"{lang}/{choice} 标签空"


def test_keywords_cover_all_native_locales():
    """Every native locale must define non-empty keyword lists for each choice."""
    from config.prompts.prompts_proactive import MINI_GAME_INVITE_KEYWORDS
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        assert lang in MINI_GAME_INVITE_KEYWORDS, f"缺 {lang} keywords"
        kws = MINI_GAME_INVITE_KEYWORDS[lang]
        for choice in ('accept', 'decline', 'later'):
            assert choice in kws, f"{lang} 缺 {choice} 关键词"
            assert isinstance(kws[choice], list) and kws[choice], (
                f"{lang}/{choice} 关键词列表空"
            )


def test_invite_game_configs_and_i18n_complete():
    from config import MINI_GAME_INVITE_AVAILABLE_GAMES, MINI_GAME_LAUNCH_URL_BY_GAME
    from config.prompts.prompts_activity import WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME
    from config.prompts.prompts_proactive import MINI_GAME_INVITE_LINES_BY_GAME

    expected_urls = {
        'watch-together': '/watch_together',
        'soccer': '/soccer_demo',
        'badminton': '/badminton_demo',
        'drawing_guess': '/drawing_guess_demo',
        'air_basketball': '/air_basketball',
    }
    assert set(MINI_GAME_INVITE_AVAILABLE_GAMES) == set(expected_urls)
    for game in MINI_GAME_INVITE_AVAILABLE_GAMES:
        url = expected_urls[game]
        assert MINI_GAME_LAUNCH_URL_BY_GAME[game] == url
        for lang in ('zh', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'):
            assert MINI_GAME_INVITE_LINES_BY_GAME[game][lang].strip()
            work_break_prompt = WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME[game][lang]
            assert work_break_prompt.strip()
            assert '{master}' in work_break_prompt
            assert '{app}' in work_break_prompt
            assert '{minutes}' in work_break_prompt


def test_accept_badminton_invite_returns_badminton_url():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 3
    state['responded_at'] = None
    state['pending_session_id'] = 'bd-sess'
    state['last_game_type'] = 'badminton'

    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='unit')

    assert result['action'] == 'open_game'
    assert result['game_type'] == 'badminton'
    assert result['game_url'].startswith('/badminton_demo?')
    assert 'mode=duel' not in result['game_url']
    assert 'session_id=bd-sess' in result['game_url']


def test_accept_drawing_guess_invite_returns_drawing_guess_url():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 3
    state['responded_at'] = None
    state['pending_session_id'] = 'dg-sess'
    state['last_game_type'] = 'drawing_guess'

    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='unit')

    assert result['action'] == 'open_game'
    assert result['game_type'] == 'drawing_guess'
    assert result['game_url'].startswith('/drawing_guess_demo?')
    assert 'session_id=dg-sess' in result['game_url']


@pytest.mark.asyncio
@pytest.mark.parametrize("retired", [False, True], ids=("owned-round", "retired-round"))
async def test_short_circuit_invite_respects_round_ownership(monkeypatch, retired):
    """A retired HTTP round's invite cannot advance the replacement round."""
    from main_logic.session_state import ProactivePhase, SessionStateMachine

    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    state_machine = SessionStateMachine(LANLAN)
    assert await state_machine.try_start_proactive(session=None, round_id="current-round")
    mgr = _make_mgr(sid="sid-round")
    mgr.state = state_machine

    out = await sr._run_mini_game_invite_short_circuit(
        lanlan_name=LANLAN,
        mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh',
        master_name=MASTER,
        round_id="retired-round" if retired else "current-round",
    )

    assert mgr.prepare_proactive_delivery.await_args.kwargs["round_id"] == (
        "retired-round" if retired else "current-round"
    )
    assert out is not None
    if retired:
        assert out.result.body["reason_code"] == sr.PROACTIVE_REASON_DELIVERY_PREEMPTED
        assert state_machine.phase is ProactivePhase.PHASE1
        mgr.feed_tts_chunk.assert_not_awaited()
        mgr.finish_proactive_delivery.assert_not_awaited()
    else:
        assert out.result.body["reason_code"] == sr.PROACTIVE_REASON_CHAT_DELIVERED
        assert state_machine.phase is ProactivePhase.PHASE2
        assert mgr.feed_tts_chunk.await_args.kwargs["expected_round_id"] == "current-round"
        assert (
            mgr.finish_proactive_delivery.await_args.kwargs["expected_round_id"]
            == "current-round"
        )
    assert state_machine.proactive_round_id == "current-round"
