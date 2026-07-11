"""Viewer address-name helpers for NEKO Live speech."""

from __future__ import annotations

import re
from typing import Any

from .contracts_public import public_text
from .viewer_preferences import safe_int


_REGULAR_VIEWER_MIN_EVENTS = 5
_POETIC_SUFFIX_ALIASES = (
    "\u661f\u8fb0",
    "\u661f\u6cb3",
    "\u6708\u5149",
    "\u665a\u98ce",
    "\u6e05\u98ce",
    "\u6e05\u97f5",
    "\u843d\u96ea",
    "\u82b1\u706b",
    "\u5c0f\u96e8",
    "\u6674\u5929",
    "\u591c\u96e8",
    "\u4e91\u6735",
    "\u6d41\u8424",
    "\u6625\u98ce",
    "\u79cb\u6c34",
)
_SEPARATOR_RE = re.compile(r"[\s_\-|/\\\u4e28\u00b7\u30fb]+")
_CJK_ALIAS_BLOCK_CHARS = set("\u4e0a\u4e0b\u524d\u540e\u5de6\u53f3\u4e2d\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u96f6\u7684\u4e86\u7740\u8fc7\u662f\u6709\u6ca1\u4e0d\u8981\u6765\u53bb\u770b\u542c\u8bf4\u5403\u559d\u73a9\u505a\u7ed9\u628a\u88ab\u548c\u4e0e\u6216\u5c31\u90fd\u4e5f")
_CJK_ALIAS_BLOCK_TERMS = {
    "\u7528\u6237",
    "\u89c2\u4f17",
    "\u5ba2\u4eba",
    "\u6280\u672f",
    "\u6d4b\u8bd5",
    "\u5403\u9c7c",
    "\u559d\u6c34",
    "\u59e5\u7237",
}
_CJK_ROLE_SUFFIXES = tuple("\u4eba\u5ba2\u54e5\u59d0\u7237\u7238\u5988\u4e3b\u8005\u5458")
_CJK_NAME_SIGNAL_CHARS = set(
    "\u6e05\u97f5\u971c\u98ce\u6708\u661f\u8fb0\u6cb3\u4e91\u96e8\u96ea\u82b1\u706b\u5149\u591c\u665a\u6625\u590f\u79cb\u51ac"
    "\u68a6\u5f71\u58f0\u97f3\u8bd7\u6b4c\u8336\u7cd6\u751c\u9c7c\u732b\u72d0\u9e7f\u5154\u6843\u68a8\u67da\u8377\u7af9\u6885\u5170"
    "\u6d45\u6df1\u5c0f\u767d\u84dd\u7eff\u7ea2\u7d2b\u91d1\u94f6\u7070\u6674\u6d41\u843d\u5bd2\u6696\u5b81\u5b89\u4e50"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_LATIN_ALIAS_BLOCK_TERMS = {
    "admin",
    "bot",
    "channel",
    "guest",
    "live",
    "official",
    "test",
    "user",
    "viewer",
}


def viewer_address_name(nickname: Any, profile: Any | None = None) -> str:
    """Return the natural spoken address for a viewer without mutating identity."""

    raw = _safe_nickname(nickname)
    if not raw:
        return ""
    if not _is_regular_viewer(profile):
        return raw
    return _short_nickname_alias(raw) or raw


def _safe_nickname(value: Any) -> str:
    text = public_text(value, max_len=32)
    return " ".join(text.strip().split())[:24]


def _is_regular_viewer(profile: Any | None) -> bool:
    if profile is None:
        return False
    danmaku_count = safe_int(_get(profile, "danmaku_count"))
    roast_count = safe_int(_get(profile, "roast_count"))
    return danmaku_count + roast_count >= _REGULAR_VIEWER_MIN_EVENTS


def _short_nickname_alias(nickname: str) -> str:
    cleaned = nickname.strip()
    parts = [part.strip() for part in _SEPARATOR_RE.split(cleaned) if part.strip()]
    if len(parts) > 1:
        for part in reversed(parts):
            if _looks_like_initialism(part):
                continue
            alias = _alias_from_piece(part)
            if alias:
                return alias

    cjk_runs = _cjk_runs(cleaned)
    for run in reversed(cjk_runs):
        alias = _natural_cjk_alias(run)
        if alias:
            return alias

    compact = "".join(ch for ch in cleaned if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if compact and not _is_cjk_name(compact) and not _looks_like_initialism(compact):
        latin_alias = _latin_alias(cleaned)
        if latin_alias:
            return latin_alias
    return ""


def _has_visible_name_signal(text: str) -> bool:
    dense = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return len(dense) >= 2


def _is_cjk_name(text: str) -> bool:
    return bool(text) and all("\u4e00" <= ch <= "\u9fff" for ch in text)


def _cjk_runs(text: str) -> list[str]:
    return [match.group(0) for match in _CJK_RE.finditer(str(text or ""))]


def _alias_from_piece(text: str) -> str:
    piece = "".join(ch for ch in str(text or "").strip() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if not piece or not _has_visible_name_signal(piece):
        return ""
    if _is_cjk_name(piece):
        if 2 <= len(piece) <= 3 and _is_natural_cjk_alias_candidate(piece):
            return piece
        return _natural_cjk_alias(piece)
    cjk_runs = _cjk_runs(piece)
    for run in reversed(cjk_runs):
        alias = _natural_cjk_alias(run)
        if alias:
            return alias
    latin_alias = _latin_alias(piece)
    if latin_alias and not _looks_like_initialism(latin_alias):
        return latin_alias
    return ""


def _looks_like_initialism(text: str) -> bool:
    compact = "".join(ch for ch in str(text or "").strip() if ch.isalnum())
    return 1 <= len(compact) <= 3 and compact.isascii() and compact.isalpha()


def _latin_alias(text: str) -> str:
    if _cjk_runs(text):
        return ""
    words = [word for word in _LATIN_WORD_RE.findall(str(text or "")) if _is_latin_alias_candidate(word)]
    if not words:
        return ""
    return words[-1]


def _is_latin_alias_candidate(text: str) -> bool:
    word = str(text or "").strip()
    if not 2 <= len(word) <= 16:
        return False
    if word.casefold() in _LATIN_ALIAS_BLOCK_TERMS:
        return False
    return any(ch.isalpha() for ch in word)


def _natural_cjk_alias(text: str) -> str:
    if not _is_cjk_name(text) or not 4 <= len(text) <= 10:
        return ""
    for candidate in (text[-2:], text[:2], text[-3:]):
        if _is_natural_cjk_alias_candidate(candidate):
            return candidate
    return ""


def _is_natural_cjk_alias_candidate(text: str) -> bool:
    if not _is_cjk_name(text) or not 2 <= len(text) <= 3:
        return False
    if any(term in text for term in _CJK_ALIAS_BLOCK_TERMS):
        return False
    if any(ch in _CJK_ALIAS_BLOCK_CHARS for ch in text):
        return False
    if text.endswith(_CJK_ROLE_SUFFIXES):
        return False
    if not any(ch in _CJK_NAME_SIGNAL_CHARS for ch in text):
        return False
    return True


def _get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
