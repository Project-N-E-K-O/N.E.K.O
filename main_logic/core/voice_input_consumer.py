"""Consumer-neutral contracts for final independent-ASR transcripts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from main_logic.asr_client.lifecycle_contracts import VoiceTurnToken


VoiceInputConsumerOwner: TypeAlias = Literal["game"]


@dataclass(frozen=True, slots=True)
class VoiceTranscriptEvent:
    """One SmartTurn-authorized logical transcript for an external consumer."""

    turn_token: VoiceTurnToken
    provider: str
    text: str


VoiceTranscriptCallback: TypeAlias = Callable[
    [VoiceTranscriptEvent],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class VoiceInputConsumerBinding:
    """An inert transcript target; MicLease remains the capture authority."""

    owner: VoiceInputConsumerOwner
    on_final: VoiceTranscriptCallback
    identity: object = field(default_factory=object, repr=False, compare=False)
