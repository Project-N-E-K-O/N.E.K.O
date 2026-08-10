"""LifeKit entry results must remain useful after projection into LLM context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from plugin.plugins.lifekit import _poi
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationProblem,
    LocationPurpose,
    assumed_location_payload,
)
from plugin.plugins.lifekit.routers.air_quality import AirQualityRouter
from plugin.plugins.lifekit.routers.current import CurrentWeatherRouter
from plugin.plugins.lifekit.routers.food import FoodRecommendRouter
from plugin.plugins.lifekit.routers.hourly import HourlyForecastRouter
from plugin.plugins.lifekit.routers.locations import LocationsRouter
from plugin.plugins.lifekit.routers.nearby import NearbyRouter
from plugin.plugins.lifekit.routers.travel import TravelAdviceRouter
from plugin.plugins.lifekit.routers.trip import TripRouter
from plugin.sdk.plugin import Ok
from utils.result_parser import parse_plugin_result


class _AmbiguousRoadPlugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._cfg: dict[str, Any] = {}
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
        self.logger = _NoopLogger()

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")

    async def _resolve_location(self, *_: Any, **__: Any):
        candidates = (
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
        )
        return assumed_location_payload(candidates[0], candidates), LocationProblem(
            error_key="error.location_ambiguous",
            requested_location="南京东路",
            purpose=LocationPurpose.NEARBY,
            candidates=candidates,
        )

    async def _get_weather_data(self, *_: Any, **__: Any):
        return None, None


class _NoopLogger:
    def info(self, *_: Any, **__: Any) -> None:
        pass

    def warning(self, *_: Any, **__: Any) -> None:
        pass


def test_nearby_entry_exposes_typed_hints_instead_of_provider_search_terms() -> None:
    entry = NearbyRouter().collect_entries()["search_nearby"]
    schema = entry.meta.input_schema or {}
    properties = schema.get("properties", {})

    assert entry.meta.description["$i18n"] == "entries.searchNearby.description"
    assert "query" in properties
    assert properties["place_intent"]["enum"] == [
        "food",
        "coffee",
        "shopping",
        "outdoors",
        "culture",
        "family",
        "nightlife",
        "service",
        "explore",
    ]
    assert properties["preference_hints"]["maxItems"] == 4
    assert "search_terms" not in properties
    assert "location_hint" in properties
    assert "location" not in properties
    assert "request" not in properties
    assert set(schema.get("required", [])) == {"query"}


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

    assert "位置存在歧义" in detail
    assert "上海市" in detail
    assert "上海景点" in detail
    assert "太仓市" in detail
    assert "太仓景点" not in detail
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
def test_location_entries_project_their_control_and_risk_scalars_to_llm(
    router_type: type,
    entry_id: str,
) -> None:
    entry = router_type().collect_entries()[entry_id]

    if router_type is NearbyRouter:
        assert entry.meta.llm_result_fields == [
            "status",
            "summary",
            "assumed",
            "assumed_location",
            "ambiguity_warning",
            "request",
            "searched_terms",
            "results",
            "location_groups",
        ]
    elif router_type is LocationsRouter:
        assert entry.meta.llm_result_fields == [
            "status", "summary", "message", "location", "choices", "confirmation_token",
        ]
    else:
        common_fields = [
            "status",
            "summary",
            "assumed",
            "assumed_location",
            "ambiguity_warning",
        ]
        useful_fields = {
            HourlyForecastRouter: ["city", "hours", "total_hours"],
            FoodRecommendRouter: [
                "recommendations", "query", "weather_reason", "provider", "next_actions",
            ],
            CurrentWeatherRouter: [
                "city", "current", "forecast", "timezone_mismatch", "vpn_detected",
                "next_actions",
            ],
            AirQualityRouter: ["city", "aqi", "advice", "next_actions"],
            TravelAdviceRouter: [
                "city", "tips", "clothing", "umbrella", "sunscreen", "next_actions",
            ],
                TripRouter: [
                    "origin", "destination", "distance_km", "routes", "weather_tips",
                    "mode_advice", "requested_mode", "selected_mode", "mode_assumption",
                    "provider", "next_actions",
                ],
        }
        assert entry.meta.llm_result_fields == common_fields + useful_fields[router_type]


def test_write_confirmation_token_survives_host_llm_projection() -> None:
    entry = LocationsRouter().collect_entries()["remove_location"]

    detail = parse_plugin_result(
        {
            "status": "clarify",
            "summary": "Confirm removal",
            "choices": ["Confirm", "Cancel"],
            "confirmation_token": "one-time-token",
            "context": {"location_id": "home"},
        },
        llm_result_fields=entry.meta.llm_result_fields,
        lang="en",
    )

    assert "one-time-token" in detail
