from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plugin.server.application.plugins.dispatch_service import PluginDispatchService
from plugin.server.application.plugins.event_contracts import (
    VOICE_TRANSCRIPT_ACTION_NOOP,
    VOICE_TRANSCRIPT_EVENT_TYPE,
)

VOICE_TRANSCRIPT_DISPATCH_TIMEOUT_SECONDS = 1.0


def voice_transcript_noop(reason: str, **extra: object) -> dict[str, object]:
    return {
        "action": VOICE_TRANSCRIPT_ACTION_NOOP,
        "reason": str(reason or "noop"),
        **extra,
    }


def voice_transcript_event_has_text(event: Mapping[str, object] | None) -> bool:
    if not isinstance(event, Mapping):
        return False
    return bool(str(event.get("transcript") or "").strip())


voice_transcript_request_has_text = voice_transcript_event_has_text


def _args_from_bridge_event(event: Mapping[str, object]) -> dict[str, object]:
    metadata = event.get("metadata")
    return {
        "transcript": str(event.get("transcript") or "").strip(),
        "lanlan_name": str(event.get("lanlan_name") or ""),
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
    }


async def resolve_voice_transcript_request(
    event: Mapping[str, object] | None,
    *,
    dispatch_service: PluginDispatchService | None = None,
    timeout: float = VOICE_TRANSCRIPT_DISPATCH_TIMEOUT_SECONDS,
) -> dict[str, object]:
    if not isinstance(event, Mapping) or not voice_transcript_event_has_text(event):
        return voice_transcript_noop("empty_transcript")
    service = dispatch_service or PluginDispatchService()
    return await service.trigger_arbitrated_custom_event(
        event_type=VOICE_TRANSCRIPT_EVENT_TYPE,
        args=_args_from_bridge_event(event),
        timeout=timeout,
    )


__all__ = [
    "VOICE_TRANSCRIPT_DISPATCH_TIMEOUT_SECONDS",
    "resolve_voice_transcript_request",
    "voice_transcript_event_has_text",
    "voice_transcript_noop",
    "voice_transcript_request_has_text",
]
