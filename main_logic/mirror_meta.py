"""Schema + detection helpers for the SessionManager *mirror* channel.

A "mirror" message is external content that occupies the chat-bubble
position (looks like a normal AI reply) but is **not** produced by the
chat LLM.  The chat LLM does not see / generate / drive these messages;
their text comes from an external controller (current consumer:
``main_routers/game_router``; future consumers: any other module that
wants verbatim AI-side rendering, e.g. plugins delivering a fixed
notification with ``visibility=["chat"]`` + ``ai_behavior="blind"``).

vs the existing axes:

- ``respond`` (LLM-rephrase + reply): chat bubble, LLM in loop
- ``read`` / passive: chat bubble (delayed), LLM in loop
- ``passthrough`` (HUD blind): HUD toast only, LLM not in loop, no
  chat bubble
- **mirror** (this module): **chat bubble**, LLM not in loop; enters
  ordinary chat history as ``AIMessage`` so future chat turns keep
  natural continuity (sub-controls below can suppress that)

Sub-control: an event payload may carry per-source memory flags (e.g.
soccer game-memory toggles) that decide whether the mirror line should
also be filtered out of *ordinary* chat history.  When filtered out the
mirror line still reaches the live frontend bubble — the filter only
controls the cross_server → ordinary-memory pipeline, not visual
display.

The schema lives in ``main_logic`` (not ``utils``) because consumers are
all in ``main_logic`` / ``main_routers``; keeping it next to
``cross_server.py`` and ``core.py`` avoids a reverse import.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def build_mirror_meta(
    *,
    source: str,
    kind: str,
    session_id: str,
    event: Optional[dict] = None,
) -> Dict[str, Any]:
    """Construct the metadata dict embedded into mirror messages.

    ``kind`` is the external-controller label (data, not prompt — e.g.
    ``"soccer"``); per the cross-module-prompt rule, prompt text should
    stay generic but data fields like this can carry specifics.
    """
    return {
        "source": source,
        "kind": kind,
        "session_id": session_id,
        "mirror": {
            "kind": kind,
            "session_id": session_id,
            "event": event if isinstance(event, dict) else {},
        },
    }


def _payload_bool_from_keys(data: dict, *keys: str) -> Optional[bool]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
    return None


def is_mirror_event_memory_disabled(event: dict) -> bool:
    """Whether a mirror event's payload says it should be filtered from
    ordinary chat memory."""
    has_user_input = event.get("hasUserSpeech") is True or event.get("hasUserText") is True
    if has_user_input:
        player_interaction_enabled = _payload_bool_from_keys(
            event,
            "soccer_game_memory_player_interaction_enabled",
            "soccerGameMemoryPlayerInteractionEnabled",
        )
        if player_interaction_enabled is not None:
            return player_interaction_enabled is False
    else:
        event_reply_enabled = _payload_bool_from_keys(
            event,
            "soccer_game_memory_event_reply_enabled",
            "soccerGameMemoryEventReplyEnabled",
        )
        if event_reply_enabled is not None:
            return event_reply_enabled is False

    legacy_enabled = _payload_bool_from_keys(event, "game_memory_enabled", "gameMemoryEnabled")
    if legacy_enabled is not None:
        return legacy_enabled is False
    return not has_user_input


def is_mirror_assistant_message(data: dict) -> bool:
    """``True`` for mirrored chat-bubble assistant lines that should be
    filtered from ordinary memory per their event policy."""
    if not isinstance(data, dict) or data.get("type") != "gemini_response":
        return False
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return False
    mirror = metadata.get("mirror")
    if not isinstance(mirror, dict):
        return False
    event = mirror.get("event")
    if not isinstance(event, dict):
        event = {}
    return is_mirror_event_memory_disabled(event)


def is_mirror_turn_end_meta(meta: Optional[dict]) -> bool:
    """``True`` for a mirror turn-end whose payload says skip ordinary
    memory pipeline."""
    if not isinstance(meta, dict):
        return False
    mirror = meta.get("mirror")
    if not isinstance(mirror, dict):
        return False
    event = mirror.get("event")
    if not isinstance(event, dict):
        return False
    return is_mirror_event_memory_disabled(event)


# Sentinel input_type values used by mirror_user_input — cross_server
# detects these to skip chat_history flush (mirror-side user inputs do
# NOT enter chat_history as UserMessage; they belong to the external
# controller's transcript log, not the chat LLM's perspective).
MIRROR_USER_TEXT_INPUT_TYPE = "mirror_text"
MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE = "mirror_voice_transcript"

MIRROR_USER_INPUT_TYPES = frozenset({
    MIRROR_USER_TEXT_INPUT_TYPE,
    MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE,
})
