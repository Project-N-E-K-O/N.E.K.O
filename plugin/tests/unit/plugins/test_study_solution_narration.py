from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
import plugin.plugins.study_companion.entry_tutor_explain_entries as explain_entries

from plugin.plugins.study_companion._solution_structure import (
    SOLUTION_NARRATION_MAX_CHARS,
    extract_solution_narration_sections,
    is_solution_structure_candidate,
    parse_solution_structure,
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
from plugin.plugins.study_companion.tutor_llm_agent_concept_explain import (
    _ensure_transfer_section,
)
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


def test_solution_structure_recognizes_safe_final_answer_alias() -> None:
    structure = parse_solution_structure(
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n逐步计算。\n\n"
        "### 最终答案\n答案是 42。\n\n"
        "### 举一反三\n替换常数后复算。"
    )

    assert structure.complete is True
    assert structure.answer == "答案是 42。"
    assert structure.missing_sections == ()


def test_solution_structure_reports_missing_answer_without_guessing_conclusion() -> None:
    structure = parse_solution_structure(
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n逐步计算。\n\n"
        "**结论：** 点 R 的坐标为（1，2）。\n\n"
        "### 举一反三\n替换常数后复算。"
    )

    assert structure.complete is False
    assert structure.answer == ""
    assert structure.missing_sections == ("answer",)


def test_solution_structure_candidate_requires_multiple_solution_signals() -> None:
    prose = parse_solution_structure("余华通过福贵的一生讨论苦难与生命韧性。")
    truncated_solution = parse_solution_structure(
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n推导中止。\n\n"
        "### 举一反三\n替换参数。"
    )

    assert is_solution_structure_candidate(prose) is False
    assert is_solution_structure_candidate(truncated_solution) is True


def test_transfer_fallback_does_not_mask_a_missing_answer_section() -> None:
    incomplete_reply = (
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n推导在公式中止。"
    )

    result = _ensure_transfer_section(
        incomplete_reply,
        "zh-CN",
        "请解题并举一反三",
    )

    assert result == incomplete_reply
    assert "举一反三" not in result


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
    def __init__(
        self,
        reply: str,
        *,
        degraded: bool = False,
        diagnostic: str | None = None,
    ) -> None:
        self.reply = reply
        self.degraded = degraded
        self.diagnostic = diagnostic
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
            diagnostic=(
                self.diagnostic
                if self.diagnostic is not None
                else ("timeout" if self.degraded else "")
            ),
            created_at="2026-08-12T00:00:00Z",
        )


class _ExplainHarness(_TutorExplainEntriesMixin, _CommunicationTutorEventsMixin):
    def __init__(
        self,
        *,
        reply: str = _STRUCTURED_REPLY,
        degraded: bool = False,
        diagnostic: str | None = None,
        communication_enabled: bool = True,
        narration_enabled: bool = True,
        general_narration_enabled: bool = True,
        event_bus: _EventBus | None = None,
        last_ocr_text: str = "",
        response_mode: str = "problem_solving",
    ) -> None:
        self._cfg = SimpleNamespace(
            language="zh-CN",
            llm_vision_enabled=True,
            communication=SimpleNamespace(
                enabled=communication_enabled,
                solution_narration_enabled=narration_enabled,
                general_narration_enabled=general_narration_enabled,
            ),
        )
        self._state = SimpleNamespace(
            active_mode=MODE_COMPANION,
            last_ocr_text=last_ocr_text,
        )
        self._lock = asyncio.Lock()
        self._agent = _TutorAgent(
            reply,
            degraded=degraded,
            diagnostic=diagnostic,
        )
        self._event_bus = event_bus if event_bus is not None else _EventBus()
        self.logger = _Logger()
        self._response_mode = response_mode

    def _resolve_study_target_lanlan(
        self, kwargs: dict[str, Any] | None = None
    ) -> str | None:
        ctx = dict(kwargs or {}).get("_ctx")
        if isinstance(ctx, dict):
            return str(ctx.get("lanlan_name") or "").strip() or None
        return None

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
        return {
            "operation": operation,
            "input_text": input_text,
            "study_response_mode": self._response_mode,
            **dict(extra or {}),
        }

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


def _install_repair_response(
    plugin: _ExplainHarness, raw_response: str
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _call_model(
        messages: list[dict[str, Any]], *, operation: str, deadline: float
    ) -> str:
        calls.append(
            {"messages": messages, "operation": operation, "deadline": deadline}
        )
        return raw_response

    def _attach_vision_image(
        messages: list[dict[str, Any]], image_base64: str
    ) -> list[dict[str, Any]]:
        result = [dict(message) for message in messages]
        result[-1]["content"] = [
            {"type": "text", "text": str(result[-1]["content"])},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            },
        ]
        return result

    plugin._agent._call_model = _call_model  # type: ignore[attr-defined]
    plugin._agent._new_operation_deadline = (  # type: ignore[attr-defined]
        lambda _operation, _messages: time.monotonic() + 60.0
    )
    plugin._agent._attach_vision_image = _attach_vision_image  # type: ignore[attr-defined]
    plugin._agent._json_corrector = SimpleNamespace(  # type: ignore[attr-defined]
        parse_json_object=lambda raw: json.loads(raw)
    )
    return calls


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
    assert result.value["solution_narration_status"] == "scheduled"
    assert result.value["solution_narration_reason"] == ""
    assert result.value["solution_repair_attempted"] is False
    assert result.value["solution_narration_missing_sections"] == []
    assert len(bus.events) == 1
    event = bus.events[0]
    assert event.name == "solution_completed"
    assert event.payload == _EXPECTED_SECTIONS
    assert _PROCESS_SENTINEL not in repr(event.payload)
    assert len(plugin._agent.calls) == 1
    assert plugin._agent.calls[0][2]["source"] == expected_source


@pytest.mark.asyncio
async def test_study_explain_text_repairs_realistic_truncated_long_process_once() -> None:
    long_process = "\n".join(
        f"{index}. 核验后的几何推导步骤 {index}。" for index in range(1, 181)
    )
    incomplete_reply = (
        "### 题目解析\n识别条件。\n\n"
        f"### 解题过程\n{long_process}\n"
        "轨迹半径 r_A = |O'X| ="
    )
    repaired_json = json.dumps(
        {
            "analysis": "识别条件。",
            "process": "完成剩余推导。",
            "answer": "最大值为 7。",
            "transfer": "替换参数后复算。",
        },
        ensure_ascii=False,
    )
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply=incomplete_reply,
        diagnostic="output_truncated",
        event_bus=bus,
    )
    repair_calls = _install_repair_response(plugin, repaired_json)

    result = await plugin.study_explain_text(text="求 |QM| 的最大值")

    assert isinstance(result, Ok)
    assert len(plugin._agent.calls) == 1
    assert len(repair_calls) == 1
    assert repair_calls[0]["operation"] == "solution_structure_repair"
    repair_prompt = str(repair_calls[0]["messages"][-1]["content"])
    assert "求 |QM| 的最大值" in repair_prompt
    assert incomplete_reply in repair_prompt
    assert result.value["solution_repair_attempted"] is True
    assert result.value["solution_narration_status"] == "scheduled"
    assert result.value["solution_narration_reason"] == ""
    assert result.value["solution_narration_missing_sections"] == []
    assert result.value["solution_narration_scheduled"] is True
    assert "### 答案\n最大值为 7。" in result.value["reply"]
    assert len(bus.events) == 1
    assert [event.name for event in bus.events] == ["solution_completed"]
    assert bus.events[0].payload["answer"] == "最大值为 7。"


@pytest.mark.asyncio
async def test_study_explain_text_reserves_work_deadline_for_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete_reply = (
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n推导中止。\n\n"
        "### 举一反三\n替换参数。"
    )
    plugin = _ExplainHarness(
        reply=incomplete_reply,
        diagnostic="output_truncated",
    )
    captured: dict[str, Any] = {}

    async def repair_with_reserved_budget(
        _agent: Any,
        *,
        source_text: str,
        incomplete_reply: str,
        language: str,
        mode: str,
        context: dict[str, Any],
    ) -> Any:
        captured.update(
            source_text=source_text,
            incomplete_reply=incomplete_reply,
            language=language,
            mode=mode,
            context=dict(context),
        )
        return parse_solution_structure(
            "### 题目解析\n识别条件。\n\n"
            "### 解题过程\n完成推导。\n\n"
            "### 答案\n答案为 42。\n\n"
            "### 举一反三\n替换参数后复算。"
        )

    monkeypatch.setattr(
        explain_entries, "repair_solution_structure", repair_with_reserved_budget
    )
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(explain_entries, "monotonic", lambda: clock.now)

    result = await plugin.study_explain_text(text="一道耗时较长的题")

    assert isinstance(result, Ok)
    primary_deadline = plugin._agent.calls[0][2]["deadline_monotonic"]
    repair_deadline = captured["context"]["deadline_monotonic"]
    assert primary_deadline == pytest.approx(170.0)
    assert repair_deadline == pytest.approx(115.0)
    assert repair_deadline - clock.now == pytest.approx(15.0)
    assert repair_deadline <= primary_deadline + 15.0
    assert result.value["reply"] != incomplete_reply
    assert result.value["degraded"] is False
    assert result.value["diagnostic"] == "output_truncated"
    assert result.value["solution_repair_attempted"] is True
    assert result.value["solution_narration_status"] == "scheduled"
    assert result.value["solution_narration_reason"] == ""
    assert result.value["solution_narration_missing_sections"] == []


@pytest.mark.asyncio
async def test_study_explain_text_repairs_truncation_before_second_heading() -> None:
    incomplete_reply = "### 题目解析\n已知圆心 O 与点 A，目标是求轨迹半径。"
    repaired_json = json.dumps(
        {
            "analysis": "列出已知条件与目标。",
            "process": "1. 使用距离公式核验。",
            "answer": "轨迹半径为 3。",
            "transfer": "将半径改为 4 后复算。",
        },
        ensure_ascii=False,
    )
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply=incomplete_reply,
        diagnostic="output_truncated",
        event_bus=bus,
    )
    repair_calls = _install_repair_response(plugin, repaired_json)

    result = await plugin.study_explain_text(text="根据图片求轨迹半径")

    assert isinstance(result, Ok)
    assert len(repair_calls) == 1
    assert result.value["solution_repair_attempted"] is True
    assert result.value["solution_narration_scheduled"] is True
    assert result.value["solution_narration_missing_sections"] == []
    assert [event.name for event in bus.events] == ["solution_completed"]
    assert bus.events[0].payload["answer"] == "轨迹半径为 3。"


@pytest.mark.asyncio
async def test_study_explain_text_repair_timeout_keeps_first_reply_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete_reply = (
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n推导中止。\n\n"
        "### 举一反三\n替换参数。"
    )
    plugin = _ExplainHarness(reply=incomplete_reply)

    async def never_returns(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(explain_entries, "repair_solution_structure", never_returns)
    monkeypatch.setattr(explain_entries, "_PRIMARY_EXPLAIN_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(explain_entries, "_SOLUTION_REPAIR_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(explain_entries, "_SOLUTION_REPAIR_MIN_REMAINING_SECONDS", 0.01)
    started = time.monotonic()

    result = await plugin.study_explain_text(text="一道修复调用不返回的题")

    assert time.monotonic() - started < 1.0
    assert isinstance(result, Ok)
    assert result.value["reply"] == incomplete_reply
    assert result.value["solution_repair_attempted"] is True
    assert result.value["solution_narration_status"] == "incomplete"
    assert result.value["solution_narration_reason"] == "insufficient_time_budget"
    assert result.value["solution_narration_missing_sections"] == ["answer"]


@pytest.mark.asyncio
async def test_study_explain_text_targets_narration_to_requesting_session() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(event_bus=bus)

    result = await plugin.study_explain_text(
        text="请解题",
        _ctx={"lanlan_name": "lanlan-requesting"},
    )

    assert isinstance(result, Ok)
    assert len(bus.events) == 1
    assert bus.events[0].payload["target_lanlan"] == "lanlan-requesting"


@pytest.mark.asyncio
async def test_study_explain_text_does_not_repair_ordinary_concept_prose() -> None:
    prose_reply = "余华通过福贵的一生，写出了苦难中的生命韧性。"
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply=prose_reply, event_bus=bus, response_mode="general_discussion"
    )
    repair_calls = _install_repair_response(plugin, "should-not-be-used")

    result = await plugin.study_explain_text(text="谈谈你对《活着》的理解")

    assert isinstance(result, Ok)
    assert repair_calls == []
    assert result.value["reply"] == prose_reply
    assert result.value["solution_narration_scheduled"] is False
    assert result.value["solution_narration_status"] == "not_applicable"
    assert result.value["solution_narration_reason"] == ""
    assert result.value["solution_narration_missing_sections"] == []
    assert result.value["general_narration_scheduled"] is True
    assert result.value["general_narration_status"] == "scheduled"
    assert result.value["general_narration_reason"] == ""
    assert result.value["general_narration_response_mode"] == "general_discussion"
    assert len(bus.events) == 1
    assert bus.events[0].name == "general_response_completed"
    assert bus.events[0].payload == {
        "response_mode": "general_discussion",
        "content": prose_reply,
    }


@pytest.mark.asyncio
async def test_general_discussion_output_truncated_does_not_trigger_solution_repair() -> None:
    prose_reply = "《战争与和平》通过个人命运呈现历史洪流中的选择"
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply=prose_reply,
        diagnostic="output_truncated",
        event_bus=bus,
        response_mode="general_discussion",
    )
    repair_calls = _install_repair_response(plugin, "must-not-be-used")

    result = await plugin.study_explain_text(text="你对《战争与和平》有什么看法")

    assert isinstance(result, Ok)
    assert repair_calls == []
    assert result.value["solution_repair_attempted"] is False
    assert result.value["solution_narration_status"] == "not_applicable"
    assert [event.name for event in bus.events] == ["general_response_completed"]


@pytest.mark.asyncio
async def test_non_truncated_natural_text_in_problem_mode_does_not_trigger_repair() -> None:
    prose_reply = "先理解题意，再选择适合的公式。"
    plugin = _ExplainHarness(reply=prose_reply, response_mode="problem_solving")
    repair_calls = _install_repair_response(plugin, "must-not-be-used")

    result = await plugin.study_explain_text(text="说说一般解题思路")

    assert isinstance(result, Ok)
    assert repair_calls == []
    assert result.value["solution_repair_attempted"] is False
    assert result.value["solution_narration_scheduled"] is False


@pytest.mark.asyncio
async def test_general_discussion_ignores_accidental_solution_headings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply=_STRUCTURED_REPLY,
        event_bus=bus,
        response_mode="general_discussion",
    )
    repair_calls = _install_repair_response(plugin, "must-not-be-used")
    monkeypatch.setattr(
        explain_entries,
        "parse_solution_structure",
        lambda _reply: (_ for _ in ()).throw(
            AssertionError("general discussion must not enter the solution parser")
        ),
    )

    result = await plugin.study_explain_text(text="谈谈你对一部文学作品的理解")

    assert isinstance(result, Ok)
    assert result.value["solution_narration_scheduled"] is False
    assert result.value["solution_narration_status"] == "not_applicable"
    assert result.value["solution_repair_attempted"] is False
    assert result.value["solution_narration_missing_sections"] == []
    assert repair_calls == []
    assert result.value["general_narration_scheduled"] is True
    assert result.value["general_narration_status"] == "scheduled"
    assert len(bus.events) == 1
    assert bus.events[0].name == "general_response_completed"
    assert bus.events[0].payload["response_mode"] == "general_discussion"


@pytest.mark.asyncio
async def test_general_explanation_schedules_general_narration_once() -> None:
    reply = "Opportunity cost is the value of the best alternative you give up."
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply=reply,
        event_bus=bus,
        response_mode="general_explanation",
    )

    result = await plugin.study_explain_text(text="What is opportunity cost?")

    assert isinstance(result, Ok)
    assert result.value["general_narration_scheduled"] is True
    assert result.value["general_narration_status"] == "scheduled"
    assert result.value["general_narration_response_mode"] == "general_explanation"
    assert [event.name for event in bus.events] == ["general_response_completed"]
    assert len(plugin._agent.calls) == 1


@pytest.mark.asyncio
async def test_problem_solving_never_schedules_duplicate_general_narration() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(event_bus=bus, response_mode="problem_solving")

    result = await plugin.study_explain_text(text="Solve the derivative problem")

    assert isinstance(result, Ok)
    assert result.value["solution_narration_scheduled"] is True
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "not_applicable"
    assert [event.name for event in bus.events] == ["solution_completed"]


@pytest.mark.asyncio
async def test_unknown_response_mode_does_not_schedule_either_narration() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply="A safe natural response.",
        event_bus=bus,
        response_mode="unknown",
    )

    result = await plugin.study_explain_text(text="Please explain this")

    assert isinstance(result, Ok)
    assert result.value["solution_narration_scheduled"] is False
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "not_applicable"
    assert result.value["general_narration_reason"] == "unsupported_response_mode"
    assert bus.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("communication_enabled", "general_narration_enabled", "reason"),
    [
        (False, True, "communication_disabled"),
        (True, False, "general_narration_disabled"),
    ],
)
async def test_general_narration_respects_independent_switches(
    communication_enabled: bool,
    general_narration_enabled: bool,
    reason: str,
) -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply="A literary discussion.",
        event_bus=bus,
        response_mode="general_discussion",
        communication_enabled=communication_enabled,
        general_narration_enabled=general_narration_enabled,
    )

    result = await plugin.study_explain_text(text="Discuss this novel")

    assert isinstance(result, Ok)
    assert result.value["solution_narration_status"] == "not_applicable"
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "disabled"
    assert result.value["general_narration_reason"] == reason
    assert bus.events == []


@pytest.mark.asyncio
async def test_general_narration_reports_degraded_without_delivery() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(
        reply="Temporary fallback prose.",
        degraded=True,
        event_bus=bus,
        response_mode="general_discussion",
    )

    result = await plugin.study_explain_text(text="Discuss this novel")

    assert isinstance(result, Ok)
    assert result.value["solution_narration_status"] == "not_applicable"
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "degraded"
    assert result.value["general_narration_reason"] == "degraded_reply"
    assert bus.events == []


@pytest.mark.asyncio
async def test_general_narration_reports_event_bus_unavailable() -> None:
    plugin = _ExplainHarness(
        reply="A literary discussion.",
        response_mode="general_discussion",
    )
    plugin._event_bus = None

    result = await plugin.study_explain_text(text="Discuss this novel")

    assert isinstance(result, Ok)
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "runtime_unavailable"
    assert result.value["general_narration_reason"] == "event_bus_unavailable"


@pytest.mark.asyncio
async def test_general_narration_reports_delivery_failure_without_losing_reply() -> None:
    reply = "A literary discussion that must remain visible."
    bus = _EventBus(fail=True)
    plugin = _ExplainHarness(
        reply=reply,
        event_bus=bus,
        response_mode="general_discussion",
    )

    result = await plugin.study_explain_text(text="Discuss this novel")

    assert isinstance(result, Ok)
    assert result.value["reply"] == reply
    assert result.value["general_narration_scheduled"] is False
    assert result.value["general_narration_status"] == "delivery_failed"
    assert result.value["general_narration_reason"] == "event_delivery_failed"


@pytest.mark.asyncio
async def test_study_explain_text_repair_preserves_original_image_context() -> None:
    incomplete_reply = (
        "### Problem Analysis\nRead the diagram.\n\n"
        "### Solution Process\nWork stops early.\n\n"
        "### Transfer Practice\nChange one condition."
    )
    repaired_json = json.dumps(
        {
            "analysis": "Read the diagram.",
            "process": "Complete the construction.",
            "answer": "The answer is B.",
            "transfer": "Change one condition.",
        }
    )
    plugin = _ExplainHarness(reply=incomplete_reply)
    repair_calls = _install_repair_response(plugin, repaired_json)

    result = await plugin.study_explain_text(
        text="solve the diagram",
        vision_image_base64=_PNG_IMAGE_BASE64,
    )

    assert isinstance(result, Ok)
    assert len(repair_calls) == 1
    user_content = repair_calls[0]["messages"][-1]["content"]
    assert isinstance(user_content, list)
    assert user_content[1]["image_url"]["url"].endswith(_PNG_IMAGE_BASE64)


@pytest.mark.asyncio
async def test_study_explain_text_reports_single_failed_repair_without_narration() -> None:
    incomplete_reply = (
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n推导中止。\n\n"
        "### 举一反三\n替换参数。"
    )
    bus = _EventBus()
    plugin = _ExplainHarness(reply=incomplete_reply, event_bus=bus)
    repair_calls = _install_repair_response(plugin, "not-json")

    result = await plugin.study_explain_text(text="一道题")

    assert isinstance(result, Ok)
    assert len(repair_calls) == 1
    assert result.value["reply"] == incomplete_reply
    assert result.value["solution_repair_attempted"] is True
    assert result.value["solution_narration_status"] == "incomplete"
    assert result.value["solution_narration_reason"] == "invalid_repair_response"
    assert result.value["solution_narration_missing_sections"] == ["answer"]
    assert result.value["solution_narration_scheduled"] is False
    assert bus.events == []


@pytest.mark.asyncio
async def test_failed_repair_logs_do_not_include_problem_image_ocr_or_full_reply() -> None:
    ocr_sentinel = "PRIVATE_OCR_SENTINEL_92731"
    reply_sentinel = "PRIVATE_FULL_REPLY_SENTINEL_38104"
    incomplete_reply = (
        f"### 题目解析\n{reply_sentinel}\n\n"
        "### 解题过程\n推导在公式中间停止。"
    )
    plugin = _ExplainHarness(
        reply=incomplete_reply,
        diagnostic="output_truncated",
        last_ocr_text=ocr_sentinel,
    )
    plugin._agent._logger = plugin.logger  # type: ignore[attr-defined]
    repair_calls = _install_repair_response(plugin, "not-json")

    result = await plugin.study_explain_text(
        text=ocr_sentinel,
        vision_image_base64=_PNG_IMAGE_BASE64,
    )

    assert isinstance(result, Ok)
    assert len(repair_calls) == 1
    assert result.value["reply"] == incomplete_reply
    assert plugin.logger.warnings
    logged = repr(plugin.logger.warnings)
    assert ocr_sentinel not in logged
    assert reply_sentinel not in logged
    assert _PNG_IMAGE_BASE64 not in logged


@pytest.mark.asyncio
async def test_study_explain_text_does_not_repair_when_narration_is_disabled() -> None:
    incomplete_reply = (
        "### 题目解析\n识别条件。\n\n"
        "### 解题过程\n推导中止。\n\n"
        "### 举一反三\n替换参数。"
    )
    plugin = _ExplainHarness(reply=incomplete_reply, narration_enabled=False)
    repair_calls = _install_repair_response(plugin, "must-not-be-used")

    result = await plugin.study_explain_text(text="一道题")

    assert isinstance(result, Ok)
    assert repair_calls == []
    assert result.value["reply"] == incomplete_reply
    assert result.value["solution_repair_attempted"] is False
    assert result.value["solution_narration_status"] == "disabled"
    assert result.value["solution_narration_reason"] == ""
    assert result.value["solution_narration_missing_sections"] == ["answer"]


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
async def test_study_explain_text_rechecks_solution_toggle_after_finalize() -> None:
    bus = _EventBus()
    plugin = _ExplainHarness(event_bus=bus)
    original_finalize = plugin._finalize_tutor_call

    async def _finalize_and_disable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = await original_finalize(*args, **kwargs)
        plugin._cfg = SimpleNamespace(
            language="zh-CN",
            llm_vision_enabled=True,
            communication=SimpleNamespace(
                enabled=True,
                solution_narration_enabled=False,
                general_narration_enabled=True,
            ),
        )
        return payload

    plugin._finalize_tutor_call = _finalize_and_disable  # type: ignore[method-assign]

    result = await plugin.study_explain_text(text="在持久化期间关闭讲题语音")

    assert isinstance(result, Ok)
    assert result.value["solution_narration_scheduled"] is False
    assert result.value["solution_narration_status"] == "disabled"
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
