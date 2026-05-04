"""Shared game-route state primitives.

Lives in ``utils/`` so that ``main_logic/`` can read route state and dispatch
voice transcripts into the active route without importing from
``main_routers/`` (which would invert the framework layering: lower-level
domain logic should not depend on the HTTP router layer).

``main_routers/game_router.py`` owns the heavy lifecycle (LLM dispatch,
finalize, archive, organizer). This module only holds:

- the global ``_game_route_states`` container
- the small read helpers used by both layers
- a registration hook so ``game_router`` can plug its voice-transcript
  handler in at module load, and ``main_logic/core.py`` can call the
  generic ``route_external_voice_transcript()`` without taking a
  reverse-direction import on ``main_routers``.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional


_game_route_states: Dict[str, dict] = {}


def _route_state_key(lanlan_name: str, game_type: str) -> str:
    return f"{lanlan_name}:{game_type}"


def _get_active_game_route_state(
    lanlan_name: str,
    game_type: str | None = None,
) -> dict | None:
    if game_type:
        state = _game_route_states.get(_route_state_key(lanlan_name, game_type))
        return state if state and state.get("game_route_active") else None
    for key, state in _game_route_states.items():
        if key.startswith(f"{lanlan_name}:") and state.get("game_route_active"):
            return state
    return None


def is_game_route_active(lanlan_name: str, game_type: str | None = None) -> bool:
    """True iff a game route for (lanlan_name, [game_type]) is currently active."""
    return _get_active_game_route_state(lanlan_name, game_type) is not None


_VoiceTranscriptHandler = Callable[..., Awaitable[bool]]
_voice_transcript_handler: Optional[_VoiceTranscriptHandler] = None


def register_voice_transcript_handler(handler: _VoiceTranscriptHandler) -> None:
    """Plug in the heavy voice-transcript handler from ``main_routers/game_router``.

    Called once at module load by ``main_routers.game_router``; allows
    ``main_logic/core.py`` to dispatch voice transcripts via the generic
    ``route_external_voice_transcript`` below without taking a
    ``main_logic → main_routers`` import.
    """
    global _voice_transcript_handler
    _voice_transcript_handler = handler


async def route_external_voice_transcript(
    lanlan_name: str,
    transcript: str,
    *,
    request_id: str | None = None,
    game_type: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Dispatch a voice transcript into the active game route, if any.

    Returns ``True`` iff the registered handler claimed the transcript.
    Returns ``False`` if no handler is registered (e.g. game_router never
    imported in this process) or no active route matched.
    """
    handler = _voice_transcript_handler
    if handler is None:
        return False
    return bool(await handler(
        lanlan_name,
        transcript,
        request_id=request_id,
        game_type=game_type,
        session_id=session_id,
    ))
