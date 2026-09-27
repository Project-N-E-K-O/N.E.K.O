from __future__ import annotations

import threading

import pytest

from plugin.server.application.monitoring import query_service as module
from plugin.server.monitoring.metrics import MetricsCollector, PluginMetrics


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_metrics_history_rejects_invalid_start_time() -> None:
    service = module.MetricsQueryService()

    with pytest.raises(module.ServerDomainError) as exc_info:
        await service.get_plugin_metrics_history(
            plugin_id="demo",
            limit=10,
            start_time="not-a-time",
            end_time=None,
        )

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.status_code == 400


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_metrics_history_accepts_blank_time_and_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    service = module.MetricsQueryService()
    called: dict[str, object] = {}

    def _fake_get_metrics_history(
        plugin_id: str,
        limit: int = 100,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[dict[str, object]]:
        called["plugin_id"] = plugin_id
        called["limit"] = limit
        called["start_time"] = start_time
        called["end_time"] = end_time
        return []

    monkeypatch.setattr(module.metrics_collector, "get_metrics_history", _fake_get_metrics_history)

    payload = await service.get_plugin_metrics_history(
        plugin_id="demo",
        limit=5,
        start_time="   ",
        end_time="",
    )

    assert payload["plugin_id"] == "demo"
    assert payload["count"] == 0
    assert called == {
        "plugin_id": "demo",
        "limit": 5,
        "start_time": None,
        "end_time": None,
    }


def test_current_metrics_serializes_after_releasing_collector_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = MetricsCollector()
    record = PluginMetrics(plugin_id="demo", timestamp="2026-01-01T00:00:00+00:00")
    collector._metrics_history["demo"] = [record]
    lock_states: list[bool] = []

    def _serialize(value: PluginMetrics) -> dict[str, object]:
        lock_states.append(collector._lock.locked())
        return {"plugin_id": value.plugin_id}

    monkeypatch.setattr(collector, "_metrics_to_dict", _serialize)

    assert collector.get_current_metrics() == [{"plugin_id": "demo"}]
    assert collector.get_current_metrics("demo") == [{"plugin_id": "demo"}]
    assert lock_states == [False, False]


def test_older_full_snapshot_does_not_overwrite_a_newer_metrics_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = MetricsCollector()
    older = PluginMetrics(plugin_id="demo", timestamp="2026-01-01T00:00:00+00:00")
    newer = PluginMetrics(plugin_id="demo", timestamp="2026-01-01T00:00:01+00:00")
    collector._metrics_history["demo"] = [older]
    collector._history_version = 1
    entered = threading.Event()
    release = threading.Event()

    def _serialize(value: PluginMetrics) -> dict[str, object]:
        if value.timestamp == older.timestamp:
            entered.set()
            assert release.wait(2)
        return {"timestamp": value.timestamp}

    monkeypatch.setattr(collector, "_metrics_to_dict", _serialize)
    stale: dict[str, object] = {}

    def _run_stale() -> None:
        stale["value"] = collector.get_current_metrics()

    worker = threading.Thread(target=_run_stale)
    worker.start()
    assert entered.wait(2)
    collector._metrics_history["demo"] = [newer]
    collector._history_version = 2
    assert collector.get_current_metrics() == [{"timestamp": newer.timestamp}]
    release.set()
    worker.join(2)

    assert collector._cache == [{"timestamp": newer.timestamp}]
    assert stale["value"] == [{"timestamp": newer.timestamp}]


def test_metrics_history_filters_and_serializes_after_releasing_collector_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = MetricsCollector()
    record = PluginMetrics(plugin_id="demo", timestamp="2026-01-01T00:00:00+00:00")
    collector._metrics_history["demo"] = [record]
    lock_states: list[bool] = []

    def _serialize(value: PluginMetrics) -> dict[str, object]:
        lock_states.append(collector._lock.locked())
        return {"timestamp": value.timestamp}

    monkeypatch.setattr(collector, "_metrics_to_dict", _serialize)

    result = collector.get_metrics_history(
        "demo", limit=10, start_time="2025-12-31T00:00:00Z"
    )

    assert result == [{"timestamp": "2026-01-01T00:00:00+00:00"}]
    assert lock_states == [False]
