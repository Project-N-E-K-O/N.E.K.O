"""Built-in adapter for the ordinary Core chat voice-input path."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from main_logic.voice_turn.contracts import (
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)


@dataclass(frozen=True, slots=True)
class CoreChatTurnContext:
    """Core resources pinned when one ASR turn is prepared."""

    token: VoiceTurnToken
    external_turn_id: str
    session_ref: object
    source_game_route_identity: tuple[str, str, str] | None = None


@dataclass(slots=True)
class CoreChatVoiceInputConsumer:
    """Keep Core turn cleanup precise across route and session changes."""

    session_ref: Callable[[], object | None]
    game_route_identity: Callable[[], tuple[str, str, str] | None]
    on_prepare: Callable[
        [VoiceTurnToken, CoreChatTurnContext],
        Awaitable[bool],
    ]
    on_partial_event: Callable[[VoicePartialEvent], Awaitable[None]]
    on_final_event: Callable[
        [VoiceTranscriptEvent, CoreChatTurnContext],
        Awaitable[None],
    ]
    on_cancelled_event: Callable[
        [CoreChatTurnContext, str],
        Awaitable[None],
    ]
    _prepared: dict[VoiceTurnToken, CoreChatTurnContext] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def is_available(self) -> bool:
        # Core is the manager-owned default route. Session readiness remains a
        # prepare-time fence so native PCM ingress and __new__-constructed test
        # managers do not become unavailable merely because no turn can be
        # prepared yet.
        return True

    async def prepare_turn(self, token: VoiceTurnToken) -> bool:
        if token in self._prepared:
            return False
        session_ref = self.session_ref()
        if session_ref is None:
            return False
        context = CoreChatTurnContext(
            token=token,
            external_turn_id=(
                f"asr-{token.ingress.session_epoch}-{token.turn_id}"
            ),
            session_ref=session_ref,
            source_game_route_identity=self.game_route_identity(),
        )
        self._prepared[token] = context
        accepted = bool(await self.on_prepare(token, context))
        # A rejected or failed prepare is terminally cleaned up by the
        # registry's keyed on_cancelled callback. Retain the captured session
        # context until then so a partially prepared Core turn cannot remain
        # paused merely because the prepare result was false.
        return accepted and self._prepared.get(token) is context

    async def on_partial(self, event: VoicePartialEvent) -> None:
        if event.turn_token not in self._prepared:
            return
        await self.on_partial_event(event)

    async def on_final(self, event: VoiceTranscriptEvent) -> None:
        context = self._prepared.pop(event.turn_token, None)
        if context is None:
            return
        await self.on_final_event(event, context)

    async def on_cancelled(self, token: VoiceTurnToken, reason: str) -> None:
        context = self._prepared.pop(token, None)
        if context is None:
            return
        await self.on_cancelled_event(context, reason)
