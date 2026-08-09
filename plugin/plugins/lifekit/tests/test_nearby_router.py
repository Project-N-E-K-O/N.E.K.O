from pathlib import Path
from typing import Any

import pytest

from plugin.sdk.plugin import Err, Ok

from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit.routers.nearby import NearbyRouter


class _NearbyPlugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
        self.messages: list[dict[str, Any]] = []

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")

    async def _resolve_location(self, *_: Any, **__: Any):
        return {"city": "吉林市", "lat": 43.8, "lon": 126.5}, None

    def push_message(self, **kwargs: Any) -> dict[str, bool]:
        self.messages.append(kwargs)
        return {"ok": True}


class _FailedLocationPlugin(_NearbyPlugin):
    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.geocode_failed"


class _AmbiguousLocationPlugin(_NearbyPlugin):
    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.location_ambiguous"


@pytest.mark.asyncio
async def test_broad_request_returns_one_host_managed_clarification() -> None:
    plugin = _NearbyPlugin()
    router = NearbyRouter()
    router._bind(plugin)

    result = await router.search_nearby(
        query="公园",
        _ctx={"latest_user_request": "我附近有啥地方可去吗？"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["choices"] == ["公园", "景点", "餐厅", "商场"]
    assert result.value["context"]["kind"] == "nearby"
    assert result.value["context"]["location"] == ""
    assert result.value["context"]["radius"] == 3000
    assert plugin.messages == []


@pytest.mark.asyncio
async def test_broad_request_keeps_provider_failure_as_error() -> None:
    plugin = _FailedLocationPlugin()
    router = NearbyRouter()
    router._bind(plugin)

    result = await router.search_nearby(
        query="公园",
        _ctx={"latest_user_request": "我附近有啥地方可去吗？"},
    )

    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_specific_request_keeps_provider_failure_as_error() -> None:
    plugin = _FailedLocationPlugin()
    router = NearbyRouter()
    router._bind(plugin)

    result = await router.search_nearby(
        query="公园",
        _ctx={"latest_user_request": "附近的公园"},
    )

    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_location_clarification_preserves_resolved_nearby_query() -> None:
    router = NearbyRouter()
    router._bind(_AmbiguousLocationPlugin())

    result = await router.search_nearby(
        query="公园",
        location="吉林",
        radius=2500,
        _ctx={"latest_user_request": "吉林附近的公园"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["context"]["query"] == "公园"
    assert result.value["context"]["category_id"] == "park"
    assert result.value["context"]["location"] == "吉林"
    assert result.value["context"]["radius"] == 2500
