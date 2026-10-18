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

"""Mini-game invite state, policy, delivery and keyword handling."""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import (
    MINI_GAME_INVITE_AVAILABLE_GAMES,
    MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS,
    MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS,
    MINI_GAME_INVITE_COOLDOWN_CHATS,
    MINI_GAME_INVITE_ENABLED,
    MINI_GAME_INVITE_FORCE_GAME_TYPE,
    MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS,
    MINI_GAME_INVITE_NEW_USER_FORCE_AT,
    MINI_GAME_INVITE_TRIGGER_PROBABILITY,
    MINI_GAME_LAUNCH_URL_BY_GAME,
)
from config.prompts.prompts_proactive import (
    MINI_GAME_INVITE_KEYWORDS,
    MINI_GAME_INVITE_LINES_BY_GAME,
    MINI_GAME_INVITE_OPTION_LABELS,
    normalize_mini_game_invite_locale,
)
from config.prompts.prompts_sys import _loc
from utils.logger_config import get_module_logger

from .contracts import (
    PROACTIVE_REASON_CHAT_DELIVERED,
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_PASS_DELIVERY_BUSY,
    ProactiveChatResult,
    _proactive_chat_body,
    _proactive_pass_body,
)
from .state import (
    _enter_proactive_phase2,
    _ensure_proactive_chat_totals_loaded,
    _get_proactive_chat_total,
    _proactive_feed_rejected_for_takeover,
    _proactive_turn_still_owned,
    _record_invite_delivery_persistent,
    _record_proactive_chat,
    _was_invite_ever_delivered,
)

logger = get_module_logger(__name__, "Main")


@dataclass(frozen=True, slots=True)
class MiniGameInviteEntryAdvance:
    """A pending invite implicitly resolved by newer user activity."""

    session_id: str
    action: str


@dataclass(frozen=True, slots=True)
class MiniGameInviteShortCircuit:
    """A completed invite attempt and optional frontend options payload."""

    result: ProactiveChatResult
    options_payload: dict[str, Any] | None = None


# --- Per-character mini-game invitation shortcut state ---
# {lanlan_name: {'delivered_at': float|None,
#                'responded_at': float|None,
#                'chats_since_response': int,
#                'last_game_type': str|None,
#                'last_response_choice': 'accept'|'decline'|None}}
# - delivered_at: last successful invitation timestamp; None means never delivered.
# - responded_at: time the user responds after delivery (any message timestamp >delivered_at).
#   Always suppress rolls while pending (delivered_at!=None and responded_at=None),
#   avoiding a second invite while the first remains unanswered.
# - chats_since_response: ordinary proactive chats successfully delivered after responded_at is set.
#   Require both choice-based time and >=COOLDOWN_CHATS before rolling again.
#   Share cooldown across game_type: one global cooldown per character; one invitation silences every mini-game
#   for the window. Specs do not require dense invitations; multiple games enrich options, not frequency.
# - last_game_type: previous invited game, randomly chosen from MINI_GAME_INVITE_AVAILABLE_GAMES;
#   PR-B buttons use it to choose which game to open.
# - last_response_choice: accept or decline, selecting cooldown
#   SECONDS thresholds (accept=2h, decline=5h). later/implicit dismiss takes reset
#   paths and never reaches this. None means no response (pending or new).
# Process-local dict resets on restart: time +10 chats is a soft cooldown; one extra invitation after restart costs far less
# than persistence dependencies. Like _proactive_chat_history, it stays in memory.
_mini_game_invite_state: dict[str, dict[str, Any]] = {}


# ---------- Mini-game invitation shortcut state management ----------
# proactive_chat calls _maybe_deliver_mini_game_invite after propensity / skip_probability /
# restricted_screen_only gates. Hits use static
# i18n templates -> feed_tts_chunk + finish_proactive_delivery directly, bypassing
# Phase 1/2 LLM. After an invitation response, require both:
#   ``time.time() - responded_at >= threshold_by_choice``
#     threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS (2h) when
#     last_response_choice='accept', otherwise ..._AFTER_DECLINE_SECONDS (5h);
# and
#   ``chats_since_response >= MINI_GAME_INVITE_COOLDOWN_CHATS``
# before rolling again. Always suppress while pending (delivered but unanswered),
# avoiding a second invitation while the first waits.

def _mini_game_invite_get_state(lanlan_name: str) -> dict[str, Any]:
    """Lazy-init per-character state。"""
    state = _mini_game_invite_state.get(lanlan_name)
    if state is None:
        state = {
            'delivered_at': None,
            'responded_at': None,
            'chats_since_response': 0,
            'last_game_type': None,
            'response_cooldowns': {},
            # Current pending invite session_id; endpoints verify responses match, preventing stale
            # clicks from answering the current pending invite. Refreshed on each new delivery.
            'pending_session_id': None,
            # D2 later suppression: do not roll immediately on the next proactive chat after reset.
            # _in_cooldown checks this extra gate. Epoch seconds; None means unsuppressed.
            'suppressed_until': None,
            # accept/decline use different cooldown thresholds. later/implicit-dismiss resets clear
            # this to None, as does pending state; cooldown reads it only when responded_at
            # !=None.
            'last_response_choice': None,
        }
        _mini_game_invite_state[lanlan_name] = state
    else:
        state.setdefault('response_cooldowns', {})
    return state


def _mini_game_invite_advance_response(
    lanlan_name: str, last_user_msg_at: float | None,
) -> dict[str, Any] | None:
    """During a pending invite the user sent any ordinary message (not an explicit
    choice / keyword hit) → silently dismiss the prompt + 5min short suppression,
    **without** starting the long cooldown.

    Returns: a dict with the same shape as ``_apply_mini_game_invite_choice``
    (containing ``action='suppress'`` + ``session_id``); the caller uses it to
    push the ``mini_game_invite_resolved`` WS event so the frontend dismisses the
    UI. Returns None when there is nothing to do.

    Called once on every entry into proactive_chat (both the voice fast path and
    the text path). last_user_msg_at is "the timestamp of the user's last
    activity" — the caller is responsible for deriving it from the right source:
    the text path back-computes it from activity_snapshot.seconds_since_user_msg;
    the voice path uses mgr.last_user_activity_time directly (voice does not go
    through the activity tracker, but the session itself tracks RMS / text input
    activity). If either is missing (None), this is a noop.

    Difference between historical and current semantics (changed after CodeRabbit
    Major):
    - Old PR #1141 era: no ChoicePrompt; "the user spoke after the invite" =
      "implicit response" → mark responded_at directly, starting the 1h+10 chats
      long cooldown.
    - Now PR #1145 introduces explicit three-option buttons + a keyword text
      fallback; the long-cooldown semantics should only be triggered by an
      **explicit choice** (accept / decline). Any non-matching message merely
      "dismisses the prompt" — keeping ever_delivered (force-first will not fire
      again) + a 5min short suppression (so the next proactive does not
      immediately re-invite), but no long lock. Equivalent to the 'later'
      choice. Otherwise, if the user first says something else and then clicks a
      button → the endpoint sees responded_at != None → "expired", and the state
      has quietly entered the 1h long cooldown (violating D2 semantics, bad UX)."""
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return None
    if state['delivered_at'] is None or state['responded_at'] is not None:
        return None
    if last_user_msg_at is None:
        return None
    if last_user_msg_at <= state['delivered_at']:
        return None
    # Any message implicitly dismisses, matching later's reset + short suppression.
    # Reuse _apply_mini_game_invite_choice as one source of truth; source='implicit_dismiss'
    # distinguishes implicit and button paths in logs.
    return _apply_mini_game_invite_choice(
        lanlan_name, 'later', source='implicit_dismiss',
    )


def _last_user_message_at_from_activity(
    activity_snapshot: Any,
    *,
    now: float | None = None,
) -> float | None:
    """Back-compute the text path's last user-message timestamp."""
    if activity_snapshot is None:
        return None
    seconds_since_user_msg = getattr(
        activity_snapshot,
        "seconds_since_user_msg",
        None,
    )
    if seconds_since_user_msg is None:
        return None
    current_time = time.time() if now is None else now
    return current_time - float(seconds_since_user_msg)


def _advance_mini_game_invite_entry(
    lanlan_name: str,
    last_user_msg_at: float | None,
) -> MiniGameInviteEntryAdvance | None:
    """Advance pending state and normalize the resolved-notification fields."""
    outcome = _mini_game_invite_advance_response(
        lanlan_name,
        last_user_msg_at,
    )
    if not outcome or not outcome.get("session_id"):
        return None
    return MiniGameInviteEntryAdvance(
        session_id=outcome["session_id"],
        action=outcome.get("action", "suppress"),
    )


def _mini_game_invite_in_cooldown(lanlan_name: str, game_type: str | None = None) -> bool:
    """Return whether this character is in a mini-game invite cooldown.

    A true value means the current turn should not roll another invite. This
    covers short suppression from the "later" choice, pending invites, and
    replied invites that have not crossed both the time and chat-count gates.
    Cooldowns after completed responses are scoped to the same game type, while
    pending invites still suppress all game types for the character.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return False
    suppressed_until = state.get('suppressed_until')
    if suppressed_until is not None and time.time() < float(suppressed_until):
        return True
    if game_type:
        cooldowns = state.get('response_cooldowns')
        if isinstance(cooldowns, dict) and isinstance(cooldowns.get(game_type), dict):
            response_state = cooldowns[game_type]
            elapsed = time.time() - float(response_state.get('responded_at') or 0.0)
            if response_state.get('last_response_choice') == 'decline':
                time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS
            else:
                time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS
            chats_since_response = int(response_state.get('chats_since_response') or 0)
            if elapsed < time_threshold or chats_since_response < MINI_GAME_INVITE_COOLDOWN_CHATS:
                return True
            cooldowns.pop(game_type, None)
    if game_type:
        last_game_type = state.get('last_game_type')
        pending = state.get('delivered_at') is not None and state.get('responded_at') is None
        if last_game_type and last_game_type != game_type and not pending:
            return False
    if state['delivered_at'] is None:
        return False
    if state['responded_at'] is None:
        return True
    elapsed = time.time() - state['responded_at']
    if state.get('last_response_choice') == 'decline':
        time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS
    else:
        time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS
    return (
        elapsed < time_threshold
        or state['chats_since_response'] < MINI_GAME_INVITE_COOLDOWN_CHATS
    )


def _mini_game_invite_record_delivered(lanlan_name: str, session_id: str) -> None:
    """Record a successfully delivered invite. Resets responded/counter, entering a new pending round.

    session_id comes from the caller (the uuid generated by
    ``_maybe_deliver_mini_game_invite`` at delivery); the endpoint verifies the
    user's response against the current pending one. A new delivery refreshes
    this id — a stale session_id left by the previous delivery is recognized as
    stale and rejected on the endpoint side."""
    state = _mini_game_invite_get_state(lanlan_name)
    state['delivered_at'] = time.time()
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = session_id
    state['last_response_choice'] = None
    # New invitation delivery clears D2 short suppression: it belonged to the previous later choice,
    # and new delivery proves that window expired.
    state['suppressed_until'] = None


def _mini_game_invite_count_post_response_chat(lanlan_name: str) -> None:
    """Advance invite cooldown chat counters after a delivered proactive turn.

    This runs immediately after _record_proactive_chat. Any channel counts as
    long as the AI actually delivered a proactive message. Pending invites are
    no-ops so the invite message itself does not spend the response gate.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return
    if state.get('delivered_at') is not None and state.get('responded_at') is None:
        return
    if state.get('responded_at') is not None:
        state['chats_since_response'] += 1
    cooldowns = state.get('response_cooldowns')
    if isinstance(cooldowns, dict):
        for response_state in cooldowns.values():
            if isinstance(response_state, dict) and response_state.get('responded_at') is not None:
                response_state['chats_since_response'] = int(response_state.get('chats_since_response') or 0) + 1


def _mini_game_invite_record_response_cooldown(
    state: dict[str, Any],
    game_type: str,
    choice: str,
    responded_at: float,
) -> None:
    cooldowns = state.setdefault('response_cooldowns', {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        state['response_cooldowns'] = cooldowns
    cooldowns[game_type] = {
        'responded_at': responded_at,
        'chats_since_response': 0,
        'last_response_choice': choice,
    }


def _mini_game_launch_url(game_type: str, lanlan_name: str, session_id: str) -> str | None:
    url_template = MINI_GAME_LAUNCH_URL_BY_GAME.get(game_type)
    if not url_template:
        return None
    from urllib.parse import urlencode as _urlencode

    query = {
        "lanlan_name": lanlan_name,
        "session_id": session_id,
    }
    separator = "&" if "?" in url_template else "?"
    return f"{url_template}{separator}{_urlencode(query)}"


def _pick_mini_game_type(lanlan_name: str | None = None, *, manager=None) -> str | None:
    """Pick an available mini-game type with invite copy configured.

    Games missing invite lines are skipped, and character-specific cooldowns are
    respected when a character name is provided.
    """
    candidates = [
        g for g in MINI_GAME_INVITE_AVAILABLE_GAMES
        if g in MINI_GAME_INVITE_LINES_BY_GAME
    ]
    if 'watch-together' in candidates:
        from main_logic.watch_together.preparation import is_available
        if not is_available(manager):
            candidates.remove('watch-together')
    if lanlan_name:
        candidates = [
            g for g in candidates
            if not _mini_game_invite_in_cooldown(lanlan_name, g)
        ]
    if not candidates:
        return None
    import random as _random
    return _random.choice(candidates)


async def _attempt_mini_game_invite_delivery(
    *,
    lanlan_name: str,
    mgr,
    activity_snapshot,
    invite_lang: str,
    master_name: str,
    user_toggle_enabled: bool = True,
    push_options_compat: bool,
    memory_dir: str | Path | None = None,
    round_id: str | None = None,
) -> dict | None:
    """Deliver an eligible invite for either the legacy or staged caller.

    Short-circuit conditions (any one unmet → return None and the caller
    continues the original Phase1/2 pipeline):
      - MINI_GAME_INVITE_ENABLED=False (global kill switch, the production master toggle)
      - user_toggle_enabled=False (the user turned the
        ``proactiveMiniGameInviteEnabled`` toggle off in the frontend CHAT_MODE_CONFIG)
      - activity_snapshot is None (privacy mode / tracker unavailable — be conservative, do not send)
      - propensity == 'restricted_screen_only' (focused_work / non-casual gaming)
      - state == 'away' (user absent; nobody to receive the invite)
      - activity_snapshot.unfinished_thread is not None (the AI just asked a
        question the user has not answered; following the thread takes priority
        over changing topics — aligned with the precedence convention of
        skip_probability / restricted_screen_only over unfinished_thread)
      - _mini_game_invite_in_cooldown
      - on the non-force-first path, random() >= MINI_GAME_INVITE_TRIGGER_PROBABILITY

    Debug flag: when ``config.MINI_GAME_INVITE_FORCE_GAME_TYPE`` is non-None it
    bypasses every gate except ``MINI_GAME_INVITE_ENABLED`` (including the user
    toggle, cooldown, probability, unfinished_thread, snapshot None / propensity
    / away, and the force-first decision), pinning game_type to the flag value.
    Local manual testing only; keep it None in production.

    Force-first branch: when
      ``state.delivered_at is None`` and
      ``proactive_chat_total >= MINI_GAME_INVITE_NEW_USER_FORCE_AT - 1``
    the 10% dice roll is bypassed and the invite goes straight out — giving
    users who have never played one deterministic "being invited" moment instead
    of relying on probability. The other gates (propensity / unfinished_thread /
    cooldown) still apply.

    The delivery path fully mirrors
    ``main_routers/game_router._deliver_postgame_text_bubble``:
    prepare_proactive_delivery → feed_tts_chunk → finish_proactive_delivery.
    No Phase 1/2 LLM involved; the line is picked from
    ``MINI_GAME_INVITE_LINES_BY_GAME[game_type]`` and game_type comes from
    random.choice over ``MINI_GAME_INVITE_AVAILABLE_GAMES``."""
    if not MINI_GAME_INVITE_ENABLED:
        return None

    state_storage_kwargs = (
        {"memory_dir": memory_dir} if memory_dir is not None else {}
    )

    # Debug shortcut: non-None skips snapshot/cooldown/probability gates and fixes game_type
    # to the flag. Still require corresponding text; invalid values warn and exit without raising,
    # avoiding broken proactive pipelines during config changes. Set force-first True to match normal
    # first-time caller behavior (without affecting persisted ever_delivered).
    #
    # Still respect user toggle proactiveMiniGameInviteEnabled: developer-local debugging
    # flags must not bypass users explicitly disabling mini-game sources in the frontend.
    force_game = MINI_GAME_INVITE_FORCE_GAME_TYPE
    debug_force = bool(force_game)
    if debug_force and not user_toggle_enabled:
        return None
    if debug_force:
        if force_game not in MINI_GAME_INVITE_LINES_BY_GAME:
            logger.warning(
                "[%s] MINI_GAME_INVITE_FORCE_GAME_TYPE=%r is not in "
                "MINI_GAME_INVITE_LINES_BY_GAME=%r — skipping invite. "
                "Set the flag to a valid key or back to None.",
                lanlan_name, force_game, list(MINI_GAME_INVITE_LINES_BY_GAME.keys()),
            )
            return None
        await _ensure_proactive_chat_totals_loaded(**state_storage_kwargs)
        game_type = force_game
        # Share this field with success logs below; debug semantics force the first-time
        # path, making printed diagnostics recognizable.
        force_first = True
    else:
        if not user_toggle_enabled:
            return None
        if activity_snapshot is None:
            return None
        propensity = getattr(activity_snapshot, 'propensity', None)
        state_label = getattr(activity_snapshot, 'state', None)
        if propensity == 'restricted_screen_only':
            return None
        if state_label == 'away':
            return None
        # AI asked a question last turn (?/吗/呢/么, etc.) without user response: prioritize thread follow-up.
        # skip_probability uses the same priority in system_router.py's propensity section;
        # mini-game invites must not steal promised follow-ups.
        if getattr(activity_snapshot, 'unfinished_thread', None) is not None:
            return None
        # Force-first: never invited +N-1 successful proactive chats -> force this delivery to an invite.
        # proactive_chat_total increments after _record_proactive_chat, so the Nth delivery sees
        # N-1. Persist counts across restarts, or every restart produces another Nth invitation
        # and unstable invitation frequency.
        #
        # New-user checks use persisted ever_delivered, never in-memory
        # ``state.delivered_at is None``, reset by PR-B later choices and process restarts.
        # Codex P1 review: without this, force-first treats previously invited users as new
        # and forces another invitation after every restart.
        await _ensure_proactive_chat_totals_loaded(**state_storage_kwargs)
        never_delivered = not _was_invite_ever_delivered(lanlan_name)
        total_so_far = _get_proactive_chat_total(lanlan_name)
        force_first = (
            never_delivered
            and total_so_far >= max(0, MINI_GAME_INVITE_NEW_USER_FORCE_AT - 1)
        )

        game_type = _pick_mini_game_type(lanlan_name, manager=mgr)
        if game_type is None:
            logger.warning(
                "[%s] mini-game invite skipped: no game_type available "
                "(MINI_GAME_INVITE_AVAILABLE_GAMES=%r, LINES keys=%r)",
                lanlan_name,
                MINI_GAME_INVITE_AVAILABLE_GAMES,
                list(MINI_GAME_INVITE_LINES_BY_GAME.keys()),
            )
            return None

        if not force_first:
            import random as _random
            if _random.random() >= MINI_GAME_INVITE_TRIGGER_PROBABILITY:
                return None
    invite_text = _render_mini_game_invite_line(game_type, invite_lang, master_name)
    if not invite_text:
        return None

    # Same round ownership as the main Phase 2 path: a retired HTTP task must
    # not claim or advance a replacement round.
    round_payload = {"round_id": round_id} if round_id is not None else {}
    delivery_round = {"expected_round_id": round_id} if round_id is not None else {}
    if not await mgr.prepare_proactive_delivery(min_idle_secs=10.0, **round_payload):
        return _proactive_pass_body(
            PROACTIVE_REASON_PASS_DELIVERY_BUSY,
            message="mini-game invite skipped: prepare_proactive_delivery refused",
        )
    proactive_sid = mgr.current_speech_id
    if not await _enter_proactive_phase2(
        mgr,
        proactive_sid,
        log=logger,
        **round_payload,
    ):
        return _proactive_pass_body(
            PROACTIVE_REASON_DELIVERY_PREEMPTED,
            message="mini-game invite skipped: user engaged before Phase 2",
        )
    expected_user_engagement_time = getattr(
        mgr,
        "last_user_engagement_time",
        None,
    )
    tts_accepted = True
    try:
        feed = getattr(mgr, 'feed_tts_chunk', None)
        if callable(feed):
            tts_accepted = await feed(
                invite_text,
                expected_speech_id=proactive_sid,
                expected_user_engagement_time=expected_user_engagement_time,
                **delivery_round,
            )
    except Exception as exc:
        logger.warning(
            "[%s] mini-game invite feed_tts_chunk failed: %s", lanlan_name, exc,
        )
    if tts_accepted is False and _proactive_feed_rejected_for_takeover(
        mgr,
        proactive_sid,
        expected_user_engagement_time,
    ):
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        return _proactive_pass_body(
            PROACTIVE_REASON_DELIVERY_PREEMPTED,
            message="mini-game invite skipped: user became active before TTS",
        )
    if tts_accepted is False:
        logger.warning(
            "[%s] mini-game invite TTS enqueue failed; committing text without audio",
            lanlan_name,
        )
    committed = await mgr.finish_proactive_delivery(
        invite_text,
        expected_speech_id=proactive_sid,
        expected_user_engagement_time=expected_user_engagement_time,
        **delivery_round,
    )
    if not committed:
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        return _proactive_pass_body(
            PROACTIVE_REASON_DELIVERY_PREEMPTED,
            message="mini-game invite skipped: user took over before delivery",
        )
    # Give each invitation its own session_id; button / keyword endpoint responses must
    # return it for backend validation, avoiding delayed stale responses answering current pending invites.
    invite_session_id = str(uuid4())

    _record_proactive_chat(lanlan_name, invite_text, channel='mini_game')
    _mini_game_invite_record_delivered(lanlan_name, invite_session_id)
    _mini_game_invite_get_state(lanlan_name)['last_game_type'] = game_type
    # Atomically persist counter+1 and ever_delivered=True under one lock; both must
    # persist together. Partial totals increment with stale ever_delivered=False
    # repeats force-first after restart (CodeRabbit Major).
    await _record_invite_delivery_persistent(lanlan_name, **state_storage_kwargs)

    try:
        from utils.instrument import counter as _instr_counter
        # Channel distinguishes proactive (this function) from work_break
        # (hydration-reminder combination downstream of _deliver_break_reminder_via_llm). Both
        # share invitation state/cooldown; totals sum both channels. force_first matters only
        # for proactive.
        _instr_counter(
            "mini_game_invited",
            game_type=str(game_type)[:24],
            channel="proactive",
            force_first=bool(force_first),
        )
    except Exception:
        # Telemetry failures must not affect invitation delivery.
        pass

    if push_options_compat:
        options_payload = _build_mini_game_invite_options_payload(
            invite_lang=invite_lang,
            game_type=game_type,
            session_id=invite_session_id,
        )
        try:
            if mgr.websocket and hasattr(mgr.websocket, 'send_json'):
                client_state = getattr(mgr.websocket, 'client_state', None)
                if client_state is None or client_state == client_state.CONNECTED:
                    await mgr.websocket.send_json(options_payload)
        except Exception as exc:
            logger.warning(
                "[%s] mini-game invite options WS push failed: %s",
                lanlan_name,
                exc,
            )

    print(
        f"[{lanlan_name}] Mini-game invite delivered "
        f"(game={game_type}, force_first={force_first}, "
        f"session_id={invite_session_id[:8]}…): {invite_text[:60]}…"
    )
    return _proactive_chat_body(
        PROACTIVE_REASON_CHAT_DELIVERED,
        message="mini-game invite delivered",
        channel="mini_game",
        game_type=game_type,
        force_first=force_first,
        lanlan_name=lanlan_name,
        turn_id=proactive_sid,
        invite_session_id=invite_session_id,
    )


async def _maybe_deliver_mini_game_invite(
    *,
    lanlan_name: str,
    mgr,
    activity_snapshot,
    invite_lang: str,
    master_name: str,
    user_toggle_enabled: bool = True,
) -> dict | None:
    """Preserve the pre-stage-5.5 direct delivery behavior and signature."""
    return await _attempt_mini_game_invite_delivery(
        lanlan_name=lanlan_name,
        mgr=mgr,
        activity_snapshot=activity_snapshot,
        invite_lang=invite_lang,
        master_name=master_name,
        user_toggle_enabled=user_toggle_enabled,
        push_options_compat=True,
    )


async def _run_mini_game_invite_short_circuit(
    *,
    lanlan_name: str,
    mgr: Any,
    activity_snapshot: Any,
    invite_lang: str,
    master_name: str,
    user_toggle_enabled: bool = True,
    memory_dir: str | Path | None = None,
    round_id: str | None = None,
) -> MiniGameInviteShortCircuit | None:
    """Run the invite attempt and expose framework-neutral short-circuit data."""
    state_storage_kwargs = (
        {"memory_dir": memory_dir} if memory_dir is not None else {}
    )
    body = await _attempt_mini_game_invite_delivery(
        lanlan_name=lanlan_name,
        mgr=mgr,
        activity_snapshot=activity_snapshot,
        invite_lang=invite_lang,
        master_name=master_name,
        user_toggle_enabled=user_toggle_enabled,
        push_options_compat=False,
        round_id=round_id,
        **state_storage_kwargs,
    )
    if body is None:
        return None

    options_payload = None
    if (
        body.get("action") == "chat"
        and body.get("channel") == "mini_game"
        and body.get("game_type")
        and body.get("invite_session_id")
    ):
        options_payload = _build_mini_game_invite_options_payload(
            invite_lang=invite_lang,
            game_type=body["game_type"],
            session_id=body["invite_session_id"],
        )
    return MiniGameInviteShortCircuit(
        result=ProactiveChatResult(body=body),
        options_payload=options_payload,
    )


def _render_mini_game_invite_line(
    game_type: str, invite_lang: str, master_name: str,
) -> str:
    """Render the canned invite line for one game.

    ``invite_lang`` is normalized to a prompt-dict key *here* rather than at the
    call site. ``MINI_GAME_INVITE_LINES_BY_GAME`` carries a ``zh-TW`` row, and an
    unnormalized tag (``zh_TW`` / ``zh-Hant`` / ``tchinese``) degrades through
    ``_loc``'s Chinese fallback to the Simplified line — a wrong-script invite, not
    an error, so nothing downstream would ever notice.

    Extracted from ``_attempt_mini_game_invite_delivery`` so this is reachable
    without standing up an activity snapshot and a delivery-capable manager: the
    locale behaviour is otherwise only testable through the whole invite flow.
    """
    template = _loc(
        MINI_GAME_INVITE_LINES_BY_GAME[game_type],
        normalize_mini_game_invite_locale(invite_lang),
    )
    safe_master = (master_name or '').strip()
    try:
        return template.format(master_name=safe_master).strip()
    except Exception:
        return template.replace('{master_name}', safe_master).strip()


def _build_mini_game_invite_options_payload(
    *,
    invite_lang: str,
    game_type: str,
    session_id: str,
) -> dict[str, Any]:
    """Build the WS payload for the frontend ChoicePrompt.

    Labels go through i18n (the accept/decline/later options); choice is the
    wire-format identifier (used when the frontend button click posts back to
    the endpoint) and stays unchanged.

    ``invite_lang`` is normalized to a prompt-dict key first: the lookup is an
    exact ``.get``, so an unnormalized tag (``zh_TW`` / ``zh-Hant`` / ``tchinese``)
    would silently fall through to the Simplified labels."""
    labels = MINI_GAME_INVITE_OPTION_LABELS.get(
        normalize_mini_game_invite_locale(invite_lang),
        MINI_GAME_INVITE_OPTION_LABELS.get('zh', {}),
    )
    options = [
        {'choice': 'accept', 'label': labels.get('accept', 'Yes')},
        {'choice': 'decline', 'label': labels.get('decline', 'No')},
        {'choice': 'later', 'label': labels.get('later', 'Later')},
    ]
    return {
        'type': 'mini_game_invite_options',
        'session_id': session_id,
        'game_type': game_type,
        'options': options,
    }


def _apply_mini_game_invite_choice(
    lanlan_name: str, choice: str, *, source: str,
) -> dict[str, Any]:
    """Handle the three-option state transition of a mini-game invite. Returns a
    structured result shared by the endpoint / keyword matcher.

    - accept: mark responded (starts the 2h+10 chats cooldown) + return game_url
    - decline: mark responded (starts the 5h+10 chats cooldown, without opening the game)
    - later (D2): reset state (delivered_at=None, restoring both force-first and
      the normal 10%) + add ``suppressed_until = now + 5min`` so the next
      proactive does not immediately roll the dice again

    state must be already-pending (delivered_at != None and responded_at is None);
    otherwise treat it as stale and return ``action='ignored'``, letting the
    caller decide whether to inform the user."""
    state = _mini_game_invite_state.get(lanlan_name)
    if not state or state.get('delivered_at') is None:
        return {'action': 'ignored', 'reason': 'no_pending_invite'}
    if state.get('responded_at') is not None:
        return {'action': 'ignored', 'reason': 'already_responded'}

    now = time.time()
    if choice == 'accept':
        state['responded_at'] = now
        state['chats_since_response'] = 0
        state['last_response_choice'] = 'accept'
        # session_id enters both game_url query and top-level result; keyword-path core.py
        # must include it in mini_game_launch WS payload so frontend dedupe shares keys
        # across paths (Codex P2: without it, one invitation triggered through multiple paths
        # opens duplicate windows).
        invite_session_id = state.get('pending_session_id') or ''
        game_type = state.get('last_game_type') or 'soccer'
        launch_game_type = game_type
        game_url = _mini_game_launch_url(launch_game_type, lanlan_name, invite_session_id)
        if not game_url:
            logger.warning(
                "[%s] accept invite but no launch URL for game_type=%r; "
                "fallback /soccer_demo", lanlan_name, game_type,
            )
            launch_game_type = "soccer"
            game_url = _mini_game_launch_url(launch_game_type, lanlan_name, invite_session_id) or "/soccer_demo"
        state['last_game_type'] = game_type
        _mini_game_invite_record_response_cooldown(state, game_type, 'accept', now)
        logger.info(
            "[%s] mini-game invite accepted via %s -> %s",
            lanlan_name, source, game_url,
        )
        return {
            'action': 'open_game',
            'game_type': launch_game_type,
            'game_url': game_url,
            'session_id': invite_session_id,
        }
    if choice == 'decline':
        # Retain session_id so callers push mini_game_invite_resolved; every
        # outcome needs frontend prompt dismissal (Codex P2).
        decline_session_id = state.get('pending_session_id') or ''
        game_type = state.get('last_game_type') or 'soccer'
        state['responded_at'] = now
        state['chats_since_response'] = 0
        state['last_response_choice'] = 'decline'
        _mini_game_invite_record_response_cooldown(state, game_type, 'decline', now)
        logger.info(
            "[%s] mini-game invite declined via %s; cooldown started",
            lanlan_name, source,
        )
        return {'action': 'cooldown', 'session_id': decline_session_id}
    if choice == 'later':
        # D2: fully reset with short suppression. Persisted ever_delivered still suppresses
        # force-first after reset; previously invited users never become new again merely
        # because state cleared.
        later_session_id = state.get('pending_session_id') or ''
        state['delivered_at'] = None
        state['responded_at'] = None
        state['chats_since_response'] = 0
        state['pending_session_id'] = None
        state['last_response_choice'] = None
        state['suppressed_until'] = now + MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS
        logger.info(
            "[%s] mini-game invite deferred via %s; suppressed for %.0fs",
            lanlan_name, source, float(MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS),
        )
        return {'action': 'suppress', 'session_id': later_session_id}
    return {'action': 'ignored', 'reason': f'unknown_choice:{choice}'}


async def _push_mini_game_invite_resolved(
    mgr,
    *,
    session_id: str,
    action: str,
    game_url: str | None = None,
    game_type: str | None = None,
) -> None:
    """Push the WS event so the frontend dismisses the ChoicePrompt (cleared on any outcome, consistent across windows).
    On accept the payload also carries game_url; the frontend treats
    ``action=='open_game'`` as the "launch" signal for window.open.

    Replaces the original ``mini_game_launch`` event — a single WS event covers
    both lifecycle termination (always clear the prompt) + optional game launch
    (on accept). codex P2 / CodeRabbit pointed out: the original only pushed
    ``mini_game_launch`` on accept, so after a decline / later keyword hit the
    frontend prompt never disappeared even though the state had already changed."""
    if not mgr or not session_id:
        return
    payload: dict[str, Any] = {
        'type': 'mini_game_invite_resolved',
        'session_id': session_id,
        'action': action,
    }
    if game_url:
        payload['game_url'] = game_url
    if game_type:
        payload['game_type'] = game_type
    try:
        ws = getattr(mgr, 'websocket', None)
        if ws is None or not hasattr(ws, 'send_json'):
            return
        client_state = getattr(ws, 'client_state', None)
        if client_state is not None and client_state != client_state.CONNECTED:
            return
        await ws.send_json(payload)
    except Exception as exc:
        logger.warning(
            "mini_game_invite_resolved WS push failed (session=%s, action=%s): %s",
            session_id, action, exc,
        )


# Match ASCII / Cyrillic keywords with word-boundary regex; other scripts (CJK / Hiragana /
# Katakana / Hangul) use substrings. Python `\b` tests \w boundaries, but East Asian characters
# are also \w, lacking internal boundaries; word-boundary matching misses 我好啊.
# Cyrillic and Latin are letter-based, so \b works. Codex P1: prevent English false matches
# such as yes in yesterday and no in no idea.
_LETTER_ONLY_KW_RE = re.compile(r"^[A-Za-z0-9\s'\-Ѐ-ӿ]+$")


_KEYWORD_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _keyword_matches(keyword: str, norm_text: str) -> bool:
    """Locale-aware substring/word-boundary match.

    Keywords made of ASCII / digits / Cyrillic / spaces / apostrophes / hyphens
    go through a word-boundary regex (``\\b...\\b``); other scripts (CJK /
    Hiragana / Katakana / Hangul) use substring matching — Python's regex counts
    those characters as \\w, so adding \\b would cause misses (in "我好啊" there
    is no boundary before '好')."""  # noqa: DOCSTRING_CJK
    if not keyword or not norm_text:
        return False
    if _LETTER_ONLY_KW_RE.fullmatch(keyword):
        pattern = _KEYWORD_PATTERN_CACHE.get(keyword)
        if pattern is None:
            pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
            _KEYWORD_PATTERN_CACHE[keyword] = pattern
        return bool(pattern.search(norm_text))
    return keyword in norm_text


def _match_mini_game_invite_keyword(text: str) -> str | None:
    """Return accept/decline/later for a user text, or None when unmatched.

    All native locale keyword lists are scanned because users may type in a
    language different from the active UI language. ASCII and Cyrillic keywords
    use word-boundary matching to avoid substring false positives; CJK keywords
    keep substring matching.

    **Priority decline > later > accept**: a sentence with an explicit negation
    must not open a game just because it also contains an accept keyword.

    Empty text and unmatched text return None.
    """
    if not text:
        return None
    norm = text.lower().strip()
    if not norm:
        return None
    hit_accept = False
    hit_decline = False
    hit_later = False
    for lang_kw in MINI_GAME_INVITE_KEYWORDS.values():
        if not hit_accept and any(_keyword_matches(kw, norm) for kw in lang_kw.get('accept', [])):
            hit_accept = True
        if not hit_later and any(_keyword_matches(kw, norm) for kw in lang_kw.get('later', [])):
            hit_later = True
        if not hit_decline and any(_keyword_matches(kw, norm) for kw in lang_kw.get('decline', [])):
            hit_decline = True
    # decline > later > accept：negation-priority。
    if hit_decline:
        return 'decline'
    if hit_later:
        return 'later'
    if hit_accept:
        return 'accept'
    return None


def _maybe_apply_mini_game_invite_keyword(
    lanlan_name: str, text: str,
) -> dict[str, Any] | None:
    """Apply mini-game invite keywords for one user-message text entry.

    Pending invites try accept, decline, and later keywords. Without a pending
    invite this helper is a no-op: ordinary chat text must not launch mini
    games implicitly. This helper does not consume the user message; normal
    chat handling should still continue.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state or state.get('delivered_at') is None or state.get('responded_at') is not None:
        return None
    choice = _match_mini_game_invite_keyword(text)
    if choice is None:
        return None
    result = _apply_mini_game_invite_choice(lanlan_name, choice, source='keyword')
    if result.get('action') == 'ignored':
        return None
    return result


def install_mini_game_invite_hooks() -> None:
    """Register mini-game keyword handling at the application boundary.

    The event bus deduplicates callbacks by identity, so repeated Router
    assembly remains safe while importing this domain module stays side-effect
    free.
    """
    try:
        from main_logic.agent_event_bus import register_text_user_message_hook

        register_text_user_message_hook(_maybe_apply_mini_game_invite_keyword)
    except Exception as exc:
        expected_absent = {"main_logic", "main_logic.agent_event_bus"}
        is_expected_absent = (
            isinstance(exc, ModuleNotFoundError)
            and getattr(exc, "name", None) in expected_absent
        )
        if not is_expected_absent:
            logger.warning(
                "proactive_chat: failed to register text_user_message_hook",
                exc_info=True,
            )
