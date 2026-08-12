from __future__ import annotations

import re


SOLUTION_NARRATION_MAX_CHARS = 1800

_TARGET_SECTIONS = ("analysis", "answer", "transfer")
_SECTION_ALIASES = {
    "解析": "analysis",
    "题目解析": "analysis",
    "題目解析": "analysis",
    "problem analysis": "analysis",
    "解题过程": "process",
    "解題過程": "process",
    "solution process": "process",
    "答案": "answer",
    "answer": "answer",
    "final answer": "answer",
    "举一反三": "transfer",
    "舉一反三": "transfer",
    "transfer practice": "transfer",
}
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,4}\s+")
_BOLD_HEADING_RE = re.compile(r"^\*\*(.+?)\*\*$")
_SENTENCE_END_RE = re.compile(r"[。！？.!?](?:[”’\"']|\))?")


def _section_name(line: str) -> str | None:
    normalized = _MARKDOWN_HEADING_RE.sub("", str(line or "").strip())
    bold = _BOLD_HEADING_RE.fullmatch(normalized)
    if bold is not None:
        normalized = bold.group(1)
    normalized = re.sub(r"[：:]\s*$", "", normalized).strip().lower()
    return _SECTION_ALIASES.get(normalized)


def _fair_budgets(sections: dict[str, str]) -> dict[str, int]:
    remaining = SOLUTION_NARRATION_MAX_CHARS
    pending = list(_TARGET_SECTIONS)
    budgets: dict[str, int] = {}
    while pending:
        share, remainder = divmod(remaining, len(pending))
        fitting = [key for key in pending if len(sections[key]) <= share]
        if not fitting:
            for index, key in enumerate(pending):
                budgets[key] = share + (1 if index < remainder else 0)
            break
        for key in fitting:
            budget = len(sections[key])
            budgets[key] = budget
            remaining -= budget
            pending.remove(key)
    return budgets


def _truncate_at_boundary(text: str, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    candidate = normalized[:limit].rstrip()
    minimum_boundary = max(1, limit // 2)

    paragraph_end = candidate.rfind("\n\n")
    if paragraph_end >= minimum_boundary:
        return candidate[:paragraph_end].rstrip()

    sentence_end = 0
    for match in _SENTENCE_END_RE.finditer(candidate):
        sentence_end = match.end()
    if sentence_end >= minimum_boundary:
        return candidate[:sentence_end].rstrip()
    return candidate


def extract_solution_narration_sections(reply: str) -> dict[str, str] | None:
    """Extract the three safe narration sections from a structured tutor reply."""

    collected: dict[str, list[str]] = {
        "analysis": [],
        "process": [],
        "answer": [],
        "transfer": [],
    }
    current: str | None = None
    for line in str(reply or "").splitlines():
        heading = _section_name(line)
        if heading is not None:
            current = heading
            continue
        if current is not None:
            collected[current].append(line)

    sections = {key: "\n".join(collected[key]).strip() for key in _TARGET_SECTIONS}
    if any(not sections[key] for key in _TARGET_SECTIONS):
        return None
    if sum(len(value) for value in sections.values()) <= SOLUTION_NARRATION_MAX_CHARS:
        return sections

    budgets = _fair_budgets(sections)
    bounded = {
        key: _truncate_at_boundary(sections[key], budgets[key])
        for key in _TARGET_SECTIONS
    }
    if any(not bounded[key] for key in _TARGET_SECTIONS):
        return None
    return bounded


__all__ = [
    "SOLUTION_NARRATION_MAX_CHARS",
    "extract_solution_narration_sections",
]
