"""Helpers for mapping content locale tags to media source preferences."""

from __future__ import annotations


_MAINLAND_CHINA_SOURCE_LOCALES = {
    "china",
    "cn",
    "mainland-china",
    "zh",
    "zh-cn",
    "zh-hans",
    "zh-hans-cn",
}

_NON_CHINA_SOURCE_LOCALES = {
    "global",
    "international",
    "non-china",
    "non_china",
}


def normalize_source_locale(source_locale: str | None) -> str:
    return str(source_locale or "").strip().lower().replace("_", "-")


def source_region_from_locale(source_locale: str | None) -> str | None:
    """Return a source region for known locale tags, or None to use runtime region."""
    normalized = normalize_source_locale(source_locale)
    if not normalized:
        return None
    if normalized in _MAINLAND_CHINA_SOURCE_LOCALES:
        return "china"
    if normalized.startswith("zh-cn-") or normalized.startswith("zh-hans-"):
        return "china"
    if normalized in _NON_CHINA_SOURCE_LOCALES:
        return "non-china"
    return "non-china"
