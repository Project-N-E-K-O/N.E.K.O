from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.study_companion.entry_tutor_context_support import (
    _TutorContextSupportMixin,
)
from plugin.plugins.study_companion.entry_tutor_explain_entries import (
    _FINALIZE_TIMEOUT_SECONDS,
    _PRIMARY_EXPLAIN_TIMEOUT_SECONDS,
    _SOLUTION_REPAIR_TIMEOUT_SECONDS,
    _TutorExplainEntriesMixin,
)
from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.qwen_native_client import QwenNativeResult
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent
from plugin.plugins.study_companion.tutor_llm_agent_concept_explain import (
    repair_solution_structure,
)


pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def test_explain_entry_budget_reserves_a_repair_window_inside_entry_timeout() -> None:
    explain_meta = _TutorExplainEntriesMixin.study_explain_text.__neko_event_meta__
    image_meta = _TutorExplainEntriesMixin.study_submit_image.__neko_event_meta__

    assert _PRIMARY_EXPLAIN_TIMEOUT_SECONDS == 70.0
    assert _SOLUTION_REPAIR_TIMEOUT_SECONDS == 15.0
    assert _FINALIZE_TIMEOUT_SECONDS == 5.0
    assert explain_meta.timeout == 105.0
    assert image_meta.timeout == 105.0
    bounded_work = (
        _PRIMARY_EXPLAIN_TIMEOUT_SECONDS
        + _SOLUTION_REPAIR_TIMEOUT_SECONDS
        + _FINALIZE_TIMEOUT_SECONDS
    )
    assert explain_meta.timeout - bounded_work == 15.0
    assert image_meta.timeout - bounded_work == 15.0


@pytest.mark.asyncio
async def test_call_model_keeps_string_contract_and_output_limit_metadata() -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    expected = QwenNativeResult(
        text="partial answer",
        model="qwen-vl-max",
        model_group="agent_vision",
        request_id="request-1",
        input_tokens=20,
        output_tokens=3072,
        finish_reason="length",
        max_output_tokens=3072,
        output_limit_reached=True,
    )

    class _Client:
        async def call(self, *_args: Any, **_kwargs: Any) -> QwenNativeResult:
            return expected

    agent._qwen_client = _Client()  # type: ignore[assignment]

    result = await agent._call_model(
        [{"role": "user", "content": "solve"}],
        deadline=time.monotonic() + 30.0,
    )

    assert isinstance(result, str)
    assert result == "partial answer"


@pytest.mark.asyncio
async def test_concept_explain_clamps_deadline_and_exposes_truncation() -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    request_deadline = time.monotonic() + 30.0
    seen_deadlines: list[float] = []

    async def _call_model_result(
        _messages: list[dict[str, Any]], *, operation: str, deadline: float
    ) -> QwenNativeResult:
        assert operation == "concept_explain"
        seen_deadlines.append(deadline)
        return QwenNativeResult(
            text="A partial explanation",
            model="qwen-vl-max",
            model_group="agent_vision",
            request_id="request-1",
            input_tokens=20,
            output_tokens=3072,
            finish_reason="length",
            max_output_tokens=3072,
            output_limit_reached=True,
        )

    agent._call_model_result = _call_model_result  # type: ignore[method-assign]
    agent._new_operation_deadline = (  # type: ignore[method-assign]
        lambda _operation, _messages: request_deadline + 60.0
    )

    reply = await agent.concept_explain(
        "solve this",
        context={"deadline_monotonic": request_deadline},
    )

    assert seen_deadlines == [request_deadline]
    assert reply.degraded is False
    assert reply.reply == "A partial explanation"
    assert reply.diagnostic == "output_truncated"


@pytest.mark.asyncio
async def test_repair_clamps_to_entry_deadline() -> None:
    request_deadline = time.monotonic() + 30.0
    seen_deadlines: list[float] = []

    class _Agent:
        _logger = _Logger()
        _json_corrector = SimpleNamespace(
            parse_json_object=lambda raw: {
                "analysis": "analysis",
                "process": "process",
                "answer": "answer",
                "transfer": "transfer",
            }
        )

        @staticmethod
        def _new_operation_deadline(
            _operation: str, _messages: list[dict[str, Any]]
        ) -> float:
            return request_deadline + 60.0

        @staticmethod
        async def _call_model(
            _messages: list[dict[str, Any]], *, operation: str, deadline: float
        ) -> str:
            assert operation == "solution_structure_repair"
            seen_deadlines.append(deadline)
            return "{}"

    repaired = await repair_solution_structure(
        _Agent(),
        source_text="problem",
        incomplete_reply=(
            "Problem Analysis\nanalysis\n\nSolution Process\nprocess\n\nAnswer\nanswer"
        ),
        language="en",
        mode="companion",
        context={"deadline_monotonic": request_deadline},
    )

    assert seen_deadlines == [request_deadline]
    assert repaired is not None and repaired.complete


@pytest.mark.asyncio
async def test_repair_does_not_call_model_after_entry_deadline() -> None:
    class _Agent:
        _logger = _Logger()

        @staticmethod
        def _new_operation_deadline(
            _operation: str, _messages: list[dict[str, Any]]
        ) -> float:
            return time.monotonic() + 60.0

        @staticmethod
        async def _call_model(*_args: Any, **_kwargs: Any) -> str:
            raise AssertionError("expired repair must not call the model")

    result = await repair_solution_structure(
        _Agent(),
        source_text="problem",
        incomplete_reply=(
            "Problem Analysis\nanalysis\n\nSolution Process\nprocess\n\nAnswer\nanswer"
        ),
        language="en",
        mode="companion",
        context={"deadline_monotonic": time.monotonic() - 1.0},
    )

    assert result is None


@pytest.mark.asyncio
async def test_semantic_route_uses_entry_deadline_and_skips_expired_call() -> None:
    calls: list[float] = []
    reservations: list[str] = []

    class _Agent:
        @staticmethod
        async def reserve_optional_agent_call(operation: str):
            reservations.append(operation)
            return True, None

        @staticmethod
        def _new_operation_deadline(
            _operation: str, _messages: list[dict[str, Any]]
        ) -> float:
            return time.monotonic() + 60.0

        @staticmethod
        async def _call_model(
            _messages: list[dict[str, Any]], *, operation: str, deadline: float
        ) -> str:
            calls.append(deadline)
            return (
                '{"subject":"math","content_type":"problem","intent":"solve",'
                '"response_mode":"problem_solving","entity":"algebra",'
                '"retrieval_concepts":["equation"],"confidence":0.9}'
            )

    harness = SimpleNamespace(
        _agent=_Agent(),
        _cfg=SimpleNamespace(language="en"),
    )
    route = _TutorContextSupportMixin._route_study_input_semantics
    request_deadline = time.monotonic() + 5.0

    semantics, status, reason = await route(
        harness,
        "solve",
        context={"deadline_monotonic": request_deadline},
    )
    expired = await route(
        harness,
        "solve",
        context={"deadline_monotonic": time.monotonic() - 1.0},
    )

    assert semantics is not None
    assert (status, reason) == ("available", "")
    assert calls == [request_deadline]
    assert reservations == ["knowledge_semantic_route"]
    assert expired == (None, "routing_unavailable", "timeout")
