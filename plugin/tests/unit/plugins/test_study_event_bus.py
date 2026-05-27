from __future__ import annotations

import asyncio
import logging

import pytest

from plugin.plugins.study_companion import _event_bus as event_bus_module
from plugin.plugins.study_companion._event_bus import StudyEvent, StudyEventBus


pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, object]] = []

    def push_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("push failed")
        self.messages.append(dict(kwargs))
        return {"ok": True}


def _screen_event(screen_type: str = "question", confidence: float = 0.8) -> StudyEvent:
    return StudyEvent(
        name="screen_context_changed",
        payload={
            "screen_type": screen_type,
            "confidence": confidence,
            "ocr_summary": "Solve x + 1 = 2",
            "previous_type": "reading",
        },
    )


@pytest.mark.asyncio
async def test_emit_screen_context_throttled_by_confidence() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(_screen_event(confidence=0.4))

    assert ctx.messages == []
    assert bus.block_count == 1


@pytest.mark.asyncio
async def test_emit_screen_context_requires_3_consecutive() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(_screen_event())
    await bus.emit(_screen_event())
    assert ctx.messages == []

    await bus.emit(_screen_event())

    assert len(ctx.messages) == 1
    assert ctx.messages[0]["ai_behavior"] == "read"


@pytest.mark.asyncio
async def test_emit_screen_context_5min_cooldown() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    for _ in range(3):
        await bus.emit(_screen_event())
    await bus.emit(_screen_event())

    assert len(ctx.messages) == 1
    assert bus.block_count == 3


@pytest.mark.asyncio
async def test_emit_answer_evaluated_incorrect_triggers_respond() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(
        StudyEvent(
            name="answer_evaluated",
            payload={
                "verdict": "incorrect",
                "score": 0.2,
                "question_summary": "Q",
                "user_answer_summary": "A",
            },
        )
    )

    assert ctx.messages[0]["ai_behavior"] == "respond"
    assert ctx.messages[0]["priority"] == 5


@pytest.mark.asyncio
async def test_emit_answer_evaluated_respond_cooldown_30s() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)
    event = StudyEvent(
        name="answer_evaluated",
        payload={
            "verdict": "incorrect",
            "score": 0.1,
            "question_summary": "Q",
            "user_answer_summary": "A",
        },
    )

    await bus.emit(event)
    await bus.emit(event)

    assert [item["ai_behavior"] for item in ctx.messages] == ["respond", "read"]


@pytest.mark.asyncio
async def test_emit_answer_respond_cooldown_starts_after_async_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 100.0
    monkeypatch.setattr(event_bus_module.time, "monotonic", lambda: current)

    class _SlowCtx(_Ctx):
        async def push_message(self, **kwargs):
            nonlocal current
            self.messages.append(dict(kwargs))
            current = 140.0
            return {"ok": True}

    ctx = _SlowCtx()
    bus = StudyEventBus(plugin_ctx=ctx)
    event = StudyEvent(
        name="answer_evaluated",
        payload={
            "verdict": "incorrect",
            "score": 0.1,
            "question_summary": "Q",
            "user_answer_summary": "A",
        },
    )

    await bus.emit(event)
    current = 140.1
    await bus.emit(event)

    assert [item["ai_behavior"] for item in ctx.messages] == ["respond", "read"]


@pytest.mark.asyncio
async def test_emit_mastery_updated_ignores_small_changes() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(
        StudyEvent(
            name="mastery_updated",
            payload={
                "topic": "derivatives",
                "mastery": 0.54,
                "mastery_before": 0.5,
            },
        )
    )

    assert ctx.messages == []
    assert bus.block_count == 1


@pytest.mark.asyncio
async def test_emit_mastery_updated_preserves_small_threshold_crossing() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(
        StudyEvent(
            name="mastery_updated",
            payload={
                "topic": "derivatives",
                "mastery": 0.51,
                "mastery_before": 0.49,
                "crossed_threshold": "0.5",
            },
        )
    )

    assert len(ctx.messages) == 1
    assert "[Mastery Updated]" in ctx.messages[0]["parts"][0]["text"]


@pytest.mark.asyncio
async def test_emit_mastery_updated_10min_cooldown_per_topic() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)
    event = StudyEvent(
        name="mastery_updated",
        payload={
            "topic": "derivatives",
            "mastery": 0.7,
            "mastery_before": 0.4,
            "direction": "up",
            "crossed_threshold": "0.7",
            "evidence_count": 3,
        },
    )

    await bus.emit(event)
    await bus.emit(event)

    assert len(ctx.messages) == 1
    assert bus.block_count == 1


@pytest.mark.asyncio
async def test_emit_review_due_30min_cooldown() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)
    event = StudyEvent(
        name="review_due",
        payload={"due_count": 5, "urgent_count": 0, "topics": ["math"]},
    )

    await bus.emit(event)
    await bus.emit(event)

    assert len(ctx.messages) == 1
    assert ctx.messages[0]["ai_behavior"] == "respond"


@pytest.mark.asyncio
async def test_emit_failure_does_not_consume_review_due_cooldown() -> None:
    ctx = _Ctx(fail=True)
    bus = StudyEventBus(plugin_ctx=ctx)
    event = StudyEvent(
        name="review_due",
        payload={"due_count": 2, "urgent_count": 0, "topics": ["math"]},
    )

    with pytest.raises(RuntimeError, match="push failed"):
        await bus.emit(event)

    assert bus.emit_count == 0
    assert "review_due" not in bus._throttle
    ctx.fail = False

    await bus.emit(event)

    assert len(ctx.messages) == 1
    assert bus.emit_count == 1


@pytest.mark.asyncio
async def test_emit_failure_does_not_consume_answer_respond_cooldown() -> None:
    ctx = _Ctx(fail=True)
    bus = StudyEventBus(plugin_ctx=ctx)
    event = StudyEvent(
        name="answer_evaluated",
        payload={
            "verdict": "incorrect",
            "score": 0.1,
            "question_summary": "Q",
            "user_answer_summary": "A",
        },
    )

    with pytest.raises(RuntimeError, match="push failed"):
        await bus.emit(event)

    assert bus.emit_count == 0
    assert bus._last_respond_at == -bus._RESPOND_COOLDOWN
    ctx.fail = False

    await bus.emit(event)

    assert ctx.messages[0]["ai_behavior"] == "respond"
    assert bus.emit_count == 1


@pytest.mark.asyncio
async def test_throttle_ttl_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 10_000.0
    monkeypatch.setattr(event_bus_module.time, "monotonic", lambda: now)
    bus = StudyEventBus(plugin_ctx=_Ctx())
    bus._throttle["old"] = now - bus._THROTTLE_TTL - 1
    bus._throttle["fresh"] = now

    await bus.emit(
        StudyEvent(
            name="session_summarized",
            payload={"duration_minutes": 1, "questions_attempted": 1},
        )
    )

    assert "old" not in bus._throttle
    assert "fresh" in bus._throttle


@pytest.mark.asyncio
async def test_format_answer_evaluated_mastery_sentinel_omits_line() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(
        StudyEvent(
            name="answer_evaluated",
            payload={
                "verdict": "correct",
                "score": 1.0,
                "question_summary": "Q",
                "user_answer_summary": "A",
                "topic": "limits",
                "mastery_before": -1.0,
                "mastery_after": -1.0,
            },
        )
    )

    text = ctx.messages[0]["parts"][0]["text"]
    assert "Topic: limits" in text
    assert "mastery" not in text


@pytest.mark.asyncio
async def test_format_answer_evaluated_includes_hint() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(
        StudyEvent(
            name="answer_evaluated",
            payload={
                "verdict": "partial",
                "score": 50,
                "question_summary": "Q",
                "user_answer_summary": "A",
                "correction_hint": "Use the chain rule.",
            },
        )
    )

    text = ctx.messages[0]["parts"][0]["text"]
    assert "partially correct" in text
    assert "Use the chain rule." in text
    assert "50%" in text


@pytest.mark.asyncio
async def test_format_session_summarized_includes_insight() -> None:
    ctx = _Ctx()
    bus = StudyEventBus(plugin_ctx=ctx)

    await bus.emit(
        StudyEvent(
            name="session_summarized",
            payload={
                "duration_minutes": 25,
                "questions_attempted": 4,
                "correct_rate": 0.75,
                "key_insight": "Chain rule is improving.",
            },
        )
    )

    text = ctx.messages[0]["parts"][0]["text"]
    assert "25 min" in text
    assert "75%" in text
    assert "Chain rule is improving." in text


@pytest.mark.asyncio
async def test_schedule_emit_logs_on_exception(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger=event_bus_module._logger.name)
    bus = StudyEventBus(plugin_ctx=_Ctx(fail=True))

    task = bus.schedule_emit(
        StudyEvent(
            name="session_summarized",
            payload={"duration_minutes": 1, "questions_attempted": 1},
        )
    )
    assert task is not None
    done, pending = await asyncio.wait({task}, timeout=1.0)
    assert task in done
    assert not pending
    assert task.exception() is not None

    assert "StudyEventBus.schedule_emit() task failed" in caplog.text


@pytest.mark.asyncio
async def test_schedule_emit_ignores_cancelled_error(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.ERROR, logger=event_bus_module._logger.name)
    bus = StudyEventBus(plugin_ctx=_Ctx())

    async def _cancelled(_event: StudyEvent) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(bus, "emit", _cancelled)
    task = bus.schedule_emit(
        StudyEvent(
            name="session_summarized",
            payload={"duration_minutes": 1, "questions_attempted": 1},
        )
    )
    assert task is not None
    done, pending = await asyncio.wait({task}, timeout=1.0)
    assert task in done
    assert not pending
    assert task.cancelled()

    assert caplog.text == ""
