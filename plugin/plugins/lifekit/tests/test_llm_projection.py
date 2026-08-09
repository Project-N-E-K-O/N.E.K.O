"""LifeKit entry results must remain useful after projection into LLM context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from plugin.sdk.plugin import Ok
from utils.result_parser import parse_plugin_result

from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit import _poi
from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationProblem,
    LocationPurpose,
)
from plugin.plugins.lifekit.routers.nearby import NearbyRouter
from plugin.plugins.lifekit.routers.air_quality import AirQualityRouter
from plugin.plugins.lifekit.routers.current import CurrentWeatherRouter
from plugin.plugins.lifekit.routers.food import FoodRecommendRouter
from plugin.plugins.lifekit.routers.hourly import HourlyForecastRouter
from plugin.plugins.lifekit.routers.locations import LocationsRouter
from plugin.plugins.lifekit.routers.travel import TravelAdviceRouter
from plugin.plugins.lifekit.routers.trip import TripRouter


class _AmbiguousRoadPlugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._cfg: dict[str, Any] = {}
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
        self.logger = _NoopLogger()

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")

    async def _resolve_location(self, *_: Any, **__: Any):
        return None, LocationProblem(
            error_key="error.location_ambiguous",
            requested_location="南京东路",
            purpose=LocationPurpose.NEARBY,
            candidates=(
                LocationCandidate(
                    display_name="南京东路",
                    latitude=31.235,
                    longitude=121.475,
                    country_code="CN",
                    admin1="上海市",
                    admin2="上海市",
                    precision="address",
                    source="nominatim",
                ),
                LocationCandidate(
                    display_name="南京东路",
                    latitude=31.45,
                    longitude=121.10,
                    country_code="CN",
                    admin1="江苏省",
                    admin2="太仓市",
                    precision="address",
                    source="nominatim",
                ),
            ),
        )


class _NoopLogger:
    def info(self, *_: Any, **__: Any) -> None:
        pass

    def warning(self, *_: Any, **__: Any) -> None:
        pass


def test_nearby_entry_exposes_only_the_discovery_plan_interface() -> None:
    entry = NearbyRouter().collect_entries()["search_nearby"]
    schema = entry.meta.input_schema or {}
    properties = schema.get("properties", {})

    assert "request" in properties
    assert "search_terms" in properties
    assert properties["search_terms"]["maxItems"] == 4
    assert "不要要求用户先明确地点类别" in properties["search_terms"]["description"]
    assert "query" not in properties
    assert set(schema.get("required", [])) == {"request", "search_terms"}


@pytest.mark.asyncio
async def test_ambiguous_nearby_results_are_visible_and_actionable_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SuccessfulClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            query = str(kwargs.get("data", {}).get("data", ""))
            is_shanghai = "31.235,121.475" in query
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 1 if is_shanghai else 2,
                            "lat": 31.236 if is_shanghai else 31.451,
                            "lon": 121.476 if is_shanghai else 121.101,
                            "tags": {
                                "name": "上海景点" if is_shanghai else "太仓景点",
                                "tourism": "attraction",
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _SuccessfulClient())
    router = NearbyRouter()
    router._bind(_AmbiguousRoadPlugin())

    result = await router.search_nearby(
        request="南京东路附近的景点",
        search_terms=["景点"],
        location="南京东路",
        _ctx={"latest_user_request": "南京东路附近的景点"},
    )

    assert isinstance(result, Ok)
    entry = router.collect_entries()["search_nearby"]
    detail = parse_plugin_result(
        result.value,
        llm_result_fields=entry.meta.llm_result_fields,
        lang="zh-CN",
    )

    assert "位置名称有歧义" in detail
    assert "上海市" in detail
    assert "上海景点" in detail
    assert "太仓市" in detail
    assert "太仓景点" in detail
    assert "补充城市" in detail


@pytest.mark.parametrize(
    ("router_type", "entry_id"),
    [
        (LocationsRouter, "add_location"),
        (HourlyForecastRouter, "hourly_forecast"),
        (NearbyRouter, "search_nearby"),
        (FoodRecommendRouter, "food_recommend"),
        (CurrentWeatherRouter, "get_weather"),
        (AirQualityRouter, "air_quality"),
        (TravelAdviceRouter, "travel_advice"),
        (TripRouter, "trip_advice"),
    ],
)
def test_every_clarifiable_entry_projects_status_and_summary_to_llm(
    router_type: type,
    entry_id: str,
) -> None:
    entry = router_type().collect_entries()[entry_id]

    if router_type is NearbyRouter:
        assert entry.meta.llm_result_fields == [
            "status",
            "summary",
            "request",
            "searched_terms",
            "results",
            "location_groups",
        ]
    else:
        assert entry.meta.llm_result_fields == ["status", "summary"]
