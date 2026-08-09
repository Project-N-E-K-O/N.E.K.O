import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent_server.channels.user_plugin import _plugin_terminal_status
from plugin.sdk.plugin import Err, Ok

from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationResolver,
)
from plugin.plugins.lifekit._api import GeocodeError
from plugin.plugins.lifekit._contracts import (
    AddLocationResult,
    AirQualityResult,
    FoodRecommendResult,
    GetWeatherResult,
    HourlyForecastResult,
    NearbyResult,
    TravelAdviceResult,
    TripAdviceResult,
)
from plugin.plugins.lifekit.routers.air_quality import AirQualityRouter
from plugin.plugins.lifekit.routers.current import CurrentWeatherRouter
from plugin.plugins.lifekit.routers.food import FoodRecommendRouter
from plugin.plugins.lifekit.routers.hourly import HourlyForecastRouter
from plugin.plugins.lifekit.routers.locations import LocationsRouter
from plugin.plugins.lifekit.routers.travel import TravelAdviceRouter
from plugin.plugins.lifekit.routers.trip import TripRouter


class _AmbiguousLocationPlugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")

    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.location_ambiguous"


class _FailedLocationPlugin(_AmbiguousLocationPlugin):
    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.geocode_failed"


class _ClarifiableLocationPlugin(_AmbiguousLocationPlugin):
    def __init__(self, error_key: str) -> None:
        super().__init__()
        self.error_key = error_key

    async def _resolve_location(self, *_: Any, **__: Any):
        return None, self.error_key


class _AmbiguousDestinationPlugin(_AmbiguousLocationPlugin):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def _resolve_location(self, *_: Any, **__: Any):
        self.calls += 1
        if self.calls == 1:
            return {"city": "上海", "lat": 31.2, "lon": 121.5}, ""
        return None, "error.location_ambiguous"


class _Logger:
    def info(self, *_: Any, **__: Any) -> None:
        return None

    def warning(self, *_: Any, **__: Any) -> None:
        return None


class _LocationStorePlugin(_AmbiguousLocationPlugin):
    logger = _Logger()

    def __init__(self) -> None:
        super().__init__()
        self._locations_lock = asyncio.Lock()


@pytest.mark.asyncio
async def test_weather_returns_the_primary_same_named_city_with_risk_disclosed() -> None:
    async def open_meteo_candidates(*_: Any, **__: Any):
        return [
            LocationCandidate(
                display_name="吉林市",
                latitude=43.85,
                longitude=126.56,
                country_code="CN",
                admin1="Jilin Province",
                precision="city",
                source="open_meteo",
            ),
            LocationCandidate(
                display_name="吉林",
                latitude=25.00,
                longitude=121.89,
                country_code="TW",
                admin1="台湾",
                precision="city",
                source="open_meteo",
            ),
        ]

    async def no_nominatim_candidates(*_: Any, **__: Any):
        return []

    class _ReadOnlyWeatherPlugin:
        plugin_id = "lifekit"
        _resolve_location = LifeKitPlugin._resolve_location
        logger = _Logger()

        def __init__(self) -> None:
            self._cfg = {"enable_geoip": False}
            self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
            self._location_resolver = LocationResolver(
                open_meteo=open_meteo_candidates,
                nominatim=no_nominatim_candidates,
            )

        def _resolve_locale(self) -> None:
            self._i18n.set_locale("zh-CN")

        async def _get_weather_data(self, loc: dict[str, Any]):
            assert loc["city"] == "吉林市"
            return {
                "current": {
                    "weather_code": 0,
                    "temperature_2m": 24,
                    "apparent_temperature": 23,
                    "relative_humidity_2m": 50,
                    "wind_speed_10m": 8,
                    "uv_index": 3,
                },
                "daily": {"time": []},
            }, None

        def _wmo_text(self, _: int) -> str:
            return "晴"

        def push_message(self, **_: Any) -> dict[str, bool]:
            return {"ok": True}

    router = CurrentWeatherRouter()
    router._bind(_ReadOnlyWeatherPlugin())

    result = await router.get_weather(city="吉林")

    assert isinstance(result, Ok)
    assert result.value["status"] == "ready"
    assert result.value["city"] == "吉林市"
    assert result.value["assumed"] is True
    assert result.value["assumed_location"] == "吉林市 · Jilin Province · CN"
    assert "吉林 · 台湾 · TW" in result.value["ambiguity_warning"]
    assert result.value["ambiguity_warning"] in result.value["summary"]


@pytest.mark.asyncio
async def test_weather_without_any_location_returns_completed_unavailable() -> None:
    router = CurrentWeatherRouter()
    router._bind(_AmbiguousLocationPlugin())

    result = await router.get_weather(city="不存在的模糊地点")

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["summary"]
    assert _plugin_terminal_status(True, result.value) == "completed"
    GetWeatherResult.model_validate(result.value)


@pytest.mark.parametrize(
    ("router_type", "method_name", "kwargs"),
    [
        (HourlyForecastRouter, "hourly_forecast", {"city": "上海"}),
        (AirQualityRouter, "air_quality", {"city": "上海"}),
        (TravelAdviceRouter, "travel_advice", {"city": "上海"}),
        (
            FoodRecommendRouter,
            "food_recommend",
            {"location": "上海", "cuisine": "火锅"},
        ),
        (
            TripRouter,
            "trip_advice",
            {"origin": "上海", "destination": "北京"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_read_only_location_entries_return_unavailable_instead_of_blocking(
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_AmbiguousLocationPlugin())

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["summary"]
    assert _plugin_terminal_status(True, result.value) == "completed"


@pytest.mark.parametrize(
    "error_key",
    [
        "error.location_confirmation_required",
        "error.city_not_found",
        "error.no_location",
    ],
)
@pytest.mark.parametrize(
    ("router_type", "method_name", "kwargs"),
    [
        (CurrentWeatherRouter, "get_weather", {"city": "上海"}),
        (HourlyForecastRouter, "hourly_forecast", {"city": "上海"}),
        (AirQualityRouter, "air_quality", {"city": "上海"}),
        (TravelAdviceRouter, "travel_advice", {"city": "上海"}),
        (
            FoodRecommendRouter,
            "food_recommend",
            {"location": "上海", "cuisine": "火锅"},
        ),
        (
            TripRouter,
            "trip_advice",
            {"origin": "上海", "destination": "北京"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_read_only_entries_complete_for_every_unusable_location_outcome(
    error_key: str,
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_ClarifiableLocationPlugin(error_key))

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["summary"]
    assert _plugin_terminal_status(True, result.value) == "completed"


@pytest.mark.asyncio
async def test_weather_provider_failure_returns_completed_unavailable() -> None:
    router = CurrentWeatherRouter()
    router._bind(_FailedLocationPlugin())

    result = await router.get_weather(city="上海")

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert _plugin_terminal_status(True, result.value) == "completed"


@pytest.mark.parametrize(
    ("router_type", "method_name", "kwargs"),
    [
        (HourlyForecastRouter, "hourly_forecast", {"city": "上海"}),
        (AirQualityRouter, "air_quality", {"city": "上海"}),
        (TravelAdviceRouter, "travel_advice", {"city": "上海"}),
        (
            FoodRecommendRouter,
            "food_recommend",
            {"location": "上海", "cuisine": "火锅"},
        ),
        (
            TripRouter,
            "trip_advice",
            {"origin": "上海", "destination": "北京"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_read_only_entries_complete_on_location_provider_failure(
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_FailedLocationPlugin())

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert _plugin_terminal_status(True, result.value) == "completed"


@pytest.mark.asyncio
async def test_trip_with_no_usable_destination_returns_completed_unavailable() -> None:
    router = TripRouter()
    router._bind(_AmbiguousDestinationPlugin())

    result = await router.trip_advice(
        origin="上海",
        destination="朝阳",
        mode="transit",
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert _plugin_terminal_status(True, result.value) == "completed"


@pytest.mark.parametrize(
    "cause",
    ["ambiguous", "needs_confirmation", "not_found", "no_location"],
)
@pytest.mark.asyncio
async def test_add_location_entry_clarifies_user_correctable_geocode_outcome(
    monkeypatch: Any,
    cause: str,
) -> None:
    async def unresolved(*_: Any, **__: Any):
        raise GeocodeError("unresolved", cause=cause)

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        unresolved,
    )
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())

    result = await router.add_location(
        label="出差",
        city="上海",
        address="浦东新区",
        set_default=True,
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["summary"]
    assert result.value["context"]["address"] == "浦东新区"
    assert result.value["context"]["set_default"] is True


@pytest.mark.asyncio
async def test_add_location_entry_keeps_provider_failure_as_error(
    monkeypatch: Any,
) -> None:
    async def failed(*_: Any, **__: Any):
        raise GeocodeError("failed", cause="network")

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        failed,
    )
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())

    result = await router.add_location(label="出差", city="上海")

    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_add_location_success_uses_localized_summary(monkeypatch: Any) -> None:
    async def resolved(*_: Any, **__: Any):
        return {
            "city": "上海",
            "lat": 31.2,
            "lon": 121.5,
            "country": "CN",
        }

    async def load_locations() -> list[dict[str, Any]]:
        return []

    async def save_locations(_: list[dict[str, Any]]) -> bool:
        return True

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        resolved,
    )
    plugin = _LocationStorePlugin()
    router = LocationsRouter()
    router._bind(plugin)
    monkeypatch.setattr(router, "_load", load_locations)
    monkeypatch.setattr(router, "_save", save_locations)

    result = await router.add_location(label="家", city="上海")

    assert isinstance(result, Ok)
    assert result.value["summary"] == "已添加：家（上海）"
    assert result.value["message"] == result.value["summary"]


@pytest.mark.parametrize("outcome", [None, RuntimeError("unexpected provider error")])
@pytest.mark.asyncio
async def test_add_location_entry_classifies_untyped_geocode_outcomes(
    monkeypatch: Any,
    outcome: object,
) -> None:
    async def geocode_outcome(*_: Any, **__: Any):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        geocode_outcome,
    )
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())

    result = await router.add_location(label="出差", city="上海")

    if outcome is None:
        assert isinstance(result, Ok)
        assert result.value["status"] == "clarify"
    else:
        assert isinstance(result, Err)


def test_write_location_result_contract_preserves_clarification() -> None:
    result = AddLocationResult.model_validate(
        {"status": "clarify", "summary": "请补充位置", "choices": []}
    )

    assert result.status == "clarify"
    assert result.summary == "请补充位置"


@pytest.mark.parametrize(
    "result_model",
    [
        GetWeatherResult,
        HourlyForecastResult,
        AirQualityResult,
        TravelAdviceResult,
        FoodRecommendResult,
        NearbyResult,
        TripAdviceResult,
    ],
)
def test_read_only_location_contracts_reject_blocking_clarification(
    result_model: type,
) -> None:
    with pytest.raises(ValidationError):
        result_model.model_validate(
            {"status": "clarify", "summary": "请补充位置", "choices": []}
        )


@pytest.mark.parametrize(
    "result_model",
    [
        AddLocationResult,
        GetWeatherResult,
        HourlyForecastResult,
        AirQualityResult,
        TravelAdviceResult,
        FoodRecommendResult,
        NearbyResult,
        TripAdviceResult,
    ],
)
def test_location_result_contracts_reject_incomplete_ready_payload(
    result_model: type,
) -> None:
    with pytest.raises(ValidationError):
        result_model.model_validate({"status": "ready"})


@pytest.mark.parametrize(
    "result_model",
    [
        AddLocationResult,
        GetWeatherResult,
        HourlyForecastResult,
        AirQualityResult,
        TravelAdviceResult,
        FoodRecommendResult,
        NearbyResult,
        TripAdviceResult,
    ],
)
def test_location_result_contracts_reject_clarification_without_summary(
    result_model: type,
) -> None:
    with pytest.raises(ValidationError):
        result_model.model_validate({"status": "clarify"})

    with pytest.raises(ValidationError):
        result_model.model_validate({"status": "clarify", "summary": "   "})


@pytest.mark.parametrize(
    ("result_model", "payload"),
    [
        (
            AddLocationResult,
            {
                "summary": "已保存",
                "message": "已保存",
                "location": {"city": "上海"},
            },
        ),
        (
            GetWeatherResult,
            {
                "city": "上海",
                "summary": "晴",
                "current": {"temperature": 30},
                "forecast": [],
            },
        ),
        (
            HourlyForecastResult,
            {"city": "上海", "summary": "未来两小时", "hours": [], "total_hours": 0},
        ),
        (
            AirQualityResult,
            {"city": "上海", "summary": "良", "aqi": {"value": 42}, "advice": []},
        ),
        (
            TravelAdviceResult,
            {"city": "上海", "summary": "适合出行", "tips": []},
        ),
        (
            FoodRecommendResult,
            {"summary": "推荐", "recommendations": [], "query": "火锅"},
        ),
        (
            NearbyResult,
            {
                "summary": "未找到",
                "request": "附近有什么值得逛的",
                "searched_terms": ["商店", "书店", "咖啡馆"],
                "results": [],
                "count": 0,
            },
        ),
        (
            TripAdviceResult,
            {
                "origin": "上海",
                "destination": "北京",
                "distance_km": 1067.0,
                "summary": "路线建议",
                "routes": [],
            },
        ),
    ],
)
def test_location_result_contracts_accept_complete_ready_payload(
    result_model: type,
    payload: dict[str, Any],
) -> None:
    result = result_model.model_validate({"status": "ready", **payload})

    assert result.status == "ready"
