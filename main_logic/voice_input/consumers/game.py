"""Built-in adapter for the active game-route voice consumer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from main_logic.voice_turn.contracts import (
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from utils.game_route_state import (
    is_game_route_active,
    route_external_voice_transcript,
)


@dataclass(slots=True)
class GameVoiceInputConsumer:
    lanlan_name: Callable[[], str]

    def is_available(self) -> bool:
        return is_game_route_active(self.lanlan_name())

    async def prepare_turn(self, token: VoiceTurnToken) -> bool:
        del token
        return self.is_available()

    async def on_partial(self, event: VoicePartialEvent) -> None:
        del event

    async def on_final(self, event: VoiceTranscriptEvent) -> None:
        token = event.turn_token
        await route_external_voice_transcript(
            self.lanlan_name(),
            event.text,
            request_id=(f"asr-{token.ingress.session_epoch}-{token.turn_id}"),
        )

    async def on_cancelled(self, token: VoiceTurnToken, reason: str) -> None:
        del token, reason
