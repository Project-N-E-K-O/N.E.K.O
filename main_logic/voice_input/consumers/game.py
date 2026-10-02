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
        return is_external_route_active(self.lanlan_name())

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
        route = get_active_external_route(lanlan_name)
        if (
            route is None
            or route.kind == "game"
            or route.route_voice_transcript is None
            or route.current_instance is None
        ):
            return False
        instance = route.current_instance(lanlan_name)
        if not instance:
            return False
        self._prepared_external_kinds[token] = (route.kind, str(instance))
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
            route = get_active_external_route(lanlan_name)
            if (
                route is None
                or route.kind != external_kind
                or route.current_instance is None
                or str(route.current_instance(lanlan_name) or "") != external_instance
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
