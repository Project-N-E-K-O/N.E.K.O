from __future__ import annotations

import json

import pytest

from plugin.plugins.study_companion.constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
)
from plugin.plugins.study_companion.llm_prompts import (
    _compact_prompt_value,
    _context_json_for_prompt,
    build_operation_messages,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "operation",
    [
        LLM_OPERATION_CONCEPT_EXPLAIN,
        LLM_OPERATION_QUESTION_GENERATE,
        LLM_OPERATION_ANSWER_EVALUATE,
        LLM_OPERATION_KNOWLEDGE_TRACK,
        LLM_OPERATION_SUMMARIZE_SESSION,
    ],
)
def test_build_operation_messages_returns_system_and_user_contract(operation: str) -> None:
    messages = build_operation_messages(
        operation,
        {
            "text": "The derivative measures instantaneous change.",
            "question": "What does derivative measure?",
            "answer": "change",
            "expected_answer": "instantaneous change",
            "language": "en",
            "mode": "teaching",
            "source": "manual",
        },
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(message["content"] for message in messages)
    assert "teaching" in messages[1]["content"]


def test_structured_prompt_injects_json_example_context_and_truncates_large_values() -> None:
    context = {
        "language": "en",
        "mode": "interactive",
        "items": list(range(100)),
        "text": "x" * 20000,
    }

    rendered = _context_json_for_prompt(LLM_OPERATION_SUMMARIZE_SESSION, context)
    parsed = json.loads(rendered)

    assert parsed["_prompt_truncated"] is True
    assert len(rendered) <= 4500
    assert "context_excerpt" in parsed or "text" in parsed


def test_compact_prompt_value_limits_depth_lists_strings_and_dict_keys() -> None:
    compact = _compact_prompt_value(
        {"a": "x" * 20, "b": [1, 2, 3, 4], "c": {"nested": {"too": "deep"}}},
        list_limit=2,
        string_limit=5,
        dict_key_limit=2,
        max_depth=3,
    )

    assert compact["a"].startswith("xxxxx")
    assert compact["b"] == [3, 4]
    assert compact["__truncated_keys__"] == "...1 keys omitted"


def test_build_operation_messages_rejects_unknown_operation() -> None:
    with pytest.raises(ValueError, match="unsupported study llm operation"):
        build_operation_messages("missing", {})
