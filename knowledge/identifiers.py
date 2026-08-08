"""Portable identifiers shared by knowledge packs and collections."""

from __future__ import annotations

import re


_IDENTIFIER_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$"
)
_WINDOWS_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{value}" for value in range(1, 10)}
    | {f"lpt{value}" for value in range(1, 10)}
)


def validate_knowledge_identifier(value: object) -> str:
    """Return one portable v1 identifier or raise ``ValueError``."""
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("identifier must be an unpadded string")
    text = value
    if not _IDENTIFIER_RE.fullmatch(text):
        raise ValueError(
            "identifier must be 1-64 lowercase letters, numbers, dots, dashes "
            "or underscores and must start and end with a letter or number"
        )
    if text.split(".", 1)[0] in _WINDOWS_RESERVED_STEMS:
        raise ValueError("identifier is reserved by the operating system")
    return text


__all__ = ["validate_knowledge_identifier"]
