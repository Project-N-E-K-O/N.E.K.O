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

"""Soccer PassiveGuard as one decision question (owner decisions D4, D15, D24, D27).

The question classifies the evidence, not the action: at the user stages what
the player's speech just now is, at stages 7 and 8 what NEKO's latest lines
show. The stage then maps the class to the action here, so the decision model
never has to know the stage rules. Vendors with a native decision model ask it
there; vendors whose chat model returns token probabilities ask it as a
one-token chat call; every other vendor, and every miss, uses the original chat
prompt (``runtime._run_soccer_passive_guard_ai``).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

USER_STAGES = ("user_soothing", "user_recovery")
OBSERVE, CANCEL, RESCUE, PREPARE = "observe_more", "cancel_candidate", "send_rescue_hint", "prepare_exit_prompt"
ACTIONS = (OBSERVE, CANCEL, RESCUE, PREPARE)
# Speech classes that ease the candidate at the user stages.
_SPEECH_HELPS = frozenset({"encourage", "ease_or_rule", "reply_engaged", "comfort", "ease", "reply_better"})
# P(speech helps) at or above this cancels the candidate.
CANCEL_THRESHOLD = 0.5
# Seconds for the decision or rehost call before the chat prompt takes over.
DECISION_TIMEOUT_SECONDS = 2.5
_MAX_STATE_DIALOGUES = 24
_LATEST_LINES = 4


def _stage_number(stage: Any) -> int:
    try:
        return int(stage)
    except (TypeError, ValueError):
        return 0


def _is_user_stage(data: Dict[str, Any]) -> bool:
    return str(data.get("stage")) in USER_STAGES


def _pre_game_context(data: Dict[str, Any], route_state: Any) -> Dict[str, Any]:
    """The request's opening context, else the one the route keeps."""
    pre = data.get("preGameContext") or (route_state.get("preGameContext") if isinstance(route_state, dict) else None)
    return pre if isinstance(pre, dict) else {}


def _teaching(data: Dict[str, Any], route_state: Any = None) -> bool:
    pre = _pre_game_context(data, route_state)
    return str(pre.get("gameStance") or "").strip().lower() == "teaching"


def _prompt_type(data: Dict[str, Any]) -> str:
    return str(data.get("promptType") or "surrender").strip()


def _dialog_lines(dialogues: list) -> list[str]:
    out = []
    for d in dialogues:
        if not isinstance(d, dict):
            continue
        did = d.get("id") or ""
        kind_of = d.get("type")
        if kind_of == "game_event":
            kind = str(d.get("kind") or "")
            line_text = f"[{did}] game event {kind}"
            if kind in ("light-balance-hint", "send-rescue-hint", "teaching-progress-hint"):
                line_text += f" (system hint sent to NEKO: {d.get('text') or ''})"
            said = str(d.get("result_line") or "").strip()
            if said:
                line_text += f" -> NEKO said: {said}"
            out.append(line_text)
        elif kind_of == "user":
            out.append(f"[{did}] player said: {d.get('text') or ''}")
        elif kind_of == "assistant":
            out.append(f"[{did}] NEKO said: {d.get('line') or ''}")
    return out


def _compact_state(data: Dict[str, Any], route_state: Any) -> str:
    """Context first, stage / trigger / speech last, so head-and-tail truncation keeps them."""
    from ._shared import _normalize_short_text
    from .game_context import _build_game_context_prompt_payload, _game_context_signals_text

    # Read as it is (the chat prompt reads it the same way): the route also
    # keeps live objects such as the context organizer's task.
    ctx = _build_game_context_prompt_payload(route_state) or {}
    pg = _pre_game_context(data, route_state)
    cs = data.get("currentState") or {}
    pgs = data.get("passiveGuardState") or {}
    score = cs.get("score") or {}
    parts = ["PassiveGuard input (soccer minigame side judge)."]
    if _prompt_type(data) == "rest":
        parts.append("Scenario: promptType=rest. NEKO (the cat-girl opponent) started this game withdrawn/low "
                     "(gameStance=withdrawn). The player keeps scoring at lv4, the weakest level where NEKO never "
                     "attacks. The candidate is 'NEKO may need a rest'; the exit window would be a rest prompt.")
    else:
        parts.append("Scenario: promptType=surrender. Ordinary game. The player keeps scoring at lv4, the weakest "
                     "level where NEKO (the cat-girl opponent) never attacks. The candidate is 'NEKO may be passively "
                     "giving up'; the exit window would be a surrender prompt.")
    parts.append(f"Opening context: gameStance={pg.get('gameStance')}, nekoEmotion={pg.get('nekoEmotion')}, "
                 f"stanceNote={pg.get('stanceNote') or ''}")
    if ctx.get("summary"):
        parts.append(f"Rolling summary of earlier play: {ctx['summary']}")
    signals = _game_context_signals_text(ctx.get("signals"))
    if signals:
        parts.append(f"Context signals: {signals}")
    if ctx.get("degraded"):
        parts.append("Context organizer is degraded; only the raw recent window is available.")
    dialogues = list(ctx.get("recent_dialogues") or [])[-_MAX_STATE_DIALOGUES:]
    parts.append("Recent game dialogue, oldest first:")
    parts.extend(_dialog_lines(dialogues) or ["(none)"])
    parts.append(f"Current state: score player {score.get('player', 0)} : NEKO {score.get('ai', 0)}, "
                 f"round {cs.get('round')}, NEKO mood {cs.get('mood')}, difficulty {cs.get('difficulty')}")
    parts.append(f"Candidate counters: ordinary lv4 player-goal streak={pgs.get('lv4PlayerGoalStreak')}, "
                 f"withdrawn rest goal streak={pgs.get('withdrawnRestGoalStreak')}")
    parts.append(f"Trigger: {json.dumps(data.get('trigger') or {}, ensure_ascii=False)}")
    parts.append(f"Stage: {data.get('stage')}")
    if data.get("userSpeech"):
        parts.append(f"Player speech just now: {_normalize_short_text(data.get('userSpeech'), max_chars=240)}")
    return "\n".join(parts)


def _neko_lines(route_state: Any) -> list[str]:
    out = []
    log = route_state.get("game_dialog_log") if isinstance(route_state, dict) else None
    for d in log or []:
        if not isinstance(d, dict):
            continue
        if d.get("type") == "game_event":
            line = str(d.get("result_line") or "").strip()
        elif d.get("type") == "assistant":
            line = str(d.get("line") or "").strip()
        else:
            continue
        if line:
            out.append(line)
    return out


def _reply_to_speech(data: Dict[str, Any], route_state: Any) -> Optional[str]:
    """NEKO's reply when the log already ends with the player's speech and her answer to it."""
    from ._shared import _normalize_short_text

    raw_log = route_state.get("game_dialog_log") if isinstance(route_state, dict) else None
    log = [d for d in raw_log or [] if isinstance(d, dict)]
    # Compared whole (the game sends the whole speech): two speeches that only
    # share their first 240 characters are different speeches.
    speech = _normalize_short_text(data.get("userSpeech") or "", max_chars=0)
    if len(log) >= 2 and log[-1].get("type") == "assistant" and log[-2].get("type") == "user":
        prev = _normalize_short_text(log[-2].get("text") or "", max_chars=0)
        if speech and prev == speech:
            return str(log[-1].get("line") or "").strip() or None
    return None


def build_question(data: Dict[str, Any], route_state: Any, text: Dict[str, Any]):
    """The state and the one ``situation`` question for this stage, in the wording *text*."""
    from utils.decision_client import ChoiceQuestion

    parts = [_compact_state(data, route_state)]
    if _is_user_stage(data):
        reply = _reply_to_speech(data, route_state)
        parts.append(f"{text['reply']} {reply}" if reply else text["no_reply"])
        key = "us" if _prompt_type(data) == "rest" else "ur"
    else:
        parts.append(text["latest"])
        latest = _neko_lines(route_state)[-_LATEST_LINES:]
        parts.extend(f"- {line}" for line in latest or ["(none)"])
        key = "r" if _prompt_type(data) == "rest" else "s"
    return "\n".join(parts), {
        "situation": ChoiceQuestion(instructions=text[key + "_ins"], criteria=dict(text[key])),
    }


def _probabilities(answer: Any) -> Dict[str, float]:
    probs = {}
    for k, v in (getattr(answer, "probabilities", None) or {}).items():
        try:
            probs[k] = float(v or 0.0)
        except (TypeError, ValueError):
            continue
    # Without probabilities there is no threshold to read: the chat prompt decides.
    return probs


def _user_stage_options(data: Dict[str, Any]) -> frozenset:
    """The options of this game's user-stage question (the same in every wording)."""
    from config.prompts.prompts_soccer import SOCCER_PASSIVE_GUARD_DECISION_TEXT as text

    return frozenset(text["us" if _prompt_type(data) == "rest" else "ur"])


def action_from_answer(answer: Any, data: Dict[str, Any], route_state: Any = None) -> Optional[str]:
    """The recommended action for the ``situation`` answer at this stage, or ``None``."""
    probs = _probabilities(answer)
    if not any(p > 0 for p in probs.values()):
        # No probability on any option (none at all, or all zero): the chat prompt decides.
        return None
    if _is_user_stage(data):
        # Only this question's own options count (the rest question's "comfort"
        # says nothing in an ordinary game).
        options = _user_stage_options(data)
        own = {k: p for k, p in probs.items() if k in options and p > 0}
        if not own:
            return None
        helps = sum(p for k, p in own.items() if k in _SPEECH_HELPS)
        return CANCEL if helps >= CANCEL_THRESHOLD else OBSERVE
    stage8 = _stage_number(data.get("stage")) >= 8
    if _prompt_type(data) == "rest":
        mapping = {"recovered": CANCEL, "slightly_better": OBSERVE,
                   "still_low": PREPARE if stage8 else RESCUE, "neutral": OBSERVE}
    else:
        mapping = {"deliberate": CANCEL, "fighting": RESCUE,
                   "giving_up": PREPARE if stage8 else RESCUE, "one_slip": RESCUE, "neutral": OBSERVE}
    actions: Dict[str, float] = {}
    for k, p in probs.items():
        # Only this stage's own options count: probability on an option the
        # question does not have says nothing about the action.
        if k in mapping and p > 0:
            actions[mapping[k]] = actions.get(mapping[k], 0.0) + p
    ranked = sorted(((actions[a], a) for a in ACTIONS if a in actions), reverse=True)
    if not ranked:
        return None
    best = ranked[0][1]
    if best == PREPARE and _stage_number(data.get("stage")) < 8:
        return OBSERVE
    if best == PREPARE and _teaching(data, route_state):
        # A teaching game never opens the exit window (teachingNeverUsesPrompt).
        return OBSERVE
    return best


async def decide_passive_guard(
    data: Dict[str, Any],
    route_state: Any,
    *,
    chat_config: Dict[str, Any],
    language: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The guard's raw result from the decision question, or ``None`` to use the chat prompt.

    The result has the chat prompt's fields, for ``_normalize_passive_guard_result``.
    """
    from config.prompts.prompts_minigame_common import _normalize_prompt_lang
    from config.prompts.prompts_soccer import (
        SOCCER_PASSIVE_GUARD_DECISION_TEXT,
        SOCCER_PASSIVE_GUARD_REHOST_TEXT_ZH,
    )
    from utils.decision_client import ROUTE_CHAT, ROUTE_REHOST, ask_site_questions, decide_route
    from utils.decision_client.routing import SITE_SOCCER_GUARD
    from utils.config_manager import get_config_manager

    cm = get_config_manager()
    try:
        decision_config = await cm.aget_model_api_config("decision")
    except Exception:
        decision_config = None
    route = decide_route(SITE_SOCCER_GUARD, decision_config=decision_config, chat_config=chat_config)
    if route == ROUTE_CHAT:
        return None
    text = SOCCER_PASSIVE_GUARD_DECISION_TEXT
    if route == ROUTE_REHOST and _normalize_prompt_lang(language).startswith("zh"):
        # A Chinese UI was evaluated with the Chinese wording on chat models.
        text = SOCCER_PASSIVE_GUARD_REHOST_TEXT_ZH
    state, questions = build_question(data, route_state, text)
    answers = await ask_site_questions(
        SITE_SOCCER_GUARD,
        state=state,
        questions=questions,
        chat_config=chat_config,
        decision_config=decision_config,
        config_manager=cm,
        timeout=DECISION_TIMEOUT_SECONDS,
        call_type="game_passive_guard",
    )
    if not answers:
        return None
    situation = answers.get("situation")
    action = action_from_answer(situation, data, route_state)
    if action is None:
        return None
    return {
        "classification": str(getattr(situation, "value", "") or ""),
        "confidence": float(getattr(situation, "probability", None) or 0.0),
        "recommendedAction": action,
        "evidenceTags": ["decision"],
        "reasonForDebug": f"decision question ({route})",
    }
