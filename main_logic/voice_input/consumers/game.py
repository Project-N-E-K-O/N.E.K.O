"""Built-in adapter for the active external-route voice consumer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from main_logic.voice_turn.contracts import (
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from utils.external_route_registry import (
    get_active_external_route,
    is_external_route_active,
    route_external_voice_transcript,
)
from utils.game_route_state import (
    get_active_game_route_generation_identity as get_active_game_route_identity,
)


def _pinnable_external_route(lanlan_name: str):
    """The active non-game route that can receive this character's voice turns.

    It must take voice transcripts and report an instance id to pin the turn
    to; anything else is None.
    """
    route = get_active_external_route(lanlan_name)
    if (
        route is None
        or route.kind == "game"
        or route.route_voice_transcript is None
        or route.current_instance is None
        or not route.current_instance(lanlan_name)
    ):
        return None
    return route


@dataclass(slots=True)
class GameVoiceInputConsumer:
    """Deliver identified non-empty finals to the active external route.

    A game route is pinned by its ``(game_type, session_id, route instance)``
    identity, exactly as before the registry existed. Any other registered
    route kind that accepts voice transcripts and reports ``current_instance``
    is pinned by ``(kind, instance)``; the final is delivered only if that same
    instance still owns the character.
    """

    lanlan_name: Callable[[], str]
    _prepared_routes: dict[VoiceTurnToken, tuple[str, str, str]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _prepared_external_kinds: dict[VoiceTurnToken, tuple[str, str]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def is_available(self) -> bool:
        # Same conditions prepare_turn will accept: a pinnable game identity,
        # or an active non-game kind that takes voice transcripts per instance.
        lanlan_name = self.lanlan_name()
        if not is_external_route_active(lanlan_name):
            return False
        if get_active_game_route_identity(lanlan_name) is not None:
            return True
        return _pinnable_external_route(lanlan_name) is not None

    async def prepare_turn(self, token: VoiceTurnToken) -> bool:
        if token in self._prepared_routes or token in self._prepared_external_kinds:
            return False
        lanlan_name = self.lanlan_name()
        identity = get_active_game_route_identity(lanlan_name)
        if identity is not None:
            if len(identity) == 2:
                identity = (identity[0], identity[1], "")
            self._prepared_routes[token] = identity
            return True
        route = _pinnable_external_route(lanlan_name)
        if route is None:
            return False
        self._prepared_external_kinds[token] = (
            route.kind, str(route.current_instance(lanlan_name)),
        )
        return True

    async def on_partial(self, event: VoicePartialEvent) -> None:
        del event

    async def on_final(self, event: VoiceTranscriptEvent) -> None:
        token = event.turn_token
        request_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
        external_route = self._prepared_external_kinds.pop(token, None)
        if external_route is not None:
            external_kind, external_instance = external_route
            lanlan_name = self.lanlan_name()
            route = _pinnable_external_route(lanlan_name)
            if (
                route is None
                or route.kind != external_kind
                or str(route.current_instance(lanlan_name)) != external_instance
            ):
                raise RuntimeError("GAME_VOICE_TRANSCRIPT_NOT_ROUTED")
            routed = await route_external_voice_transcript(
                lanlan_name,
                event.text,
                request_id=request_id,
                route_instance=external_instance,
            )
            if not routed:
                raise RuntimeError("GAME_VOICE_TRANSCRIPT_NOT_ROUTED")
            return
        route_identity = self._prepared_routes.pop(token, None)
        if route_identity is None:
            raise RuntimeError("GAME_VOICE_TURN_NOT_PREPARED")
        # A game turn may only reach a game route: if another kind took over
        # since prepare, the registry would hand the stale game utterance to it.
        active_route = get_active_external_route(self.lanlan_name())
        if active_route is not None and active_route.kind != "game":
            raise RuntimeError("GAME_VOICE_TRANSCRIPT_NOT_ROUTED")
        game_type, session_id, route_instance_id = route_identity
        route_kwargs = {
            "request_id": request_id,
            "game_type": game_type,
            "session_id": session_id,
        }
        if route_instance_id:
            route_kwargs["sdk_route_instance_id"] = route_instance_id
        routed = await route_external_voice_transcript(
            self.lanlan_name(),
            event.text,
            **route_kwargs,
        )
        if not routed:
            raise RuntimeError("GAME_VOICE_TRANSCRIPT_NOT_ROUTED")

    async def on_cancelled(self, token: VoiceTurnToken, reason: str) -> None:
        self._prepared_routes.pop(token, None)
        self._prepared_external_kinds.pop(token, None)
        del reason
