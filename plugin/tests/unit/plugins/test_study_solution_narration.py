from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.study_companion._solution_narration import (
    SOLUTION_NARRATION_MAX_CHARS,
    extract_solution_narration_sections,
)
from plugin.plugins.study_companion.constants import (
    MODE_COMPANION,
    MODE_CONCEPT_EXPLAIN,
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


_PROCESS_SENTINEL = "PROCESS_SENTINEL_MUST_NEVER_BE_NARRATED"
_STRUCTURED_REPLY = f"""先说一句过渡语，这句也不能进入讲述。

## 题目解析
先识别条件，再判断题目要求。

## 解题过程
{_PROCESS_SENTINEL}

## 答案
答案是 42。

## 举一反三
把常数换成 84 后使用同一种关系。
"""
_EXPECTED_SECTIONS = {
    "analysis": "先识别条件，再判断题目要求。",
    "answer": "答案是 42。",
    "transfer": "把常数换成 84 后使用同一种关系。",
}
_PNG_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _reply_with_headings(headings: tuple[str, str, str, str]) -> str:
    analysis, process, answer, transfer = headings
    return (
        "不应讲述的标题前过渡语。\n\n"
        f"{analysis}\n分析正文。\n\n"
        f"{process}\n{_PROCESS_SENTINEL}\n\n"
        f"{answer}\n答案正文。\n\n"
        f"{transfer}\n迁移正文。"
    )


def test_extract_solution_narration_keeps_only_three_sections_from_four_part_reply() -> (
    None
):
    sections = extract_solution_narration_sections(_STRUCTURED_REPLY)

    assert sections == _EXPECTED_SECTIONS
    combined = "\n".join(sections.values()) if sections else ""
    assert _PROCESS_SENTINEL not in combined
    assert "过渡语" not in combined


@pytest.mark.parametrize(
    "headings",
    [
        ("## 题目解析", "## 解题过程", "## 答案", "## 举一反三"),
        ("**题目解析**", "**解题过程**", "**答案**", "**举一反三**"),
        ("题目解析：", "解题过程：", "答案：", "举一反三："),
        ("### 題目解析：", "### 解題過程：", "### 答案：", "### 舉一反三："),
        (
            "#### Problem Analysis:",
            "#### Solution Process:",
            "#### Final Answer:",
            "#### Transfer Practice:",
        ),
        (
            "Problem Analysis",
            "Solution Process",
            "Answer",
            "Transfer Practice",
        ),
        ("解析", "解题过程", "答案", "举一反三"),
    ],
    ids=[
        "markdown",
        "bold",
        "colon",
        "traditional-chinese",
        "english-final-answer",
        "english-answer",
        "short-analysis-alias",
    ],
)
def test_extract_solution_narration_matches_frontend_heading_variants(
    headings: tuple[str, str, str, str],
) -> None:
    sections = extract_solution_narration_sections(_reply_with_headings(headings))

    assert sections == {
        "analysis": "分析正文。",
        "answer": "答案正文。",
        "transfer": "迁移正文。",
    }
    assert _PROCESS_SENTINEL not in "\n".join(sections.values())


@pytest.mark.parametrize(
    "reply",
    [
        "题目解析\n分析。\n\n解题过程\n过程。\n\n答案\n答案。",
        "题目解析\n分析。\n\n解题过程\n过程。\n\n答案\n\n举一反三\n迁移。",
        "题目解析\n\n解题过程\n过程。\n\n答案\n答案。\n\n举一反三\n迁移。",
    ],
    ids=["missing-transfer", "empty-answer", "empty-analysis"],
)
def test_extract_solution_narration_requires_every_target_section(reply: str) -> None:
    assert extract_solution_narration_sections(reply) is None


def _without_truncation_marker(value: str) -> str:
    for marker in ("...", "…"):
        if value.endswith(marker):
            return value[: -len(marker)].rstrip()
    return value


def test_extract_solution_narration_truncates_total_at_sentence_boundaries() -> None:
    analysis = "分析句。" * SOLUTION_NARRATION_MAX_CHARS
    answer = "答案句。" * SOLUTION_NARRATION_MAX_CHARS
    transfer = "迁移句。" * SOLUTION_NARRATION_MAX_CHARS
    reply = (
        f"题目解析\n{analysis}\n\n"
        f"解题过程\n{_PROCESS_SENTINEL}\n\n"
        f"答案\n{answer}\n\n"
        f"举一反三\n{transfer}"
    )

    sections = extract_solution_narration_sections(reply)

    assert sections is not None
    assert set(sections) == {"analysis", "answer", "transfer"}
    assert all(sections.values())
    assert (
        sum(len(value) for value in sections.values()) <= SOLUTION_NARRATION_MAX_CHARS
    )
    for key, original in (
        ("analysis", analysis),
        ("answer", answer),
        ("transfer", transfer),
    ):
        bounded = _without_truncation_marker(sections[key])
        assert original.startswith(bounded)
        assert bounded.endswith("。")
    assert _PROCESS_SENTINEL not in "\n".join(sections.values())


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))


class _EventBus:
    def __init__(self, *, accept: bool = True, fail: bool = False) -> None:
        self.accept = accept
        self.fail = fail
        self.events: list[Any] = []

    def _record(self, event: Any) -> bool:
        if self.fail:
            raise RuntimeError("event delivery failed")
        if not self.accept:
            return False
        self.events.append(event)
        return True

    def schedule_emit(self, event: Any) -> object | None:
        return object() if self._record(event) else None

    async def emit(self, event: Any) -> None:
        if not self._record(event):
            raise RuntimeError("event delivery rejected")


class _TutorAgent:
    def __init__(self, reply: str, *, degraded: bool = False) -> None:
        self.reply = reply
        self.degraded = degraded
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        self.calls.append((text, mode, dict(context or {})))
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text=text,
            reply=self.reply,
            degraded=self.degraded,
            diagnostic="timeout" if self.degraded else "",
            created_at="2026-08-12T00:00:00Z",
        )


class _ExplainHarness(_TutorExplainEntriesMixin, _CommunicationTutorEventsMixin):
    def __init__(
        self,
        *,
        reply: str = _STRUCTURED_REPLY,
        degraded: bool = False,
        communication_enabled: bool = True,
        narration_enabled: bool = True,
        event_bus: _EventBus | None = None,
        last_ocr_text: str = "",
    ) -> None:
        self._cfg = SimpleNamespace(
            language="zh-CN",
            llm_vision_enabled=True,
            communication=SimpleNamespace(
                enabled=communication_enabled,
                solution_narration_enabled=narration_enabled,
            ),
        )
        self._state = SimpleNamespace(
            active_mode=MODE_COMPANION,
            last_ocr_text=last_ocr_text,
        )
        self._lock = asyncio.Lock()
        self._agent = _TutorAgent(reply, degraded=degraded)
        self._event_bus = event_bus if event_bus is not None else _EventBus()
        self.logger = _Logger()

    async def _apply_mode_switch(
        self,
        mode: str,
        _reason: str,
        *,
        language: str,
    ) -> dict[str, Any]:
        self._state.active_mode = mode
        return {
            "changed": True,
            "old_mode": MODE_COMPANION,
            "new_mode": mode,
            "transition_phrase": "教学模式已开启。"
            if language.startswith("zh")
            else "Teaching mode enabled.",
        }

    async def _build_learning_context(
        self,
        operation: str,
        *,
        input_text: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"operation": operation, "input_text": input_text, **dict(extra or {})}

    async def _finalize_tutor_call(
        self,
        _operation: str,
        reply: TutorReply,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "operation": reply.operation,
            "input_text": reply.input_text,
            "reply": reply.reply,
            "summary": reply.reply,
            "degraded": reply.degraded,
            "diagnostic": reply.diagnostic,
            "created_at": reply.created_at,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "last_ocr_text", "expected_source"),
    [
        ({"text": "一道手输题目"}, "", "manual"),
        ({}, "OCR 缓存题目", "ocr_snapshot"),
        ({"vision_image_base64": _PNG_IMAGE_BASE64}, "", "vision_image"),
    ],
    ids=["manual-text", "ocr-cache", "pasted-vision-image"],
)
async def test_study_explain_text_schedules_one_solution_event_for_each_input_path(
    kwargs: dict[str, str],
    last_ocr_text: str,
    expected_source: str,
) -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(event_bus=bus, last_ocr_text=last_ocr_text)

    result = await plugin.study_explain_text(**kwargs)

    assert isinstance(result, Ok)
    assert result.value["reply"] == _STRUCTURED_REPLY
    assert result.value["solution_narration_scheduled"] is True
    assert len(bus.events) == 1
    event = bus.events[0]
    assert event.name == "solution_completed"
    assert event.payload == _EXPECTED_SECTIONS
    assert _PROCESS_SENTINEL not in repr(event.payload)
    assert len(plugin._agent.calls) == 1
    assert plugin._agent.calls[0][2]["source"] == expected_source


@pytest.mark.asyncio
async def test_study_explain_text_does_not_schedule_degraded_reply() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(event_bus=bus, degraded=True)

    result = await plugin.study_explain_text(text="超时但页面仍需拿到降级结果")

    assert isinstance(result, Ok)
    assert result.value["reply"] == _STRUCTURED_REPLY
    assert result.value["degraded"] is True
    assert result.value["solution_narration_scheduled"] is False
    assert bus.events == []


@pytest.mark.asyncio
async def test_study_explain_text_does_not_schedule_pure_mode_switch() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(event_bus=bus)

    result = await plugin.study_explain_text(text="教我")

    assert isinstance(result, Ok)
    assert result.value["reply"] == "教学模式已开启。"
    assert result.value["solution_narration_scheduled"] is False
    assert bus.events == []
    assert plugin._agent.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("communication_enabled", "narration_enabled"),
    [(False, True), (True, False)],
    ids=["communication-disabled", "solution-narration-disabled"],
)
async def test_study_explain_text_respects_both_communication_switches(
    communication_enabled: bool,
    narration_enabled: bool,
) -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(
        event_bus=bus,
        communication_enabled=communication_enabled,
        narration_enabled=narration_enabled,
    )

    result = await plugin.study_explain_text(text="开关测试题目")

    assert isinstance(result, Ok)
    assert result.value["reply"] == _STRUCTURED_REPLY
    assert result.value["solution_narration_scheduled"] is False
    assert bus.events == []


@pytest.mark.asyncio
async def test_study_explain_text_keeps_page_reply_when_target_section_is_missing() -> (
    None
):
    reply = (
        "题目解析\n分析仍会显示。\n\n解题过程\n过程仍会显示。\n\n答案\n答案仍会显示。"
    )
    bus = _EventBus()
    plugin = _ExplainHarness(reply=reply, event_bus=bus)

    result = await plugin.study_explain_text(text="缺少举一反三的题目")

    assert isinstance(result, Ok)
    assert result.value["reply"] == reply
    assert result.value["solution_narration_scheduled"] is False
    assert bus.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("accept", "fail"), [(False, False), (True, True)])
async def test_study_explain_text_keeps_page_reply_when_event_delivery_fails(
    accept: bool,
    fail: bool,
) -> None:
    bus = _EventBus(accept=accept, fail=fail)
    plugin = _ExplainHarness(event_bus=bus)

    result = await plugin.study_explain_text(text="投递失败也要保留页面解答")

    assert isinstance(result, Ok)
    assert result.value["reply"] == _STRUCTURED_REPLY
    assert result.value["solution_narration_scheduled"] is False
    assert bus.events == []
    if fail:
        assert plugin.logger.warnings
        logged = repr(plugin.logger.warnings)
        assert _PROCESS_SENTINEL not in logged
        assert "投递失败也要保留页面解答" not in logged
