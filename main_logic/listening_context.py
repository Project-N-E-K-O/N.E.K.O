"""Validate a frontend observation against player authority, then carry it once.

No callback queue, history append, persistent field or inference call. The offline
client's ephemeral request view excludes this block from saved conversations and
therefore memory extraction. Ordinary user/assistant dialogue is unchanged.
"""

from dataclasses import replace
import math
import re
import time

from config.prompts.prompts_listening_context import (
    get_listening_context_text,
)
from main_logic.omni_offline_client import OmniOfflineClient, TurnContext
from services.chat_sync.source import is_chat_input_source
from utils.llm_client import HumanMessage
from utils.language_utils import get_global_language_full
from utils.tokenize import truncate_to_tokens

LISTENING_CONTEXT_MAX_TOKENS = 450
_EXPRESSION = re.compile(
    r"expressions/listening(?:-(joyful|tender|calm|sad|intense))?\.exp3\.json"
)


def _text(value, limit=120):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def listening_observation(manager, message, *, now=None):
    """Fail closed on stale/replaced playback, malformed packets or synthetic turns."""
    packet = message.get("listening_context")
    if (
        message.get("input_type") != "text"
        or not is_chat_input_source(message.get("source"))
        or not isinstance(packet, dict)
        or packet.get("version") != 1
    ):
        return None
    request_id = message.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or packet.get("request_id") != request_id
    ):
        return None
    state = getattr(manager, "_music_confirmed_state", None)
    owner = getattr(manager, "_music_playback_owner_key", None)
    if not isinstance(state, dict) or state.get("state") != "playing" or not owner:
        return None
    if packet.get("playback_id") != owner[1]:
        return None
    captured = packet.get("captured_at")
    current = time.time() if now is None else now
    if type(captured) not in (int, float) or not math.isfinite(captured):
        return None
    # A pause/resume also changes confirmed_at even if the song/playback ID is reused.
    if (
        not 0 <= current * 1000 - captured <= 30000
        or captured < state.get("confirmed_at", current) * 1000
    ):
        return None
    track = packet.get("track")
    if not isinstance(track, dict) or _text(track.get("name")) != _text(
        state.get("name")
    ):
        return None
    name = _text(state.get("name"))
    if not name:
        return None
    text = get_listening_context_text(get_global_language_full())
    lines = [text["playing"].format(title=name)]
    mood = packet.get("mood")
    if isinstance(mood, dict):
        confidence = mood.get("confidence")
        category = (
            text["moods"].get(mood.get("category"))
            if isinstance(mood.get("category"), str)
            else None
        )
        source = (
            text["sources"].get(mood.get("source"))
            if isinstance(mood.get("source"), str)
            else None
        )
        song_id = state.get("song_id")
        matching_track = not song_id or mood.get("track_id") == f"netease:{song_id}"
        if (
            matching_track
            and category
            and source
            and type(confidence) in (float, int)
            and 0.6 <= confidence <= 1
            and _text(mood.get("classifier_version"), 80)
        ):
            lines.append(text["mood"].format(source=source, mood=category))
        else:
            lines.append(text["unknown"])
    else:
        lines.append(text["unknown"])
    expression = packet.get("expression")
    facts = packet.get("facts")
    if (
        isinstance(expression, str)
        and _EXPRESSION.fullmatch(expression)
        and isinstance(facts, list)
    ):
        observed = [
            text["facts"][fact]
            for fact in dict.fromkeys(f for f in facts[:8] if isinstance(f, str))
            if fact in text["facts"]
        ]
        if observed:
            lines.append(
                text["observed"].format(
                    name=_text(manager.lanlan_name, 80),
                    facts=text["separator"].join(observed),
                )
            )
        else:
            lines.append(text["no_pose"])
    else:
        lines.append(text["unconfirmed"])
    return truncate_to_tokens(
        text["note"] + "\n" + "".join(lines), LISTENING_CONTEXT_MAX_TOKENS
    )


def attach_listening_context(manager, message, kwargs):
    """Called at actual text dispatch, after awaits; preserve other context blocks."""
    if not isinstance(manager.session, OmniOfflineClient):
        return
    text = listening_observation(manager, message)
    if not text:
        return
    context = kwargs.get("turn_context") or TurnContext()
    previous = getattr(context.ephemeral, "content", "")
    if not isinstance(previous, str):
        return
    kwargs["turn_context"] = replace(
        context,
        ephemeral=HumanMessage(
            content=(previous + "\n\n" if previous else "") + text,
            metadata={"turn_context": "ephemeral"},
        ),
    )
