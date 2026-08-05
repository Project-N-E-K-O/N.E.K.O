"""Deterministic text handling for untrusted encyclopedia content."""

from __future__ import annotations

import re
import unicodedata


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CHATML_RE = re.compile(r"<\|(?:im_start|im_end|endoftext)\|>", re.IGNORECASE)
_ROLE_MARKER_RE = re.compile(r"(?im)^\s*(?:system|developer|assistant|user)\s*[:：].*$")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")
_FTS_TOKEN_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def sanitize_external_text(value: str, *, max_chars: int = 80_000) -> str:
    """Normalize externally sourced text without interpreting it as instructions."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _CONTROL_CHARS_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CHATML_RE.sub("", text)
    text = _ROLE_MARKER_RE.sub("", text)
    text = "\n".join(_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    text = _NEWLINES_RE.sub("\n\n", text).strip()
    return text[:max_chars].strip()


def normalize_search_text(value: str) -> str:
    """Return a comparison-friendly value for title, alias, and tag matching."""
    return "".join(_FTS_TOKEN_RE.split(unicodedata.normalize("NFKC", str(value or "")).casefold()))


def make_fts_query(value: str) -> str:
    """Build a conservative FTS5 query from user-supplied text.

    Quoted tokens keep FTS operators in the input from changing query semantics.
    """
    tokens = [token for token in _FTS_TOKEN_RE.split(sanitize_external_text(value, max_chars=200)) if token]
    return " AND ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
