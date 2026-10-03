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
    ExternalRouteKind,
    external_route_identity,
    get_active_external_route,
    is_external_route_active,
    route_external_voice_transcript,
    same_external_route_owner,
)
from utils.game_route_state import (
    get_active_game_route_generation_identity as get_active_game_route_identity,
)


def _pinnable_external_route(
    lanlan_name: str,
) -> tuple[ExternalRouteKind, str | None] | None:
    """Registry identity of the active non-game route that takes voice turns.

    The registry decides what can be pinned: an identity that does not even
    match itself (no usable instance id) is not pinnable, so a turn is never
    delivered to an instance the registry's own re-checks could not tell apart.
    """
    identity = external_route_identity(lanlan_name)
    if identity is None:
        return None
    route = identity[0]
    if route.kind == "game" or route.route_voice_transcript is None:
        return None
    if not same_external_route_owner(identity, identity):
        return None
    return identity


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
    _prepared_external_kinds: dict[VoiceTurnToken, tuple[ExternalRouteKind, str | None]] = field(
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
        pinned = _pinnable_external_route(lanlan_name)
        if pinned is None:
            return False
        self._prepared_external_kinds[token] = pinned
        return True

    async def on_partial(self, event: VoicePartialEvent) -> None:
        del event

    async def on_final(self, event: VoiceTranscriptEvent) -> None:
        token = event.turn_token
        request_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
        pinned = self._prepared_external_kinds.pop(token, None)
        if pinned is not None:
            lanlan_name = self.lanlan_name()
            if not same_external_route_owner(pinned, external_route_identity(lanlan_name)):
                raise RuntimeError("GAME_VOICE_TRANSCRIPT_NOT_ROUTED")
            routed = await route_external_voice_transcript(
                lanlan_name,
                event.text,
                request_id=request_id,
                route_instance=pinned[1],
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
