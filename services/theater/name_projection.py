"""Safely project author names to runtime names in one pass."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import re
from typing import Any, Iterable


def _source_pattern(source: str) -> str:
    """Add character boundaries for names that easily occur inside ordinary words."""  # noqa: DOCSTRING_CJK

    escaped = re.escape(source)
    if len(source) == 1 and (source.isalnum() or source == "_"):
        # Single-character names must stand alone, or "林" (Lin) would corrupt compounds such as "森林" (forest).
        return rf"(?<!\w){escaped}(?!\w)"
    if source.isascii():
        # Latin names allow spaces and hyphens, but May must not be extracted from Maybe.
        left = r"(?<![A-Za-z0-9_])" if source[0].isalnum() or source[0] == "_" else ""
        right = r"(?![A-Za-z0-9_])" if source[-1].isalnum() or source[-1] == "_" else ""
        return f"{left}{escaped}{right}"
    return escaped


def replace_names(value: Any, replacements: Iterable[tuple[Any, Any]]) -> str:
    """Replace all nonempty source names in one pass, longest names first."""  # noqa: DOCSTRING_CJK

    text = str(value or "")
    mapping: dict[str, str] = {}
    for source, target in replacements:
        source_text = str(source or "")
        target_text = str(target or "")
        # Include longer unchanged names in longest-match selection, or renaming "小明" to "阿晨" would corrupt "小明月".
        if source_text:
            if source_text in mapping and mapping[source_text] != target_text:
                raise ValueError("conflicting_name_replacement")
            mapping[source_text] = target_text
    if not mapping:
        return text
    pattern = re.compile("|".join(
        _source_pattern(source)
        for source in sorted(mapping, key=len, reverse=True)
    ))
    return pattern.sub(lambda match: mapping[match.group(0)], text)


__all__ = ["replace_names"]
