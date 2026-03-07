from __future__ import annotations

import pytest

from plugin.server.logs import filter_logs


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
