from __future__ import annotations

from dataclasses import dataclass
import re


_SECTION_ORDER = ("analysis", "process", "answer", "transfer")
_NARRATION_SECTION_ORDER = ("analysis", "answer", "transfer")
SOLUTION_NARRATION_MAX_CHARS = 1800
_SECTION_ALIASES = {
    "解析": "analysis",
    "题目解析": "analysis",
    "題目解析": "analysis",
    "problem analysis": "analysis",
    "解题过程": "process",
    "解題過程": "process",
    "solution process": "process",
    "答案": "answer",
    "最终答案": "answer",
    "最終答案": "answer",
    "answer": "answer",
    "final answer": "answer",
    "举一反三": "transfer",
    "舉一反三": "transfer",
    "transfer practice": "transfer",
}
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,4}\s+")
_BOLD_HEADING_RE = re.compile(r"^\*\*(.+?)\*\*$")
_SENTENCE_END_RE = re.compile(r"[。！？.!?](?:[”’\"']|\))?")


@dataclass(frozen=True, slots=True)
class SolutionStructure:
    analysis: str
    process: str
    answer: str
    transfer: str
    missing_sections: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_sections


def _section_name(line: str) -> str | None:
    normalized = _MARKDOWN_HEADING_RE.sub("", str(line or "").strip())
    bold = _BOLD_HEADING_RE.fullmatch(normalized)
    if bold is not None:
        normalized = bold.group(1)
    normalized = re.sub(r"[：:]\s*$", "", normalized).strip().lower()
    return _SECTION_ALIASES.get(normalized)


def parse_solution_structure(reply: str) -> SolutionStructure:
    """Parse the four-section solution contract without inventing content."""

    collected: dict[str, list[str]] = {key: [] for key in _SECTION_ORDER}
    current: str | None = None
    for line in str(reply or "").splitlines():
        heading = _section_name(line)
        if heading is not None:
            current = heading
            continue
        if current is not None:
            collected[current].append(line)
    values = {key: "\n".join(collected[key]).strip() for key in _SECTION_ORDER}
    missing = tuple(key for key in _SECTION_ORDER if not values[key])
    return SolutionStructure(
        analysis=values["analysis"],
        process=values["process"],
        answer=values["answer"],
        transfer=values["transfer"],
        missing_sections=missing,
    )


def is_solution_structure_candidate(structure: SolutionStructure) -> bool:
    """Return whether a reply already exhibits a structured problem solution."""

    present = {
        key
        for key in _SECTION_ORDER
        if str(getattr(structure, key, "") or "").strip()
    }
    return len(present) >= 2 and bool(present.intersection({"process", "answer"}))


def _fair_narration_budgets(sections: dict[str, str]) -> dict[str, int]:
    remaining = SOLUTION_NARRATION_MAX_CHARS
    pending = list(_NARRATION_SECTION_ORDER)
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


def _truncate_narration_at_boundary(text: str, limit: int) -> str:
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
    """Project a complete solution onto the sections safe for narration."""

    structure = parse_solution_structure(reply)
    sections = {
        key: str(getattr(structure, key, "") or "").strip()
        for key in _NARRATION_SECTION_ORDER
    }
    if any(not sections[key] for key in _NARRATION_SECTION_ORDER):
        return None
    if sum(len(value) for value in sections.values()) <= SOLUTION_NARRATION_MAX_CHARS:
        return sections

    budgets = _fair_narration_budgets(sections)
    bounded = {
        key: _truncate_narration_at_boundary(sections[key], budgets[key])
        for key in _NARRATION_SECTION_ORDER
    }
    if any(not bounded[key] for key in _NARRATION_SECTION_ORDER):
        return None
    return bounded


def structure_from_mapping(payload: object) -> SolutionStructure:
    values = dict(payload) if isinstance(payload, dict) else {}
    sections = {
        key: str(values.get(key) or "").strip()
        if isinstance(values.get(key), str)
        else ""
        for key in _SECTION_ORDER
    }
    missing = tuple(key for key in _SECTION_ORDER if not sections[key])
    return SolutionStructure(
        analysis=sections["analysis"],
        process=sections["process"],
        answer=sections["answer"],
        transfer=sections["transfer"],
        missing_sections=missing,
    )


def render_solution_structure(
    structure: SolutionStructure, *, language: str | None
) -> str:
    normalized = str(language or "").strip().lower()
    if normalized.startswith(("zh-tw", "zh-hk", "zh-hant")):
        headings = ("題目解析", "解題過程", "答案", "舉一反三")
    elif normalized.startswith("zh"):
        headings = ("题目解析", "解题过程", "答案", "举一反三")
    else:
        headings = (
            "Problem Analysis",
            "Solution Process",
            "Answer",
            "Transfer Practice",
        )
    values = (
        structure.analysis,
        structure.process,
        structure.answer,
        structure.transfer,
    )
    return "\n\n".join(
        f"### {heading}\n{value.strip()}" for heading, value in zip(headings, values)
    )


__all__ = [
    "SOLUTION_NARRATION_MAX_CHARS",
    "SolutionStructure",
    "extract_solution_narration_sections",
    "is_solution_structure_candidate",
    "parse_solution_structure",
    "render_solution_structure",
    "structure_from_mapping",
]
