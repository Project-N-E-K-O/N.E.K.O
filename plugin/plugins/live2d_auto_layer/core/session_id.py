from __future__ import annotations

import re

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_session_id(session_id: str) -> str:
    """Return a normalized safe session id or raise ValueError."""

    clean = str(session_id or "").strip()
    if not clean:
        raise ValueError("session_id is required")
    if not _SAFE_SESSION_ID_RE.fullmatch(clean):
        raise ValueError(
            "session_id must be 1-64 chars and contain only letters, numbers, dots, underscores, or hyphens"
        )
    if clean in {".", ".."} or clean.startswith(".") or ".." in clean:
        raise ValueError("session_id must not be hidden or contain parent-directory segments")
    return clean


__all__ = ["validate_session_id"]
