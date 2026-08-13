from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import plugin.plugins.study_companion.entry_tutor_explain_entries as explain_entries
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
from plugin.sdk.plugin import Err, Ok


pytestmark = pytest.mark.unit


_INCOMPLETE_REPLY = (
    "### 题目解析\n识别条件。\n\n### 解题过程\n推导中止。\n\n### 举一反三\n替换参数。"
)


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))


class _ScheduleOnlyBus:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.emit_started = False

    def schedule_emit(self, event: Any) -> object:
        self.events.append(event)
        return object()

    async def emit(self, _event: Any) -> None:
        self.emit_started = True
        await asyncio.Event().wait()


class _Agent:
    def __init__(self, reply: str, *, block: bool = False) -> None:
        self.reply = reply
        self.block = block
        self.contexts: list[dict[str, Any]] = []

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        self.contexts.append(dict(context or {}))
        if self.block:
            await asyncio.Event().wait()
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text=text,
            reply=self.reply,
            created_at="2026-08-13T00:00:00Z",
        )


class _Harness(_TutorExplainEntriesMixin, _CommunicationTutorEventsMixin):
    def __init__(
        self,
        *,
        reply: str = "普通解释内容。",
        response_mode: str = "general_explanation",
        block_primary: bool = False,
        finalize_behavior: str = "success",
        bus: _ScheduleOnlyBus | None = None,
    ) -> None:
        self._cfg = SimpleNamespace(
            language="zh-CN",
            llm_vision_enabled=True,
            communication=SimpleNamespace(
                enabled=True,
                solution_narration_enabled=True,
                general_narration_enabled=True,
            ),
        )
        self._state = SimpleNamespace(active_mode=MODE_COMPANION, last_ocr_text="")
        self._lock = asyncio.Lock()
        self._agent = _Agent(reply, block=block_primary)
        self._event_bus = bus
        self._response_mode = response_mode
        self._finalize_behavior = finalize_behavior
        self.logger = _Logger()

    async def _apply_mode_switch(
        self, mode: str, _reason: str, *, language: str
    ) -> dict[str, Any]:
        self._state.active_mode = mode
        return {"changed": True, "new_mode": mode, "transition_phrase": language}

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
        self, _operation: str, reply: TutorReply, **_kwargs: Any
    ) -> dict[str, Any]:
        if self._finalize_behavior == "block":
            await asyncio.Event().wait()
        if self._finalize_behavior == "error":
            raise RuntimeError("history store unavailable")
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
async def test_primary_phase_is_enforced_by_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _Harness(block_primary=True)
    monkeypatch.setattr(explain_entries, "_PRIMARY_EXPLAIN_TIMEOUT_SECONDS", 0.01)

    result = await asyncio.wait_for(
        plugin.study_explain_text(text="主模型不返回"), timeout=0.1
    )

    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_repair_timeout_keeps_original_reply_and_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _Harness(reply=_INCOMPLETE_REPLY, response_mode="problem_solving")

    async def never_returns(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(explain_entries, "repair_solution_structure", never_returns)
    monkeypatch.setattr(
        explain_entries, "_SOLUTION_REPAIR_TIMEOUT_SECONDS", 0.01, raising=False
    )
    monkeypatch.setattr(
        explain_entries,
        "_SOLUTION_REPAIR_MIN_REMAINING_SECONDS",
        0.001,
    )

    result = await asyncio.wait_for(
        plugin.study_explain_text(text="修复不返回"), timeout=0.1
    )

    assert isinstance(result, Ok)
    assert result.value["reply"] == _INCOMPLETE_REPLY
    assert result.value["solution_repair_attempted"] is True
    assert result.value["solution_narration_status"] == "incomplete"
    assert result.value["solution_narration_reason"] == "insufficient_time_budget"


@pytest.mark.asyncio
async def test_invalid_repair_keeps_original_reply_and_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _Harness(reply=_INCOMPLETE_REPLY, response_mode="problem_solving")

    async def invalid_repair(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(explain_entries, "repair_solution_structure", invalid_repair)

    result = await plugin.study_explain_text(text="修复返回无效结构")

    assert isinstance(result, Ok)
    assert result.value["reply"] == _INCOMPLETE_REPLY
    assert result.value["solution_narration_status"] == "incomplete"
    assert result.value["solution_narration_reason"] == "invalid_repair_response"


@pytest.mark.asyncio
async def test_malformed_repair_object_keeps_original_reply_and_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _Harness(reply=_INCOMPLETE_REPLY, response_mode="problem_solving")

    async def malformed_repair(*_args: Any, **_kwargs: Any) -> object:
        return object()

    monkeypatch.setattr(explain_entries, "repair_solution_structure", malformed_repair)

    result = await plugin.study_explain_text(text="修复返回错误类型")

    assert isinstance(result, Ok)
    assert result.value["reply"] == _INCOMPLETE_REPLY
    assert result.value["solution_narration_status"] == "incomplete"
    assert result.value["solution_narration_reason"] == "invalid_repair_response"


@pytest.mark.asyncio
async def test_repair_exception_keeps_original_reply_and_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _Harness(reply=_INCOMPLETE_REPLY, response_mode="problem_solving")

    async def failing_repair(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("repair provider unavailable")

    monkeypatch.setattr(explain_entries, "repair_solution_structure", failing_repair)

    result = await plugin.study_explain_text(text="修复调用失败")

    assert isinstance(result, Ok)
    assert result.value["reply"] == _INCOMPLETE_REPLY
    assert result.value["solution_narration_status"] == "incomplete"
    assert result.value["solution_narration_reason"] == "invalid_repair_response"
    assert plugin.logger.warnings


@pytest.mark.asyncio
async def test_finalize_timeout_returns_compatible_visible_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _Harness(finalize_behavior="block")
    monkeypatch.setattr(
        explain_entries, "_FINALIZE_TIMEOUT_SECONDS", 0.01, raising=False
    )

    result = await asyncio.wait_for(
        plugin.study_explain_text(text="历史保存不返回"), timeout=0.1
    )

    assert isinstance(result, Ok)
    assert result.value["reply"] == "普通解释内容。"
    assert result.value["summary"] == "普通解释内容。"
    assert result.value["history_persisted"] is False
    assert result.value["diagnostic"] == "history_persist_timeout"
    assert plugin.logger.warnings


@pytest.mark.asyncio
async def test_finalize_failure_returns_compatible_visible_reply() -> None:
    plugin = _Harness(finalize_behavior="error")

    result = await plugin.study_explain_text(text="历史保存失败")

    assert isinstance(result, Ok)
    assert result.value["reply"] == "普通解释内容。"
    assert result.value["history_persisted"] is False
    assert result.value["diagnostic"] == "history_persist_failed"


@pytest.mark.asyncio
async def test_general_narration_uses_schedule_emit_without_awaiting_delivery() -> None:
    bus = _ScheduleOnlyBus()
    plugin = _Harness(bus=bus)

    result = await asyncio.wait_for(
        plugin.study_explain_text(text="解释机会成本"), timeout=0.1
    )

    assert isinstance(result, Ok)
    assert result.value["general_narration_scheduled"] is True
    assert [event.name for event in bus.events] == ["general_response_completed"]
    assert bus.emit_started is False


def test_deadline_constants_match_entry_budget_contract() -> None:
    assert explain_entries._PRIMARY_EXPLAIN_TIMEOUT_SECONDS == 70.0
    assert explain_entries._SOLUTION_REPAIR_TIMEOUT_SECONDS == 15.0
    assert explain_entries._FINALIZE_TIMEOUT_SECONDS == 5.0
