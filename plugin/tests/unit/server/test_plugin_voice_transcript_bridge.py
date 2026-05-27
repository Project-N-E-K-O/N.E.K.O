from __future__ import annotations

import pytest

from plugin.server.application.plugins import voice_transcript_bridge

pytestmark = pytest.mark.plugin_unit


class _DispatchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def trigger_arbitrated_custom_event(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {
            "action": "prime_context",
            "context": "screen context",
            "source_plugin": "study_companion",
        }


@pytest.mark.asyncio
async def test_resolve_voice_transcript_request_returns_noop_for_empty_text() -> None:
    result = await voice_transcript_bridge.resolve_voice_transcript_request(
        {"transcript": "   "}
    )

    assert result == {"action": "noop", "reason": "empty_transcript"}


@pytest.mark.asyncio
async def test_resolve_voice_transcript_request_dispatches_arbitrated_event() -> None:
    dispatch_service = _DispatchService()

    result = await voice_transcript_bridge.resolve_voice_transcript_request(
        {
            "transcript": "  Yui explain this step  ",
            "lanlan_name": "Yui",
            "metadata": {"session_id": "s1"},
        },
        dispatch_service=dispatch_service,  # type: ignore[arg-type]
        timeout=0.25,
    )

    assert result == {
        "action": "prime_context",
        "context": "screen context",
        "source_plugin": "study_companion",
    }
    assert dispatch_service.calls == [
        {
            "event_type": "voice_transcript",
            "args": {
                "transcript": "Yui explain this step",
                "lanlan_name": "Yui",
                "metadata": {"session_id": "s1"},
            },
            "timeout": 0.25,
        }
    ]


@pytest.mark.asyncio
async def test_resolve_voice_transcript_request_drops_invalid_metadata() -> None:
    dispatch_service = _DispatchService()

    await voice_transcript_bridge.resolve_voice_transcript_request(
        {
            "transcript": "Yui explain this step",
            "lanlan_name": "Yui",
            "metadata": "bad",
        },
        dispatch_service=dispatch_service,  # type: ignore[arg-type]
        timeout=0.25,
    )

    assert dispatch_service.calls[0]["args"] == {
        "transcript": "Yui explain this step",
        "lanlan_name": "Yui",
        "metadata": {},
    }
