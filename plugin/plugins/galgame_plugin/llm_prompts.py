from __future__ import annotations

import json
from typing import Any

_PROMPT_CONTEXT_MAX_CHARS = 12000


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


def _context_json_for_prompt(context: dict[str, Any]) -> str:
    raw = _json_dump(context)
    if len(raw) <= _PROMPT_CONTEXT_MAX_CHARS:
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
        if len(rendered) <= _PROMPT_CONTEXT_MAX_CHARS:
            return rendered
    excerpt = raw[: max(0, _PROMPT_CONTEXT_MAX_CHARS - 200)]
    return _json_dump(
        {
            "_prompt_truncated": True,
            "context_excerpt": f"{excerpt}\n...[truncated {len(raw) - len(excerpt)} chars]",
        }
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
        "Do not role-play. Summarize only based on the given context; never invent plot "
        "points that do not exist. Return exactly one valid JSON object."
    ),
    "suggest_choice": (
        "You are the N.E.K.O galgame choice suggestion backend, a game assistance system. "
        "Do not role-play. Only rank the given visible_choices; never invent new choice_id "
        "values. Return exactly one valid JSON object."
    ),
    "agent_reply": (
        "You are the N.E.K.O galgame Game LLM assistance system. "
        "Do not role-play or adopt any personality. Your goal is to help the catgirl "
        "understand the game state. Replies must be concise, direct, and based on the "
        "given public_context; never expose internal private memory structures. "
        "Do not speak as a game character, the catgirl, or any independent persona; "
        "output only the assistance system's assessment. "
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
        "8. scene_summary_seed is a local conservative summary; it may inform but "
        "should not be copied verbatim.\n"
        "9. Output must match this JSON structure:\n"
    ),
    "suggest_choice": (
        "Task: Rank the current visible choices by recommendation.\n"
        "Requirements:\n"
        "1. Only return choice_id values that appear in context.visible_choices.\n"
        "2. rank starts at 1 (lower = more recommended).\n"
        "3. reason: briefly explain the basis for the ranking.\n"
        "4. Output must match this JSON structure:\n"
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
    system_prompt = _SYSTEM_PROMPTS[operation]
    user_prompt = (
        _USER_PROMPT_PREFIXES[operation]
        + f"{_json_dump(_EXAMPLES[operation])}\n\n"
        + "context:\n"
        + _context_json_for_prompt(context)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
