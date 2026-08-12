from __future__ import annotations

from dataclasses import dataclass
import re


_SECTION_ORDER = ("analysis", "process", "answer", "transfer")
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
    "SolutionStructure",
    "is_solution_structure_candidate",
    "parse_solution_structure",
    "render_solution_structure",
    "structure_from_mapping",
]
