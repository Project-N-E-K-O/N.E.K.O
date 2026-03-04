from __future__ import annotations

import pytest

from plugin.server.application.messages import query_service as module
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import MESSAGE_QUEUE_MAX


@pytest.mark.plugin_unit
def test_query_messages_sync_clamps_max_count_to_configured_max(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_refresh(*, limit: int, timeout: float, ttl_seconds: float, force: bool) -> None:
        captured["limit"] = limit

    monkeypatch.setattr(module.state, "refresh_messages_cache_from_message_plane", _fake_refresh)
    monkeypatch.setattr(module.state, "iter_message_records_reverse", lambda: iter([]))

    module._query_messages_sync(plugin_id=None, max_count=MESSAGE_QUEUE_MAX + 9999, priority_min=None)

    assert captured["limit"] == MESSAGE_QUEUE_MAX


@pytest.mark.plugin_unit
def test_query_messages_sync_filters_by_plugin_and_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.state,
        "refresh_messages_cache_from_message_plane",
        lambda **kwargs: None,
    )

    records = [
        {"plugin_id": "a", "priority": 1, "content": "low", "time": "2026-01-01T00:00:00Z"},
        {"plugin_id": "b", "priority": 9, "content": "other", "time": "2026-01-01T00:00:01Z"},
        {
            "plugin_id": "a",
            "priority": 8,
            "content": "ok",
            "message_type": "binary",
            "binary_data": b"abc",
            "metadata": {"k": "v"},
            "time": "2026-01-01T00:00:02Z",
            "message_id": 123,
        },
    ]
    monkeypatch.setattr(module.state, "iter_message_records_reverse", lambda: iter(records))

    payload = module._query_messages_sync(plugin_id="a", max_count=10, priority_min=5)

    assert len(payload) == 1
    item = payload[0]
    assert item["plugin_id"] == "a"
    assert item["priority"] == 8
    assert item["binary_data"] == "YWJj"
    assert item["metadata"] == {"k": "v"}
    assert item["message_id"] == "123"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_messages_wraps_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    service = module.MessageQueryService()

    def _boom(*, plugin_id: str | None, max_count: int, priority_min: int | None):
        raise KeyError("x")

    monkeypatch.setattr(module, "_query_messages_sync", _boom)

    with pytest.raises(ServerDomainError) as exc_info:
        await service.get_plugin_messages(plugin_id="demo", max_count=5, priority_min=None)

    assert exc_info.value.code == "MESSAGE_QUERY_FAILED"
