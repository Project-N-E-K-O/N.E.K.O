from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.study_companion._event_bus import StudyEvent, StudyEventBus
from plugin.plugins.study_companion._general_narration import (
    GENERAL_NARRATION_MAX_CHARS,
    prepare_general_narration_content,
)
from plugin.plugins.study_companion.entry_communication_tutor_events import (
    _CommunicationTutorEventsMixin,
)


pytestmark = pytest.mark.unit


class TestPrepareGeneralNarrationContent:
    def test_removes_markdown_structure_code_blocks_and_internal_diagnostics(
        self,
    ) -> None:
        source = """# 关于《活着》

生命的韧性是作品的重要主题。

```json
{"knowledge_guidance": "must-not-be-narrated"}
```
study_semantic_status=routing_unavailable
knowledge_guidance_status=not_matched
- knowledge_guidance_status=not_matched
> solution_narration_status: not_applicable
1. general_narration_status=scheduled
{"knowledge_guidance": "must-not-be-narrated-json"}
### general_narration_status=delivery_failed

## 我的理解
- 活着本身就是一种力量。
"""

        prepared = prepare_general_narration_content(source)

        assert prepared == (
            "关于《活着》\n\n"
            "生命的韧性是作品的重要主题。\n\n"
            "我的理解\n"
            "- 活着本身就是一种力量。"
        )
        assert "```" not in prepared
        assert "must-not-be-narrated" not in prepared
        assert "study_semantic" not in prepared
        assert "knowledge_guidance" not in prepared
        assert "solution_narration" not in prepared
        assert "general_narration" not in prepared
        assert "must-not-be-narrated-json" not in prepared

    def test_returns_empty_for_empty_or_diagnostic_only_content(self) -> None:
        assert prepare_general_narration_content("") == ""
        assert prepare_general_narration_content(None) == ""
        assert (
            prepare_general_narration_content(
                "study_semantic_status=unavailable\n"
                "general_narration_status=delivery_failed"
            )
            == ""
        )

    def test_truncates_at_a_late_sentence_boundary(self) -> None:
        source = "甲" * 1500 + "。" + "乙" * 300

        prepared = prepare_general_narration_content(source)

        assert len(prepared) == 1501
        assert prepared.endswith("。")
        assert "乙" not in prepared

    def test_hard_truncates_when_no_late_sentence_or_paragraph_boundary_exists(
        self,
    ) -> None:
        prepared = prepare_general_narration_content("甲" * 2000)

        assert len(prepared) == GENERAL_NARRATION_MAX_CHARS == 1600
        assert prepared == "甲" * 1600


class _RecordingBus:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.events: list[StudyEvent] = []

    async def emit(self, event: StudyEvent) -> None:
        self.events.append(event)
        if self.failure is not None:
            raise self.failure


class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))


class _CommunicationHarness(_CommunicationTutorEventsMixin):
    def __init__(self, event_bus: _RecordingBus | None) -> None:
        self._event_bus = event_bus
        self.logger = _RecordingLogger()


class TestGeneralResponseEventHelper:
    @pytest.mark.asyncio
    async def test_emits_exactly_once_with_prepared_content(self) -> None:
        bus = _RecordingBus()
        harness = _CommunicationHarness(bus)

        scheduled = await harness._emit_general_response_completed_event(
            response_mode="general_discussion",
            content="# 主题\n\n作品强调生命的韧性。",
        )

        assert scheduled is True
        assert len(bus.events) == 1
        assert bus.events[0].name == "general_response_completed"
        assert bus.events[0].payload == {
            "response_mode": "general_discussion",
            "content": "主题\n\n作品强调生命的韧性。",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("response_mode", "content"),
        [
            ("unknown", "自然回答"),
            ("problem_solving", "题目答案"),
            ("general_explanation", ""),
        ],
    )
    async def test_rejects_unsupported_modes_or_empty_content(
        self, response_mode: str, content: str
    ) -> None:
        bus = _RecordingBus()
        harness = _CommunicationHarness(bus)

        scheduled = await harness._emit_general_response_completed_event(
            response_mode=response_mode,
            content=content,
        )

        assert scheduled is False
        assert bus.events == []

    @pytest.mark.asyncio
    async def test_returns_false_without_runtime(self) -> None:
        harness = _CommunicationHarness(None)

        scheduled = await harness._emit_general_response_completed_event(
            response_mode="general_explanation",
            content="机会成本是选择一种方案时放弃的最佳替代方案。",
        )

        assert scheduled is False

    @pytest.mark.asyncio
    async def test_hides_content_when_delivery_fails(self) -> None:
        secret = "reply-content-must-not-enter-logs"
        bus = _RecordingBus(failure=RuntimeError("transport rejected the message"))
        harness = _CommunicationHarness(bus)

        scheduled = await harness._emit_general_response_completed_event(
            response_mode="general_discussion",
            content=secret,
        )

        assert scheduled is False
        assert len(bus.events) == 1
        assert harness.logger.warnings
        assert secret not in repr(harness.logger.warnings)


class _RecordingCtx:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def push_message(self, **kwargs: Any) -> dict[str, bool]:
        self.messages.append(dict(kwargs))
        return {"submitted": True}


class TestGeneralResponseEventBusContract:
    @pytest.mark.asyncio
    async def test_uses_active_reply_contract_and_safe_formatter(self) -> None:
        content = "《活着》展现了生命面对苦难时的韧性。"
        ctx = _RecordingCtx()
        bus = StudyEventBus(plugin_ctx=ctx)

        await bus.emit(
            StudyEvent(
                name="general_response_completed",
                payload={
                    "response_mode": "general_discussion",
                    "content": content,
                    "original_question": "must-not-be-forwarded",
                    "knowledge_guidance": "must-not-be-forwarded",
                    "image": "must-not-be-forwarded",
                },
            )
        )

        assert len(ctx.messages) == 1
        message = ctx.messages[0]
        assert message["visibility"] == ["chat"]
        assert message["ai_behavior"] == "respond"
        assert message["priority"] == 5
        assert message["source"] == "study_companion"
        instruction = message["parts"][0]["text"]
        assert content in instruction
        assert "忠实" in instruction
        assert "自然" in instruction
        assert "不要添加" in instruction
        assert "事实" in instruction
        assert "评价" in instruction
        assert "追问" in instruction
        assert "must-not-be-forwarded" not in instruction

    @pytest.mark.asyncio
    async def test_ignores_answer_cooldown_and_pending_respond_slot(self) -> None:
        ctx = _RecordingCtx()
        bus = StudyEventBus(plugin_ctx=ctx)
        bus._last_respond_at = 123.0
        bus._pending_respond_count = 1

        await bus.emit(
            StudyEvent(
                name="general_response_completed",
                payload={
                    "response_mode": "general_explanation",
                    "content": "机会成本是放弃的最佳替代方案。",
                },
            )
        )

        assert ctx.messages[0]["ai_behavior"] == "respond"
        assert bus._last_respond_at == 123.0
        assert bus._pending_respond_count == 1
