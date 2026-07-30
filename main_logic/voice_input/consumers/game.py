"""Built-in adapter for the active game-route voice consumer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from main_logic.voice_turn.contracts import (
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from utils.game_route_state import (
    get_active_game_route_identity,
    is_game_route_active,
    route_external_voice_transcript,
)


@dataclass(slots=True)
class GameVoiceInputConsumer:
    """Deliver identified non-empty finals to the existing game route."""

    lanlan_name: Callable[[], str]
    _prepared_routes: dict[VoiceTurnToken, tuple[str, str]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def is_available(self) -> bool:
        return is_game_route_active(self.lanlan_name())

    async def prepare_turn(self, token: VoiceTurnToken) -> bool:
        if token in self._prepared_routes:
            return False
        identity = get_active_game_route_identity(self.lanlan_name())
        if identity is None:
            return False
        self._prepared_routes[token] = identity
        return True

    async def on_partial(self, event: VoicePartialEvent) -> None:
        del event

    async def on_final(self, event: VoiceTranscriptEvent) -> None:
        token = event.turn_token
        route_identity = self._prepared_routes.pop(token, None)
        if route_identity is None:
            raise RuntimeError("GAME_VOICE_TURN_NOT_PREPARED")
        game_type, session_id = route_identity
        routed = await route_external_voice_transcript(
            self.lanlan_name(),
            event.text,
            request_id=f"asr-{token.ingress.session_epoch}-{token.turn_id}",
            game_type=game_type,
            session_id=session_id,
        )
        if not routed:
            raise RuntimeError("GAME_VOICE_TRANSCRIPT_NOT_ROUTED")

    async def on_cancelled(self, token: VoiceTurnToken, reason: str) -> None:
        self._prepared_routes.pop(token, None)
        del reason
