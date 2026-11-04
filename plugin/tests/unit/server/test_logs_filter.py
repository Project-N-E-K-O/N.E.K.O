from __future__ import annotations

import pytest

from plugin.server.application.logs.text import filter_logs


@pytest.mark.plugin_unit
def test_filter_logs_applies_time_range() -> None:
    logs = [
        {"timestamp": "2026-03-01 10:00:00", "level": "INFO", "message": "a"},
        {"timestamp": "2026-03-01 10:05:00", "level": "INFO", "message": "b"},
        {"timestamp": "2026-03-01 10:10:00", "level": "INFO", "message": "c"},
    ]

    filtered = filter_logs(
        logs,
        start_time="2026-03-01 10:02:00",
        end_time="2026-03-01 10:08:00",
    )

    assert [entry["message"] for entry in filtered] == ["b"]


@pytest.mark.plugin_unit
def test_filter_logs_ignores_invalid_time_filter_values() -> None:
    logs = [
        {"timestamp": "2026-03-01 10:00:00", "level": "INFO", "message": "a"},
        {"timestamp": "2026-03-01 10:05:00", "level": "INFO", "message": "b"},
    ]

    filtered = filter_logs(logs, start_time="bad-time")

    assert [entry["message"] for entry in filtered] == ["a", "b"]


@pytest.mark.plugin_unit
@pytest.mark.parametrize("search", ["ALPHA.PY", "alpha.py:7", "INFO", "2026-03-01", "ready"])
def test_viewer_search_matches_every_displayed_field_without_changing_default(search):
    entry = {"timestamp": "2026-03-01 10:00:00", "level": "INFO",
             "file": "alpha.py", "line": 7, "message": "ready"}
    assert filter_logs([entry], search=search, search_all_fields=True) == [entry]
    assert filter_logs([entry], search=search) == ([entry] if search == "ready" else [])
