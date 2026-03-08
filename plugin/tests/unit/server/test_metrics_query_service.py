from __future__ import annotations

import pytest

from plugin.server.application.monitoring import query_service as module


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
