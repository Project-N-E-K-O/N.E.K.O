from __future__ import annotations

from typing import Any

CONCEPT_EXPLAIN_SYSTEM_PROMPT = (
    "You are a concise study tutor. Explain the concept clearly, "
    "identify prerequisite ideas, and give one short check question. "
    "Do not invent source material beyond the supplied text."
)


def build_concept_explain_messages(
    *,
    text: str,
    language: str,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context if isinstance(context, dict) else {}
    source = str(context.get("source") or "manual").strip() or "manual"
    return [
        {
            "role": "system",
            "content": CONCEPT_EXPLAIN_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"Language: {language}\n"
                f"Source: {source}\n"
                "Task: concept_explain\n\n"
                f"Study text:\n{text.strip()}"
            ),
        },
    ]


__all__ = [
    "CONCEPT_EXPLAIN_SYSTEM_PROMPT",
    "build_concept_explain_messages",
]
