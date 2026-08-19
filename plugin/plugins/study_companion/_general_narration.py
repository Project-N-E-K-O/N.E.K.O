from __future__ import annotations

import re


GENERAL_NARRATION_MAX_CHARS = 1600

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
_QUOTE_OR_LIST_PREFIX_RE = re.compile(r"^\s*(?:>\s*|[-+*]\s+|\d+[.)]\s+)+")
_MARKDOWN_RULE_RE = re.compile(r"^\s*(?:(?:[-*_]\s*){3,}|[=-]{3,})\s*$")
_INTERNAL_DIAGNOSTIC_RE = re.compile(
    r"^\s*(?:"
    r"\[(?:study semantic|knowledge graph guidance|solution narration|general narration)"
    r"[^]]*\]"
    r"|(?:study_semantic|knowledge_guidance|solution_narration|general_narration)"
    r"[a-z0-9_]*\s*[:=]"
    r")",
    re.IGNORECASE,
)
_INTERNAL_JSON_RE = re.compile(
    r'^\s*\{\s*"(?:study_semantic|knowledge_guidance|solution_narration|general_narration)[^"}]*"\s*:',
    re.IGNORECASE,
)
_LATE_BOUNDARY_RE = re.compile(r"\n\n|\n|[。！？；]|[.!?;](?=\s|$)")


def prepare_general_narration_content(content: str | None) -> str:
    """Prepare an existing tutor reply for narration without generating text."""

    if not isinstance(content, str) or not content.strip():
        return ""

    prepared_lines: list[str] = []
    inside_code_fence = False
    for raw_line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw_line.strip()
        if stripped.startswith(("```", "~~~")):
            inside_code_fence = not inside_code_fence
            continue
        if inside_code_fence:
            continue
        line = _HEADING_RE.sub("", raw_line).rstrip()
        diagnostic_candidate = _QUOTE_OR_LIST_PREFIX_RE.sub("", line.strip())
        if _INTERNAL_DIAGNOSTIC_RE.match(
            diagnostic_candidate
        ) or _INTERNAL_JSON_RE.match(diagnostic_candidate):
            continue
        if _MARKDOWN_RULE_RE.match(stripped):
            continue

        if not line.strip():
            if prepared_lines and prepared_lines[-1] != "":
                prepared_lines.append("")
            continue
        prepared_lines.append(line.strip())

    prepared = "\n".join(prepared_lines).strip()
    if len(prepared) <= GENERAL_NARRATION_MAX_CHARS:
        return prepared

    prefix = prepared[:GENERAL_NARRATION_MAX_CHARS]
    minimum_boundary = int(GENERAL_NARRATION_MAX_CHARS * 0.6)
    boundary = 0
    for match in _LATE_BOUNDARY_RE.finditer(prefix):
        candidate = match.start() if match.group().startswith("\n") else match.end()
        if candidate >= minimum_boundary:
            boundary = candidate
    if boundary:
        return prefix[:boundary].rstrip()
    return prefix.rstrip()


__all__ = [
    "GENERAL_NARRATION_MAX_CHARS",
    "prepare_general_narration_content",
]
