from __future__ import annotations

import pytest

from plugin.plugins.study_companion.voice_contracts import (
    arbitrate_voice_transcript_results,
)

pytestmark = pytest.mark.plugin_unit


def test_voice_transcript_arbitration_prefers_cancel_response() -> None:
    result = arbitrate_voice_transcript_results(
        [
            {
                "plugin_id": "math_helper",
                "event_id": "voice_math",
                "success": True,
                "result": {
                    "action": "prime_context",
                    "context": "explain this",
                    "priority": 100,
                },
            },
            {
                "plugin_id": "study_companion",
                "event_id": "handle_transcript",
                "success": True,
                "result": {
                    "action": "cancel_response",
                    "reason": "ocr_overlap",
                    "priority": -10,
                },
            },
        ]
    )

    assert result["action"] == "cancel_response"
    assert result["reason"] == "ocr_overlap"
    assert result["priority"] == -10.0
    assert result["skipped"] is False
    assert result["source_plugin"] == "study_companion"
    assert result["source_event_id"] == "handle_transcript"


def test_voice_transcript_arbitration_orders_prime_context_by_priority() -> None:
    result = arbitrate_voice_transcript_results(
        [
            {
                "plugin_id": "low",
                "event_id": "low_handler",
                "success": True,
                "result": {
                    "action": "prime_context",
                    "context": "low context",
                    "priority": 1,
                },
            },
            {
                "plugin_id": "high",
                "event_id": "high_handler",
                "success": True,
                "result": {
                    "action": "prime_context",
                    "context": "high context",
                    "priority": "5",
                    "skipped": True,
                },
            },
        ]
    )

    assert result["action"] == "prime_context"
    assert result["context"] == "high context"
    assert result["priority"] == 5.0
    assert result["skipped"] is True
    assert result["source_plugin"] == "high"
    assert result["source_event_id"] == "high_handler"


def test_voice_transcript_arbitration_all_noop_continues_ordinary_flow() -> None:
    result = arbitrate_voice_transcript_results(
        [
            {
                "plugin_id": "alpha",
                "event_id": "a",
                "success": True,
                "result": {"action": "noop", "reason": "not_matched"},
            },
            {
                "plugin_id": "beta",
                "event_id": "b",
                "success": False,
                "error": "timeout",
            },
        ]
    )

    assert result == {
        "action": "noop",
        "reason": "all_noop",
        "priority": 0.0,
        "skipped": False,
        "handlers": 1,
        "failures": 1,
    }
