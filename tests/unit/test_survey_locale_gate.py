# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Audience scoping for version surveys (``locales`` allowlist).

The survey fallback chain always ends at the Simplified Chinese base file, so a
base-only survey reaches every locale unless it narrows its audience. These tests
pin that narrowing, and pin that the shipped 0.9.0 announcement is scoped.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from main_routers.system_router import changelog_survey

REPO_ROOT = Path(__file__).resolve().parents[2]
SURVEYS_DIR = REPO_ROOT / "config" / "surveys"


@pytest.fixture
def surveys(tmp_path, monkeypatch):
    """Point the loader at a temp surveys dir and return a writer for it."""
    fake_pkg = tmp_path / "main_routers" / "system_router" / "changelog_survey.py"
    fake_pkg.parent.mkdir(parents=True)
    monkeypatch.setattr(changelog_survey, "__file__", str(fake_pkg))
    root = tmp_path / "config" / "surveys"
    root.mkdir(parents=True)

    def write(relative: str, payload: dict) -> None:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    return write


def test_without_locales_every_language_still_falls_back_to_base(surveys):
    """No allowlist = previous behaviour: non-Chinese locales land on the base file."""
    surveys("1.0.0.json", {"title": "base", "questions": []})

    for lang in ("zh-CN", "zh-TW", "en", "ja", ""):
        loaded = changelog_survey._load_survey_for_version("1.0.0", lang)
        assert loaded is not None, f"{lang!r} should still receive the unscoped survey"
        assert loaded["title"] == "base"


@pytest.mark.parametrize(
    "lang, served",
    [
        ("zh-CN", True),
        ("zh-TW", False),   # Chinese variant, but Traditional — must not get Simplified copy
        ("en", False),
        ("ja", False),
        ("ko", False),
        ("ru", False),
        ("es", False),
        ("pt", False),
        ("", False),        # i18n not ready -> unknown audience -> withhold
        ("zh", False),      # only the exact code in the allowlist matches
        ("ZH-CN", False),   # case-sensitive: no accidental widening
    ],
)
def test_locales_allowlist_serves_only_listed_locale(surveys, lang, served):
    surveys("1.0.0.json", {"title": "zh only", "locales": ["zh-CN"], "questions": []})

    loaded = changelog_survey._load_survey_for_version("1.0.0", lang)
    assert (loaded is not None) is served, (
        f"lang={lang!r} expected served={served}, got {loaded is not None}"
    )


def test_localized_file_gate_is_not_bypassed_by_falling_through_to_base(surveys):
    """A scoped per-locale file must not fall through to an unscoped base.

    The base is deliberately left unscoped here: if the loader treated a blocked
    locale file as "keep looking", ja would end up served the base copy.
    """
    surveys("1.0.0.json", {"title": "base", "questions": []})
    surveys("ja/1.0.0.json", {"title": "ja", "locales": ["zh-CN"], "questions": []})

    assert changelog_survey._load_survey_for_version("1.0.0", "ja") is None
    # The unscoped base is still reachable by anyone who does not hit the ja file.
    assert changelog_survey._load_survey_for_version("1.0.0", "ko")["title"] == "base"


@pytest.mark.parametrize(
    "today, served",
    [
        ("2026-08-19", True),    # before the deadline
        ("2026-08-20", True),    # the deadline day itself is inclusive
        ("2026-08-21", False),   # the day after: event is over, stay quiet
        ("2027-01-01", False),   # a late first launch must not resurrect it
    ],
)
def test_expires_at_stops_serving_after_the_deadline(surveys, monkeypatch, today, served):
    surveys("1.0.0.json", {"title": "notice", "expires_at": "2026-08-20", "questions": []})
    monkeypatch.setattr(
        changelog_survey, "_utc_today", lambda: date.fromisoformat(today)
    )

    loaded = changelog_survey._load_survey_for_version("1.0.0", "zh-CN")
    assert (loaded is not None) is served


def test_malformed_expires_at_withholds_rather_than_serves(surveys, monkeypatch):
    """A typo must not silently turn into "no expiry" for a time-bound notice."""
    surveys("1.0.0.json", {"title": "notice", "expires_at": "2026-13-99", "questions": []})
    monkeypatch.setattr(changelog_survey, "_utc_today", lambda: date(2026, 8, 1))

    assert changelog_survey._load_survey_for_version("1.0.0", "zh-CN") is None


def test_survey_without_expires_at_never_expires(surveys, monkeypatch):
    surveys("1.0.0.json", {"title": "evergreen", "questions": []})
    monkeypatch.setattr(changelog_survey, "_utc_today", lambda: date(2099, 1, 1))

    assert changelog_survey._load_survey_for_version("1.0.0", "zh-CN") is not None


def test_empty_allowlist_is_treated_as_unscoped(surveys):
    """``"locales": []`` must not silently mute a survey for everyone."""
    surveys("1.0.0.json", {"title": "base", "locales": [], "questions": []})

    assert changelog_survey._load_survey_for_version("1.0.0", "en") is not None


def test_shipped_0_9_0_announcement_is_scoped_to_simplified_chinese():
    """Guard the real file: this announcement must never reach other locales."""
    payload = json.loads((SURVEYS_DIR / "0.9.0.json").read_text(encoding="utf-8"))

    assert payload["locales"] == ["zh-CN"], "0.9.0 announcement must stay zh-CN only"
    assert payload["survey_version"] == "0.9.0"
    # Pure notice: adding questions would turn the announcement into a real survey.
    assert payload["questions"] == []
    # Time-bound ask (event ends 2026-08-20) — must not outlive the event.
    assert payload["expires_at"] == "2026-08-20"
    # A localized copy would be served to that locale and defeat the scoping.
    strays = sorted(p.parent.name for p in SURVEYS_DIR.glob("*/0.9.0.json"))
    assert not strays, f"0.9.0 must have no per-locale copies, found: {strays}"
