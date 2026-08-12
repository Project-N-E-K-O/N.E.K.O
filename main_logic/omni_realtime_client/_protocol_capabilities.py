# -- coding: utf-8 --
"""Resolve provider-specific Realtime lifecycle capabilities.

Routes select a protocol profile; they don't directly change arbiter policy.
Unknown routes stay on the strict OpenAI-compatible lifecycle so a provider
must be explicitly verified before content events can stand in for a missing
``response.created`` announcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from utils.tts.native_voice_registry import (
    is_free_lanlan_app_route,
    is_free_lanlan_tech_route,
)


# Only events that prove actual model output may replace a missing
# response.created on a capability-approved route. Boundary-only events such
# as output_item.added are intentionally excluded: allocating an item is not
# proof that the response itself has started producing content.
ID_BEARING_RESPONSE_CONTENT_EVENT_TYPES = frozenset(
    {
        "response.text.delta",
        "response.output_text.delta",
        "response.audio.delta",
        "response.output_audio.delta",
        "response.audio_transcript.delta",
        "response.output_audio_transcript.delta",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
    }
)


class ResponseStartEvidence(str, Enum):
    """Evidence the arbiter may accept as proof an owned response started."""

    ANNOUNCEMENT_ONLY = "response_created_only"
    ANNOUNCEMENT_OR_ID_BEARING_CONTENT = "response_created_or_id_bearing_content"


@dataclass(frozen=True, slots=True)
class RealtimeProtocolCapabilities:
    """Immutable capabilities for one resolved Realtime route."""

    route_key: str
    response_start_evidence: ResponseStartEvidence

    @property
    def accepts_id_bearing_content_start(self) -> bool:
        return (
            self.response_start_evidence
            is ResponseStartEvidence.ANNOUNCEMENT_OR_ID_BEARING_CONTENT
        )


STRICT_REALTIME_PROTOCOL_CAPABILITIES = RealtimeProtocolCapabilities(
    route_key="strict_default",
    response_start_evidence=ResponseStartEvidence.ANNOUNCEMENT_ONLY,
)

LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES = RealtimeProtocolCapabilities(
    route_key="lanlan_app_gemini",
    response_start_evidence=(
        ResponseStartEvidence.ANNOUNCEMENT_OR_ID_BEARING_CONTENT
    ),
)

LANLAN_TECH_REALTIME_PROTOCOL_CAPABILITIES = RealtimeProtocolCapabilities(
    route_key="lanlan_tech_stepfun",
    response_start_evidence=ResponseStartEvidence.ANNOUNCEMENT_ONLY,
)


def resolve_realtime_protocol_capabilities(
    api_type: str | None,
    realtime_base_url: str | None,
) -> RealtimeProtocolCapabilities:
    """Return the verified lifecycle profile for a concrete route."""

    if is_free_lanlan_app_route(api_type, realtime_base_url):
        return LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES
    if is_free_lanlan_tech_route(api_type, realtime_base_url):
        return LANLAN_TECH_REALTIME_PROTOCOL_CAPABILITIES
    return STRICT_REALTIME_PROTOCOL_CAPABILITIES
