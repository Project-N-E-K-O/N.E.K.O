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
    collector._live_plugin_ids = {"demo"}
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
    collector._live_plugin_ids = {"demo"}
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
    collector._live_plugin_ids = {"demo"}
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


class _FakeProcess:
    def __init__(self, pid: int, *, alive: bool = True) -> None:
        self.pid = pid
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


class _FakeHost:
    def __init__(self, pid: int) -> None:
        self.process = _FakeProcess(pid)


def _run_collector_ticks(
    monkeypatch: pytest.MonkeyPatch,
    collector: MetricsCollector,
    hosts_per_tick: list[dict[str, object]],
    collect=None,
) -> None:
    """Drive ``_collect_loop`` for exactly ``len(hosts_per_tick)`` ticks."""
    import asyncio

    from plugin.server.monitoring import metrics as metrics_module

    monkeypatch.setattr(metrics_module, "PSUTIL_AVAILABLE", True)

    def _fake_collect(plugin_id: str, host: object, _ps_processes: object = None) -> PluginMetrics | None:
        process = getattr(host, "process", None)
        if process is None or not process.is_alive():
            return None
        return PluginMetrics(
            plugin_id=plugin_id,
            timestamp="2026-01-01T00:00:00+00:00",
            pid=process.pid,
            memory_mb=100.0,
            num_threads=10,
        )

    monkeypatch.setattr(collector, "_collect_plugin_metrics_sync", collect or _fake_collect)
    ticks = iter(hosts_per_tick)
    collector._plugin_hosts_getter = lambda: next(ticks)
    remaining = [len(hosts_per_tick)]

    async def _sleep(_seconds: float) -> None:
        remaining[0] -= 1
        if remaining[0] <= 0:
            raise asyncio.CancelledError

    class _ScopedAsyncio:
        """Override ``sleep`` only. Other threads keep the real asyncio module."""

        def __init__(self, sleep):
            self._sleep = sleep

        def __getattr__(self, name: str):
            if name == "sleep":
                return self._sleep
            return getattr(asyncio, name)

    monkeypatch.setattr(metrics_module, "asyncio", _ScopedAsyncio(_sleep))
    try:
        asyncio.run(collector._collect_loop({}))
    except asyncio.CancelledError:
        # The end-of-tick sleep sits outside the loop's try, so the stop signal
        # escapes; the empty-host path swallows it and returns normally.
        pass
    assert remaining[0] == 0


def test_current_metrics_drop_plugins_after_they_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = MetricsCollector()
    _run_collector_ticks(
        monkeypatch,
        collector,
        [{"a": _FakeHost(1), "b": _FakeHost(2)}, {"a": _FakeHost(1)}],
    )

    current = collector.get_current_metrics()
    assert [row["plugin_id"] for row in current] == ["a"]
    assert collector.get_current_metrics("b") == []
    # History is kept for the stopped plugin.
    assert len(collector.get_metrics_history("b")) == 1


def test_current_metrics_are_empty_once_every_plugin_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = MetricsCollector()
    _run_collector_ticks(
        monkeypatch,
        collector,
        [{"a": _FakeHost(1), "b": _FakeHost(2)}, {}],
    )

    # Regression guard: an empty host map used to skip the tick entirely, so the
    # dashboard kept summing every stopped plugin's last sample.
    assert collector.get_current_metrics() == []
    assert collector.get_current_metrics("a") == []


def test_current_metrics_skip_a_hosted_but_dead_process(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = MetricsCollector()
    crashed = _FakeHost(2)
    _run_collector_ticks(
        monkeypatch,
        collector,
        [{"a": _FakeHost(1), "b": crashed}, {"a": _FakeHost(1), "b": crashed}],
    )
    assert {row["plugin_id"] for row in collector.get_current_metrics()} == {"a", "b"}

    crashed.process.alive = False
    _run_collector_ticks(monkeypatch, collector, [{"a": _FakeHost(1), "b": crashed}])

    assert [row["plugin_id"] for row in collector.get_current_metrics()] == ["a"]


def test_liveness_change_invalidates_a_fresh_full_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = MetricsCollector()
    _run_collector_ticks(monkeypatch, collector, [{"a": _FakeHost(1)}])
    assert [row["plugin_id"] for row in collector.get_current_metrics()] == ["a"]

    # Within the 500ms cache TTL: the stop must still be visible immediately.
    _run_collector_ticks(monkeypatch, collector, [{}])
    assert collector.get_current_metrics() == []


def test_stale_full_query_does_not_republish_stopped_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = MetricsCollector()
    _run_collector_ticks(
        monkeypatch,
        collector,
        [{"a": _FakeHost(1), "b": _FakeHost(2)}],
    )
    collector._cache_timestamp = 0.0
    original = collector._metrics_to_dict

    def _publish_stop_while_serializing(metrics: PluginMetrics) -> dict[str, object]:
        if metrics.plugin_id == "b":
            collector._publish_live_plugin_ids({"a"})
        return original(metrics)

    monkeypatch.setattr(collector, "_metrics_to_dict", _publish_stop_while_serializing)

    assert [row["plugin_id"] for row in collector.get_current_metrics()] == ["a"]
    # The in-flight snapshot must not refill the TTL cache.
    assert [row["plugin_id"] for row in collector.get_current_metrics()] == ["a"]


def test_failed_sample_keeps_last_metrics_for_a_live_process(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = MetricsCollector()
    _run_collector_ticks(
        monkeypatch,
        collector,
        [{"a": _FakeHost(1), "b": _FakeHost(2)}],
    )

    def _fail_b(plugin_id: str, host: object, _ps_processes: object = None) -> PluginMetrics | None:
        if plugin_id == "b":
            return None
        process = getattr(host, "process", None)
        return PluginMetrics(
            plugin_id=plugin_id,
            timestamp="2026-01-01T00:00:01+00:00",
            pid=process.pid,
            memory_mb=110.0,
            num_threads=10,
        )

    _run_collector_ticks(
        monkeypatch,
        collector,
        [{"a": _FakeHost(1), "b": _FakeHost(2)}],
        collect=_fail_b,
    )

    assert {row["plugin_id"] for row in collector.get_current_metrics()} == {"a", "b"}


def test_failed_sample_after_restart_drops_the_previous_process_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = MetricsCollector()
    _run_collector_ticks(monkeypatch, collector, [{"a": _FakeHost(1), "b": _FakeHost(2)}])

    def _fail_b(plugin_id: str, host: object, _ps_processes: object = None) -> PluginMetrics | None:
        if plugin_id == "b":
            return None
        process = getattr(host, "process", None)
        return PluginMetrics(plugin_id=plugin_id, timestamp="2026-01-01T00:00:01+00:00", pid=process.pid)

    # "b" restarted under a new PID and its first read failed. The last sample
    # belongs to the old process, so it must not stay in the current totals.
    _run_collector_ticks(monkeypatch, collector, [{"a": _FakeHost(1), "b": _FakeHost(3)}], collect=_fail_b)

    assert [row["plugin_id"] for row in collector.get_current_metrics()] == ["a"]
    assert collector.get_current_metrics("b") == []
    assert len(collector.get_metrics_history("b")) == 1


def test_full_cache_hit_reads_list_and_timestamp_together(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reading _cache and _cache_timestamp separately lets a query pair the
    # pre-stop list with a timestamp published after the stop, and serve stopped
    # plugins as fresh. Both must be read under the collector lock.
    timestamp_reads_locked: list[bool] = []

    class _Collector(MetricsCollector):
        @property
        def _cache_timestamp(self) -> float:
            timestamp_reads_locked.append(self._lock.locked())
            return self.__dict__.get("_ts", 0.0)

        @_cache_timestamp.setter
        def _cache_timestamp(self, value: float) -> None:
            self.__dict__["_ts"] = value

    collector = _Collector()
    _run_collector_ticks(monkeypatch, collector, [{"a": _FakeHost(1)}])
    assert [row["plugin_id"] for row in collector.get_current_metrics()] == ["a"]
    timestamp_reads_locked.clear()

    # Served from the fresh TTL cache.
    assert [row["plugin_id"] for row in collector.get_current_metrics()] == ["a"]
    assert timestamp_reads_locked and all(timestamp_reads_locked)
