from __future__ import annotations

import json
from typing import Any

from .prompt_templates import (
    STUDY_ANSWER_EVALUATE_EXAMPLE,
    STUDY_ANSWER_EVALUATE_REQUIREMENTS,
    STUDY_ANSWER_EVALUATE_SYSTEM_PROMPT,
    STUDY_CONCEPT_EXPLAIN_SYSTEM_WITH_MODE_TEMPLATE,
    STUDY_CONCEPT_EXPLAIN_SYSTEM_PROMPT,
    STUDY_CONCEPT_EXPLAIN_USER_TEMPLATE,
    STUDY_KNOWLEDGE_TRACK_EXAMPLE,
    STUDY_KNOWLEDGE_TRACK_REQUIREMENTS,
    STUDY_KNOWLEDGE_TRACK_SYSTEM_PROMPT,
    STUDY_MODE_SYSTEM_GUIDANCE,
    STUDY_PROMPT_CONTEXT_MAX_TOKENS,
    STUDY_QUESTION_GENERATE_EXAMPLE,
    STUDY_QUESTION_GENERATE_REQUIREMENTS,
    STUDY_QUESTION_GENERATE_SYSTEM_PROMPT,
    STUDY_STRUCTURED_MODE_PREFIX_TEMPLATE,
    STUDY_STRUCTURED_USER_TEMPLATE,
    STUDY_SUMMARIZE_SESSION_EXAMPLE,
    STUDY_SUMMARIZE_SESSION_REQUIREMENTS,
    STUDY_SUMMARIZE_SESSION_SYSTEM_PROMPT,
)
from utils.tokenize import count_tokens, truncate_to_tokens

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    MODE_COMPANION,
    SUPPORTED_LLM_OPERATIONS,
)
from .mode_manager import normalize_mode


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
    limit = STUDY_PROMPT_CONTEXT_MAX_TOKENS.get(operation, 4000)
    raw = _json_dump(context)
    if count_tokens(raw) <= limit:
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
        if count_tokens(rendered) <= limit:
            return rendered
    excerpt = truncate_to_tokens(raw, max(0, limit - 100))
    fallback = {
        "_prompt_truncated": True,
        "context_excerpt": f"{excerpt}\n...[truncated {len(raw) - len(excerpt)} chars]",
    }
    while count_tokens(_json_dump(fallback)) > limit and excerpt:
        excerpt = excerpt[:-1]
        fallback["context_excerpt"] = f"{excerpt}\n...[truncated {len(raw) - len(excerpt)} chars]"
    return _json_dump(
        fallback
    )


def _mode_guidance(mode: str) -> str:
    selected_mode = normalize_mode(mode)
    return STUDY_MODE_SYSTEM_GUIDANCE.get(selected_mode, STUDY_MODE_SYSTEM_GUIDANCE[MODE_COMPANION])


def _build_structured_messages(
    *,
    operation: str,
    system_prompt: str,
    requirements: str,
    context: dict[str, Any],
    example: dict[str, Any],
    mode: str = MODE_COMPANION,
) -> list[dict[str, str]]:
    prompt = STUDY_STRUCTURED_USER_TEMPLATE.format(
        requirements=requirements,
        example_json=_json_dump(example),
        context_json=_context_json_for_prompt(operation, context),
    )
    if mode:
        prompt = STUDY_STRUCTURED_MODE_PREFIX_TEMPLATE.format(mode=normalize_mode(mode), prompt=prompt)
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
    guidance = context.get("knowledge_guidance")
    guidance_block = ""
    if isinstance(guidance, dict) and guidance:
        prompt_guidance = (
            guidance.get("model_context")
            if isinstance(guidance.get("model_context"), dict)
            else guidance
        )
        guidance_block = (
            "\n\nKnowledge graph guidance:\n"
            f"{_context_json_for_prompt(LLM_OPERATION_CONCEPT_EXPLAIN, {'knowledge_guidance': prompt_guidance})}"
        )
    return [
        {
            "role": "system",
            "content": STUDY_CONCEPT_EXPLAIN_SYSTEM_WITH_MODE_TEMPLATE.format(
                system_prompt=STUDY_CONCEPT_EXPLAIN_SYSTEM_PROMPT,
                mode_guidance=_mode_guidance(selected_mode),
            ),
        },
        {
            "role": "user",
            "content": STUDY_CONCEPT_EXPLAIN_USER_TEMPLATE.format(
                language=language,
                source=source,
                mode=selected_mode,
                text=f"{text.strip()}{guidance_block}",
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
        system_prompt=STUDY_QUESTION_GENERATE_SYSTEM_PROMPT,
        requirements=STUDY_QUESTION_GENERATE_REQUIREMENTS,
        context=context,
        example=STUDY_QUESTION_GENERATE_EXAMPLE,
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
        system_prompt=STUDY_ANSWER_EVALUATE_SYSTEM_PROMPT,
        requirements=STUDY_ANSWER_EVALUATE_REQUIREMENTS,
        context=context,
        example=STUDY_ANSWER_EVALUATE_EXAMPLE,
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
        system_prompt=STUDY_KNOWLEDGE_TRACK_SYSTEM_PROMPT,
        requirements=STUDY_KNOWLEDGE_TRACK_REQUIREMENTS,
        context=context,
        example=STUDY_KNOWLEDGE_TRACK_EXAMPLE,
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
        system_prompt=STUDY_SUMMARIZE_SESSION_SYSTEM_PROMPT,
        requirements=STUDY_SUMMARIZE_SESSION_REQUIREMENTS,
        context=context,
        example=STUDY_SUMMARIZE_SESSION_EXAMPLE,
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
    "build_answer_evaluate_messages",
    "build_concept_explain_messages",
    "build_knowledge_track_messages",
    "build_operation_messages",
    "build_question_generate_messages",
    "build_summarize_session_messages",
]
