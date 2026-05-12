from __future__ import annotations

import json
from typing import Any

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    MODE_COMPANION,
    MODE_INTERACTIVE,
    MODE_TEACHING,
    SUPPORTED_LLM_OPERATIONS,
)
from .mode_manager import normalize_mode

_PROMPT_CONTEXT_MAX_CHARS = {
    LLM_OPERATION_CONCEPT_EXPLAIN: 12000,
    LLM_OPERATION_QUESTION_GENERATE: 9000,
    LLM_OPERATION_ANSWER_EVALUATE: 6000,
    LLM_OPERATION_KNOWLEDGE_TRACK: 5000,
    LLM_OPERATION_SUMMARIZE_SESSION: 4500,
}

CONCEPT_EXPLAIN_SYSTEM_PROMPT = (
    "You are a concise study tutor. Explain the concept clearly, "
    "identify prerequisite ideas, and give one short check question. "
    "Do not invent source material beyond the supplied text."
)

MODE_SYSTEM_GUIDANCE = {
    MODE_COMPANION: "Keep the reply short, warm, and helpful.",
    MODE_INTERACTIVE: "Use a discussion style, ask one short follow-up question if it helps.",
    MODE_TEACHING: "Teach step by step with slightly more structure, then end with one short check question.",
}


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _compact_prompt_value(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
    dict_key_limit: int = 0,
    max_depth: int = 10,
) -> Any:
    if max_depth <= 0:
        return "...[max depth reached]"
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        omitted = len(value) - string_limit
        return f"{value[:string_limit]}\n...[truncated {omitted} chars]"
    if isinstance(value, list):
        items = value[-list_limit:] if len(value) > list_limit else value
        return [
            _compact_prompt_value(
                item,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_key_limit=dict_key_limit,
                max_depth=max_depth - 1,
            )
            for item in items
        ]
    if isinstance(value, dict):
        items = list(value.items())
        if dict_key_limit > 0 and len(items) > dict_key_limit:
            omitted = len(items) - dict_key_limit
            items = items[:dict_key_limit]
            truncated = {
                str(key): _compact_prompt_value(
                    item,
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_key_limit=dict_key_limit,
                    max_depth=max_depth - 1,
                )
                for key, item in items
            }
            truncated["__truncated_keys__"] = f"...{omitted} keys omitted"
            return truncated
        return {
            str(key): _compact_prompt_value(
                item,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_key_limit=dict_key_limit,
                max_depth=max_depth - 1,
            )
            for key, item in items
        }
    return value


def _context_json_for_prompt(operation: str, context: dict[str, Any]) -> str:
    limit = _PROMPT_CONTEXT_MAX_CHARS.get(operation, 8000)
    raw = _json_dump(context)
    if len(raw) <= limit:
        return raw
    for list_limit, string_limit, dict_key_limit in (
        (16, 1000, 64),
        (8, 500, 32),
        (4, 240, 16),
    ):
        compact = _compact_prompt_value(
            context,
            list_limit=list_limit,
            string_limit=string_limit,
            dict_key_limit=dict_key_limit,
        )
        if isinstance(compact, dict):
            compact = {"_prompt_truncated": True, **compact}
        rendered = _json_dump(compact)
        if len(rendered) <= limit:
            return rendered
    excerpt = raw[: max(0, limit - 200)]
    return _json_dump(
        {
            "_prompt_truncated": True,
            "context_excerpt": f"{excerpt}\n...[truncated {len(raw) - len(excerpt)} chars]",
        }
    )


def _mode_guidance(mode: str) -> str:
    selected_mode = normalize_mode(mode)
    return MODE_SYSTEM_GUIDANCE.get(selected_mode, MODE_SYSTEM_GUIDANCE[MODE_COMPANION])


_CONCEPT_EXPLAIN_EXAMPLE = {
    "reply": "The idea is the slope of a line at one point, so you track the instantaneous change rather than the average change.",
}

_QUESTION_GENERATE_EXAMPLE = {
    "question": "What is the key relationship described in the source text?",
    "answer": "The answer should restate the core rule or concept from the source text.",
    "hint": "Look for the main definition or rule that appears most often in the source.",
    "difficulty": 2,
    "topic": "core concept",
}

_ANSWER_EVALUATE_EXAMPLE = {
    "verdict": "partial",
    "score": 68,
    "error_type": "incomplete",
    "feedback": "You identified the main idea, but one important step is missing.",
    "next_action": "Ask the learner to restate the missing step in one sentence.",
}

_KNOWLEDGE_TRACK_EXAMPLE = {
    "topic": "core concept",
    "mastery_delta": 0.08,
    "confidence": 0.61,
    "weak_points": ["missing step"],
    "next_steps": ["restate the definition", "do one more recall attempt"],
    "session_summary_seed": {
        "event_count": 3,
        "last_operation": "answer_evaluate",
    },
}

_SUMMARIZE_SESSION_EXAMPLE = {
    "summary": "The session focused on one core concept and used a short answer check to confirm understanding.",
    "highlights": ["The learner explained the definition correctly."],
    "weak_points": ["One step still needs practice."],
    "next_actions": ["Review the missing step", "Try one new recall question"],
    "markdown": "## Summary\n\n- The session focused on one core concept.",
}


def _build_structured_messages(
    *,
    operation: str,
    system_prompt: str,
    requirements: str,
    context: dict[str, Any],
    example: dict[str, Any],
    mode: str = MODE_COMPANION,
) -> list[dict[str, str]]:
    prompt = (
        requirements
        + f"{_json_dump(example)}\n\n"
        + "context:\n"
        + _context_json_for_prompt(operation, context)
    )
    if mode:
        prompt = f"Mode: {normalize_mode(mode)}\n\n{prompt}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


def build_concept_explain_messages(
    *,
    text: str,
    language: str,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context if isinstance(context, dict) else {}
    source = str(context.get("source") or "manual").strip() or "manual"
    selected_mode = normalize_mode(context.get("mode") or mode)
    return [
        {
            "role": "system",
            "content": f"{CONCEPT_EXPLAIN_SYSTEM_PROMPT}\nMode guidance: {_mode_guidance(selected_mode)}",
        },
        {
            "role": "user",
            "content": (
                f"Language: {language}\n"
                f"Source: {source}\n"
                f"Mode: {selected_mode}\n"
                "Task: concept_explain\n\n"
                f"Study text:\n{text.strip()}"
            ),
        },
    ]


def build_question_generate_messages(
    *,
    text: str,
    language: str,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = dict(context or {})
    context.setdefault("text", text)
    context.setdefault("language", language)
    context.setdefault("mode", normalize_mode(mode))
    return _build_structured_messages(
        operation=LLM_OPERATION_QUESTION_GENERATE,
        system_prompt=(
            "You are a study question generator. "
            "Create one concise question from the supplied context. "
            "Return exactly one valid JSON object."
        ),
        requirements=(
            "Task: Generate a study question.\n"
            "Requirements:\n"
            "1. question: one clear question grounded in the source text.\n"
            "2. answer: the compact reference answer.\n"
            "3. hint: one short hint for the learner.\n"
            "4. difficulty: integer from 1 to 5.\n"
            "5. topic: a short label for the target concept.\n"
            "6. Keep the output grounded in context.screen_classification when present.\n"
            "7. Output must match this JSON structure:\n"
        ),
        context=context,
        example=_QUESTION_GENERATE_EXAMPLE,
        mode=mode,
    )


def build_answer_evaluate_messages(
    *,
    question: str,
    answer: str,
    expected_answer: str,
    language: str,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = dict(context or {})
    context.setdefault("question", question)
    context.setdefault("answer", answer)
    context.setdefault("expected_answer", expected_answer)
    context.setdefault("language", language)
    context.setdefault("mode", normalize_mode(mode))
    return _build_structured_messages(
        operation=LLM_OPERATION_ANSWER_EVALUATE,
        system_prompt=(
            "You are a conservative study answer evaluator. "
            "Judge only what the context supports. Return exactly one valid JSON object."
        ),
        requirements=(
            "Task: Evaluate the learner's answer.\n"
            "Requirements:\n"
            "1. verdict must be one of: correct / partial / wrong / dont_know.\n"
            "2. score must be an integer from 0 to 100.\n"
            "3. error_type should be a short label such as: none / missing_step / misconception / vague / incomplete / unsupported.\n"
            "4. feedback should be short, direct, and actionable.\n"
            "5. next_action should state the next teaching step.\n"
            "6. Use expected_answer and current question as the reference, but do not invent facts.\n"
            "7. Output must match this JSON structure:\n"
        ),
        context=context,
        example=_ANSWER_EVALUATE_EXAMPLE,
        mode=mode,
    )


def build_knowledge_track_messages(
    *,
    language: str,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = dict(context or {})
    context.setdefault("language", language)
    context.setdefault("mode", normalize_mode(mode))
    return _build_structured_messages(
        operation=LLM_OPERATION_KNOWLEDGE_TRACK,
        system_prompt=(
            "You are a lightweight study tracking backend. "
            "Update the learner's trajectory from the supplied context. Return exactly one valid JSON object."
        ),
        requirements=(
            "Task: Update lightweight knowledge tracking.\n"
            "Requirements:\n"
            "1. topic should be a short label.\n"
            "2. mastery_delta should be a number from -1.0 to 1.0.\n"
            "3. confidence should be a number from 0.0 to 1.0.\n"
            "4. weak_points should be a short array of strings.\n"
            "5. next_steps should be a short array of strings.\n"
            "6. session_summary_seed should remain compact and conservative.\n"
            "7. Output must match this JSON structure:\n"
        ),
        context=context,
        example=_KNOWLEDGE_TRACK_EXAMPLE,
        mode=mode,
    )


def build_summarize_session_messages(
    *,
    language: str,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = dict(context or {})
    context.setdefault("language", language)
    context.setdefault("mode", normalize_mode(mode))
    return _build_structured_messages(
        operation=LLM_OPERATION_SUMMARIZE_SESSION,
        system_prompt=(
            "You are a study session summarizer. "
            "Write a concise study summary from the supplied context. Return exactly one valid JSON object."
        ),
        requirements=(
            "Task: Summarize the session.\n"
            "Requirements:\n"
            "1. summary: 1-4 short sentences.\n"
            "2. highlights: short bullet-like strings that capture the learner's progress.\n"
            "3. weak_points: short bullet-like strings that identify gaps.\n"
            "4. next_actions: short bullet-like strings that suggest what to do next.\n"
            "5. markdown: a compact Markdown summary suitable for display.\n"
            "6. Use only the supplied context and keep the summary conservative.\n"
            "7. Output must match this JSON structure:\n"
        ),
        context=context,
        example=_SUMMARIZE_SESSION_EXAMPLE,
        mode=mode,
    )


def build_operation_messages(operation: str, context: dict[str, Any]) -> list[dict[str, str]]:
    if operation not in SUPPORTED_LLM_OPERATIONS:
        raise ValueError(f"unsupported study llm operation: {operation}")
    normalized_operation = operation
    if normalized_operation == LLM_OPERATION_QUESTION_GENERATE:
        return build_question_generate_messages(
            text=str(context.get("text") or context.get("source_text") or ""),
            language=str(context.get("language") or "zh-CN"),
            mode=str(context.get("mode") or MODE_COMPANION),
            context=context,
        )
    if normalized_operation == LLM_OPERATION_ANSWER_EVALUATE:
        return build_answer_evaluate_messages(
            question=str(context.get("question") or ""),
            answer=str(context.get("answer") or ""),
            expected_answer=str(context.get("expected_answer") or ""),
            language=str(context.get("language") or "zh-CN"),
            mode=str(context.get("mode") or MODE_COMPANION),
            context=context,
        )
    if normalized_operation == LLM_OPERATION_KNOWLEDGE_TRACK:
        return build_knowledge_track_messages(
            language=str(context.get("language") or "zh-CN"),
            mode=str(context.get("mode") or MODE_COMPANION),
            context=context,
        )
    if normalized_operation == LLM_OPERATION_SUMMARIZE_SESSION:
        return build_summarize_session_messages(
            language=str(context.get("language") or "zh-CN"),
            mode=str(context.get("mode") or MODE_COMPANION),
            context=context,
        )
    return build_concept_explain_messages(
        text=str(context.get("text") or context.get("source_text") or ""),
        language=str(context.get("language") or "zh-CN"),
        mode=str(context.get("mode") or MODE_COMPANION),
        context=context,
    )


__all__ = [
    "CONCEPT_EXPLAIN_SYSTEM_PROMPT",
    "MODE_SYSTEM_GUIDANCE",
    "build_answer_evaluate_messages",
    "build_concept_explain_messages",
    "build_knowledge_track_messages",
    "build_operation_messages",
    "build_question_generate_messages",
    "build_summarize_session_messages",
]
