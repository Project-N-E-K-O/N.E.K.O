"""Built-in adapter for the ordinary Core chat voice-input path."""

from __future__ import annotations

from dataclasses import dataclass

from main_logic.voice_turn.contracts import (
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)

from ..contracts import (
    ConsumerCancelledCallback,
    ConsumerFinalCallback,
    ConsumerPartialCallback,
    ConsumerPrepareCallback,
)


@dataclass(slots=True)
class CoreChatVoiceInputConsumer:
    on_prepare: ConsumerPrepareCallback
    on_partial_event: ConsumerPartialCallback
    on_final_event: ConsumerFinalCallback
    on_cancelled_event: ConsumerCancelledCallback

    def is_available(self) -> bool:
        return True

    async def prepare_turn(self, token: VoiceTurnToken) -> bool:
        return bool(await self.on_prepare(token))

    async def on_partial(self, event: VoicePartialEvent) -> None:
        await self.on_partial_event(event)

    async def on_final(self, event: VoiceTranscriptEvent) -> None:
        await self.on_final_event(event)

    async def on_cancelled(self, token: VoiceTurnToken, reason: str) -> None:
        await self.on_cancelled_event(token, reason)
