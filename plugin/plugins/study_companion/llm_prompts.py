from __future__ import annotations

from typing import Any

from .constants import MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING
from .mode_manager import normalize_mode

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
            "content": f"{CONCEPT_EXPLAIN_SYSTEM_PROMPT}\nMode guidance: {MODE_SYSTEM_GUIDANCE.get(selected_mode, MODE_SYSTEM_GUIDANCE[MODE_COMPANION])}",
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


__all__ = [
    "CONCEPT_EXPLAIN_SYSTEM_PROMPT",
    "MODE_SYSTEM_GUIDANCE",
    "build_concept_explain_messages",
]
