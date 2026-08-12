import asyncio
from pathlib import Path
from typing import Any

import pytest
from plugin.plugins.lifekit import LifeKitPlugin
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
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationResolver,
)
from plugin.plugins.lifekit._routing import Route, RouteStep, RoutingResult
from plugin.plugins.lifekit.routers.air_quality import AirQualityRouter
from plugin.plugins.lifekit.routers.current import CurrentWeatherRouter
from plugin.plugins.lifekit.routers.food import FoodRecommendRouter
from plugin.plugins.lifekit.routers.hourly import HourlyForecastRouter
from plugin.plugins.lifekit.routers.locations import LocationsRouter
from plugin.plugins.lifekit.routers.travel import TravelAdviceRouter
from plugin.plugins.lifekit.routers.trip import TripRouter
from plugin.sdk.plugin import Err, Ok
from pydantic import ValidationError


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


async def _confirmed_add(router: LocationsRouter, **payload: Any):
    challenge = await router.add_location(**payload)
    assert isinstance(challenge, Ok)
    assert challenge.value["status"] == "clarify"
    return await router.add_location(**challenge.value["context"])


@pytest.mark.asyncio
async def test_location_write_requires_explicit_confirmation(monkeypatch: Any) -> None:
    geocode_called = False

    async def geocode(*_: Any, **__: Any):
        nonlocal geocode_called
        geocode_called = True
        return None

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        geocode,
    )
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())

    result = await router.add_location(label="家", city="上海")

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["context"]["confirmed"] is True
    assert result.value["context"]["confirmation_token"]
    assert result.value["confirmation_token"] == result.value["context"]["confirmation_token"]
    assert geocode_called is False

    bypass = await router.add_location(
        label="家", city="上海", confirmed=True, _ctx={"source": "chat"},
    )
    assert bypass.value["status"] == "clarify"
    assert geocode_called is False

    confirmed = await router.add_location(
        **bypass.value["context"],
        _ctx={"source": "chat"},
    )
    assert confirmed.value["status"] == "clarify"
    assert geocode_called is True


@pytest.mark.parametrize("method_name", ["remove_location", "set_default_location"])
@pytest.mark.asyncio
async def test_saved_location_mutations_require_confirmation(
    monkeypatch: Any,
    method_name: str,
) -> None:
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())
    save_called = False

    async def load_locations() -> list[dict[str, Any]]:
        return [{"id": "home", "label": "家", "is_default": True}]

    async def save_locations(_: list[dict[str, Any]]) -> bool:
        nonlocal save_called
        save_called = True
        return True

    monkeypatch.setattr(router, "_load", load_locations)
    monkeypatch.setattr(router, "_save", save_locations)

    result = await getattr(router, method_name)(location_id="home")

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["context"]["confirmed"] is True
    assert save_called is False


class _WeatherProviderFailurePlugin(_AmbiguousLocationPlugin):
    logger = _Logger()

    def __init__(self) -> None:
        super().__init__()
        self._cfg = {"timezone": "Asia/Shanghai"}

    async def _resolve_location(self, *_: Any, **__: Any):
        return {"city": "上海", "lat": 31.2, "lon": 121.5}, None

    async def _get_weather_data(self, *_: Any, **__: Any):
        return None, "error.forecast_timeout"


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
async def test_weather_reports_timezone_mismatch_without_claiming_proxy() -> None:
    class _TimezonePlugin(_AmbiguousLocationPlugin):
        def _resolve_locale(self) -> None:
            self._i18n.set_locale("en")

        async def _resolve_location(self, *_: Any, **__: Any):
            return {
                "city": "New York",
                "lat": 40.71,
                "lon": -74.00,
                "_timezone_mismatch": True,
            }, None

        async def _get_weather_data(self, *_: Any, **__: Any):
            return {
                "current": {
                    "weather_code": 0,
                    "temperature_2m": 20,
                    "apparent_temperature": 20,
                    "relative_humidity_2m": 50,
                    "wind_speed_10m": 5,
                },
                "daily": {},
            }, None

        def _wmo_text(self, _code: int) -> str:
            return "Clear"

        def push_message(self, **_: Any) -> dict[str, bool]:
            return {"ok": True}

    router = CurrentWeatherRouter()
    router._bind(_TimezonePlugin())

    result = await router.get_weather()

    assert isinstance(result, Ok)
    assert result.value["timezone_mismatch"] is True
    assert result.value["vpn_detected"] is False
    assert "timezone" in result.value["summary"].casefold()
    assert "proxy" not in result.value["summary"].casefold()
    assert "vpn" not in result.value["summary"].casefold()


@pytest.mark.asyncio
async def test_weather_without_any_location_fails_before_query() -> None:
    router = CurrentWeatherRouter()
    router._bind(_AmbiguousLocationPlugin())

    result = await router.get_weather(city="不存在的模糊地点")

    assert isinstance(result, Err)
    assert result.error.code == "LOCATION_REQUIRED"
    assert result.error.details["summary"]


@pytest.mark.parametrize(
    ("router_type", "entry_name", "result_model"),
    [
        (CurrentWeatherRouter, "get_weather", GetWeatherResult),
        (TravelAdviceRouter, "travel_advice", TravelAdviceResult),
    ],
)
@pytest.mark.asyncio
async def test_weather_provider_failures_fail_the_entry(
    router_type: type,
    entry_name: str,
    result_model: type,
) -> None:
    router = router_type()
    router._bind(_WeatherProviderFailurePlugin())

    result = await getattr(router, entry_name)(city="上海")

    assert isinstance(result, Err)
    assert result.error.code == "UPSTREAM_UNAVAILABLE"
    assert result.error.details["summary"]


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
async def test_read_only_location_entries_fail_when_location_is_ambiguous(
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_AmbiguousLocationPlugin())

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Err)
    assert result.error.code == "LOCATION_REQUIRED"
    assert result.error.details["summary"]


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
async def test_read_only_entries_fail_for_every_unusable_location_outcome(
    error_key: str,
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_ClarifiableLocationPlugin(error_key))

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Err)
    assert result.error.code == "LOCATION_REQUIRED"
    assert result.error.details["summary"]


@pytest.mark.asyncio
async def test_weather_location_provider_failure_fails_the_entry() -> None:
    router = CurrentWeatherRouter()
    router._bind(_FailedLocationPlugin())

    result = await router.get_weather(city="上海")

    assert isinstance(result, Err)
    assert result.error.code == "LOCATION_PROVIDER_UNAVAILABLE"


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
async def test_read_only_entries_fail_on_location_provider_failure(
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_FailedLocationPlugin())

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Err)
    assert result.error.code == "LOCATION_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_trip_with_no_usable_destination_fails_before_routing() -> None:
    router = TripRouter()
    router._bind(_AmbiguousDestinationPlugin())

    result = await router.trip_advice(
        origin="上海",
        destination="朝阳",
        mode="transit",
    )

    assert isinstance(result, Err)
    assert result.error.code == "LOCATION_REQUIRED"


@pytest.mark.asyncio
async def test_trip_route_provider_failure_fails_the_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TripPlugin(_WeatherProviderFailurePlugin):
        async def _resolve_location(self, value: str | None, **_: Any):
            if value == "北京":
                return {"city": "北京", "lat": 39.9, "lon": 116.4}, None
            return {"city": "上海", "lat": 31.2, "lon": 121.5}, None

    async def failed_plan(*_: Any, **__: Any) -> RoutingResult:
        return RoutingResult(
            origin_name="上海",
            destination_name="北京",
            error="timeout:routing budget exceeded",
        )

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.trip.RoutingService.plan",
        failed_plan,
    )
    router = TripRouter()
    router._bind(_TripPlugin())

    result = await router.trip_advice(origin="上海", destination="北京")

    assert isinstance(result, Err)
    assert result.error.code == "UPSTREAM_UNAVAILABLE"


@pytest.mark.asyncio
async def test_unknown_trip_mode_falls_back_without_hiding_the_assumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_modes: list[list[str] | None] = []

    class _TripPlugin(_WeatherProviderFailurePlugin):
        async def _resolve_location(self, value: str | None, **_: Any):
            if value == "北京":
                return {"city": "北京", "lat": 39.9, "lon": 116.4}, None
            return {"city": "上海", "lat": 31.2, "lon": 121.5}, None

        def push_message(self, **_: Any) -> dict[str, bool]:
            return {"ok": True}

    async def successful_plan(*_: Any, **kwargs: Any) -> RoutingResult:
        captured_modes.append(kwargs.get("modes"))
        return RoutingResult(origin_name="上海", destination_name="北京", provider="test")

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.trip.RoutingService.plan",
        successful_plan,
    )
    router = TripRouter()
    router._bind(_TripPlugin())

    result = await router.trip_advice(
        origin="上海",
        destination="北京",
        mode="motorcycle",
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "ready"
    assert captured_modes == [None]
    assert result.value["requested_mode"] == "motorcycle"
    assert result.value["selected_mode"] == "auto"
    assert "motorcycle" in result.value["mode_assumption"]
    assert "bicycling" not in result.value["mode_assumption"]
    assert "driving" not in result.value["mode_assumption"]
    assert result.value["mode_assumption"] in result.value["summary"]


@pytest.mark.asyncio
async def test_trip_router_localizes_structured_provider_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TripPlugin(_WeatherProviderFailurePlugin):
        def _resolve_locale(self) -> None:
            self._i18n.set_locale("en")

        async def _resolve_location(self, value: str | None, **_: Any):
            if value == "Paris":
                return {"city": "Paris", "lat": 48.85, "lon": 2.35}, None
            return {"city": "London", "lat": 51.50, "lon": -0.12}, None

        def push_message(self, **_: Any) -> dict[str, bool]:
            return {"ok": True}

    async def successful_plan(*_: Any, **__: Any) -> RoutingResult:
        return RoutingResult(
            origin_name="London",
            destination_name="Paris",
            provider="test",
            routes=[
                Route(
                    mode="transit",
                    distance_m=1000,
                    duration_s=600,
                    steps=[
                        RouteStep(
                            instruction="",
                            distance_m=900,
                            duration_s=500,
                            mode="subway",
                            line_name="Metro Line 2",
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.trip.RoutingService.plan",
        successful_plan,
    )
    router = TripRouter()
    router._bind(_TripPlugin())

    result = await router.trip_advice(
        origin="London",
        destination="Paris",
        mode="transit",
    )

    assert isinstance(result, Ok)
    instruction = result.value["routes"][0]["steps"][0]["instruction"]
    assert instruction == "Take Metro Line 2"
    assert "乘坐" not in instruction


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

    result = await _confirmed_add(
        router,
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

    result = await _confirmed_add(router, label="出差", city="上海")

    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_add_location_success_uses_localized_summary(monkeypatch: Any) -> None:
    geocode_queries: list[str] = []

    async def resolved(*_: Any, **__: Any):
        geocode_queries.append(str(_[0]))
        return {
            "city": "上海",
            "lat": 31.2,
            "lon": 121.5,
            "country": "CN",
            "timezone": "Asia/Shanghai",
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

    result = await _confirmed_add(
        router, label="家", city="上海", address="南京东路 1 号",
    )

    assert isinstance(result, Ok)
    assert result.value["summary"] == "已添加：家（上海）"
    assert result.value["message"] == result.value["summary"]
    assert geocode_queries == ["上海 南京东路 1 号"]
    assert result.value["location"]["timezone"] == "Asia/Shanghai"
    assert result.value["location"]["country_code"] == "CN"
    assert result.value["location"]["verified"] is False


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

    result = await _confirmed_add(router, label="出差", city="上海")

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
        GetWeatherResult,
        HourlyForecastResult,
        AirQualityResult,
        TravelAdviceResult,
        FoodRecommendResult,
        NearbyResult,
        TripAdviceResult,
    ],
)
def test_read_only_result_contracts_reject_failures_disguised_as_results(
    result_model: type,
) -> None:
    with pytest.raises(ValidationError):
        result_model.model_validate(
            {"status": "unavailable", "summary": "上游服务不可用"}
        )
    assert result_model.model_json_schema()["properties"]["status"] == {
        "enum": ["ready"],
        "title": "Status",
        "type": "string",
    }


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
                "location": {
                    "label": "家",
                    "city": "上海",
                    "lat": 31.2,
                    "lon": 121.5,
                },
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
