# -*- coding: utf-8 -*-
"""Tests for utils.time_format.format_elapsed."""

import pytest

from utils.time_format import format_elapsed


@pytest.mark.parametrize(("gap_seconds", "lang", "expected"), [
    # Day boundary with leftover minutes — regression guard for the dropped-minutes bug.
    (86400 + 1800, "en", "1 days and 30 minutes"),
    (86400 + 60, "en", "1 days and 1 minutes"),
    (2 * 86400 + 300, "en", "2 days and 5 minutes"),
    # Exactly on a day boundary — must use day-only template, no dangling minute suffix.
    (86400, "en", "1 days"),
    # Day + hours still uses DH (minutes dropped by design for hours>0).
    (86400 + 2 * 3600, "en", "1 days and 2 hours"),
    (86400 + 3600 + 60, "en", "1 days and 1 hours"),
    # Sub-day behaviour unchanged: HM for hours<3, H for hours>=3, M otherwise.
    (2 * 3600 + 1800, "en", "2 hours and 30 minutes"),
    (4 * 3600, "en", "4 hours"),
    (300, "en", "5 minutes"),
    # Every supported locale ships an ELAPSED_TIME_DM template; verify none render a placeholder.
    (86400 + 1800, "zh", "1天30分钟"),
    (86400 + 1800, "ja", "1日30分"),
    (86400 + 1800, "ko", "1일 30분"),
    (86400 + 1800, "ru", "1 дн. 30 мин."),
    (86400 + 1800, "es", "1 días y 30 minutos"),
    (86400 + 1800, "pt", "1 dias e 30 minutos"),
])
def test_format_elapsed(gap_seconds, lang, expected):
    assert format_elapsed(lang, gap_seconds) == expected


def test_format_elapsed_does_not_leave_unsubstituted_placeholder():
    """If a template key is missing from _loc, .format would leave a literal '{...}' behind."""
    for gap_seconds in (86400 + 1800, 86400, 86400 + 3600, 2 * 3600 + 1800, 300):
        out = format_elapsed("en", gap_seconds)
        assert "{" not in out and "}" not in out, out
