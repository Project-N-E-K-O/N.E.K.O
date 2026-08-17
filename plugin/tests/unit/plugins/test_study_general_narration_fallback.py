from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.study_companion.entry_communication_review_events import (
    _CommunicationReviewEventsMixin,
)
from plugin.plugins.study_companion.entry_communication_tutor_events import (
    _CommunicationTutorEventsMixin,
)
from plugin.plugins.study_companion.entry_tutor_explain_entries import (
    _TutorExplainEntriesMixin,
)
from plugin.plugins.study_companion.models import TutorReply
from plugin.sdk.plugin import Ok


pytestmark = pytest.mark.unit


_PNG_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)
_DEFAULT_EVENT_BUS = object()


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))


class _EventBus:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("event delivery failed")


class _TutorAgent:
    def __init__(self, *, reply: str, degraded: bool = False) -> None:
        self.reply = reply
        self.degraded = degraded

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        return TutorReply(
            operation="concept_explain",
            input_text=text,
            reply=self.reply,
            degraded=self.degraded,
            diagnostic="timeout" if self.degraded else "",
            created_at="2026-08-13T00:00:00Z",
        )


class _ExplainHarness(
    _CommunicationReviewEventsMixin,
    _TutorExplainEntriesMixin,
    _CommunicationTutorEventsMixin,
):
    def __init__(
        self,
        *,
        reply: str = "A safe literary discussion.",
        response_mode: str = "unknown",
        semantic_status: str = "routing_unavailable",
        semantic_reason: str = "timeout",
        current_question: dict[str, Any] | None = None,
        degraded: bool = False,
        communication_enabled: bool = True,
        general_narration_enabled: bool = True,
        event_bus: _EventBus | None | object = _DEFAULT_EVENT_BUS,
    ) -> None:
        self._cfg = SimpleNamespace(
            language="en",
            llm_vision_enabled=True,
            communication=SimpleNamespace(
                enabled=communication_enabled,
                solution_narration_enabled=True,
                general_narration_enabled=general_narration_enabled,
            ),
        )
        self._state = SimpleNamespace(
            active_mode="companion",
            last_ocr_text="PRIVATE_OCR_SENTINEL",
        )
        self._lock = asyncio.Lock()
        self._agent = _TutorAgent(reply=reply, degraded=degraded)
        self._event_bus = _EventBus() if event_bus is _DEFAULT_EVENT_BUS else event_bus
        self.logger = _Logger()
        self.response_mode = response_mode
        self.semantic_status = semantic_status
        self.semantic_reason = semantic_reason
        self.current_question = dict(current_question or {})

    async def _build_learning_context(
        self,
        operation: str,
        *,
        input_text: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "input_text": input_text,
            "study_response_mode": self.response_mode,
            "study_semantic_status": self.semantic_status,
            "study_semantic_reason": self.semantic_reason,
            "current_question": dict(self.current_question),
            **dict(extra or {}),
        }

    async def _finalize_tutor_call(
        self,
        _operation: str,
        reply: TutorReply,
        *,
        extra_context: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        context = dict(extra_context or {})
        return {
            "reply": reply.reply,
            "summary": reply.reply,
            "degraded": reply.degraded,
            "diagnostic": reply.diagnostic,
            "study_response_mode": context.get("study_response_mode", "unknown"),
            "study_semantic_status": context.get("study_semantic_status", ""),
            "study_semantic_reason": context.get("study_semantic_reason", ""),
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "semantic_reason",
    ["timeout", "call_failed", "invalid_response"],
)
async def test_routing_unavailable_unknown_schedules_one_general_fallback(
    semantic_reason: str,
) -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(semantic_reason=semantic_reason, event_bus=bus)

    result = await plugin.study_explain_text(text="Discuss War and Peace")

    assert isinstance(result, Ok)
    assert result.value["study_response_mode"] == "unknown"
    assert result.value["study_semantic_status"] == "routing_unavailable"
    assert result.value["study_semantic_reason"] == semantic_reason
    assert result.value["general_narration_response_mode"] == "general_fallback"
    assert result.value["general_narration_scheduled"] is True
    assert result.value["general_narration_status"] == "scheduled"
    assert len(bus.events) == 1
    assert bus.events[0].name == "general_response_completed"
    assert bus.events[0].payload == {
        "response_mode": "general_fallback",
        "content": "A safe literary discussion.",
    }


@pytest.mark.asyncio
async def test_general_fallback_rechecks_toggle_after_finalize() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(event_bus=bus)
    original_finalize = plugin._finalize_tutor_call

    async def _finalize_and_disable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = await original_finalize(*args, **kwargs)
        plugin._cfg = SimpleNamespace(
            language="en",
            llm_vision_enabled=True,
            communication=SimpleNamespace(
                enabled=True,
                solution_narration_enabled=True,
                general_narration_enabled=False,
            ),
        )
        return payload

    plugin._finalize_tutor_call = _finalize_and_disable  # type: ignore[method-assign]

    result = await plugin.study_explain_text(text="Disable narration while finalizing")

    assert isinstance(result, Ok)
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "disabled"
    assert result.value["general_narration_reason"] == "general_narration_disabled"
    assert bus.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic_status", ["available", "low_confidence"])
async def test_other_unknown_sources_do_not_use_general_fallback(
    semantic_status: str,
) -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(semantic_status=semantic_status, event_bus=bus)

    result = await plugin.study_explain_text(text="Please explain this")

    assert isinstance(result, Ok)
    assert result.value["study_response_mode"] == "unknown"
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_response_mode"] == "unknown"
    assert bus.events == []


@pytest.mark.asyncio
async def test_trusted_current_question_never_uses_general_fallback() -> None:
    question = "What is the derivative of x squared?"
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply="A natural answer without solution headings.",
        current_question={"question": question},
        event_bus=bus,
    )

    result = await plugin.study_explain_text(text=question)

    assert isinstance(result, Ok)
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_response_mode"] == "unknown"
    assert all(event.name != "general_response_completed" for event in bus.events)


@pytest.mark.asyncio
async def test_fallback_event_contains_only_cleaned_bounded_reply() -> None:
    question = "PRIVATE_QUESTION_SENTINEL"
    diagnostic = "study_semantic_status=routing_unavailable"
    visible = "The novel contrasts private life with historical change."
    reply = f"# Discussion\n{visible}\n{diagnostic}\n" + "x" * 2000
    bus = _EventBus()
    plugin = _ExplainHarness(reply=reply, event_bus=bus)

    result = await plugin.study_explain_text(
        text=question,
        vision_image_base64=_PNG_IMAGE_BASE64,
    )

    assert isinstance(result, Ok)
    assert len(bus.events) == 1
    payload = bus.events[0].payload
    assert set(payload) == {"response_mode", "content"}
    assert payload["response_mode"] == "general_fallback"
    assert visible in payload["content"]
    assert len(payload["content"]) <= 1600
    serialized = repr(payload)
    assert question not in serialized
    assert "PRIVATE_OCR_SENTINEL" not in serialized
    assert _PNG_IMAGE_BASE64 not in serialized
    assert diagnostic not in serialized


@pytest.mark.asyncio
async def test_fallback_delivery_failure_keeps_visible_reply() -> None:
    reply = "The visible answer must survive event delivery failure."
    bus = _EventBus(fail=True)
    plugin = _ExplainHarness(reply=reply, event_bus=bus)

    result = await plugin.study_explain_text(text="Discuss a novel")

    assert isinstance(result, Ok)
    assert result.value["reply"] == reply
    assert result.value["study_response_mode"] == "unknown"
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "delivery_failed"
    assert result.value["general_narration_reason"] == "event_delivery_failed"
    assert len(bus.events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness_overrides", "expected_status", "expected_reason"),
    [
        (
            {"communication_enabled": False},
            "disabled",
            "communication_disabled",
        ),
        (
            {"general_narration_enabled": False},
            "disabled",
            "general_narration_disabled",
        ),
        ({"degraded": True}, "degraded", "degraded_reply"),
        ({"reply": ""}, "not_applicable", "empty_reply"),
        ({"event_bus": None}, "runtime_unavailable", "event_bus_unavailable"),
    ],
)
async def test_fallback_preserves_existing_narration_guards(
    harness_overrides: dict[str, Any],
    expected_status: str,
    expected_reason: str,
) -> None:
    plugin = _ExplainHarness(**harness_overrides)

    result = await plugin.study_explain_text(text="Discuss a novel")

    assert isinstance(result, Ok)
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == expected_status
    assert result.value["general_narration_reason"] == expected_reason
