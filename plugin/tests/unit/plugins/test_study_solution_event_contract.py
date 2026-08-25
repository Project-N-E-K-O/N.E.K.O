from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.study_companion._event_bus import StudyEvent, StudyEventBus
from plugin.plugins.study_companion.entry_communication_tutor_events import (
    _CommunicationTutorEventsMixin,
)
from plugin.plugins.study_companion.entry_neko_commands import _NekoCommandsMixin
from plugin.sdk.plugin import Ok


pytestmark = pytest.mark.unit


_ANALYSIS = "analysis-contract-sentinel"
_ANSWER = "answer-contract-sentinel"
_TRANSFER = "transfer-contract-sentinel"
_PROCESS = "process-must-never-be-narrated-sentinel"
_OCR = "ocr-must-never-be-forwarded-sentinel"
_IMAGE = "image-must-never-be-forwarded-sentinel"
_USER_ANSWER = "user-answer-must-never-be-forwarded-sentinel"
_SCORE = "score-must-never-be-forwarded-sentinel"


def _solution_event_payload() -> dict[str, Any]:
    return {
        "analysis": _ANALYSIS,
        "answer": _ANSWER,
        "transfer": _TRANSFER,
        "process": _PROCESS,
        "solution_process": _PROCESS,
        "ocr_text": _OCR,
        "image": _IMAGE,
        "user_answer": _USER_ANSWER,
        "score": _SCORE,
    }


class _RecordingCtx:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def push_message(self, **kwargs: Any) -> dict[str, bool]:
        self.messages.append(dict(kwargs))
        return {"submitted": True}


@pytest.mark.asyncio
async def test_solution_completed_uses_exact_active_reply_contract_and_safe_formatter() -> None:
    ctx = _RecordingCtx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(StudyEvent(name="solution_completed", payload=_solution_event_payload()))

    assert len(ctx.messages) == 1
    message = ctx.messages[0]
    assert message["visibility"] == ["chat"]
    assert message["ai_behavior"] == "respond"
    assert message["priority"] == 5
    assert message["source"] == "study_companion"
    assert len(message["parts"]) == 1
    assert message["parts"][0]["type"] == "text"

    narration_instruction = message["parts"][0]["text"]
    for expected in (_ANALYSIS, _ANSWER, _TRANSFER):
        assert expected in narration_instruction
    for forbidden in (_PROCESS, _OCR, _IMAGE, _USER_ANSWER, _SCORE):
        assert forbidden not in narration_instruction


@pytest.mark.asyncio
async def test_solution_completed_ignores_answer_cooldown_and_pending_respond_slot() -> None:
    ctx = _RecordingCtx()
    bus = StudyEventBus(plugin_ctx=ctx)
    bus._last_respond_at = 123.0
    bus._pending_respond_count = 1

    await bus.emit(
        StudyEvent(
            name="solution_completed",
            payload={
                "analysis": _ANALYSIS,
                "answer": _ANSWER,
                "transfer": _TRANSFER,
            },
        )
    )

    assert ctx.messages[0]["ai_behavior"] == "respond"
    assert bus._last_respond_at == 123.0
    assert bus._pending_respond_count == 1


class _RecordingBus:
    def __init__(
        self,
        *,
        accepted: bool = True,
        failure: Exception | None = None,
    ) -> None:
        self.accepted = accepted
        self.failure = failure
        self.events: list[StudyEvent] = []

    def schedule_emit(self, event: StudyEvent) -> object | None:
        self.events.append(event)
        if self.failure is not None:
            raise self.failure
        return object() if self.accepted else None


class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))


class _CommunicationHarness(_CommunicationTutorEventsMixin):
    def __init__(self, event_bus: _RecordingBus | None) -> None:
        self._event_bus = event_bus
        self.logger = _RecordingLogger()


def _narration_sections() -> dict[str, str]:
    return {
        "analysis": _ANALYSIS,
        "answer": _ANSWER,
        "transfer": _TRANSFER,
    }


@pytest.mark.asyncio
async def test_solution_communication_helper_emits_exactly_once_on_success() -> None:
    bus = _RecordingBus()
    harness = _CommunicationHarness(bus)

    scheduled = await harness._emit_solution_completed_event(_narration_sections())

    assert scheduled is True
    assert len(bus.events) == 1
    assert bus.events[0].name == "solution_completed"
    assert bus.events[0].payload == _narration_sections()


@pytest.mark.asyncio
async def test_solution_communication_helper_returns_false_without_bus() -> None:
    harness = _CommunicationHarness(None)

    scheduled = await harness._emit_solution_completed_event(_narration_sections())

    assert scheduled is False
    assert harness.logger.warnings == []


@pytest.mark.asyncio
async def test_solution_communication_helper_returns_false_when_queue_rejects() -> None:
    bus = _RecordingBus(accepted=False)
    harness = _CommunicationHarness(bus)

    scheduled = await harness._emit_solution_completed_event(_narration_sections())

    assert scheduled is False
    assert len(bus.events) == 1
    assert harness.logger.warnings == []


@pytest.mark.asyncio
async def test_solution_communication_helper_hides_content_when_scheduling_fails() -> None:
    bus = _RecordingBus(failure=RuntimeError("transport rejected the message"))
    harness = _CommunicationHarness(bus)

    scheduled = await harness._emit_solution_completed_event(_narration_sections())

    assert scheduled is False
    assert len(bus.events) == 1
    assert harness.logger.warnings
    rendered_logs = repr(harness.logger.warnings)
    for secret in (_ANALYSIS, _ANSWER, _TRANSFER, _PROCESS, _OCR):
        assert secret not in rendered_logs


@pytest.mark.asyncio
async def test_solution_communication_helper_does_not_wait_for_push_message() -> None:
    push_started = asyncio.Event()
    release_push = asyncio.Event()

    class _SlowCtx(_RecordingCtx):
        async def push_message(self, **kwargs: Any) -> dict[str, bool]:
            push_started.set()
            await release_push.wait()
            self.messages.append(dict(kwargs))
            return {"submitted": True}

    ctx = _SlowCtx()
    bus = StudyEventBus(plugin_ctx=ctx)
    harness = _CommunicationHarness(bus)

    scheduled = await harness._emit_solution_completed_event(_narration_sections())

    assert scheduled is True
    assert ctx.messages == []
    await asyncio.wait_for(push_started.wait(), timeout=1.0)
    assert ctx.messages == []

    release_push.set()
    await asyncio.wait_for(bus._queue.join(), timeout=1.0)
    assert len(ctx.messages) == 1
    await bus.stop_worker()


class _SnapshotPipeline:
    def __init__(self, text: str) -> None:
        self._text = text

    def capture_snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(text=self._text)


class _NekoCommandLogger:
    def exception(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        pass


class _ExplainCurrentHarness(_NekoCommandsMixin):
    def __init__(self, *, scheduled: bool) -> None:
        self._ocr_pipeline = _SnapshotPipeline(_OCR)
        self._scheduled = scheduled
        self.logger = _NekoCommandLogger()
        self.explain_calls: list[str] = []
        self.pushed_messages: list[dict[str, Any]] = []

    async def study_explain_text(self, *, text: str) -> Ok:
        self.explain_calls.append(text)
        return Ok(
            {
                "reply": (
                    f"## 题目解析\n{_ANALYSIS}\n\n"
                    f"## 解题过程\n{_PROCESS}\n\n"
                    f"## 答案\n{_ANSWER}\n\n"
                    f"## 举一反三\n{_TRANSFER}"
                ),
                "solution_narration_scheduled": self._scheduled,
            }
        )

    async def _push_neko_command_message(self, **kwargs: Any) -> None:
        self.pushed_messages.append(dict(kwargs))


@pytest.mark.asyncio
async def test_explain_current_does_not_push_a_second_explanation_when_scheduled() -> None:
    harness = _ExplainCurrentHarness(scheduled=True)

    await harness._handle_neko_explain_current({})

    assert harness.explain_calls == [_OCR]
    assert harness.pushed_messages == []


@pytest.mark.asyncio
async def test_explain_current_uses_only_short_safe_notice_when_not_scheduled() -> None:
    harness = _ExplainCurrentHarness(scheduled=False)

    await harness._handle_neko_explain_current({})

    assert harness.explain_calls == [_OCR]
    assert len(harness.pushed_messages) == 1
    pushed = harness.pushed_messages[0]
    assert pushed["ai_behavior"] == "respond"
    assert pushed["priority"] == 5
    assert len(pushed["text"]) <= 120
    for forbidden in (_OCR, _ANALYSIS, _PROCESS, _ANSWER, _TRANSFER):
        assert forbidden not in pushed["text"]
