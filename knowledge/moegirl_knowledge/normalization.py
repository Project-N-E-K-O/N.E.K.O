"""Narrow normalization rules owned by the public-meme domain."""

from __future__ import annotations

import re

from ..engine.filters import normalize_search_text


_FILLER_RE = re.compile(r"(?:这是|这个是|就是|是|吧|啊|呀|呢|了|的|嘛|啦|么|吗)")
_PRONOUN_RE = re.compile(r"[我你他她它]")


def normalize_meme_phrase(value: str) -> str:
    """Normalize only the conversational variants supported by this domain."""
    normalized = normalize_search_text(value)
    return _PRONOUN_RE.sub("人", _FILLER_RE.sub("", normalized))
