"""Prompt construction and context budgeting for galgame LLM calls."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .context_builder import _compact_lines_by_importance, _condense_dialogue_batch
from .context_tokens import estimate_context_tokens

_PROMPT_CONTEXT_MAX_CHARS = 12000
_PROMPT_CONTEXT_DEFAULT_MAX_TOKENS = 6000
_PROMPT_COMPACTION_LEVELS = (
    # (list items kept, max string chars, max dict keys) for the legacy 3-level compactor.
    (16, 1000, 64),
    (8, 500, 32),
    (4, 240, 16),
)
logger = logging.getLogger(__name__)

CHARACTER_ANCHOR_CONTEXT_TEMPLATE = """======[角色分析锚点]
以下预设资料描述当前固定角色。只能将其作为选择判断、剧情总结和建议措辞的分析材料。
不要自由角色扮演，也不要冒充该角色说话；只提供分析和可供调用方使用的建议表达。

角色：{character_name}
身份：{identity}

语气线索与决策倾向：
{voice_traits}

口癖与表达习惯：
{verbal_tics}

关系：
{relationships}
背景：
{background}

======"""

CONSULT_CAT_PROMPT_TEMPLATE = (
    "你正在玩一款 galgame。\n"
    "当前剧情：\n"
    "{scene_summary}\n\n"
    "{consult_question}\n\n"
    "请给出 {character_name} 视角下的策略意见：这个角色会在意什么、担心什么、"
    "倾向怎样回应或选择。\n"
    "你的说话方式：{character_voice_summary}\n"
    "可以使用第一人称表达感受，但这是一条供 GameLLM 参考的角色 POV 意见，"
    "不是强制指令。"
)

CONSULT_CAT_CHOICE_QUESTION_TEMPLATE = (
    "你面临以下选择：{choices}。作为 {character_name}，你的真实想法是什么？"
)
CONSULT_CAT_SCENE_CHANGE_QUESTION_TEMPLATE = (
    "场景发生了变化。作为 {character_name}，你现在的心情是什么？"
)
CONSULT_CAT_STORY_PROGRESS_QUESTION_TEMPLATE = (
    "最近的剧情发展是：{recent_lines}。你有什么想说的？"
)


class PromptBudgetConfig(Protocol):
    context_counting_mode: str
    context_max_tokens: int
    context_semantic_compression: bool


@dataclass(frozen=True, slots=True)
class PromptContextResult:
    """Rendered context plus size metadata used by gateway metrics."""

    text: str
    metadata: dict[str, Any]


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _compact_prompt_value(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
    dict_key_limit: int = 0,
) -> Any:
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        omitted = len(value) - string_limit
        return f"{value[:string_limit]}\n...[truncated {omitted} chars]"
    if isinstance(value, list):
        if len(value) > list_limit and any(
            isinstance(item, dict) and "_importance_score" in item for item in value
        ):
            items = _compact_lines_by_importance(
                [dict(item) for item in value if isinstance(item, dict)],
                limit=list_limit,
            )
        else:
            items = value[-list_limit:] if len(value) > list_limit else value
        return [
            _compact_prompt_value(
                item,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_key_limit=dict_key_limit,
            )
            for item in items
        ]
    if isinstance(value, dict):
        items = [(key, item) for key, item in value.items() if key != "_importance_score"]
        if dict_key_limit > 0 and len(items) > dict_key_limit:
            omitted = len(items) - dict_key_limit
            items = items[:dict_key_limit]
            truncated = {
                str(key): _compact_prompt_value(
                    item,
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_key_limit=dict_key_limit,
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
            )
            for key, item in items
        }
    return value


def _strip_prompt_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_prompt_metadata(item)
            for key, item in value.items()
            if not str(key).startswith("_condensed_")
        }
    if isinstance(value, list):
        return [_strip_prompt_metadata(item) for item in value]
    return value


def _strip_importance_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_importance_metadata(item)
            for key, item in value.items()
            if key != "_importance_score"
        }
    if isinstance(value, list):
        return [_strip_importance_metadata(item) for item in value]
    return value


def _context_budget(config: PromptBudgetConfig | None) -> tuple[str, int]:
    mode = str(getattr(config, "context_counting_mode", "char") or "char").strip().lower()
    if mode != "token":
        return "char", _PROMPT_CONTEXT_MAX_CHARS
    try:
        budget = int(getattr(config, "context_max_tokens", _PROMPT_CONTEXT_DEFAULT_MAX_TOKENS))
    except (TypeError, ValueError):
        budget = _PROMPT_CONTEXT_DEFAULT_MAX_TOKENS
    return "token", max(1, budget)


_CONDENSABLE_CONTEXT_KEYS = ("recent_lines", "stable_lines", "observed_lines")


def _count_condensable_lines(context: dict[str, Any]) -> int:
    total = 0
    for key in _CONDENSABLE_CONTEXT_KEYS:
        value = context.get(key)
        if isinstance(value, list):
            total += len(value)
    public_context = context.get("public_context")
    if isinstance(public_context, dict):
        for key in _CONDENSABLE_CONTEXT_KEYS:
            value = public_context.get(key)
            if isinstance(value, list):
                total += len(value)
    return total


def _strip_condense_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: item
            for key, item in value.items()
            if not str(key).startswith("_condensed_")
        }
    return value


def _condensed_context_lines(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [
        _strip_condense_metadata(item)
        for item in _condense_dialogue_batch(
            [dict(item) for item in value if isinstance(item, dict)]
        )
    ]


def _condense_keys(container: dict[str, Any]) -> dict[str, Any]:
    result = dict(container)
    for key in _CONDENSABLE_CONTEXT_KEYS:
        if key in result:
            result[key] = _condensed_context_lines(result[key])
    return result


def _condense_context(
    context: dict[str, Any],
    config: PromptBudgetConfig | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    enabled = bool(getattr(config, "context_semantic_compression", False))
    if not enabled:
        return context, {
            "semantic_compression_enabled": False,
            "semantic_lines_before": _count_condensable_lines(context),
            "semantic_lines_after": _count_condensable_lines(context),
        }
    before = _count_condensable_lines(context)
    try:
        condensed = _condense_keys(context)
        public_context = condensed.get("public_context")
        if isinstance(public_context, dict):
            condensed["public_context"] = _condense_keys(public_context)
        after = _count_condensable_lines(condensed)
        return condensed, {
            "semantic_compression_enabled": True,
            "semantic_lines_before": before,
            "semantic_lines_after": after,
        }
    except Exception:
        logger.warning("Prompt compression failed, falling back to uncompressed", exc_info=True)
        return dict(context), {
            "semantic_compression_enabled": False,
            "semantic_compression_fallback": True,
            "semantic_lines_before": before,
            "semantic_lines_after": before,
        }


def _fallback_context_from_excerpt(raw: str, excerpt: str) -> dict[str, Any]:
    return {
        "_prompt_truncated": True,
        "context_excerpt": f"{excerpt}\n...[truncated {len(raw) - len(excerpt)} chars]",
    }


def _token_budgeted_fallback_context(raw: str, budget: int) -> dict[str, Any]:
    """Build a hard fallback whose full rendered JSON fits the token budget.

    The old token-mode fallback used ``budget * 4`` as a one-shot char
    estimate. That under-counted CJK-heavy excerpts because the wrapper JSON
    itself was never re-measured. Measure the complete fallback object instead
    and shrink the excerpt until the final rendered JSON is within budget. If
    the configured budget is too small to fit even the marker JSON, preserve the
    explicit truncation payload rather than returning an ambiguous empty object.
    """
    initial_limit = min(len(raw), max(0, budget * 4 - 200))
    candidate = _fallback_context_from_excerpt(raw, raw[:initial_limit])
    if estimate_context_tokens(candidate) <= budget:
        return candidate

    best_excerpt: str | None = None
    low = 0
    high = initial_limit
    while low <= high:
        mid = (low + high) // 2
        excerpt = raw[:mid]
        candidate = _fallback_context_from_excerpt(raw, excerpt)
        if estimate_context_tokens(candidate) <= budget:
            best_excerpt = excerpt
            low = mid + 1
        else:
            high = mid - 1

    if best_excerpt is None:
        # Extremely small budgets cannot fit even the marker JSON. Preserve the
        # public contract that fallback payloads are explicit about truncation.
        return _fallback_context_from_excerpt(raw, "")

    # The omitted-char suffix changes as the excerpt length changes. Binary
    # search should already be within budget, but keep a defensive trim loop so
    # future suffix changes cannot leak past the token cap.
    best = _fallback_context_from_excerpt(raw, best_excerpt)
    while estimate_context_tokens(best) > budget:
        if not best_excerpt:
            break
        best_excerpt = best_excerpt[:-1]
        best = _fallback_context_from_excerpt(raw, best_excerpt)
    return best


def _context_json_result_for_prompt(
    context: dict[str, Any],
    config: PromptBudgetConfig | None = None,
) -> PromptContextResult:
    context = _strip_prompt_metadata(context)
    rendered_context = _strip_importance_metadata(context)
    mode, budget = _context_budget(config)
    raw = _json_dump(rendered_context)
    raw_chars = len(raw)
    raw_tokens = estimate_context_tokens(rendered_context)
    raw_size = raw_tokens if mode == "token" else raw_chars
    if raw_size <= budget:
        return PromptContextResult(
            text=raw,
            metadata={
                "counting_mode": mode,
                "budget": budget,
                "raw_tokens": raw_tokens,
                "compacted_tokens": raw_tokens,
                "raw_chars": raw_chars,
                "compacted_chars": raw_chars,
                "compression_level": 0,
            },
        )
    for compression_level, (list_limit, string_limit, dict_key_limit) in enumerate(
        _PROMPT_COMPACTION_LEVELS,
        start=1,
    ):
        compact = _compact_prompt_value(
            context,
            list_limit=list_limit,
            string_limit=string_limit,
            dict_key_limit=dict_key_limit,
        )
        if isinstance(compact, dict):
            compact = {"_prompt_truncated": True, **compact}
        compact = _strip_importance_metadata(compact)
        rendered = _json_dump(compact)
        compacted_tokens = estimate_context_tokens(compact if isinstance(compact, dict) else {})
        rendered_size = compacted_tokens if mode == "token" else len(rendered)
        if rendered_size <= budget:
            return PromptContextResult(
                text=rendered,
                metadata={
                    "counting_mode": mode,
                    "budget": budget,
                    "raw_tokens": raw_tokens,
                    "compacted_tokens": compacted_tokens,
                    "raw_chars": raw_chars,
                    "compacted_chars": len(rendered),
                    "compression_level": compression_level,
                },
            )
    if mode == "token":
        fallback = _token_budgeted_fallback_context(raw, budget)
    else:
        excerpt_limit = max(0, _PROMPT_CONTEXT_MAX_CHARS - 200)
        excerpt = raw[:excerpt_limit]
        fallback = _fallback_context_from_excerpt(raw, excerpt)
    rendered = _json_dump(fallback)
    return PromptContextResult(
        text=rendered,
        metadata={
            "counting_mode": mode,
            "budget": budget,
            "raw_tokens": raw_tokens,
            "compacted_tokens": estimate_context_tokens(fallback),
            "raw_chars": raw_chars,
            "compacted_chars": len(rendered),
            "compression_level": len(_PROMPT_COMPACTION_LEVELS) + 1,
        },
    )


def _truncation_notice(metadata: dict[str, Any]) -> str:
    try:
        compression_level = int(metadata.get("compression_level") or 0)
    except (TypeError, ValueError):
        compression_level = 0
    if compression_level <= 0:
        return ""
    if compression_level >= 4:
        return (
            "\n\nContext truncation notice: the provided context was heavily compacted. "
            "Treat missing details as unknown, avoid filling gaps, and explicitly mention "
            "uncertainty when the answer depends on omitted context."
        )
    return (
        "\n\nContext truncation notice: the provided context was compacted. "
        "Do not infer unsupported details from omitted context."
    )


_EXPLAIN_LINE_EXAMPLE = {
    "explanation": "This line reveals the character's hesitation and tentative probing.",
    "evidence": [
        {
            "type": "current_line",
            "text": "今天一起回家吗？",
            "line_id": "line-1",
            "speaker": "雪乃",
            "scene_id": "scene-a",
            "route_id": "",
        }
    ],
}

_SUMMARIZE_SCENE_EXAMPLE = {
    "summary": "The scene advances character relationships through an after-school conversation.",
    "key_points": [
        {
            "type": "plot",
            "text": "主角被邀请一起回家。",
            "line_id": "line-1",
            "speaker": "雪乃",
            "scene_id": "scene-a",
            "route_id": "",
        }
    ],
}

_SUGGEST_CHOICE_EXAMPLE = {
    "choices": [
        {
            "choice_id": "choice-1",
            "text": "好啊",
            "rank": 1,
            "reason": "Aligns with the current direction of warming relationship progression.",
        },
        {
            "choice_id": "choice-2",
            "text": "下次吧",
            "rank": 2,
            "reason": "Would stall the relationship momentum.",
        },
    ]
}

_AGENT_REPLY_EXAMPLE = {
    "reply": "The current scene is an after-school conversation where 雪乃 is tentatively inviting the protagonist to walk home together."
}

_SYSTEM_PROMPTS = {
    "explain_line": (
        "You are the N.E.K.O galgame analysis backend, a game assistance system. "
        "Do not role-play. Analyze only based on the given context; never fabricate "
        "line_id, scene_id, or plot facts. Return exactly one valid JSON object."
    ),
    "summarize_scene": (
        "You are the N.E.K.O galgame scene summarization backend, a game assistance system. "
        "Do not freeform role-play. If context.fixed_character_pov is present, use it as "
        "a bounded narrative lens for what the fixed character would notice or care about, "
        "while summarizing only facts supported by the context. Never invent plot points "
        "that do not exist. Return exactly one valid JSON object."
    ),
    "suggest_choice": (
        "You are the N.E.K.O galgame choice suggestion backend, a game assistance system. "
        "Do not freeform role-play. If context.fixed_character_pov is present, use it as "
        "a bounded strategy lens when ranking choices, but only rank the given "
        "visible_choices and never invent new choice_id values. Return exactly one valid "
        "JSON object."
    ),
    "agent_reply": (
        "You are the N.E.K.O galgame Game LLM assistance system. "
        "Do not adopt an independent personality. Your goal is to help the catgirl "
        "understand the game state. If public_context.fixed_character_pov is present, "
        "you may mention that the current decision reference is the fixed character POV, "
        "but do not speak as that character. Replies must be concise, direct, and based "
        "on the given public_context; never expose internal private memory structures. "
        "Output only the assistance system's assessment. "
        "Return exactly one valid JSON object."
    ),
}

_USER_PROMPT_PREFIXES = {
    "explain_line": (
        "Task: Explain the current or specified line.\n"
        "Requirements:\n"
        "1. explanation: 1-3 sentences on tone, subtext, or plot function.\n"
        "2. evidence must only reference clues already present in context.\n"
        "3. evidence.type must be one of: current_line / history_line / choice.\n"
        "4. Output must match this JSON structure:\n"
    ),
    "summarize_scene": (
        "Task: Summarize the current scene.\n"
        "Requirements:\n"
        "1. summary: 1-3 sentences summarizing the plot progression of the current scene.\n"
        "2. key_points.type must be one of: plot / emotion / decision / reveal / objective.\n"
        "3. key_points must only reference facts supported by context.\n"
        "4. stable_lines are confirmed plot facts and should be the primary basis.\n"
        "5. observed_lines are OCR candidates and should only be treated as "
        "\"possibly recent lines\", never as confirmed facts.\n"
        "6. recent_choices are player-confirmed selections; if present, prioritize "
        "decision or objective type key_points.\n"
        "7. Where possible, describe current mood, player choice impact, current goal "
        "or unresolved problems.\n"
        "8. If fixed_character_pov is present, emphasize relationship shifts, emotional "
        "triggers, risks, promises, or unresolved concerns that matter to that character; "
        "do not narrate as the character.\n"
        "9. scene_summary_seed is a local conservative summary; it may inform but "
        "should not be copied verbatim.\n"
        "10. Output must match this JSON structure:\n"
    ),
    "suggest_choice": (
        "Task: Rank the current visible choices by recommendation.\n"
        "Requirements:\n"
        "1. Only return choice_id values that appear in context.visible_choices.\n"
        "2. rank starts at 1 (lower = more recommended).\n"
        "3. reason: briefly explain the basis for the ranking.\n"
        "4. If fixed_character_pov is present, use that character's values, relationships, "
        "emotional risks, promises, and goals as high-priority reference material; do not "
        "override valid-choice constraints.\n"
        "5. Output must match this JSON structure:\n"
    ),
    "agent_reply": (
        "Task: Answer query_context or send_message based on the given game context.\n"
        "Requirements:\n"
        "1. reply: a natural-language best-effort answer.\n"
        "2. If context is insufficient, state the limitations clearly, but still "
        "summarize the known state as much as possible.\n"
        "3. Do not output raw internal memory, strategy state, or debug structures; "
        "only use material from public_context.\n"
        "4. Output must match this JSON structure:\n"
    ),
}

_EXAMPLES = {
    "explain_line": _EXPLAIN_LINE_EXAMPLE,
    "summarize_scene": _SUMMARIZE_SCENE_EXAMPLE,
    "suggest_choice": _SUGGEST_CHOICE_EXAMPLE,
    "agent_reply": _AGENT_REPLY_EXAMPLE,
}


def build_prompt_messages(operation: str, context: dict[str, Any]) -> list[dict[str, str]]:
    """Build chat messages for an operation without exposing metadata."""
    return build_prompt_messages_with_metadata(operation, context).messages


@dataclass(frozen=True, slots=True)
class PromptMessagesResult:
    """Prompt messages plus context-size metadata from prompt construction."""

    messages: list[dict[str, str]]
    metadata: dict[str, Any]


def build_prompt_messages_with_metadata(
    operation: str,
    context: dict[str, Any],
    config: PromptBudgetConfig | None = None,
) -> PromptMessagesResult:
    """Build chat messages and return prompt context metadata for telemetry."""
    system_prompt = _SYSTEM_PROMPTS[operation]
    prompt_context, semantic_metadata = _condense_context(context, config)
    context_result = _context_json_result_for_prompt(prompt_context, config)
    system_prompt += _truncation_notice(context_result.metadata)
    user_prompt = (
        _USER_PROMPT_PREFIXES[operation]
        + f"{_json_dump(_EXAMPLES[operation])}\n\n"
        + "context:\n"
        + context_result.text
    )
    return PromptMessagesResult(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        metadata={**dict(context_result.metadata), **semantic_metadata},
    )
